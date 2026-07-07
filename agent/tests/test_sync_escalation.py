"""Tests for `sync.maybe_escalate` / `sync.escalate_conversation`: a Chatwoot
conversation labeled `escalate` becomes a Zammad ticket, with a
`conversation_links` row and a private Chatwoot note recording the ticket
number. Re-processing the same conversation must not create a second
ticket.
"""

import httpx
import respx
from sqlalchemy import select

from app.db.models import ConversationLink
from app.db.session import async_session_maker
from app.services import sync

CHATWOOT = "http://chatwoot-rails:3000"
ZAMMAD = "http://zammad-nginx:8080"

MESSAGES_RESPONSE = {
    "payload": [
        {
            "id": 1,
            "content": "Hi, my invoice is wrong",
            "message_type": 0,
            "private": False,
            "created_at": 1_700_000_000,
            "sender": {"id": 55, "name": "Jane Doe", "email": "jane@example.com"},
        },
        {
            "id": 2,
            "content": "internal note, ignore",
            "message_type": 1,
            "private": True,
            "created_at": 1_700_000_100,
            "sender": {"id": 9, "name": "Agent Bob"},
        },
        {
            "id": 3,
            "content": "Looking into it now",
            "message_type": 1,
            "private": False,
            "created_at": 1_700_000_200,
            "sender": {"id": 9, "name": "Agent Bob"},
        },
    ]
}


def _mock_escalation_routes():
    respx.get(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json=MESSAGES_RESPONSE))
    respx.get(f"{ZAMMAD}/api/v1/users/search").mock(
        return_value=httpx.Response(200, json=[])
    )
    create_user = respx.post(f"{ZAMMAD}/api/v1/users").mock(
        return_value=httpx.Response(201, json={"id": 88})
    )
    create_ticket = respx.post(f"{ZAMMAD}/api/v1/tickets").mock(
        return_value=httpx.Response(
            201, json={"id": 777, "number": "10777"}
        )
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    return create_user, create_ticket, create_message


@respx.mock
async def test_maybe_escalate_creates_ticket_link_and_private_note():
    create_user, create_ticket, create_message = _mock_escalation_routes()

    payload = {
        "event": "conversation_updated",
        "id": 42,
        "labels": ["escalate", "billing"],
        "meta": {"sender": {"id": 55, "email": "jane@example.com"}},
        "messages": [],
    }
    await sync.maybe_escalate(payload)

    assert create_ticket.call_count == 1
    ticket_request = create_ticket.calls.last.request
    # Zammad's tickets_controller does `params[:tags].split(',')` -- it
    # expects a comma-separated string, not a JSON array. Assert the exact
    # scalar-string encoding, not just that the substring appears (a JSON
    # array `["from-chatwoot"]` would also contain this substring but 500s
    # against real Zammad).
    assert (
        b'"tags": "from-chatwoot"' in ticket_request.content
        or b'"tags":"from-chatwoot"' in ticket_request.content
    )
    assert b"Users" in ticket_request.content

    assert create_message.call_count == 1
    note_body = create_message.calls.last.request.content
    assert b"Escalated to Zammad ticket #10777" in note_body
    assert b"#ticket/zoom/777" in note_body
    assert b'"private": true' in note_body or b'"private":true' in note_body

    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLink).where(
                ConversationLink.chatwoot_conversation_id == 42
            )
        )
        link = result.scalar_one_or_none()
    assert link is not None
    assert link.zammad_ticket_id == 777


@respx.mock
async def test_maybe_escalate_is_idempotent_for_repeated_event():
    create_user, create_ticket, create_message = _mock_escalation_routes()

    payload = {
        "event": "conversation_updated",
        "id": 42,
        "labels": ["escalate"],
        "meta": {},
        "messages": [],
    }
    await sync.maybe_escalate(payload)
    await sync.maybe_escalate(payload)

    assert create_ticket.call_count == 1
    assert create_message.call_count == 1


@respx.mock
async def test_escalation_note_failure_does_not_raise_and_keeps_ticket_and_link():
    """The private "Escalated to ticket #N" note is posted only after the
    ticket and conversation_links row are committed. If that final Chatwoot
    call fails, the escalation itself already succeeded — the background
    task must log and swallow the error, not raise, and the link must stay
    intact (a lost note is strictly better than a crashed task)."""
    respx.get(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json=MESSAGES_RESPONSE))
    respx.get(f"{ZAMMAD}/api/v1/users/search").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(f"{ZAMMAD}/api/v1/users").mock(
        return_value=httpx.Response(201, json={"id": 88})
    )
    create_ticket = respx.post(f"{ZAMMAD}/api/v1/tickets").mock(
        return_value=httpx.Response(201, json={"id": 777, "number": "10777"})
    )
    respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(500, json={"error": "boom"}))

    payload = {
        "event": "conversation_updated",
        "id": 42,
        "labels": ["escalate"],
        "meta": {},
        "messages": [],
    }
    await sync.maybe_escalate(payload)  # must not raise

    assert create_ticket.call_count == 1
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLink).where(
                ConversationLink.chatwoot_conversation_id == 42
            )
        )
        link = result.scalar_one_or_none()
    assert link is not None
    assert link.zammad_ticket_id == 777


@respx.mock
async def test_escalate_conversation_maps_priority_to_zammad_name():
    """`escalate_conversation`'s `priority` arg (the AI tool's low/normal/high
    vocabulary) must be mapped to Zammad's priority-by-name convention and
    threaded into the ticket create payload."""
    create_user, create_ticket, create_message = _mock_escalation_routes()

    await sync.escalate_conversation(42, priority="high")

    assert create_ticket.call_count == 1
    ticket_request = create_ticket.calls.last.request
    assert (
        b'"priority": "3 high"' in ticket_request.content
        or b'"priority":"3 high"' in ticket_request.content
    )


@respx.mock
async def test_escalate_conversation_without_priority_omits_field():
    create_user, create_ticket, create_message = _mock_escalation_routes()

    await sync.escalate_conversation(42)

    assert create_ticket.call_count == 1
    ticket_request = create_ticket.calls.last.request
    assert b'"priority"' not in ticket_request.content


@respx.mock
async def test_escalate_conversation_uses_public_urls_for_human_facing_links(monkeypatch):
    """Links embedded in notes for humans (the Zammad ticket body linking
    back to Chatwoot, the Chatwoot note linking to the Zammad ticket) should
    use the public URLs when configured, not the internal docker hostnames
    -- API calls themselves still go to the internal ZAMMAD/CHATWOOT constants
    since the respx routes above are keyed on those internal hosts."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "chatwoot_public_url", "http://crm.1-2-3-4.nip.io")
    monkeypatch.setattr(settings, "zammad_public_url", "http://tickets.1-2-3-4.nip.io")

    create_user, create_ticket, create_message = _mock_escalation_routes()

    await sync.escalate_conversation(42)

    ticket_request = create_ticket.calls.last.request
    assert b"http://crm.1-2-3-4.nip.io/app/accounts/1/conversations/42" in ticket_request.content

    note_body = create_message.calls.last.request.content
    assert b"http://tickets.1-2-3-4.nip.io/#ticket/zoom/777" in note_body


@respx.mock
async def test_maybe_escalate_ignores_payload_without_escalate_label():
    create_user, create_ticket, create_message = _mock_escalation_routes()

    payload = {"event": "conversation_updated", "id": 43, "labels": ["billing"]}
    await sync.maybe_escalate(payload)

    assert not create_ticket.called
    assert not create_message.called
