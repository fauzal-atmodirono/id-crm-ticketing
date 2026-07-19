"""EscalationNotifier — side-effects on case escalation (Phase 2, items 13+14).

Orchestrates:
1. Email the PIC (To) + optional CC; body = case summary + Zammad link.
2. WhatsApp alert to the PIC's registered number via Twilio.
3. Write `case_state=WIP` to the Chatwoot conversation custom attributes.

All three are best-effort: a failure in any step logs a warning and does NOT
propagate — the escalation itself (Zammad ticket + Chatwoot labels) has already
succeeded before this is called.
"""
from __future__ import annotations

import textwrap
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR, CaseState

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
    from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
    from chatbot.features.metrics.email_sender import SmtpEmailSender
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Type alias: the ChatwootAdapter._request signature we inject.
_CWRequest = Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]


class EscalationNotifier:
    """Fire the three escalation side-effects for a newly escalated case."""

    def __init__(
        self,
        settings: Settings,
        pic_registry: PicRegistry,
        email_sender: SmtpEmailSender,
        twilio_adapter: TwilioChannelAdapter | None,
        chatwoot_request: _CWRequest,
    ) -> None:
        self._settings = settings
        self._pic_registry = pic_registry
        self._email_sender = email_sender
        self._twilio = twilio_adapter
        self._cw = chatwoot_request

    async def notify(
        self,
        *,
        conv_id: str,
        ticket_id: str,
        title: str,
        body: str,
        department: str | None,
        zammad_ticket_number: str | None,
    ) -> PicEntry | None:
        """Run all three side-effects; return the resolved PicEntry or None."""
        pic = self._resolve_pic(department)

        await self._write_case_state(conv_id)

        if pic is None:
            _log.info("escalation_notifier_no_pic_for_dept", department=department)
            return None

        if self._settings.escalation_email_enabled:
            self._send_email(pic, title=title, body=body,
                             ticket_id=ticket_id,
                             zammad_ticket_number=zammad_ticket_number)
        await self._send_wa(pic, conv_id=conv_id, title=title)
        return pic

    def _resolve_pic(self, department: str | None) -> PicEntry | None:
        if not department:
            return None
        # dept label is "dept_apps" — strip prefix if present
        key = department.removeprefix("dept_")
        return self._pic_registry.lookup(key)

    def _send_email(
        self,
        pic: PicEntry,
        *,
        title: str,
        body: str,
        ticket_id: str,
        zammad_ticket_number: str | None,
    ) -> None:
        zammad_ref = (
            f"Zammad ticket #{zammad_ticket_number}" if zammad_ticket_number else ticket_id
        )
        email_body = textwrap.dedent(f"""\
            A case has been escalated to your team.

            Subject  : {title}
            Reference: {zammad_ref}

            --- Summary ---
            {body}

            Please action this case promptly.
        """)
        try:
            self._email_sender.send(
                to=[pic.pic_email],
                cc=[],
                subject=f"[Escalation] {title}",
                body=email_body,
                attachments=[],
            )
        except Exception as exc:
            _log.warning("escalation_email_failed", pic_email=pic.pic_email, error=str(exc))

    async def _send_wa(self, pic: PicEntry, *, conv_id: str, title: str) -> None:
        if self._twilio is None:
            return
        to = "whatsapp:" + pic.pic_whatsapp.removeprefix("whatsapp:")
        text = f"🔔 New escalation (case {conv_id}): {title}. Please review and action."
        try:
            await self._twilio.send_message(conversation_id=to, text=text)
        except Exception as exc:
            _log.warning("escalation_wa_failed", to=to, error=str(exc))

    async def _write_case_state(self, conv_id: str) -> None:
        try:
            await self._cw(
                "POST",
                f"/conversations/{conv_id}/custom_attributes",
                {"custom_attributes": {CHATWOOT_CASE_STATE_ATTR: CaseState.WIP.value}},
            )
        except Exception as exc:
            _log.warning("escalation_case_state_write_failed", conv_id=conv_id, error=str(exc))
