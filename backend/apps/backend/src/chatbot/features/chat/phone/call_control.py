"""Twilio REST call control for the phone channel.

The Twilio SDK is synchronous, so every call runs through asyncio.to_thread —
blocking the event loop here would stall the Media Stream audio pump.

Every method is fail-open by design: this code sits in the path of a live
conversation, and a Twilio API failure must degrade the feature rather than
drop the caller. "Fail-open" covers client *construction* as well as the API
call itself — ``_twilio()`` swallows any error building the ``twilio.rest.Client``
(bad credentials, a missing/broken SDK install, a future constructor that adds
validation) and returns ``None`` rather than letting the exception propagate,
so both public methods can stay simple ``if client is None: return <falsy>``
guards without needing their own try/except around client acquisition. Any
future call-control method added here (Tasks 4, 6) should follow the same
shape: acquire the client via ``self._twilio()``, treat ``None`` as "already
handled, return falsy", and wrap only the actual SDK call in try/except.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Review fix (Important 3, Task 6 round 2): the Twilio SDK's own httpx-less
# HTTP client has no timeout by default, so a blackholed Twilio API call
# would previously hang the underlying thread indefinitely. bridge.py's
# `asyncio.wait_for` bound around `redirect()` only cancels OUR await of
# that thread (`asyncio.to_thread`), not the thread itself -- a redirect
# that eventually lands on Twilio's side just past that bound would leave
# `PhoneBridge._transfer_dialed` False even though a `<Dial>` may actually
# be in flight, after which a second `request_human_handoff` would no
# longer be suppressed and could replace a live call. Set here, shorter
# than bridge.py's `_HANDOFF_REDIRECT_TIMEOUT_SECONDS`, so a slow call
# FAILS on the SDK side well before that bound fires -- turning an
# abandoned thread into an exceptional case, not the routine one the bound
# alone would otherwise make it.
#
# Whole-branch review fix (Important 6): lowered 4.0 -> 2.5 alongside
# dropping that bound 5.0 -> 3.0. `redirect()` is the one Twilio call made
# INLINE in the audio pump, so its worst case is dead air the caller
# actually hears; a real Twilio REST call from the VM is sub-second, so
# 2.5s is still ~10x headroom, and the ordering invariant above (SDK
# timeout strictly shorter than the asyncio bound) is preserved.
_TWILIO_HTTP_TIMEOUT_SECONDS = 2.5


class CallControl:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def _twilio(self) -> Any | None:
        """Return a Twilio client, constructing one lazily if needed. Never
        raises: construction failure (bad credentials, a broken SDK install,
        anything) is fail-open, same as the API calls made with the client."""
        if self._client is not None:
            return self._client
        sid = self._settings.twilio_account_sid
        token = self._settings.twilio_auth_token
        if not sid or not token:
            return None
        try:
            from twilio.http.http_client import TwilioHttpClient  # noqa: PLC0415
            from twilio.rest import Client  # noqa: PLC0415

            http_client = TwilioHttpClient(timeout=_TWILIO_HTTP_TIMEOUT_SECONDS)
            self._client = Client(sid, token, http_client=http_client)
        except Exception as e:
            _log.error("call_control_client_init_failed", error=str(e))
            return None
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

    async def fetch_call_to(self, call_sid: str) -> str | None:
        """The `to` of a call leg -- e.g. `client:agent_17` or `+60388889999`.

        Used to learn WHICH agent answered a fan-out `<Dial>`: Twilio's action
        callback carries `DialCallSid` (the child leg) but not the endpoint it
        reached, so this is the one way to map the winning leg back to a
        Chatwoot user.

        Safe to call here despite being a Twilio round trip: the only caller is
        `_enter_acw_best_effort`, which Starlette runs as a background task
        AFTER the TwiML response has been sent, so this is nowhere near the
        live call's critical path -- unlike `redirect()` above.
        """
        client = self._twilio()
        if client is None:
            return None
        try:
            call = await asyncio.to_thread(lambda: client.calls(call_sid).fetch())
            return str(call.to) if call.to else None
        except Exception as e:
            _log.error("call_fetch_to_failed", call_sid=call_sid, error=str(e))
            return None

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
