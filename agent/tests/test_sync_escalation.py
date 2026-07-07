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
    assert b"from-chatwoot" in ticket_request.content
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
async def test_maybe_escalate_ignores_payload_without_escalate_label():
    create_user, create_ticket, create_message = _mock_escalation_routes()

    payload = {"event": "conversation_updated", "id": 43, "labels": ["billing"]}
    await sync.maybe_escalate(payload)

    assert not create_ticket.called
    assert not create_message.called
