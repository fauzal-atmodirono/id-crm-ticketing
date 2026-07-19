"""Integration tests for the proton-backend path in the orchestrator.

Tests here verify:
  - inbox mode "off"      → Gemini NOT called, no message posted
  - inbox mode "auto"     → public reply posted via bot token
  - inbox mode "suggest"  → private note posted + conversation reopened
  - proton unconfigured   → existing settings.agent_mode governs (regression
                            guard; main coverage in test_orchestrator.py)

HTTP is mocked with respx (Chatwoot + proton backend). Gemini is stubbed via
monkeypatch. The proton client cache is bypassed by constructing a fresh
ProtonConfigClient with ttl=0 per test and injecting it via monkeypatching
`orchestrator.get_proton_config_client` (the name as it was imported into the
orchestrator module namespace, not the deps module attribute).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.ai import gemini
from app.clients.proton import ProtonConfigClient
from app.services import orchestrator

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"

# Conversation response includes inbox_id
CONVERSATION_RESPONSE = {"id": 42, "inbox_id": 7, "status": "pending"}

MESSAGES_RESPONSE = {
    "payload": [
        {
            "id": 1,
            "content": "Help me",
            "message_type": 0,
            "private": False,
            "created_at": 1_700_000_000,
            "sender": {"id": 55, "name": "Alice", "email": "alice@example.com"},
        }
    ]
}

INBOXES_WITH_MODE = {
    "off": {"inboxes": [{"inbox_id": 7, "name": "Inbox7", "mode": "off", "source": "manual"}]},
    "auto": {"inboxes": [{"inbox_id": 7, "name": "Inbox7", "mode": "auto", "source": "manual"}]},
    "suggest": {"inboxes": [{"inbox_id": 7, "name": "Inbox7", "mode": "suggest", "source": "manual"}]},
    # With assistant_id for persona-message tests
    "suggest_with_assistant": {
        "inboxes": [{"inbox_id": 7, "name": "Inbox7", "mode": "suggest", "source": "manual", "assistant_id": "asst-test"}]
    },
}

ASSISTANT_RESPONSE_WITH_HANDOFF = {
    "id": "asst-test",
    "config": {
        "welcome_message": "Welcome!",
        "handoff_message": "A human agent will be with you shortly.",
        "resolution_message": "Your issue has been resolved.",
    },
}

ASSISTANT_RESPONSE_EMPTY_HANDOFF = {
    "id": "asst-test",
    "config": {
        "welcome_message": "Welcome!",
        "handoff_message": "",
        "resolution_message": "",
    },
}

SETTINGS_RESPONSE = {
    "settings": {"debounce_seconds": {"value": 0.0, "source": "test"}}
}


def _payload(conversation_id=42, status="pending", sender_type="contact"):
    return {
        "event": "message_created",
        "id": 501,
        "content": "Help me",
        "message_type": "incoming",
        "private": False,
        "conversation": {"id": conversation_id, "status": status},
        "sender": {"id": 55, "name": "Alice", "email": "alice@example.com", "type": sender_type},
    }


def _stub_decide(decision):
    async def _fake(system_prompt, context, client=None):
        return decision

    return _fake


def _make_proton_client() -> ProtonConfigClient:
    """Build a fresh ProtonConfigClient (TTL=0, so no caching) backed by a
    real httpx.AsyncClient pointing at the respx-mocked PROTON base URL."""
    inner = httpx.AsyncClient(base_url=PROTON, headers={"x-api-key": "testkey"})
    return ProtonConfigClient(base_url=PROTON, api_key="testkey", client=inner, ttl=0.0)


@pytest.fixture(autouse=True)
def _fast_debounce_and_clear(monkeypatch):
    monkeypatch.setattr(orchestrator, "DEBOUNCE_SECONDS", 0)
    orchestrator._pending_tasks.clear()
    yield
    orchestrator._pending_tasks.clear()


def _mock_chatwoot_and_proton_routes(mode_key: str):
    """Register the Chatwoot + proton respx routes for *mode_key*."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_WITH_MODE[mode_key])
    )
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(200, json=SETTINGS_RESPONSE)
    )


