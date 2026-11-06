"""The driver agent calling a driver: Twilio call -> Gemini Live -> Chatwoot.

A small, separate bridge rather than a branch inside Proton's `PhoneBridge`:
that one is built around a customer calling in (CSAT/NPS survey, transfer to
a human, a ticket per call) and none of it applies when Logisly's agent rings
a driver about a trip. What is shared is the plumbing underneath it: the
Gemini Live session (`phone.gemini_live.connect_live`, ADC/Vertex, the
`gemini_live_model` setting), the audio codecs and the normalised Live events.

Flow: `start_call` asks Twilio to ring the driver app's browser softphone
(`client:logisly-driver`) with inline TwiML that streams the call to
`/logisly/call/stream`, passing the conversation's session id as a stream
parameter. The bridge gives the model the trip and alert context of that
conversation, has it speak first, lets it record trip updates or escalate
through two tools, and on hang-up posts the transcript to the same Chatwoot
conversation (plus the pre-filled escalation ticket when it escalated).
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import quoteattr

import httpx
import structlog
from google.genai import types

from chatbot.features.chat.phone.audio_codec import mulaw8k_to_pcm16k, pcm24k_to_mulaw8k
from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ToolCall,
)
from chatbot.features.logisly import trips as trips_module
from chatbot.features.logisly.channel import ChatwootDriverChannel
from chatbot.features.logisly.escalation import file_escalation

if TYPE_CHECKING:
    from chatbot.features.chat.phone.gemini_live import LiveSession
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

DRIVER_IDENTITY = "logisly-driver"

CALL_INSTRUCTION = (
    "You are Logisly's driver follow-up assistant, now on a PHONE CALL you placed to a "
    "truck driver because they did not answer a WhatsApp message about their trip. "
    "Speak casual, polite Bahasa Indonesia, address the driver as 'Pak', and keep every "
    "turn short: one or two sentences, then let the driver talk. This is spoken audio: no "
    "lists, no markdown, no reading out codes letter by letter unless asked.\n\n"
    "Start by greeting the driver by name, say you are calling from Logisly about their "
    "order, mention briefly why (the alert below), and ask what is happening.\n\n"
    "Traffic, rest stops, refuelling, a tyre the driver is changing, weather and small "
    "delays are normal: confirm what you understood, ask for the new estimated arrival "
    "time if it matters, record it with `update_trip_status` (pass the new arrival time as `eta` when the driver "
    "gives one), and end the call politely. "
    "If the driver is unclear or the line is bad, ask them to repeat; never escalate "
    "because you could not hear them.\n\n"
    "Call `escalate_to_operations` ONLY for an accident or safety risk "
    "(safety_incident), damaged, missing or refused cargo (cargo_issue), a truck that "
    "cannot continue (trip_blocked), or when the driver asks to talk to a person at "
    "Logisly (help_request). Give it a short Bahasa Indonesia summary for the operations "
    "team, then tell the driver the operations team will contact them shortly.\n\n"
    "Never mention tools, statuses or system details, and never mention any company "
    "other than Logisly."
)

START_HINT = (
    "(Panggilan tersambung, driver sudah mengangkat telepon. Mulai bicara sekarang: "
    "sapa driver dan sampaikan alasan Anda menelepon.)"
)

UPDATE_TRIP_TOOL = types.FunctionDeclaration(
    name="update_trip_status",
    description="Record what the driver reported on this trip.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "status": types.Schema(
                type=types.Type.STRING,
                description="One of on_trip, delayed, issue, arrived, delivered.",
            ),
            "note": types.Schema(
                type=types.Type.STRING,
                description="One sentence in Bahasa Indonesia describing what happened.",
            ),
            "eta": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The driver's new estimated arrival, e.g. '2026-11-06 15:45 WIB', "
                    "only if the driver gave one."
                ),
            ),
        },
        required=["status", "note"],
    ),
)

ESCALATE_TOOL = types.FunctionDeclaration(
    name="escalate_to_operations",
    description="Hand this trip problem to Logisly's operations team.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reason": types.Schema(
                type=types.Type.STRING,
                description="One of safety_incident, cargo_issue, trip_blocked, help_request.",
            ),
            "summary": types.Schema(
                type=types.Type.STRING,
                description="Two or three sentences in Bahasa Indonesia for the operations team.",
            ),
        },
        required=["reason", "summary"],
    ),
)

CALL_TOOLS = [types.Tool(function_declarations=[UPDATE_TRIP_TOOL, ESCALATE_TOOL])]


def call_instruction(session_id: str) -> str:
    context = trips_module.context_for_session(session_id)
    return f"{CALL_INSTRUCTION}\n\n{context}" if context else CALL_INSTRUCTION


def stream_twiml(wss_url: str, session_id: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
        f"<Stream url={quoteattr(wss_url)}>"
        f'<Parameter name="session" value={quoteattr(session_id)} />'
        "</Stream></Connect></Response>"
    )


def public_wss_url(settings: Settings) -> str:
    base = settings.twilio_webhook_base_url.rstrip("/")
    return base.replace("https://", "wss://").replace("http://", "ws://") + "/logisly/call/stream"


async def start_call(
    settings: Settings, session_id: str, client: httpx.AsyncClient | None = None
) -> str:
    """Ring the driver app's browser softphone. Returns the Twilio CallSid."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Calls.json"
    data = {
        "To": f"client:{DRIVER_IDENTITY}",
        "From": "client:logisly-operations",
        "Twiml": stream_twiml(public_wss_url(settings), session_id),
    }
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    if client is not None:
        res = await client.post(url, data=data, auth=auth)
    else:
        async with httpx.AsyncClient(timeout=15.0) as c:
            res = await c.post(url, data=data, auth=auth)
    res.raise_for_status()
    return str(res.json()["sid"])


