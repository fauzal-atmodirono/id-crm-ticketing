"""Chatwoot-side sync and notification helpers (Chatwoot-only; no external
ticketing backend).

  - EM-7 two-thread email-channel escalation notification
    (`maybe_escalate` / `_maybe_notify_email_escalation`).
  - Dealer-label escalation timestamping for reporting
    (`maybe_stamp_dealer_escalation`).
  - `upsert_contact` / `record_conversation_status`: no-op stubs kept as the
    Chatwoot router's dispatch targets for contact/status events, so the
    router doesn't need to change and a future Chatwoot-side integration has
    a place to hook in.

Every entry point here is designed to run as a FastAPI background task: it
takes an already-parsed webhook payload and never raises out to the caller
for expected "nothing to do" cases (missing fields, unknown ids) — those are
logged and skipped, not treated as errors.
"""

import logging
import re
from datetime import datetime, timezone

from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings

logger = logging.getLogger(__name__)

# Dealer labels are applied manually today (an agent picks `dealer_<slug>`
# from Chatwoot's native label picker) -- see `maybe_stamp_dealer_escalation`.
_DEALER_LABEL = re.compile(r"^dealer_(.+)$")
_DEPT_LABEL = re.compile(r"^dept_(.+)$")


async def upsert_contact(payload: dict) -> None:
    """Handle a Chatwoot `contact_created`/`contact_updated` event.

    No-op: see module docstring. Kept as the router's dispatch target.
    """
    return None


async def record_conversation_status(payload: dict) -> None:
    """Handle a Chatwoot `conversation_status_changed`/`conversation_resolved`
    event.

    No-op: see module docstring. Kept as the router's dispatch target.
    """
    return None


async def _maybe_notify_email_escalation(conversation_id: int, labels: list[str]) -> None:
    """EM-7: for an Email-channel conversation, ask the backend to send the
    two-thread escalation email (customer ack + PIC/dealer forward).

    Fail-open throughout: any missing config, unreachable service, or
    resolution failure just means no email fires -- never raises, matching
    every other background-task helper in this module.
    """
    settings = get_settings()
    if not settings.email_escalation_enabled:
        return

    proton = get_proton_config_client()
    if proton is None:
        return

    try:
        chatwoot = get_chatwoot_client()
        conversation = await chatwoot.get_conversation(conversation_id)
        inbox_id = (conversation or {}).get("inbox_id")
        if inbox_id is None:
            return
        inbox = await chatwoot.get_inbox(inbox_id)
    except Exception:
        logger.exception(
            "maybe_escalate: failed to resolve channel for conversation %s", conversation_id
        )
        return

    if (inbox or {}).get("channel_type") != "Channel::Email":
        return

    department = next(
        (m.group(1) for lbl in labels if (m := _DEPT_LABEL.match(lbl))), None
    )
    dealer = next(
        (m.group(1) for lbl in labels if (m := _DEALER_LABEL.match(lbl))), None
    )

    title = f"Escalated conversation #{conversation_id}"
    body = f"Conversation #{conversation_id} was escalated by an agent."
    try:
        raw_messages = await chatwoot.get_messages(conversation_id)
        if isinstance(raw_messages, dict):
            message_list = raw_messages.get("payload") or []
        else:
            message_list = raw_messages or []

        first_incoming_text: str | None = None
        transcript_lines: list[str] = []
        for message in message_list:
            if message.get("private"):
                continue
            sender_name = (message.get("sender") or {}).get("name", "Customer")
            text = message.get("content") or ""
            transcript_lines.append(f"{sender_name}: {text}")

            if first_incoming_text is None and message.get("message_type") == 0:
                first_incoming_text = text

        if first_incoming_text:
            title = first_incoming_text[:100]
        if transcript_lines:
            body = "\n".join(transcript_lines[-10:])
    except Exception:
        logger.exception(
            "maybe_escalate: failed to build email-escalation transcript for "
            "conversation %s; falling back to generic title/body",
            conversation_id,
        )

    await proton.notify_email_escalation(
        conversation_id=conversation_id,
        title=title,
        body=body,
        department=department,
        dealer=dealer,
    )


async def maybe_escalate(payload: dict) -> None:
    """Handle a Chatwoot `conversation_updated` event: fire the EM-7
    email-channel escalation notification when the `escalate` label is
    present. Escalation stays entirely inside Chatwoot / the agent-bot's
    handoff path -- there is no external ticketing backend to sync to."""
    conversation_id = payload.get("id")
    labels = payload.get("labels") or []
    if conversation_id is None or "escalate" not in labels:
        return

    await _maybe_notify_email_escalation(conversation_id, labels)


async def maybe_stamp_dealer_escalation(payload: dict) -> None:
    """Handle a Chatwoot `conversation_updated` event: stamp a
    `dealer_escalated_at` custom attribute the first time a `dealer_<slug>`
    label appears on the conversation, so the BI turnaround-time view has a
    real escalation timestamp to diff against `resolved_at`. Idempotent
    (never overwrites an existing stamp) and fail-open -- a Chatwoot API
    error here must never affect the rest of the webhook dispatch."""
    conversation_id = payload.get("id")
    labels = payload.get("labels") or []
    if conversation_id is None or not any(_DEALER_LABEL.match(lbl) for lbl in labels):
        return

    try:
        chatwoot = get_chatwoot_client()
        conversation = await chatwoot.get_conversation(conversation_id)
        existing = (conversation or {}).get("custom_attributes") or {}
        if existing.get("dealer_escalated_at"):
            return  # already stamped -- never overwrite

        await chatwoot.set_custom_attributes(
            conversation_id,
            {"dealer_escalated_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        logger.exception(
            "maybe_stamp_dealer_escalation: failed for conversation %s",
            conversation_id,
        )

