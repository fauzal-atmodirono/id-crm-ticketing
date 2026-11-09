"""Notice when an escalation email bounced, and say so on the case.

This is the half of §4.39 that the send-failure note does not cover. That note
fires when SMTP refuses the handoff. The commoner failure is quieter: SMTP
accepts the mail, and the recipient's server rejects it minutes later with a
delivery-status notification. The PIC was never told, and until now nobody
found out.

**No bounce mailbox is required**, contrary to the original scoping. Gmail
returns the DSN to the envelope sender, which IS the tenant's Email inbox: 23
such notices were found sitting in proton's inbox on 2026-08-09, filed as
ordinary conversations that nobody had read. The signal was already arriving.
(A dedicated mailbox would still be better for volume tenants, since DSNs then
never touch the agent queue at all -- that remains client question Q6.)

Two jobs, and the second matters as much as the first:

1. Link the bounce back to the case that caused it, as a private note plus a
   stamp reporting can read.
2. Get the DSN conversation out of the agent queue. Left alone they accumulate
   as open cases and inflate the SLA backlog -- not hypothetically; that is
   what happened on the live tenant, where SLA breach alerts were firing on
   bounce notices.

Fail-open like every background task here: an unparseable DSN, an unknown case
or an unreachable Chatwoot means the bounce is simply not linked. Logged and
skipped, never raised.
"""

import logging
import re
from datetime import datetime, timezone

from app.clients.deps import get_chatwoot_client
from app.config import get_settings

logger = logging.getLogger(__name__)

_BOUNCED_ATTR = "escalation_bounced_at"
_BOUNCE_LABEL = "bounce"

# `[CASE-42]` quoted in the DSN's copy of the original headers. The DSN's own
# subject is "Delivery Status Notification"; the case tag survives only in the
# quoted original, which is why this reads the body rather than the subject.
_CASE_TOKEN = re.compile(r"\[CASE-(\d+)\]", re.IGNORECASE)

# RFC 5322 Message-IDs quoted by a DSN in `Message-ID:`, `In-Reply-To:`,
# `References:` or the attached `message/rfc822` part. Chatwoot stores the
# Message-ID of each mail it SENDS as that outgoing message's `source_id`
# (without angle brackets), so a quoted id identifies the originating message
# even when the subject carries no [CASE-n] tag (a plain agent reply).
_MESSAGE_ID = re.compile(r"<([^<>\s@]+@[^<>\s]+)>")

# Cap on conversations whose messages are scanned for a source_id, so one
# unmatched bounce cannot fan out into hundreds of Chatwoot calls.
_MAX_CANDIDATE_CONVERSATIONS = 15

_ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")

# Senders and subjects that mean "this is a delivery report, not a customer".
_BOUNCE_SENDERS = ("mailer-daemon", "postmaster")
_BOUNCE_SUBJECTS = (
    "delivery status notification",
    "undeliverable",
    "returned mail",
    "delivery failure",
    "mail delivery failed",
)

# Addresses that appear in every DSN and are never the failed recipient: the
# reporting system itself.
_REPORTER_HINTS = ("mailer-daemon", "postmaster", "googlemail.com")


def _email_meta(message: dict) -> dict:
    attrs = message.get("content_attributes")
    if not isinstance(attrs, dict):
        return {}
    meta = attrs.get("email")
    return meta if isinstance(meta, dict) else {}


def is_bounce(message: dict) -> bool:
    """True when this inbound message is a delivery-status notification.

    Deliberately generous on the sender (any mailer-daemon/postmaster) and on
    the subject, because DSN wording is not standardised across providers. A
    false positive costs one wrongly-resolved conversation, which an operator
    can reopen; a false negative costs a silently undelivered escalation.
    """
    if message.get("message_type") != "incoming":
        return False
    meta = _email_meta(message)
    senders = meta.get("from") or []
    if isinstance(senders, str):
        senders = [senders]
    sender_blob = " ".join(str(s) for s in senders).lower()
    if not sender_blob:
        sender_blob = str((message.get("sender") or {}).get("email") or "").lower()
    if any(hint in sender_blob for hint in _BOUNCE_SENDERS):
        return True
    subject = str(meta.get("subject") or "").lower()
    return any(hint in subject for hint in _BOUNCE_SUBJECTS)


def failed_recipients(text: str) -> list[str]:
    """Addresses named in a DSN body, minus the reporting system's own.

    Order-preserving and deduped. A DSN quotes the original headers, so this
    can pick up more than strictly bounced -- naming one address too many in a
    private note is a far cheaper error than naming none.
    """
    seen: set[str] = set()
    out: list[str] = []
    for address in _ADDRESS.findall(text or ""):
        low = address.lower()
        if low in seen or any(hint in low for hint in _REPORTER_HINTS):
            continue
        seen.add(low)
        out.append(address)
    return out


def extract_case_id(text: str) -> int | None:
    """The escalated conversation a DSN refers to, from the quoted subject."""
    match = _CASE_TOKEN.search(text or "")
    return int(match.group(1)) if match else None