def session_from_start(start_msg: dict[str, Any]) -> str:
    start = start_msg.get("start") if isinstance(start_msg, dict) else None
    params = (start or {}).get("customParameters") or {}
    return str(params.get("session") or "")


class LogislyCallBridge:
    def __init__(
        self,
        live: LiveSession,
        send_twilio: Callable[[dict[str, Any]], Awaitable[None]],
        settings: Settings,
        session_id: str,
        channel: ChatwootDriverChannel | None = None,
    ) -> None:
        self._live = live
        self._send = send_twilio
        self._settings = settings
        self.session_id = session_id
        self._channel = channel
        self.stream_sid: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.escalation: dict[str, str] | None = None
        self.updates: list[str] = []

    @property
    def order_id(self) -> str | None:
        return (trips_module.ACTIVE_SESSIONS.get(self.session_id) or {}).get("order_id")

    async def handle_twilio(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "start":
            start = msg.get("start") or {}
            self.stream_sid = start.get("streamSid")
            await self._live.send_text_hint(START_HINT)
        elif event == "media":
            payload = (msg.get("media") or {}).get("payload")
            if payload:
                await self._live.send_audio(mulaw8k_to_pcm16k(base64.b64decode(str(payload))))

    def _append(self, role: str, text: str) -> None:
        if self.transcript and self.transcript[-1][0] == role:
            self.transcript[-1] = (role, self.transcript[-1][1] + text)
        else:
            self.transcript.append((role, text))

    async def pump(self) -> None:
        async for event in self._live.events():
            if isinstance(event, AudioOut) and self.stream_sid:
                mulaw = pcm24k_to_mulaw8k(event.pcm)
                await self._send(
                    {
                        "event": "media",
                        "streamSid": self.stream_sid,
                        "media": {"payload": base64.b64encode(mulaw).decode()},
                    }
                )
            elif isinstance(event, Interrupted) and self.stream_sid:
                await self._send({"event": "clear", "streamSid": self.stream_sid})
            elif isinstance(event, InputTranscript):
                self._append("Driver", event.text)
            elif isinstance(event, OutputTranscript):
                self._append("Agen", event.text)
            elif isinstance(event, ToolCall):
                await self._handle_tool(event)

    async def _handle_tool(self, event: ToolCall) -> None:
        args = event.args or {}
        if event.name == "update_trip_status" and self.order_id:
            note = str(args.get("note") or "")
            trips_module.TRIPS.update(
                self.order_id,
                str(args.get("status") or ""),
                f"Telepon: {note}",
                str(args.get("eta") or ""),
            )
            self.updates.append(note)
            result: dict[str, object] = {"status": "recorded"}
        elif event.name == "escalate_to_operations":
            self.escalation = {
                "reason": str(args.get("reason") or "help_request"),
                "summary": str(args.get("summary") or ""),
            }
            result = {"status": "escalated"}
        else:
            result = {"status": "ignored"}
        await self._live.send_tool_response(event.id, event.name, result)

    def transcript_note(self) -> str:
        lines = ["📞 Panggilan otomatis ke driver (Gemini Live)"]
        if self.order_id:
            lines[0] += f" · order {self.order_id}"
        if not self.transcript:
            lines.append("Panggilan tidak dijawab atau tidak ada percakapan.")
            return "\n".join(lines)
        lines.append("")
        lines += [f"{role}: {text.strip()}" for role, text in self.transcript]
        if self.updates:
            lines += ["", "Dicatat ke trip: " + " / ".join(self.updates)]
        return "\n".join(lines)

    async def finalize(self) -> None:
        """Transcript to the Chatwoot conversation; escalation ticket if raised."""
        if self.session_id.startswith("crm-"):
            try:
                cw = self._channel or ChatwootDriverChannel(self._settings)
                await cw.add_private_note(
                    int(self.session_id.removeprefix("crm-")), self.transcript_note()
                )
            except Exception as exc:
                _log.warning(
                    "logisly_call_transcript_failed", session_id=self.session_id, error=str(exc)
                )
        if self.escalation is not None:
            state = {
                "handoff_reason": self.escalation["reason"],
                "handoff_summary": self.escalation["summary"],
                "order_id": self.order_id,
            }
            await file_escalation(self._settings, self.session_id, state, channel=self._channel)
            if self.session_id.startswith("crm-"):
                try:
                    cw = self._channel or ChatwootDriverChannel(self._settings)
                    await cw.set_status(int(self.session_id.removeprefix("crm-")), "open")
                except Exception as exc:
                    _log.warning(
                        "logisly_call_reopen_failed", session_id=self.session_id, error=str(exc)
                    )
        _log.info(
            "logisly_call_finished",
            session_id=self.session_id,
            turns=len(self.transcript),
            escalated=self.escalation is not None,
        )
