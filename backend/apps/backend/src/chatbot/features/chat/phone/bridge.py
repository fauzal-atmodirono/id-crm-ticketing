"""Orchestrates a single phone call: Twilio Media Stream ⇄ Gemini Live."""

from __future__ import annotations

import asyncio
import base64
import time
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
from chatbot.features.chat.phone.transcript_sink import TranscriptSink

if TYPE_CHECKING:
    from chatbot.features.chat.phone.gemini_live import LiveSession
    from chatbot.features.chat.ports import ConversationLogPort, KnowledgePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class PhoneBridge:
    def __init__(
        self,
        live: LiveSession,
        knowledge_port: KnowledgePort,
        conversation_log_port: ConversationLogPort,
        send_twilio: Callable[[dict[str, object]], Awaitable[None]],
        settings: Settings,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._live = live
        self._knowledge = knowledge_port
        self._log_port = conversation_log_port
        self._send_twilio = send_twilio
        self._settings = settings
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.handoff: dict[str, str] | None = None
        self.csat_score: int | None = None
        # Ticket created at call start (Task 3), not just at hangup, so a
        # live transcript has somewhere to stream into. None until the
        # "start" event fires with the flag on, or (fallback) until
        # finalize() creates it the old way.
        self.ticket_id: str | None = None
        self._sink = TranscriptSink(
            flush_seconds=settings.phone_transcript_flush_seconds,
            now=clock or time.monotonic,
        )
        # Queued live-transcript blocks are posted by a single detached
        # worker task, one at a time, so Chatwoot latency never stalls the
        # audio pump AND blocks land in the order they were spoken (posting
        # each block as its own concurrent task would race on the network).
        self._flush_queue: asyncio.Queue[str] = asyncio.Queue()
        self._flush_worker: asyncio.Task[None] | None = None

    async def handle_twilio(self, msg: dict[str, object]) -> None:
        event = msg.get("event")
        if event == "start":
            start = msg.get("start")
            if isinstance(start, dict):
                sid = start.get("streamSid")
                self.stream_sid = str(sid) if sid is not None else None
                csid = start.get("callSid")
                self.call_sid = str(csid) if csid is not None else None
                if (
                    self.call_sid
                    and self.ticket_id is None
                    and self._settings.phone_transcript_live_enabled
                ):
                    await self._create_ticket_at_start(self.call_sid)
        elif event == "media":
            media = msg.get("media")
            if isinstance(media, dict):
                payload = media.get("payload")
                if payload:
                    pcm = mulaw8k_to_pcm16k(base64.b64decode(str(payload)))
                    await self._live.send_audio(pcm)
        # "stop"/"connected" need no action here; finalize() runs on socket close.

    async def _create_ticket_at_start(self, call_sid: str) -> None:
        """Create the conversation's ticket as soon as the call starts.

        Fail-open: a Chatwoot outage here must degrade to "no ticket yet,
        no live transcript" rather than drop the call -- the caller (the
        websocket handler) awaits this in series with nothing else running
        yet, so a slow/failing call here delays call setup, not live audio.
        ``ensure_conversation_ticket`` is keyed on session_id (find-or-
        create), so a retried/duplicated "start" event, or finalize()'s own
        fallback create, safely resolves to the SAME ticket -- never a
        second one for the same call.
        """
        session_id = f"phone-{call_sid}"
        try:
            self.ticket_id = await self._log_port.ensure_conversation_ticket(
                session_id=session_id,
                subject=f"[phone] Conversation {session_id}",
                customer_name=None,
                customer_phone=None,
            )
        except Exception as e:
            _log.error("phone_ticket_create_failed", session_id=session_id, error=str(e))

    def _poll_transcript_flush(self) -> None:
        """Check whether the sink has a block due to post, and if so queue
        it for the detached flush worker.

        Called on EVERY pump() loop tick -- not only right after a
        transcript fragment is added -- because ``TranscriptSink`` has no
        clock of its own: its flush-interval timer only advances when
        something calls ``take_if_due()``. Restricting this call to the
        InputTranscript/OutputTranscript branches would mean a long
        assistant reply (all AudioOut/OutputTranscript, no new caller
        speech) never gets polled between transcript fragments, so a
        completed caller turn could sit unposted until finalize()'s forced
        flush at call end -- silently defeating "live" streaming.
        """
        if self.ticket_id is None:
            return
        block = self._sink.take_if_due()
        if block is None:
            return
        self._flush_queue.put_nowait(block)
        if self._flush_worker is None or self._flush_worker.done():
            self._flush_worker = asyncio.create_task(self._run_flush_worker())

    async def _run_flush_worker(self) -> None:
        """Drain the flush queue, posting one block at a time.

        Runs as a task detached from pump()'s event loop so a slow or
        failing Chatwoot call never stalls the audio pump; posting blocks
        one-at-a-time (rather than one task per block) is what keeps them
        landing in the ticket in the order they were spoken -- concurrent
        HTTP calls could otherwise complete out of order. A failed post is
        logged and skipped, not retried, so one bad block can't wedge every
        block queued after it.
        """
        while True:
            try:
                block = self._flush_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            ticket_id = self.ticket_id
            if ticket_id is None:
                return  # ticket vanished (shouldn't happen); nothing to post to
            try:
                await self._log_port.append_conversation_comment(ticket_id, block)
            except Exception as e:
                _log.error("phone_transcript_flush_failed", call_sid=self.call_sid, error=str(e))

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
                is_new_user_turn = not (self.transcript and self.transcript[-1][0] == "USER")
                self._append_transcript("USER", event.text)
                if self._settings.phone_transcript_live_enabled:
                    self._sink.add("USER", event.text)
                if is_new_user_turn and self._settings.phone_language_nudge_enabled:
                    await self._live.send_text_hint(
                        "(Reminder: match your next reply's language to what "
                        "the caller just said, even mid-conversation.)"
                    )
            elif isinstance(event, OutputTranscript):
                self._append_transcript("ASSISTANT", event.text)
                if self._settings.phone_transcript_live_enabled:
                    self._sink.add("ASSISTANT", event.text)
            elif isinstance(event, ToolCall):
                await self._handle_tool_call(event)
            if self._settings.phone_transcript_live_enabled:
                self._poll_transcript_flush()

    async def finalize(self) -> None:
        # Force out whatever's left in the live-transcript sink (a partial
        # turn, or something that hadn't hit the flush interval yet) BEFORE
        # the whole-transcript summary comment below, so streamed blocks and
        # the final summary land in speaking order. The sink itself is
        # idempotent -- take_if_due() empties what it returns -- so nothing
        # posted live gets posted again here. A call that ends before anyone
        # spoke leaves the sink empty, so this is a no-op (no blank post).
        final_block = self._sink.take_if_due(force=True)
        if final_block is not None and self.ticket_id is not None:
            self._flush_queue.put_nowait(final_block)
        if not self._flush_queue.empty() and (
            self._flush_worker is None or self._flush_worker.done()
        ):
            self._flush_worker = asyncio.create_task(self._run_flush_worker())
        if self._flush_worker is not None:
            await self._flush_worker

        if not self.transcript or not self.call_sid:
            return
        session_id = f"phone-{self.call_sid}"
        body = "\n".join(f"{role}: {text}" for role, text in self.transcript)
        status = "open" if self.handoff is not None else "solved"
        try:
            # Reuse the ticket created at call start (Task 3) when we have
            # one; ensure_conversation_ticket is keyed on session_id anyway,
            # so calling it again here would be safe too -- this just skips
            # a redundant lookup on the common path.
            ticket_id = self.ticket_id or await self._log_port.ensure_conversation_ticket(
                session_id=session_id,
                subject=f"[phone] Conversation {session_id}",
                customer_name=None,
                customer_phone=None,
            )
            self.ticket_id = ticket_id
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
