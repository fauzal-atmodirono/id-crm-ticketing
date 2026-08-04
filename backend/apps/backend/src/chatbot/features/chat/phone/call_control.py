"""Twilio REST call control for the phone channel.

The Twilio SDK is synchronous, so every call runs through asyncio.to_thread —
blocking the event loop here would stall the Media Stream audio pump.

Every method is fail-open by design: this code sits in the path of a live
conversation, and a Twilio API failure must degrade the feature rather than
drop the caller.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class CallControl:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def _twilio(self) -> Any | None:
        if self._client is not None:
            return self._client
        sid = self._settings.twilio_account_sid
        token = self._settings.twilio_auth_token
        if not sid or not token:
            return None
        from twilio.rest import Client  # noqa: PLC0415

        self._client = Client(sid, token)
        return self._client

    async def redirect(self, call_sid: str, twiml: str) -> bool:
        """Replace the in-progress call's TwiML. Ends the current <Connect><Stream>
        and runs the new verbs on the same call."""
        client = self._twilio()
        if client is None:
            _log.warning("call_control_unconfigured", call_sid=call_sid)
            return False
        try:
            await asyncio.to_thread(lambda: client.calls(call_sid).update(twiml=twiml))
            return True
        except Exception as e:
            _log.error("call_redirect_failed", call_sid=call_sid, error=str(e))
            return False

    async def start_recording(self, call_sid: str, status_callback: str) -> str | None:
        """Start a dual-channel recording on a live call. Returns the recording SID."""
        client = self._twilio()
        if client is None:
            return None
        try:
            rec = await asyncio.to_thread(
                lambda: client.calls(call_sid).recordings.create(
                    recording_channels="dual",
                    recording_status_callback=status_callback,
                )
            )
            return str(rec.sid)
        except Exception as e:
            _log.error("call_recording_start_failed", call_sid=call_sid, error=str(e))
            return None
