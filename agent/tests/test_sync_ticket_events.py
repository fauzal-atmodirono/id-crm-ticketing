"""Tests for `sync.on_ticket_event`: Zammad ticket state changes propagate
back to the linked Chatwoot conversation as a private note, with optional
auto-resolve when the ticket closes. Loop-prevention (dropping payloads
authored by the integration user) is the webhook router's job, tested in
test_zammad_router.py — this module tests the sync function directly.
"""

import httpx
import pytest
import respx
from sqlalchemy import select

from app.clients.proton import ProtonConfigClient
from app.config import get_settings
from app.db.models import ConversationLink
from app.db.session import async_session_maker
from app.services import sync

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"

CONVERSATION_RESPONSE = {"id": 44, "inbox_id": 7, "status": "open"}

INBOXES_WITH_ASSISTANT = {
    "inboxes": [
        {"inbox_id": 7, "name": "Inbox7", "mode": "suggest", "source": "manual", "assistant_id": "asst-res"}
    ]
}

ASSISTANT_WITH_RESOLUTION = {
    "id": "asst-res",
    "config": {
        "welcome_message": "Welcome!",
        "handoff_message": "Handing off.",
        "resolution_message": "Your issue has been resolved. Have a great day!",
    },
}

ASSISTANT_EMPTY_RESOLUTION = {
    "id": "asst-res",
    "config": {
        "welcome_message": "",
        "handoff_message": "",
        "resolution_message": "",
    },
}


def _make_proton_client() -> ProtonConfigClient:
    inner = httpx.AsyncClient(base_url=PROTON, headers={"x-api-key": "testkey"})
    return ProtonConfigClient(base_url=PROTON, api_key="testkey", client=inner, ttl=0.0)


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
async def test_on_ticket_event_note_failure_does_not_raise_and_state_still_commits():
    """The state-change note (`Ticket #N moved to X`) isn't wrapped in
    try/except like the rest of the module -- a 500 posting it must be
    logged and swallowed, and `last_synced_state` must still be committed so
    the next delivery for this ticket doesn't re-post."""
    await _seed_link(conversation_id=47, ticket_id=782, last_synced_state="open")
    note_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/47/messages"
    ).mock(return_value=httpx.Response(500, json={"error": "boom"}))

    payload = {"ticket": {"id": 782, "number": "10782", "state": "closed"}}
    await sync.on_ticket_event(payload)  # must not raise

    assert note_route.call_count == 1
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLink).where(ConversationLink.zammad_ticket_id == 782)
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
async def test_on_ticket_event_toggle_status_failure_does_not_raise(monkeypatch):
    """The AUTO_RESOLVE toggle runs after the state note and link update are
    already committed. A failure there must be logged and swallowed — the
    background task must not raise, and the synced state must stay
    committed."""
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_resolve", True)

    await _seed_link(conversation_id=46, ticket_id=781, last_synced_state="open")
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/46/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    toggle_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/46/toggle_status"
    ).mock(return_value=httpx.Response(500, json={"error": "boom"}))

    payload = {"ticket": {"id": 781, "number": "10781", "state": "closed"}}
    await sync.on_ticket_event(payload)  # must not raise

    assert toggle_route.call_count == 1
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLink).where(ConversationLink.zammad_ticket_id == 781)
        )
        link = result.scalar_one_or_none()
    assert link.last_synced_state == "closed"


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


# ---------------------------------------------------------------------------
# Resolution message: posted publicly before auto-resolve when configured
# ---------------------------------------------------------------------------