# ---------------------------------------------------------------------------
# inbox mode "off" — Gemini must NOT be called, nothing posted
# ---------------------------------------------------------------------------


@respx.mock
async def test_inbox_mode_off_skips_gemini_and_posts_nothing(monkeypatch):
    _mock_chatwoot_and_proton_routes("off")

    gemini_called = {"n": 0}

    async def _fake_decide(*args, **kwargs):
        gemini_called["n"] += 1
        return gemini.Decision("send_reply", {"text": "Hi"}, None, 1)

    monkeypatch.setattr(gemini, "decide", _fake_decide)

    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    )
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    )

    client = _make_proton_client()
    # Patch the name as imported into the orchestrator module namespace
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    assert gemini_called["n"] == 0
    assert not create_message.called
    assert not toggle_status.called

    await client.aclose()


# ---------------------------------------------------------------------------
# inbox mode "auto" — public reply posted via bot token
# ---------------------------------------------------------------------------


@respx.mock
async def test_inbox_mode_auto_sends_public_reply(monkeypatch):
    from app.config import get_settings

    _mock_chatwoot_and_proton_routes("auto")

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("send_reply", {"text": "Auto answer."}, None, 3)),
    )

    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    )

    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    assert create_message.call_count == 1
    request = create_message.calls.last.request
    assert b'"private": false' in request.content or b'"private":false' in request.content
    assert b"Auto answer." in request.content
    # Bot token header must be used for auto-send
    settings = get_settings()
    assert request.headers["api_access_token"] == settings.chatwoot_bot_token
    # In auto mode, conversation is NOT toggled (stays pending)
    assert not toggle_status.called

    await client.aclose()


# ---------------------------------------------------------------------------
# inbox mode "suggest" — private note + reopen
# ---------------------------------------------------------------------------


@respx.mock
async def test_inbox_mode_suggest_posts_private_note_and_reopens(monkeypatch):
    _mock_chatwoot_and_proton_routes("suggest")

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("send_reply", {"text": "Suggested answer."}, None, 2)),
    )

    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"Suggested reply" in body
    assert b"Suggested answer." in body
    assert b'"private": true' in body or b'"private":true' in body

    assert toggle_status.call_count == 1
    assert b"open" in toggle_status.calls.last.request.content

    await client.aclose()


# ---------------------------------------------------------------------------
# proton UNCONFIGURED — falls back to settings.agent_mode (regression guard)
# ---------------------------------------------------------------------------


