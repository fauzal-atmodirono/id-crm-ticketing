"""EscalationNotifier — side-effects on case escalation (Phase 2, items 13+14).

Orchestrates:
1. Email the PIC (To) + the department's CC "relevant personnel" (gated by
   escalation_cc_pic); body references the Chatwoot conversation.
2. WhatsApp alert to the PIC's registered number via Twilio.
3. Write `case_state=WIP` to the Chatwoot conversation custom attributes.

All three are best-effort: a failure in any step logs a warning and does NOT
propagate — the escalation itself (Chatwoot labels) has already succeeded
before this is called.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR, CaseState
from chatbot.features.chat.settings_facade import get_effective_value

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.tenant_settings_store import TenantSettingsStorePort
    from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
    from chatbot.features.chat.pic_registry import PicEntry, PicRegistry
    from chatbot.features.chat.pic_store import DealerStore
    from chatbot.features.metrics.email_sender import SmtpEmailSender
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Type alias: ChatwootAdapter._merge_custom_attributes's signature (ticket_id,
# attributes) -> None. Package C Task 5 review fix (Critical 1, round 2):
# _write_case_state below used to call a raw ChatwootAdapter._request-shaped
# callable directly, with a bare `{"custom_attributes": {...}}` POST body --
# but Chatwoot's custom-attributes endpoint REPLACES the whole object, so
# that call was clobbering case_category/recording_url/external_id/etc.
# every time an escalation fired (notify() calls _write_case_state
# unconditionally, before create_ticket/open_handoff's own now-merge-safe
# custom_attrs write -- so a reused conversation's prior attributes were
# being wiped one frame before this class's own caller had a chance to
# preserve them). This is now injected as ChatwootAdapter._merge_custom_
# attributes itself (GET, union, POST -- see that method's docstring), not
# a hand-rolled reimplementation. The type alias below is deliberately
# narrow (not the old raw-request shape) so a future accidental revert to
# injecting `_request` again is a mypy error, not a silent regression.
_CWPostMessage = Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]]
_CWMergeCustomAttributes = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


def build_dealer_email_map(settings: Settings) -> dict[str, list[str]]:
    """Parse dealer_email_map_json into a lower-cased slug -> [email] dict.

    A value may be a single string (the original shape) or a list of
    addresses (dealers as groups) -- mirrors build_pic_registry's fail-safe
    parsing so a misconfigured map never crashes the app, it just means fewer
    dealers resolve. Returns {} on absent/blank/malformed JSON, and silently
    drops entries that are neither a non-empty string nor a list containing
    at least one address.
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
    result: dict[str, list[str]] = {}
    for key, val in data.items():
        if not isinstance(key, str):
            continue
        if isinstance(val, str) and val:
            result[key.lower()] = [val]
        elif isinstance(val, list):
            members = [str(v) for v in val if isinstance(v, str) and v]
            if members:
                result[key.lower()] = members
    return result