def extract_message_ids(text: str) -> list[str]:
    """Quoted Message-IDs in a DSN body, bracket-stripped, order-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for mid in _MESSAGE_ID.findall(text or ""):
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def _payload_list(raw) -> list:
    if isinstance(raw, dict):
        inner = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        raw = inner.get("payload") or []
    return [x for x in (raw or []) if isinstance(x, dict)]


async def _conversation_sent(chatwoot, conversation_id: int, wanted: set[str]) -> bool:
    """True when any message on this conversation (any direction -- the
    message type is deliberately not checked) has a source_id in `wanted`.
    The ids come from the DSN's own quoted headers, so a match is the mail
    that bounced. Looks at the latest page of messages only -- the
    escalation mail is recent by construction."""
    for message in _payload_list(await chatwoot.get_messages(conversation_id)):
        sid = str(message.get("source_id") or "").strip().strip("<>")
        if sid and sid in wanted:
            return True
    return False


async def find_case_by_message_id(
    chatwoot, message_ids: list[str], recipients: list[str], inbox_id, skip_conv
) -> int | None:
    """Locate the conversation whose outgoing mail has one of `message_ids`.

    Chatwoot has no query-by-source_id endpoint, so candidates are gathered:
    first conversations of contacts matching a failed recipient address (the
    usual case: an agent reply to the customer), then the most recent
    conversations of the DSN's own inbox. Each is checked for a message
    (any direction) whose source_id is a quoted id. Fail-open: errors skip a candidate.
    """
    wanted = {m.strip("<>") for m in message_ids}
    if not wanted:
        return None
    candidates: list[int] = []

    def _add(cid) -> None:
        if isinstance(cid, int) and cid != skip_conv and cid not in candidates:
            candidates.append(cid)

    for address in recipients:
        try:
            for contact in await chatwoot.search_contacts(address):
                if str(contact.get("email") or "").lower() != address.lower():
                    continue
                for conv in await chatwoot.list_contact_conversations(contact["id"]):
                    _add(conv.get("id"))
        except Exception:
            logger.debug("bounce_handler: contact lookup failed for %s", address, exc_info=True)
    if inbox_id is not None and len(candidates) < _MAX_CANDIDATE_CONVERSATIONS:
        try:
            recent = _payload_list(
                await chatwoot.list_conversations(status="all", assignee_type="all")
            )
            for conv in recent:
                if conv.get("inbox_id") == inbox_id:
                    _add(conv.get("id"))
        except Exception:
            logger.debug("bounce_handler: recent conversation listing failed", exc_info=True)

    for cid in candidates[:_MAX_CANDIDATE_CONVERSATIONS]:
        try:
            if await _conversation_sent(chatwoot, cid, wanted):
                return cid
        except Exception:
            logger.debug("bounce_handler: message scan failed for %s", cid, exc_info=True)
    return None


async def maybe_handle_bounce(payload: dict) -> None:
    """Handle a Chatwoot `message_created` event that is a bounce notice."""
    settings = get_settings()
    if not settings.bounce_handling_enabled:
        return
    if not is_bounce(payload):
        return

    body = str(payload.get("content") or "")
    bounce_conv_id = (payload.get("conversation") or {}).get("id")
    case_id = extract_case_id(body)
    recipients = failed_recipients(body)

    chatwoot = get_chatwoot_client()

    if case_id is None:
        # No [CASE-n] (a plain agent reply, say): fall back to the original
        # mail's Message-ID, from Chatwoot's parsed In-Reply-To/References on
        # the DSN itself plus anything quoted in its body.
        meta = _email_meta(payload)
        header_blob = " ".join(
            str(meta.get(k) or "") for k in ("in_reply_to", "references", "message_id")
        )
        # Our own DSN's message_id is not the original's, but a stray match
        # is harmless: it can only equal a source_id of a mail WE sent.
        ids = extract_message_ids(header_blob + " " + body)
        bare = str(meta.get("in_reply_to") or "").strip().strip("<>")
        if bare and bare not in ids:
            ids.insert(0, bare)  # In-Reply-To may arrive without brackets
        try:
            case_id = await find_case_by_message_id(
                chatwoot,
                ids,
                recipients,
                (payload.get("inbox") or {}).get("id"),
                bounce_conv_id,
            )
        except Exception:
            logger.exception("bounce_handler: message-id lookup failed")

    if case_id is not None:
        try:
            who = ", ".join(recipients) if recipients else "the escalation recipient"
            await chatwoot.create_message(
                case_id,
                (
                    f"⚠️ The escalation email to {who} BOUNCED and was never "
                    f"delivered. Please contact them another way. "
                    f"(Delivery report in conversation {bounce_conv_id}.)"
                ),
                private=True,
            )
            await chatwoot.set_custom_attributes(
                case_id, {_BOUNCED_ATTR: datetime.now(timezone.utc).isoformat()}
            )
        except Exception:
            # Note-then-stamp, and both best-effort: a failure here must not
            # stop the tidy-up below, or the DSN stays in the queue forever.
            logger.exception(
                "bounce_handler: failed to annotate case %s from bounce %s",
                case_id,
                bounce_conv_id,
            )
    else:
        logger.info(
            "bounce_handler: bounce %s matches no case; tidying only",
            bounce_conv_id,
        )

    # Tidy up regardless of whether we could attribute it. An unattributable
    # DSN is still not a customer conversation and must not sit in the queue.
    if bounce_conv_id is None:
        return
    try:
        await chatwoot.add_labels(bounce_conv_id, [_BOUNCE_LABEL])
        await chatwoot.toggle_status(bounce_conv_id, "resolved")
    except Exception:
        logger.exception(
            "bounce_handler: failed to tidy bounce conversation %s", bounce_conv_id
        )
