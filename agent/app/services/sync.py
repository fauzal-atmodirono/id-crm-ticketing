"""Sync flows between Chatwoot (CRM) and Zammad (ticketing).

Three directions of sync live here:
  - Chatwoot contact events -> Zammad user (`upsert_contact`).
  - Chatwoot conversation escalation -> Zammad ticket
    (`maybe_escalate` / `escalate_conversation`).
  - Zammad ticket events -> Chatwoot conversation note/status
    (`on_ticket_event`).

Every entry point here is designed to run as a FastAPI background task: it
takes an already-parsed webhook payload, does its own DB session management
(there's no request-scoped session to reuse), and never raises out to the
caller for expected "nothing to do" cases (missing fields, unknown ids) —
those are logged and skipped, not treated as errors.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.clients.deps import get_chatwoot_client, get_zammad_client
from app.config import get_settings
from app.db.models import ContactLink, ConversationLink
from app.db.session import async_session_maker

logger = logging.getLogger(__name__)


def _format_timestamp(value: object) -> str:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except (OverflowError, OSError, ValueError):
            return str(value)
    return str(value) if value is not None else "unknown-time"


async def _find_contact_link(chatwoot_contact_id: int) -> ContactLink | None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(ContactLink).where(
                ContactLink.chatwoot_contact_id == chatwoot_contact_id
            )
        )
        return result.scalar_one_or_none()


async def _ensure_zammad_customer(
    chatwoot_contact_id: int,
    email: str,
    name: str | None,
    company: str | None = None,
) -> int:
    """Ensure a Zammad user + `contact_links` row exist for this Chatwoot
    contact, creating or updating as needed. Returns the Zammad user id."""
    zammad = get_zammad_client()

    organization = None
    if company:
        organization = await zammad.find_or_create_organization(company)

    fields: dict = {"email": email}
    if name:
        first, _, last = name.partition(" ")
        fields["firstname"] = first
        fields["lastname"] = last
    if organization:
        fields["organization_id"] = organization.get("id")

    existing_link = await _find_contact_link(chatwoot_contact_id)

    if existing_link:
        user = await zammad.update_user(existing_link.zammad_user_id, **fields)
        user_id = user.get("id", existing_link.zammad_user_id)
    else:
        users = await zammad.search_users(email)
        matched = next(
            (u for u in (users or []) if u.get("email") == email), None
        )
        if matched:
            user = await zammad.update_user(matched["id"], **fields)
            user_id = user.get("id", matched["id"])
        else:
            user = await zammad.create_user(**fields)
            user_id = user["id"]

    async with async_session_maker() as session:
        result = await session.execute(
            select(ContactLink).where(
                ContactLink.chatwoot_contact_id == chatwoot_contact_id
            )
        )
        link = result.scalar_one_or_none()
        if link:
            link.zammad_user_id = user_id
            link.email = email
        else:
            session.add(
                ContactLink(
                    chatwoot_contact_id=chatwoot_contact_id,
                    zammad_user_id=user_id,
                    email=email,
                )
            )
        await session.commit()

    return user_id


async def upsert_contact(payload: dict) -> None:
    """Handle a Chatwoot `contact_created`/`contact_updated` event."""
    contact_id = payload.get("id")
    email = payload.get("email")
    name = payload.get("name")
    company = (payload.get("additional_attributes") or {}).get("company_name")

    if contact_id is None or not email:
        logger.info(
            "upsert_contact: skipping contact %s with no email", contact_id
        )
        return

    await _ensure_zammad_customer(contact_id, email, name, company)


async def record_conversation_status(payload: dict) -> None:
    """Handle a Chatwoot `conversation_status_changed`/`conversation_resolved`
    event: record-only, no downstream Zammad call. Updates
    `conversation_links.last_synced_state` if a link already exists;
    otherwise a no-op (nothing to record against)."""
    conversation_id = payload.get("id")
    status = payload.get("status")
    if conversation_id is None or not status:
        logger.info(
            "record_conversation_status: skipping payload with missing id/status"
        )
        return

    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLink).where(
                ConversationLink.chatwoot_conversation_id == conversation_id
            )
        )
        link = result.scalar_one_or_none()
        if link is None:
            return
        link.last_synced_state = status
        await session.commit()


async def maybe_escalate(payload: dict) -> None:
    """Handle a Chatwoot `conversation_updated` event: escalate to a Zammad
    ticket if the `escalate` label is present and the conversation isn't
    already linked."""
    conversation_id = payload.get("id")
    labels = payload.get("labels") or []
    if conversation_id is None or "escalate" not in labels:
        return

    existing = await _conversation_link_by_chatwoot_id(conversation_id)
    if existing is not None:
        logger.info(
            "maybe_escalate: conversation %s already escalated, skipping",
            conversation_id,
        )
        return

    await escalate_conversation(conversation_id)


async def _conversation_link_by_chatwoot_id(
    conversation_id: int,
) -> ConversationLink | None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLink).where(
                ConversationLink.chatwoot_conversation_id == conversation_id
            )
        )
        return result.scalar_one_or_none()


async def escalate_conversation(
    conversation_id: int,
    reason: str | None = None,
    priority: str | None = None,
    summary: str | None = None,
) -> None:
    """Create a Zammad ticket from a Chatwoot conversation's transcript and
    link the two. Idempotent via a pre-check plus the `conversation_links`
    unique constraint (belt-and-suspenders against a concurrent duplicate
    call racing past the pre-check).

    `reason` and `priority` are accepted for forward compatibility with the
    Task 5 AI layer (which will derive them and call this directly) but
    aren't threaded into the Zammad payload yet: `ZammadClient.create_ticket`
    doesn't expose ticket-priority/reason fields, and extending that
    interface is out of scope here.
    """
    if await _conversation_link_by_chatwoot_id(conversation_id) is not None:
        logger.info(
            "escalate_conversation: conversation %s already escalated",
            conversation_id,
        )
        return

    settings = get_settings()
    chatwoot = get_chatwoot_client()
    zammad = get_zammad_client()

    raw_messages = await chatwoot.get_messages(conversation_id)
    if isinstance(raw_messages, dict):
        message_list = raw_messages.get("payload") or []
    else:
        message_list = raw_messages or []

    transcript_lines: list[str] = []
    first_incoming_text: str | None = None
    contact_sender: dict | None = None
    for message in message_list:
        if message.get("private"):
            continue
        sender = message.get("sender") or {}
        sender_name = sender.get("name", "Unknown")
        text = message.get("content") or ""
        timestamp = _format_timestamp(message.get("created_at"))
        transcript_lines.append(f"[{timestamp}] {sender_name}: {text}")

        if message.get("message_type") == 0:
            if first_incoming_text is None:
                first_incoming_text = text
            if contact_sender is None and sender:
                contact_sender = sender

    transcript = "\n".join(transcript_lines) or "(no messages)"
    title = (summary or first_incoming_text or f"Chatwoot conversation {conversation_id}")[
        :100
    ]

    customer_id: int | None = None
    if contact_sender and contact_sender.get("id") is not None:
        contact_email = contact_sender.get("email") or (
            f"contact-{contact_sender['id']}@unknown.invalid"
        )
        customer_id = await _ensure_zammad_customer(
            contact_sender["id"], contact_email, contact_sender.get("name")
        )

    if customer_id is None:
        logger.warning(
            "escalate_conversation: could not resolve a Zammad customer for "
            "conversation %s; aborting escalation",
            conversation_id,
        )
        return

    conversation_url = (
        f"{settings.chatwoot_url}/app/accounts/{settings.chatwoot_account_id}"
        f"/conversations/{conversation_id}"
    )
    article = {
        "body": f"{transcript}\n\nOriginal Chatwoot conversation: {conversation_url}",
        "internal": False,
    }

    ticket = await zammad.create_ticket(
        title=title,
        group="Users",
        customer_id=customer_id,
        article=article,
        tags=["from-chatwoot"],
    )
    ticket_id = ticket["id"]
    ticket_number = ticket.get("number", ticket_id)

    async with async_session_maker() as session:
        session.add(
            ConversationLink(
                chatwoot_conversation_id=conversation_id,
                zammad_ticket_id=ticket_id,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.info(
                "escalate_conversation: conversation %s escalated concurrently, "
                "not posting a duplicate note",
                conversation_id,
            )
            return

    ticket_url = f"{settings.zammad_url}/#ticket/zoom/{ticket_id}"
    await chatwoot.create_message(
        conversation_id,
        f"Escalated to Zammad ticket #{ticket_number}: {ticket_url}",
        private=True,
    )


async def on_ticket_event(payload: dict) -> None:
    """Handle a Zammad ticket trigger webhook: propagate state changes back
    to the linked Chatwoot conversation as a private note, and optionally
    auto-resolve when the ticket closes."""
    ticket = payload.get("ticket") or {}
    ticket_id = ticket.get("id")
    state = ticket.get("state")

    if ticket_id is None:
        logger.info("on_ticket_event: payload missing ticket id")
        return

    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLink).where(
                ConversationLink.zammad_ticket_id == ticket_id
            )
        )
        link = result.scalar_one_or_none()
        if link is None:
            logger.info("on_ticket_event: unknown ticket %s, ignoring", ticket_id)
            return

        if not state or state == link.last_synced_state:
            return

        conversation_id = link.chatwoot_conversation_id
        ticket_number = ticket.get("number", ticket_id)

        chatwoot = get_chatwoot_client()
        await chatwoot.create_message(
            conversation_id,
            f"Ticket #{ticket_number} moved to {state}",
            private=True,
        )
        link.last_synced_state = state
        await session.commit()

    settings = get_settings()
    if state == "closed" and settings.auto_resolve:
        chatwoot = get_chatwoot_client()
        await chatwoot.toggle_status(conversation_id, "resolved")
