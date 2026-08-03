"""EscalationNotifier — side-effects on case escalation (Phase 2, items 13+14).

Orchestrates:
1. Email the PIC (To) + the department's CC "relevant personnel" (gated by
   escalation_cc_pic); body references the Zammad ticket if one exists, else the
   Chatwoot conversation — so this works in a Chatwoot-only deployment.
2. WhatsApp alert to the PIC's registered number via Twilio.
3. Write `case_state=WIP` to the Chatwoot conversation custom attributes.

All three are best-effort: a failure in any step logs a warning and does NOT
propagate — the escalation itself (Chatwoot labels, and a Zammad ticket when
direct ticketing is on) has already succeeded before this is called.
"""

from __future__ import annotations

import json
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


def build_dealer_email_map(settings: Settings) -> dict[str, str]:
    """Parse dealer_email_map_json into a lower-cased slug -> email dict.

    Returns {} on absent/blank/malformed JSON or a non-dict/non-string-keyed
    shape -- mirrors build_pic_registry's fail-safe parsing so a misconfigured
    map never crashes the app, it just means no dealer email is ever sent.
    """
    raw = (settings.dealer_email_map_json or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, str] = {}
    for key, val in data.items():
        if isinstance(key, str) and isinstance(val, str) and val:
            result[key.lower()] = val
    return result


class EscalationNotifier:
    """Fire the three escalation side-effects for a newly escalated case."""

    def __init__(
        self,
        settings: Settings,
        pic_registry: PicRegistry,
        email_sender: SmtpEmailSender,
        twilio_adapter: TwilioChannelAdapter | None,
        chatwoot_request: _CWRequest,
        dealer_email_map: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._pic_registry = pic_registry
        self._email_sender = email_sender
        self._twilio = twilio_adapter
        self._cw = chatwoot_request
        self._dealer_email_map = dealer_email_map or {}

    async def notify(
        self,
        *,
        conv_id: str,
        title: str,
        body: str,
        department: str | None,
        zammad_ticket_number: str | None = None,
    ) -> PicEntry | None:
        """Run all three side-effects; return the resolved PicEntry or None.

        ``zammad_ticket_number`` is optional: when a back-office Zammad ticket
        exists the email references it, otherwise it references the Chatwoot
        conversation. All side-effects work in a Chatwoot-only deployment.
        """
        pic = self._resolve_pic(department)

        await self._write_case_state(conv_id)

        if pic is None:
            _log.info("escalation_notifier_no_pic_for_dept", department=department)
            return None

        if self._settings.escalation_email_enabled:
            self._send_email(
                pic,
                conv_id=conv_id,
                title=title,
                body=body,
                zammad_ticket_number=zammad_ticket_number,
            )
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
        conv_id: str,
        title: str,
        body: str,
        zammad_ticket_number: str | None,
    ) -> None:
        reference = (
            f"Zammad ticket #{zammad_ticket_number}"
            if zammad_ticket_number
            else f"Chatwoot conversation #{conv_id}"
        )
        # CC the department's configured "relevant personnel" (managers / DLs),
        # gated by escalation_cc_pic. Empty list = To-the-PIC only.
        cc = list(pic.cc_emails) if self._settings.escalation_cc_pic else []
        email_body = textwrap.dedent(f"""\
            A case has been escalated to your team.

            Subject  : {title}
            Reference: {reference}

            --- Summary ---
            {body}

            Please action this case promptly.
        """)
        try:
            self._email_sender.send(
                to=[pic.pic_email],
                cc=cc,
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

    async def notify_email_channel_escalation(
        self,
        *,
        conv_id: str,
        title: str,
        body: str,
        department: str | None,
        dealer: str | None,
        customer_email: str | None,
    ) -> None:
        """Two-thread email escalation (EM-7) for a natively-escalated
        Email-channel conversation -- reached only via the /escalation/notify
        endpoint, called from agent/'s maybe_escalate() when a human applies
        the `escalate` label. Independent of notify(), which is the AI's own
        autonomous escalation path and is never touched by this method.

        Three independent, best-effort sends: customer ack, PIC email, dealer
        forward. Each failure is logged and does not affect the others.
        """
        if self._settings.email_escalation_ack_enabled and customer_email:
            self._send_customer_ack(customer_email, title=title)

        if self._settings.escalation_email_enabled:
            pic = self._resolve_pic(department)
            if pic is not None:
                self._send_email(
                    pic, conv_id=conv_id, title=title, body=body, zammad_ticket_number=None
                )

        if dealer:
            self._send_dealer_forward(dealer, conv_id=conv_id, title=title, body=body)

    def _send_customer_ack(self, to_email: str, *, title: str) -> None:
        try:
            self._email_sender.send(
                to=[to_email],
                cc=[],
                subject=f"Update on your case: {title}",
                body=self._settings.email_escalation_ack_template,
                attachments=[],
            )
        except Exception as exc:
            _log.warning("escalation_customer_ack_failed", to_email=to_email, error=str(exc))

    def _send_dealer_forward(self, dealer_slug: str, *, conv_id: str, title: str, body: str) -> None:
        email = self._dealer_email_map.get(dealer_slug.lower())
        if not email:
            _log.info("escalation_dealer_unmapped", dealer=dealer_slug)
            return
        email_body = textwrap.dedent(f"""\
            A case has been escalated and forwarded to your dealership.

            Subject  : {title}
            Reference: Chatwoot conversation #{conv_id}

            --- Summary ---
            {body}

            Please action this case promptly.
        """)
        try:
            self._email_sender.send(
                to=[email],
                cc=[],
                subject=f"[Escalation - Dealer Forward] {title}",
                body=email_body,
                attachments=[],
            )
        except Exception as exc:
            _log.warning("escalation_dealer_forward_failed", dealer=dealer_slug, error=str(exc))

    async def _write_case_state(self, conv_id: str) -> None:
        try:
            await self._cw(
                "POST",
                f"/conversations/{conv_id}/custom_attributes",
                {"custom_attributes": {CHATWOOT_CASE_STATE_ATTR: CaseState.WIP.value}},
            )
        except Exception as exc:
            _log.warning("escalation_case_state_write_failed", conv_id=conv_id, error=str(exc))