class EscalationNotifier:
    """Fire the three escalation side-effects for a newly escalated case."""

    def __init__(
        self,
        settings: Settings,
        pic_registry: PicRegistry,
        email_sender: SmtpEmailSender,
        twilio_adapter: TwilioChannelAdapter | None,
        chatwoot_request: _CWMergeCustomAttributes,
        dealer_email_map: dict[str, list[str]] | None = None,
        dealer_store: DealerStore | None = None,
        tenant_settings_store: TenantSettingsStorePort | None = None,
        chatwoot_post_message: _CWPostMessage | None = None,
    ) -> None:
        self._settings = settings
        self._pic_registry = pic_registry
        self._email_sender = email_sender
        self._twilio = twilio_adapter
        self._cw = chatwoot_request
        self._dealer_email_map = dealer_email_map or {}
        self._dealer_store = dealer_store
        # Task 18: lets the customer ack body be operator-edited from the CRM
        # (Knowledge Settings) rather than fixed at deploy time. None (the
        # default) means "no store configured" -- _resolve_ack_template then
        # falls back to self._settings.email_escalation_ack_template exactly
        # as before, so this is byte-identical to pre-Task-18 behavior when
        # unset.
        self._tenant_settings_store = tenant_settings_store
        # P2: posts the customer acknowledgement into the conversation thread
        # on every non-Email channel. None means "not wired" -- a composition
        # root that predates P2 simply sends no chat ack, rather than raising.
        self._post_message = chatwoot_post_message

    async def notify(
        self,
        *,
        conv_id: str,
        title: str,
        body: str,
        department: str | None,
    ) -> PicEntry | None:
        """Run all three side-effects; return the resolved PicEntry or None.

        The email references the Chatwoot conversation -- this is the
        Chatwoot-only deployment path.
        """
        pic = await self._resolve_pic(department)

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
            )
        await self._send_wa(pic, conv_id=conv_id, title=title)
        return pic

    def _reply_to_for(self, conv_id: str) -> str | None:
        """Correlation Reply-To for this conversation, or None when the
        template is unset (the default -- mail then goes out untagged, byte-
        identical to pre-reply-loop behavior)."""
        template = (self._settings.escalation_reply_to_template or "").strip()
        if not template:
            return None
        try:
            return template.format(conv_id=conv_id)
        except Exception:
            # Any .format() failure (bad placeholder name, bad format spec,
            # etc.) must fall back to untagged mail here, not bubble up --
            # this runs inline while the send()'s subject= is being built,
            # before the broad except around the send() call even starts,
            # so a narrower catch here would drop the whole email instead
            # of just the tag.
            _log.warning("escalation_reply_to_template_invalid", template=template)
            return None

    def _case_tag(self, conv_id: str) -> str:
        """`[CASE-n] ` subject prefix, or "" when the reply loop is off.

        Internal mail (PIC/dealer) only -- the customer ack never carries a
        visible tag, so that thread stays clean for the customer.
        """
        return f"[CASE-{conv_id}] " if self._reply_to_for(conv_id) else ""

    async def _resolve_pic(self, department: str | None) -> PicEntry | None:
        if not department:
            return None
        # dept label is "dept_apps" — strip prefix if present
        key = department.removeprefix("dept_")
        return await self._pic_registry.lookup(key)

    def _send_email(
        self,
        pic: PicEntry,
        *,
        conv_id: str,
        title: str,
        body: str,
    ) -> None:
        reference = f"Chatwoot conversation #{conv_id}"
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
                subject=f"[Escalation] {self._case_tag(conv_id)}{title}",
                body=email_body,
                attachments=[],
                reply_to=self._reply_to_for(conv_id),
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

    async def notify_escalation(
        self,
        *,
        conv_id: str,
        title: str,
        body: str,
        department: str | None,
        dealer: str | None,
        customer_email: str | None,
        ack_transport: str = "email",
    ) -> None:
        """Escalation fan-out (EM-7) for a conversation a human labelled
        `escalate` -- reached only via the /escalation/notify endpoint, called
        from agent/'s maybe_escalate(). Independent of notify(), which is the
        AI's own autonomous escalation path and is never touched here.

        Three independent, best-effort sends: customer ack, PIC email, dealer
        forward. Each failure is logged and does not affect the others.

        ``ack_transport`` decides only how the CUSTOMER is acknowledged --
        ``email`` (mail, the pre-P2 behaviour and the default), ``conversation``
        (an outgoing message in the thread) or ``none`` (voice: the caller has
        already been spoken to). The PIC and dealer legs never depended on the
        channel and are unaffected by it.
        """
        if self._settings.email_escalation_ack_enabled:
            if ack_transport == "email" and customer_email:
                await self._send_customer_ack(customer_email, conv_id=conv_id, title=title)
            elif ack_transport == "conversation":
                await self._send_chat_ack(conv_id, title=title)

        if self._settings.escalation_email_enabled:
            pic = await self._resolve_pic(department)
            if pic is not None:
                self._send_email(pic, conv_id=conv_id, title=title, body=body)

        if dealer:
            await self._send_dealer_forward(dealer, conv_id=conv_id, title=title, body=body)

    async def _resolve_ack_template(self) -> str:
        """Operator-edited template from the tenant store, else the env
        default -- mirrors assist/router.py's `_resolve_model` pattern.

        Fail-open: any store error (unreachable Firestore, etc.) is caught
        inside get_effective_settings and degrades that key to source="env",
        so this only raises on a programmer error (an unregistered key), not
        on a store outage -- but the try/except here is belt-and-suspenders
        against that too, since a missing acknowledgement email is worse
        than one that used the env default it would have used anyway.
        """
        if self._tenant_settings_store is not None:
            try:
                return await get_effective_value(
                    self._tenant_settings_store, self._settings, "email_escalation_ack_template"
                )
            except Exception as exc:
                _log.warning("escalation_ack_template_resolve_failed", error=str(exc))
        return self._settings.email_escalation_ack_template

    async def _send_customer_ack(self, to_email: str, *, conv_id: str, title: str) -> None:
        # No _case_tag here, deliberately: the customer thread must stay
        # clean -- only the invisible Reply-To carries the correlation token.
        try:
            self._email_sender.send(
                to=[to_email],
                cc=[],
                subject=f"Update on your case: {title}",
                body=await self._resolve_ack_template(),
                attachments=[],
                reply_to=self._reply_to_for(conv_id),
            )
        except Exception as exc:
            _log.warning("escalation_customer_ack_failed", to_email=to_email, error=str(exc))

    async def _send_chat_ack(self, conv_id: str, *, title: str) -> None:
        """Acknowledge the customer in the conversation thread.

        This MUST be an outgoing public message. A private note would leave the
        conversation looking handled while the customer received nothing --
        exactly the failure commit `0aa643d` shipped on the reply path, and the
        reason `test_escalation_chat_ack.py` asserts the payload rather than
        the call.

        Best-effort like every other leg: a Chatwoot rejection is logged and
        the PIC and dealer legs still fire.
        """
        del title  # the thread already shows what the case is about
        if self._post_message is None:
            _log.info("escalation_chat_ack_unwired", conv_id=conv_id)
            return
        content = (self._settings.escalation_ack_chat_template or "").strip()
        if not content:
            # An operator emptying the template is an opt-out, not a mandate to
            # post an empty message at the customer.
            return
        try:
            await self._post_message(
                conv_id,
                {"content": content, "private": False, "message_type": "outgoing"},
            )
        except Exception as exc:
            _log.warning("escalation_chat_ack_failed", conv_id=conv_id, error=str(exc))

    async def _send_dealer_forward(
        self, dealer_slug: str, *, conv_id: str, title: str, body: str
    ) -> None:
        emails: list[str] = []
        if self._dealer_store is not None:
            record = await self._dealer_store.get(dealer_slug.lower())
            if record is not None:
                emails = list(record.emails)
        if not emails:
            emails = list(self._dealer_email_map.get(dealer_slug.lower()) or [])
        if not emails:
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
                to=emails,
                cc=[],
                subject=f"[Escalation - Dealer Forward] {self._case_tag(conv_id)}{title}",
                body=email_body,
                attachments=[],
                reply_to=self._reply_to_for(conv_id),
            )
        except Exception as exc:
            _log.warning("escalation_dealer_forward_failed", dealer=dealer_slug, error=str(exc))

    async def _write_case_state(self, conv_id: str) -> None:
        try:
            await self._cw(conv_id, {CHATWOOT_CASE_STATE_ATTR: CaseState.WIP.value})
        except Exception as exc:
            _log.warning("escalation_case_state_write_failed", conv_id=conv_id, error=str(exc))
