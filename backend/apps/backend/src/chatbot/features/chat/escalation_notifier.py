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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.case_state import CHATWOOT_CASE_STATE_ATTR, CaseState
from chatbot.features.chat.escalation_attachments import AttachmentFetcher
from chatbot.features.chat.escalation_attachments import collect as collect_attachments
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.settings_facade import get_effective_value

# Delivery outcomes recorded on each escalation leg. `delivered` means the SMTP
# handoff succeeded -- NOT that the mail was accepted by the recipient's server.
# Bounce/DSN handling needs a bounce mailbox (client question Q6) and is not
# covered here; do not report §4.39 as fully closed on the strength of this.
ESCALATION_DELIVERED = "delivered"
ESCALATION_FAILED = "failed"

# What the SOP asks the recipient of each rung to do. Kept beside the send so
# a reminder says what a reminder has to say -- "please action this promptly"
# is the right line for a first contact and far too soft for a 2nd reminder
# addressed to a Dealer Owner.
_STEP_EXPECTATION = {
    3: "Please confirm the action taken and provide a status update.",
    4: "Immediate action and a resolution status are required.",
    5: (
        "Respond within 1 hour. Failure to respond is a non-compliance under "
        "the Daily Complaint Clause."
    ),
}

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.tenant_settings_store import TenantSettingsStorePort
    from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
    from chatbot.features.chat.escalation_policy import EscalationStep
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
        attachment_fetcher: AttachmentFetcher | None = None,
        audit: Any | None = None,
        presence: Any | None = None,
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
        # P2: pulls the customer's photos/PDFs into the internal legs. None
        # means "not wired" -- no attachment work is attempted at all.
        self._attachment_fetcher = attachment_fetcher
        # P2: records what each leg actually delivered. None = not wired, and
        # the sends proceed regardless -- recording the escalation matters
        # less than making it.
        self._audit = audit
        # P2 task 6: reads who is actually on duty, to WIDEN the PIC leg's
        # recipients. None = not wired; gated additionally by
        # escalation_presence_check_enabled so the API call is opt-in.
        self._presence = presence

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

    async def _pic_recipients(self, pic: PicEntry, department: str | None) -> list[str]:
        """Who the PIC leg actually mails.

        Without the on-duty check this is just the PIC, exactly as before. With
        it, an offline PIC's online colleagues are added so somebody at their
        desk sees the escalation -- the PIC is never dropped, so this can only
        ever widen the list.
        """
        base = [pic.pic_email] if pic.pic_email else []
        if not getattr(self._settings, "escalation_presence_check_enabled", False):
            return base
        if self._presence is None or not department:
            return base
        try:
            resolution = await self._pic_registry.resolve(
                department.removeprefix("dept_"), presence=self._presence
            )
        except Exception as exc:
            _log.warning("escalation_presence_resolve_failed", error=str(exc))
            return base
        return resolution.recipients or base

    async def _record_delivery(
        self,
        conv_id: str,
        *,
        leg: str,
        recipients: list[str],
        transport: str,
        ok: bool,
        error: str = "",
    ) -> None:
        """Write one audit row per escalation leg, and warn the operator when
        a leg failed.

        Both halves are best-effort and independent. A dead audit store or an
        unpostable note must never stop the remaining legs -- an escalation
        that reached the dealer but was not logged is a reporting gap; one that
        never left is the failure this package exists to prevent.
        """
        status = ESCALATION_DELIVERED if ok else ESCALATION_FAILED
        if self._audit is not None:
            try:
                await self._audit.append(
                    AuditEntry(
                        ticket_id=conv_id,
                        session_id=f"chatwoot-conv-{conv_id}",
                        actor="escalation-notifier",
                        from_state="OPEN",
                        to_state=f"ESCALATION_{leg.upper()}",
                        at=datetime.now(UTC).isoformat(),
                        remark=error,
                        recipients=list(recipients),
                        transport=transport,
                        delivery_status=status,
                    )
                )
            except Exception as exc:
                _log.warning(
                    "escalation_audit_write_failed", conv_id=conv_id, error=str(exc)
                )

        if ok or not getattr(self._settings, "escalation_failure_note_enabled", False):
            return
        if self._post_message is None:
            return
        # Private: this is an internal delivery problem. The customer must
        # never be told their escalation email bounced off our own SMTP.
        try:
            await self._post_message(
                conv_id,
                {
                    "content": (
                        f"⚠️ The {leg} escalation could not be delivered to "
                        f"{', '.join(recipients)}. Please contact them directly. "
                        f"({error})"
                    ),
                    "private": True,
                },
            )
        except Exception as exc:
            _log.warning(
                "escalation_failure_note_failed", conv_id=conv_id, error=str(exc)
            )

    async def _collect_attachments(self, conv_id: str) -> tuple[list, list[str]]:
        """The customer's evidence, for the internal legs only.

        Returns empty lists when the feature is off or no fetcher is wired --
        and in the off case `collect` short-circuits before any HTTP call, so
        a tenant that has not opted in pays nothing.
        """
        if self._attachment_fetcher is None:
            return [], []
        return await collect_attachments(
            self._attachment_fetcher,
            conv_id,
            budget_bytes=int(
                getattr(self._settings, "escalation_attachment_budget_bytes", 0) or 0
            ),
        )

    @staticmethod
    def _with_skip_notes(body: str, skipped: list[str]) -> str:
        """Tell the reader what they did NOT receive.

        A PIC who can see that a photo exists but did not arrive can go and
        look at it. One who is told nothing does not know to."""
        if not skipped:
            return body
        lines = "\n".join(f"  - {note}" for note in skipped)
        return f"{body}\n\n--- Attachments not included ---\n{lines}\n"

    def _send_email(
        self,
        pic: PicEntry,
        *,
        conv_id: str,
        title: str,
        body: str,
        attachments: list | None = None,
        skipped: list[str] | None = None,
        recipients: list[str] | None = None,
    ) -> tuple[bool, str]:
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
        email_body = self._with_skip_notes(email_body, skipped or [])
        try:
            self._email_sender.send(
                to=list(recipients or [pic.pic_email]),
                cc=cc,
                subject=f"[Escalation] {self._case_tag(conv_id)}{title}",
                body=email_body,
                attachments=list(attachments or []),
                reply_to=self._reply_to_for(conv_id),
            )
            return True, ""
        except Exception as exc:
            _log.warning("escalation_email_failed", pic_email=pic.pic_email, error=str(exc))
            return False, str(exc)

    def send_ladder_step(
        self,
        *,
        conv_id: str,
        step: EscalationStep,
        to: list[str],
        cc: list[str],
        title: str,
        body: str,
        elapsed_working_hours: float,
    ) -> tuple[bool, str]:
        """Send one rung of the dealer ladder.

        Deliberately not routed through `_send_email`: that builds the
        first-contact "a case has been escalated to your team" body, and a
        reminder addressed to a Dealer Owner has to say something different --
        which rung this is, how long the case has been unanswered, and what
        response the SOP requires. Same `[CASE-n]` tag and Reply-To, so a
        reminder reply links back onto the case exactly like the first mail.

        Empty ``to`` is the caller's mistake to avoid; refused here as well
        because an email with only CC recipients would reach the wider group
        while the person actually being chased receives nothing.
        """
        if not to:
            return False, "no recipients"

        required = _STEP_EXPECTATION.get(step.step_no, "Please action this case promptly.")
        prefix = f"[{step.label}] " if step.label else "[Escalation] "
        email_body = textwrap.dedent(f"""\
            {step.label or "Escalation"} -- case unanswered for {elapsed_working_hours:.1f} working hours.

            Subject  : {title}
            Reference: Chatwoot conversation #{conv_id}

            --- Summary ---
            {body}

            {required}
        """)
        try:
            self._email_sender.send(
                to=list(to),
                cc=list(cc),
                subject=f"{prefix}{self._case_tag(conv_id)}{title}",
                body=email_body,
                attachments=[],
                reply_to=self._reply_to_for(conv_id),
            )
            return True, ""
        except Exception as exc:
            _log.warning(
                "escalation_ladder_send_failed",
                conv_id=conv_id,
                step_no=step.step_no,
                error=str(exc),
            )
            return False, str(exc)

    async def raise_phone_task(
        self,
        *,
        conv_id: str,
        step: EscalationStep,
        contacts: list[str],
        deadline: datetime,
    ) -> bool:
        """Step 5: the SOP says telephone, so the CRM raises a task, not mail.

        Placing the call automatically is Package C's job and is deliberately
        out of scope here. What this owes the agent is everything they need to
        make it: who to ring, what the case is, and the one-hour clock that
        starts when they do. `follow_up_at` puts it in the existing My-Tasks
        view rather than inventing a surface for one step of one flow.

        Best-effort like every other leg. Returns whether the note landed --
        the sweep stamps the step either way, because a repeated step-5 note
        every five minutes would be worse than a missed one.
        """
        if self._post_message is None:
            _log.info("escalation_phone_task_unwired", conv_id=conv_id)
            return False

        who = ", ".join(contacts) if contacts else "the dealer principal, then the owner"
        note = textwrap.dedent(f"""\
            ☎️ FINAL ESCALATION -- TELEPHONE REQUIRED

            The dealer has not responded through {step.delay_working_hours:.0f} working
            hours of written escalation. Under the SOP this step is a phone call,
            not an email.

            Call, in order: {who}
            Response required within 1 hour of the call.
            Failure to respond is a non-compliance under the Daily Complaint Clause.

            Record the outcome of the call on this case.
        """)
        try:
            await self._post_message(conv_id, {"content": note, "private": True})
        except Exception as exc:
            _log.warning("escalation_phone_task_note_failed", conv_id=conv_id, error=str(exc))
            return False

        # The deadline is a separate best-effort write: an agent who has the
        # note has what they need to act, so a failed attribute write must not
        # cost them the note.
        try:
            await self._cw(conv_id, {"follow_up_at": deadline.isoformat()})
        except Exception as exc:
            _log.warning("escalation_phone_task_deadline_failed", conv_id=conv_id, error=str(exc))
        return True

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
        customer_subject: str | None = None,
        customer_in_reply_to: str | None = None,
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

        ``customer_in_reply_to`` threads the acknowledgement onto the mail the
        customer actually sent. Like ``customer_subject`` it is the customer
        leg's alone: the PIC and dealer legs are new threads to different
        people, and threading them onto the customer's mail would be wrong.

        ``customer_subject`` is the customer leg's subject and ONLY the
        customer leg's. ``title`` is the first ~100 characters of the
        customer's own first message: exactly what a PIC triaging an inbox
        wants to see, and exactly what the customer must not be sent -- a
        2026-08-19 live run mailed him his own words cut mid-word. Absent
        (an agent service that predates this) falls back to the old
        ``f"Update on your case: {title}"`` so that deploy stays
        byte-identical.
        """
        if self._settings.email_escalation_ack_enabled:
            if ack_transport == "email" and customer_email:
                ok, error = await self._send_customer_ack(
                    customer_email,
                    conv_id=conv_id,
                    title=title,
                    customer_subject=customer_subject,
                    in_reply_to=customer_in_reply_to,
                )
                await self._record_delivery(
                    conv_id,
                    leg="customer_ack",
                    recipients=[customer_email],
                    transport="email",
                    ok=ok,
                    error=error,
                )
            elif ack_transport == "conversation":
                await self._send_chat_ack(conv_id, title=title)

        # Collected ONCE for both internal legs -- two fetches of the same
        # files would double the download cost for no benefit. Never passed to
        # the customer ack: they sent these files, and mailing them back is at
        # best noise and at worst a privacy surprise when a case has been
        # touched by several people.
        attachments, skipped = await self._collect_attachments(conv_id)

        if self._settings.escalation_email_enabled:
            pic = await self._resolve_pic(department)
            if pic is not None:
                recipients = await self._pic_recipients(pic, department)
                ok, error = self._send_email(
                    pic,
                    conv_id=conv_id,
                    title=title,
                    body=body,
                    attachments=attachments,
                    skipped=skipped,
                    recipients=recipients,
                )
                await self._record_delivery(
                    conv_id,
                    leg="pic",
                    recipients=recipients,
                    transport="email",
                    ok=ok,
                    error=error,
                )

        if dealer:
            ok, error, dealer_emails = await self._send_dealer_forward(
                dealer,
                conv_id=conv_id,
                title=title,
                body=body,
                customer_email=customer_email,
                attachments=attachments,
                skipped=skipped,
            )
            if dealer_emails:
                await self._record_delivery(
                    conv_id,
                    leg="dealer",
                    recipients=dealer_emails,
                    transport="email",
                    ok=ok,
                    error=error,
                )

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

    async def _send_customer_ack(
        self,
        to_email: str,
        *,
        conv_id: str,
        title: str,
        customer_subject: str | None = None,
        in_reply_to: str | None = None,
    ) -> tuple[bool, str]:
        # No _case_tag here, deliberately: the customer thread must stay
        # clean -- only the invisible Reply-To carries the correlation token.
        try:
            self._email_sender.send(
                to=[to_email],
                cc=[],
                subject=customer_subject or f"Update on your case: {title}",
                body=await self._resolve_ack_template(),
                attachments=[],
                reply_to=self._reply_to_for(conv_id),
                in_reply_to=in_reply_to,
            )
            return True, ""
        except Exception as exc:
            _log.warning("escalation_customer_ack_failed", to_email=to_email, error=str(exc))
            return False, str(exc)

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
        self, dealer_slug: str, *, conv_id: str, title: str, body: str,
        customer_email: str | None = None,
        attachments: list | None = None,
        skipped: list[str] | None = None,
    ) -> tuple[bool, str, list[str]]:
        emails: list[str] = []
        cc: list[str] = []
        if self._dealer_store is not None:
            record = await self._dealer_store.get(dealer_slug.lower())
            if record is not None:
                emails = list(record.emails)
                if self._settings.escalation_cc_dealer:
                    # getattr, not attribute access: this leg must not fail on
                    # a record shape that predates cc_emails. Losing the CC is
                    # a nuisance; losing the dealer forward is not.
                    cc = list(getattr(record, "cc_emails", None) or [])
        if not emails:
            emails = list(self._dealer_email_map.get(dealer_slug.lower()) or [])
        if not emails:
            _log.info("escalation_dealer_unmapped", dealer=dealer_slug)
            return False, "no dealer email configured", []
        email_body = textwrap.dedent(f"""\
            A case has been escalated and forwarded to your dealership.

            Subject  : {title}
            Reference: Chatwoot conversation #{conv_id}

            --- Summary ---
            {body}

            Please action this case promptly.
        """)
        email_body = self._with_skip_notes(email_body, skipped or [])
        # This mail carries the full transcript. If the dealer's CC list happens
        # to hold the customer's own address, that transcript would go straight
        # back to them -- drop it rather than trust the routing config.
        if customer_email:
            target = customer_email.strip().lower()
            kept = [a for a in cc if a.strip().lower() != target]
            if len(kept) != len(cc):
                _log.warning(
                    "escalation_dealer_cc_dropped_customer",
                    dealer=dealer_slug,
                    conv_id=conv_id,
                )
            cc = kept

        try:
            self._email_sender.send(
                to=emails,
                cc=cc,
                subject=f"[Escalation - Dealer Forward] {self._case_tag(conv_id)}{title}",
                body=email_body,
                attachments=list(attachments or []),
                reply_to=self._reply_to_for(conv_id),
            )
            return True, "", emails
        except Exception as exc:
            _log.warning("escalation_dealer_forward_failed", dealer=dealer_slug, error=str(exc))
            return False, str(exc), emails

    async def _write_case_state(self, conv_id: str) -> None:
        try:
            await self._cw(conv_id, {CHATWOOT_CASE_STATE_ATTR: CaseState.WIP.value})
        except Exception as exc:
            _log.warning("escalation_case_state_write_failed", conv_id=conv_id, error=str(exc))
