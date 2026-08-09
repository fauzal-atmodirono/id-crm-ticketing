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
            "bounce_handler: bounce %s carries no case tag; tidying only",
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
