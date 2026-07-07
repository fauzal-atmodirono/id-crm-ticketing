"""Tests for `sync.on_ticket_event`: Zammad ticket state changes propagate
back to the linked Chatwoot conversation as a private note, with optional
auto-resolve when the ticket closes. Loop-prevention (dropping payloads
authored by the integration user) is the webhook router's job, tested in
test_zammad_router.py — this module tests the sync function directly.
"""

import httpx
import respx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import ConversationLink
from app.db.session import async_session_maker
from app.services import sync

CHATWOOT = "http://chatwoot-rails:3000"


async def _seed_link(conversation_id: int, ticket_id: int, last_synced_state=None):
    async with async_session_maker() as session:
        session.add(
            ConversationLink(
                chatwoot_conversation_id=conversation_id,
                zammad_ticket_id=ticket_id,
                last_synced_state=last_synced_state,
            )
        )
        await session.commit()


@respx.mock
async def test_on_ticket_event_posts_note_on_state_change():
    await _seed_link(conversation_id=42, ticket_id=777, last_synced_state="open")
    note_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 1}))

    payload = {"ticket": {"id": 777, "number": "10777", "state": "closed"}}
    await sync.on_ticket_event(payload)

    assert note_route.call_count == 1
    body = note_route.calls.last.request.content
    assert b"Ticket #10777 moved to closed" in body

    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLink).where(ConversationLink.zammad_ticket_id == 777)
        )
        link = result.scalar_one_or_none()
    assert link.last_synced_state == "closed"


@respx.mock
async def test_on_ticket_event_unknown_ticket_is_ignored():
    note_route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages")

    payload = {"ticket": {"id": 999999, "number": "1", "state": "closed"}}
    await sync.on_ticket_event(payload)

    assert not note_route.called


@respx.mock
async def test_on_ticket_event_no_state_change_is_noop():
    await _seed_link(conversation_id=42, ticket_id=778, last_synced_state="open")
    note_route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages")

    payload = {"ticket": {"id": 778, "number": "10778", "state": "open"}}
    await sync.on_ticket_event(payload)

    assert not note_route.called


@respx.mock
async def test_on_ticket_event_closed_with_auto_resolve_toggles_status(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_resolve", True)

    await _seed_link(conversation_id=44, ticket_id=779, last_synced_state="open")
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/44/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    toggle_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/44/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    payload = {"ticket": {"id": 779, "number": "10779", "state": "closed"}}
    await sync.on_ticket_event(payload)

    assert toggle_route.call_count == 1
    assert b'"resolved"' in toggle_route.calls.last.request.content


@respx.mock
async def test_on_ticket_event_closed_without_auto_resolve_does_not_toggle():
    settings = get_settings()
    assert settings.auto_resolve is False  # default from conftest env

    await _seed_link(conversation_id=45, ticket_id=780, last_synced_state="open")
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/45/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    toggle_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/45/toggle_status"
    )

    payload = {"ticket": {"id": 780, "number": "10780", "state": "closed"}}
    await sync.on_ticket_event(payload)

    assert not toggle_route.called
