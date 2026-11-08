"""Task 31: the IVR's Twilio webhooks (`/voice/phone/ivr/*`).

Registered by `ChatRouter` only when `PHONE_DTMF_MENU_ENABLED` is on (the
voicemail route only when `PHONE_VOICEMAIL_INGEST_ENABLED` is on too), so a
tenant with the flag off has no such routes at all -- FastAPI's own 404.

Paths live under `/voice/` on purpose: Proton's Caddy matcher already sends
`/voice/*` to the backend (`/voice/phone/incoming` is the Twilio voice URL), so
no proxy change is needed to switch this on.

**Every route verifies `X-Twilio-Signature`** over the exact public URL Twilio
posted to, query string included -- the IVR keeps its state (language,
attempt, category, ring stage) in the action URL, so the signature is what
stops a forged post from, say, skipping the office-hour check. No
`twilio_auth_token` -> 401 (same hard refusal as recording-status: a forged
voicemail post would create a Chatwoot conversation). `/voice/phone/incoming`
itself is not signature-checked, as before: with the menu on it only returns
menu TwiML and has no side effect, and a misconfigured check there would drop
every inbound call.

Every handler returns valid TwiML on every branch it can reach once the
signature passes: this is a live call, and a 500 or an empty body hangs up on
the caller.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from chatbot.features.chat.phone import ivr, ivr_prompts
from chatbot.features.chat.phone.after_hours import evaluate_after_hours_call
from chatbot.features.chat.phone.recording_transcriber import process_recording
from chatbot.features.chat.phone.voicemail_ingest import ingest_voicemail
from chatbot.features.chat.twilio_signature import verify_twilio_signature

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_ANSWERED = {"completed", "answered"}


def _now() -> datetime:
    """Seam for tests (office hours)."""
    return datetime.now(UTC)


def _xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


def ivr_base_url(settings: Settings) -> str:
    """Public base for the IVR's action URLs, or "" when the IVR cannot run
    (no base to build verifiable URLs from, or no token to verify with)."""
    if not settings.twilio_webhook_base_url or not settings.twilio_auth_token:
        return ""
    return settings.twilio_webhook_base_url.rstrip("/")


class IvrRoutes:
    """Handlers for the IVR. Collaborators are passed as callables so this
    module does not import `router.py` (which imports it)."""

    def __init__(
        self,
        settings: Settings,
        log_port: Any,
        bridge_twiml: Callable[[], str],
        fanout_identities: Callable[[], Awaitable[list[str]]],
        record_dial_outcome: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._log_port = log_port
        self._bridge_twiml = bridge_twiml
        self._fanout_identities = fanout_identities
        self._record_dial_outcome = record_dial_outcome

    def register(self, router: APIRouter) -> None:
        router.add_api_route(ivr.PATH_LANGUAGE, self.language, methods=["POST"])
        router.add_api_route(ivr.PATH_MENU, self.menu, methods=["POST"])
        router.add_api_route(ivr.PATH_RING, self.ring, methods=["POST"])
        if self._settings.phone_voicemail_ingest_enabled:
            router.add_api_route(ivr.PATH_VOICEMAIL, self.voicemail, methods=["POST"])

    # --- entry --------------------------------------------------------------

    def entry_twiml(self) -> str | None:
        """What `/voice/phone/incoming` answers with when the menu is on, or
        None to keep today's bridge TwiML (misconfigured tenant, logged)."""
        base = ivr_base_url(self._settings)
        if not base:
            _log.warning(
                "phone_ivr_not_configured_using_bridge",
                has_base=bool(self._settings.twilio_webhook_base_url),
                has_token=bool(self._settings.twilio_auth_token),
            )
            return None
        return ivr.language_menu_twiml(self._settings, base)

    # --- helpers ------------------------------------------------------------

    async def _verified(self, request: Request) -> dict[str, str]:
        settings = self._settings
        token = settings.twilio_auth_token
        if not token or not settings.twilio_webhook_base_url:
            _log.warning("phone_ivr_no_auth_token_configured", path=request.url.path)
            raise HTTPException(status_code=401, detail="IVR webhook not configured")
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        url = f"{settings.twilio_webhook_base_url.rstrip('/')}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        if not verify_twilio_signature(token, url, params, request.headers.get("X-Twilio-Signature")):
            _log.warning("phone_ivr_signature_invalid", path=request.url.path)
            raise HTTPException(status_code=401, detail="Invalid signature")
        return params

    @staticmethod
    def _lang(request: Request) -> str:
        lang = request.query_params.get("lang", "en")
        return lang if lang in ivr.LANGUAGES else "en"

    @staticmethod
    def _cat(request: Request) -> str:
        cat = request.query_params.get("cat", "unselected")
        return cat if cat in ivr.CATEGORIES else "unselected"

    @staticmethod
    def _attempt(request: Request) -> int:
        try:
            return max(0, min(5, int(request.query_params.get("attempt", "0"))))
        except ValueError:
            return 0

    @staticmethod
    def _digit(params: dict[str, str]) -> str | None:
        digit = params.get("Digits", "").strip()
        return digit[:1] or None

    async def _agent_nouns(self) -> str:
        """Online softphone agents first (the existing fan-out set), else
        the PSTN hunt-group `phone_handoff_target_number`. "" = nobody."""
        try:
            identities = await self._fanout_identities()
        except Exception as e:
            _log.warning("phone_ivr_fanout_lookup_failed", error=str(e))
            identities = []
        if identities:
            return ivr.client_nouns(identities, {"source": "ivr"})
        number = self._settings.phone_handoff_target_number.strip()
        return ivr.number_noun(number) if number else ""

    async def _route(self, lang: str, cat: str) -> str:
        """The SOP's routing after the menu: RSA 24/7, everything else
        through the office-hour check, then the configured non-RSA route."""
        settings = self._settings
        base = ivr_base_url(settings)
        is_rsa = cat == "rsa"
        within = ivr.within_office_hours(settings, _now()) if settings.phone_after_hours_enabled else True
        decision = evaluate_after_hours_call(is_rsa, within, settings)
        _log.info("phone_ivr_route", lang=lang, category=cat, within_hours=within, action=decision["action"])
        if decision["action"] == "play_after_hours_voicemail":
            return ivr.after_hours_twiml(settings, base, lang, cat)

        caller_id = settings.phone_handoff_caller_id.strip()
        if is_rsa:
            rsa = settings.phone_ivr_rsa_number.strip()
            if not rsa:
                return self._bridge_twiml()
            return ivr.dial_twiml(
                ivr.number_noun(rsa),
                ivr.ring_url(base, lang, cat, "final"),
                settings.phone_ivr_agent_attend_seconds,
                caller_id,
            )

        if settings.phone_ivr_non_rsa_route.strip().lower() != "agent":
            return self._bridge_twiml()
        nouns = await self._agent_nouns()
        if not nouns:
            _log.warning("phone_ivr_agent_route_no_target_using_bridge", category=cat)
            return self._bridge_twiml()
        return ivr.dial_twiml(
            nouns,
            ivr.ring_url(base, lang, cat, "1"),
            settings.phone_ivr_queue_busy_after_seconds,
            caller_id,
        )

    # --- webhooks -----------------------------------------------------------

    async def language(self, request: Request) -> Response:
        params = await self._verified(request)
        settings = self._settings
        base = ivr_base_url(settings)
        what, value = ivr.decide_language(self._digit(params), self._attempt(request))
        if what == "menu":
            return _xml(ivr.category_menu_twiml(settings, base, str(value)))
        if what == "repeat":
            return _xml(ivr.language_menu_twiml(settings, base, int(value)))
        return _xml(await self._route(str(value), "unselected"))

    async def menu(self, request: Request) -> Response:
        params = await self._verified(request)
        lang = self._lang(request)
        what, value = ivr.decide_category(self._digit(params), self._attempt(request))
        if what == "repeat":
            return _xml(ivr.category_menu_twiml(self._settings, ivr_base_url(self._settings), lang, int(value)))
        return _xml(await self._route(lang, str(value)))

    async def ring(self, request: Request, background_tasks: BackgroundTasks) -> Response:
        """`<Dial action>` for the RSA line and both non-RSA ring stages."""
        params = await self._verified(request)
        settings = self._settings
        base = ivr_base_url(settings)
        lang, cat = self._lang(request), self._cat(request)
        stage = request.query_params.get("stage", "final")
        status = params.get("DialCallStatus", "")
        call_sid = params.get("CallSid", "")
        redial: str | None = None
        if status not in _ANSWERED and status != "canceled" and cat != "rsa" and stage == "1":
            nouns = await self._agent_nouns()
            if nouns:
                busy = ivr.say(settings, lang, ivr_prompts.PROMPTS[lang]["queue_busy"])
                redial = ivr.dial_twiml(
                    nouns,
                    ivr.ring_url(base, lang, cat, "2"),
                    ivr.second_ring_seconds(settings),
                    settings.phone_handoff_caller_id.strip(),
                    prefix=busy,
                )
        if redial is not None:
            return _xml(redial)
        # The ring stage is over: same abandon/answer bookkeeping as
        # /webhooks/phone/dial-status (Task 18), once per call.
        if call_sid and self._record_dial_outcome is not None:
            background_tasks.add_task(self._record_dial_outcome, call_sid, status)
        if status in _ANSWERED or status == "canceled":
            return _xml(ivr.HANGUP_TWIML)
        if cat == "rsa":
            # Safety: an unanswered RSA line must still reach help 24/7.
            _log.warning("phone_ivr_rsa_unanswered_using_bridge", status=status)
            return _xml(self._bridge_twiml())
        return _xml(ivr.no_agent_twiml(settings, base, lang, cat))

    async def voicemail(self, request: Request, background_tasks: BackgroundTasks) -> Response:
        """`<Record action>`: say goodbye now, write the case afterwards."""
        params = await self._verified(request)
        lang, cat = self._lang(request), self._cat(request)
        reason = request.query_params.get("reason", "")
        reason = reason if reason in ("after_hours", "no_answer") else "no_answer"
        if params.get("RecordingUrl") and params.get("RecordingSid"):
            background_tasks.add_task(self._ingest, params, lang, cat, reason)
        else:
            _log.info("phone_ivr_voicemail_without_recording", call_sid=params.get("CallSid", ""))
        return _xml(ivr.goodbye_twiml(self._settings, lang))

    async def _ingest(self, params: dict[str, str], lang: str, cat: str, reason: str) -> None:
        settings = self._settings
        ticket_id = await ingest_voicemail(
            params, settings, self._log_port, language=lang, category=cat, reason=reason
        )
        if ticket_id and settings.phone_recording_transcription_enabled:
            try:
                await process_recording(
                    settings, self._log_port, ticket_id, params["RecordingUrl"], params.get("CallSid", "")
                )
            except Exception as e:
                _log.error("phone_ivr_voicemail_transcription_failed", error=str(e))