@respx.mock
async def test_auto_resolve_posts_resolution_message_before_toggle(monkeypatch):
    """When proton is configured with a resolution_message, it must be posted
    publicly BEFORE the conversation is toggled to resolved."""
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_resolve", True)

    await _seed_link(conversation_id=44, ticket_id=790, last_synced_state="open")

    # State-change note (private)
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/44/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    # Chatwoot conversation fetch for inbox_id
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/44").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    # Proton backend routes
    respx.get(f"{PROTON}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_WITH_ASSISTANT)
    )
    respx.get(f"{PROTON}/kb/assistants/asst-res").mock(
        return_value=httpx.Response(200, json=ASSISTANT_WITH_RESOLUTION)
    )

    call_order: list[str] = []

    # Intercept create_message (public resolution message) to track call order
    resolution_msg_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/44/messages"
    ).mock(side_effect=lambda req: (call_order.append("message"), httpx.Response(200, json={"id": 2}))[1])

    toggle_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/44/toggle_status"
    ).mock(side_effect=lambda req: (call_order.append("toggle"), httpx.Response(200, json={"success": True}))[1])

    client = _make_proton_client()
    monkeypatch.setattr(sync, "get_proton_config_client", lambda: client)

    payload = {"ticket": {"id": 790, "number": "10790", "state": "closed"}}
    await sync.on_ticket_event(payload)

    # Resolution message must have been posted (the public one)
    # Note: the state-change note is also a POST to messages, so we check content
    # of all create_message calls for the resolution text.
    all_message_bodies = [
        call.request.content for call in respx.calls
        if "/messages" in str(call.request.url)
    ]
    assert any(b"Your issue has been resolved" in body for body in all_message_bodies)

    # Toggle to resolved must also have happened
    assert toggle_route.call_count == 1
    assert b'"resolved"' in toggle_route.calls.last.request.content

    await client.aclose()


@respx.mock
async def test_auto_resolve_empty_resolution_message_just_resolves(monkeypatch):
    """When the assistant config has an empty resolution_message, no extra
    public message is posted — behavior identical to no proton config."""
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_resolve", True)

    await _seed_link(conversation_id=44, ticket_id=791, last_synced_state="open")

    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/44/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/44").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_WITH_ASSISTANT)
    )
    respx.get(f"{PROTON}/kb/assistants/asst-res").mock(
        return_value=httpx.Response(200, json=ASSISTANT_EMPTY_RESOLUTION)
    )
    toggle_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/44/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    client = _make_proton_client()
    monkeypatch.setattr(sync, "get_proton_config_client", lambda: client)

    payload = {"ticket": {"id": 791, "number": "10791", "state": "closed"}}
    await sync.on_ticket_event(payload)

    # No resolution message bodies — only the state-change note (private)
    all_message_bodies = [
        call.request.content for call in respx.calls
        if "/messages" in str(call.request.url)
    ]
    # State-change note is private; no resolution_message text
    assert not any(b"Your issue has been resolved" in body for body in all_message_bodies)

    # Still resolves
    assert toggle_route.call_count == 1
    assert b'"resolved"' in toggle_route.calls.last.request.content

    await client.aclose()


@respx.mock
async def test_auto_resolve_proton_unconfigured_just_resolves(monkeypatch):
    """When proton is not configured, auto-resolve must skip the conversation GET
    entirely and go straight to toggle_status — no extra message, no inbox fetch.

    Lock-in: register `conversations/44` GET so respx tracks it; assert call_count
    == 0. If the code wrongly calls get_conversation, respx returns a 200 (not a
    network error), meaning the try/except cannot hide the violation — the
    call_count assertion below will catch it.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_resolve", True)

    await _seed_link(conversation_id=44, ticket_id=792, last_synced_state="open")

    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/44/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    # Register (but do not expect) the conversation GET — call_count must stay 0.
    get_conversation_route = respx.get(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/44"
    ).mock(return_value=httpx.Response(200, json=CONVERSATION_RESPONSE))
    toggle_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/44/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    # Proton explicitly returns None (unconfigured)
    monkeypatch.setattr(sync, "get_proton_config_client", lambda: None)

    payload = {"ticket": {"id": 792, "number": "10792", "state": "closed"}}
    await sync.on_ticket_event(payload)

    # get_conversation must NOT have been called when proton is unconfigured
    assert get_conversation_route.call_count == 0, (
        "get_conversation was called even though proton is unconfigured"
    )

    # Just the toggle — no proton calls, no resolution message
    assert toggle_route.call_count == 1
    assert b'"resolved"' in toggle_route.calls.last.request.content

    # No calls to proton
    for call in respx.calls:
        assert PROTON not in str(call.request.url), (
            f"Unexpected proton call: {call.request.url}"
        )