@respx.mock
async def test_proton_unconfigured_uses_global_agent_mode(monkeypatch):
    """When get_proton_config_client() returns None, the orchestrator must
    behave exactly as before: use settings.agent_mode, no conversation fetch,
    no proton HTTP calls."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    # Explicitly make proton unavailable — patch the orchestrator's local name
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: None)

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("send_reply", {"text": "Fallback."}, None, 1)),
    )

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    # In default "suggest" mode: private note + reopen
    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"Suggested reply" in body
    assert b'"private": true' in body or b'"private":true' in body
    assert toggle_status.call_count == 1

    # No proton routes should have been registered/hit
    for call in respx.calls:
        assert PROTON not in str(call.request.url), \
            f"Unexpected call to proton backend: {call.request.url}"


# ---------------------------------------------------------------------------
# proton UNREACHABLE — fails open (uses global agent_mode)
# ---------------------------------------------------------------------------


@respx.mock
async def test_proton_unreachable_falls_back_to_global_mode(monkeypatch):
    """If the proton backend is configured but returns an error, the
    orchestrator must fail open and use settings.agent_mode."""
    # Chatwoot conversation + messages succeed
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    # Proton backend returns 503 for all calls
    respx.get(f"{PROTON}/kb/inboxes").mock(
        return_value=httpx.Response(503, json={"error": "down"})
    )
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(503, json={"error": "down"})
    )

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("send_reply", {"text": "Fallback reply."}, None, 1)),
    )

    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    # Falls back to suggest mode (default settings.agent_mode)
    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"Suggested reply" in body

    await client.aclose()


# ---------------------------------------------------------------------------
# Handoff message: public message posted before reopen when configured
# ---------------------------------------------------------------------------


@respx.mock
async def test_handoff_to_human_with_handoff_message_posts_public_message(monkeypatch):
    """When proton is configured and returns a handoff_message, the orchestrator
    must post it publicly to the customer BEFORE reopening the conversation."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_WITH_MODE["suggest_with_assistant"])
    )
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(200, json=SETTINGS_RESPONSE)
    )
    respx.get(f"{PROTON}/kb/assistants/asst-test").mock(
        return_value=httpx.Response(200, json=ASSISTANT_RESPONSE_WITH_HANDOFF)
    )

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("handoff_to_human", {"reason": "complex issue"}, None, 2)),
    )

    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    # One public message posted with the handoff text; then conversation reopened
    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"A human agent will be with you shortly." in body
    # Must be public (not private)
    assert b'"private": false' in body or b'"private":false' in body
    assert toggle_status.call_count == 1
    assert b"open" in toggle_status.calls.last.request.content

    await client.aclose()


@respx.mock
async def test_handoff_to_human_with_empty_handoff_message_no_extra_post(monkeypatch):
    """When the assistant config has an empty handoff_message, no extra public
    message should be posted — behavior must be identical to no proton config."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_WITH_MODE["suggest_with_assistant"])
    )
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(200, json=SETTINGS_RESPONSE)
    )
    respx.get(f"{PROTON}/kb/assistants/asst-test").mock(
        return_value=httpx.Response(200, json=ASSISTANT_RESPONSE_EMPTY_HANDOFF)
    )

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("handoff_to_human", {"reason": "unclear"}, None, 1)),
    )

    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages")
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    # No message posted (empty handoff_message), just reopen
    assert not create_message.called
    assert toggle_status.call_count == 1

    await client.aclose()


@respx.mock
async def test_handoff_to_human_proton_unconfigured_no_extra_post(monkeypatch):
    """When proton is not configured, handoff must behave identically to today:
    no extra public message, just reopen the conversation."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("handoff_to_human", {"reason": "unclear"}, None, 1)),
    )
    # Proton explicitly unconfigured
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: None)

    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages")
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    assert not create_message.called
    assert toggle_status.call_count == 1


# ---------------------------------------------------------------------------
# Garbage/unknown inbox mode — must degrade to SUGGEST (customer-safe default)
# ---------------------------------------------------------------------------


@respx.mock
async def test_unknown_inbox_mode_degrades_to_suggest(monkeypatch):
    """When proton returns an unknown/garbage mode string (e.g. "banana"),
    the orchestrator must fall back to the safe default (suggest): post a
    private note + reopen. It must NEVER auto-send a public reply.

    This locks in the customer-facing safety guarantee: any unrecognized mode
    from the backend is treated the same as "suggest", never as "auto".
    """
    # Return an inbox with an unrecognized mode
    inboxes_garbage_mode = {
        "inboxes": [{"inbox_id": 7, "name": "Inbox7", "mode": "banana", "source": "manual"}]
    }
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=inboxes_garbage_mode)
    )
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(200, json=SETTINGS_RESPONSE)
    )

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("send_reply", {"text": "Some reply."}, None, 1)),
    )

    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    # Must post a private note (suggest branch), NOT a public reply (auto branch)
    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b'"private": true' in body or b'"private":true' in body, (
        "Garbage mode must degrade to suggest (private note), not auto-send"
    )
    # Must reopen (suggest branch reopens for a human)
    assert toggle_status.call_count == 1
    assert b"open" in toggle_status.calls.last.request.content

    await client.aclose()
