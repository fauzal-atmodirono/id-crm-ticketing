"""Orchestrates a single phone call: Twilio Media Stream ⇄ Gemini Live."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog

from chatbot.features.chat.csat import record_csat_on_ticket
from chatbot.features.chat.phone.audio_codec import mulaw8k_to_pcm16k, pcm24k_to_mulaw8k
from chatbot.features.chat.phone.handoff_csat_tools import parse_csat_score
from chatbot.features.chat.phone.kb_tool import dispatch_kb_search
from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ToolCall,
)

if TYPE_CHECKING:
    from chatbot.features.chat.phone.gemini_live import LiveSession
    from chatbot.features.chat.ports import ConversationLogPort, KnowledgePort

_log = structlog.get_logger(__name__)


class PhoneBridge:
    def __init__(
        self,
        live: LiveSession,
        knowledge_port: KnowledgePort,
        conversation_log_port: ConversationLogPort,
        send_twilio: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        self._live = live
        self._knowledge = knowledge_port
        self._log_port = conversation_log_port
        self._send_twilio = send_twilio
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.handoff: dict[str, str] | None = None
        self.csat_score: int | None = None

    async def handle_twilio(self, msg: dict[str, object]) -> None:
        event = msg.get("event")
        if event == "start":
            start = msg.get("start")
            if isinstance(start, dict):
                sid = start.get("streamSid")
                self.stream_sid = str(sid) if sid is not None else None
                csid = start.get("callSid")
                self.call_sid = str(csid) if csid is not None else None
        elif event == "media":
            media = msg.get("media")
            if isinstance(media, dict):
                payload = media.get("payload")
                if payload:
                    pcm = mulaw8k_to_pcm16k(base64.b64decode(str(payload)))
                    await self._live.send_audio(pcm)
        # "stop"/"connected" need no action here; finalize() runs on socket close.

    def _append_transcript(self, role: str, text: str) -> None:
        # Gemini Live streams transcription as incremental deltas; concatenate
        # consecutive same-role fragments into one coherent turn rather than many
        # short rows. (If a live smoke test ever shows CUMULATIVE transcripts
        # instead of deltas, switch this to replace-last instead of concatenate.)
        if self.transcript and self.transcript[-1][0] == role:
            prev_role, prev_text = self.transcript[-1]
            self.transcript[-1] = (prev_role, prev_text + text)
        else:
            self.transcript.append((role, text))

    async def _handle_tool_call(self, event: ToolCall) -> None:
        if event.name == "kb_search":
            result = await dispatch_kb_search(event.args, self._knowledge)
            await self._live.send_tool_response(event.id, event.name, result)
        elif event.name == "request_human_handoff":
            self.handoff = {
                "reason": str(event.args.get("reason") or ""),
                "summary": str(event.args.get("summary") or ""),
            }
            await self._live.send_tool_response(event.id, event.name, {"status": "ticket_created"})
        elif event.name == "submit_csat":
            score = parse_csat_score(event.args)
            if score is not None:
                self.csat_score = score
            await self._live.send_tool_response(
                event.id,
                event.name,
                {"status": "recorded" if score is not None else "ignored"},
            )
        else:
            _log.warning("phone_unknown_tool", name=event.name, call_id=event.id)
            await self._live.send_tool_response(
                event.id, event.name, {"error": f"unknown tool: {event.name}"}
            )

    async def pump(self) -> None:
        async for event in self._live.events():
            if isinstance(event, AudioOut):
                if self.stream_sid:
                    mulaw = pcm24k_to_mulaw8k(event.pcm)
                    await self._send_twilio(
                        {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": base64.b64encode(mulaw).decode()},
                        }
                    )
            elif isinstance(event, Interrupted):
                if self.stream_sid:
                    await self._send_twilio({"event": "clear", "streamSid": self.stream_sid})
            elif isinstance(event, InputTranscript):
                self._append_transcript("USER", event.text)
            elif isinstance(event, OutputTranscript):
                self._append_transcript("ASSISTANT", event.text)
            elif isinstance(event, ToolCall):
                await self._handle_tool_call(event)

    async def finalize(self) -> None:
        if not self.transcript or not self.call_sid:
            return
        session_id = f"phone-{self.call_sid}"
        body = "\n".join(f"{role}: {text}" for role, text in self.transcript)
        status = "open" if self.handoff is not None else "solved"
        try:
            ticket_id = await self._log_port.ensure_conversation_ticket(
                session_id=session_id,
                subject=f"[phone] Conversation {session_id}",
                customer_name=None,
                customer_phone=None,
            )
            if self.handoff is not None:
                note = (
                    "[Handoff to human agent]\n"
                    f"{self.handoff.get('reason', '')}\n{self.handoff.get('summary', '')}"
                )
                await self._log_port.append_conversation_comment(ticket_id, note, status="open")
            await self._log_port.append_conversation_comment(ticket_id, body, status=status)
            await self._log_port.set_ticket_external_id(ticket_id, session_id)
            if self.handoff is None and self.csat_score is not None:
                await record_csat_on_ticket(self._log_port, ticket_id, self.csat_score, "phone")
        except Exception as e:
            _log.error(
                "phone_finalize_failed", session_id=session_id, error=str(e), transcript=body
            )
