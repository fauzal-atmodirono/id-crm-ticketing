from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.adapters.handoff_store import InMemoryHandoffStore
from chatbot.features.chat.handoff_bridge import HandoffBridge
from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock, bridge: HandoffBridge, adapter: ChatwootAdapter) -> TestClient:
    app = create_app(orch._settings)
    app.include_router(build_chat_router(orch, handoff_bridge=bridge, human_agent_bridge=adapter))
    return TestClient(app)


@pytest.mark.asyncio
async def test_agent_outgoing_is_relayed_and_published() -> None:
    settings = Settings(crm_provider="chatwoot", phone_enabled=False, chatwoot_webhook_secret="")
    adapter = ChatwootAdapter(settings)
    orch = MagicMock()
    orch._settings = settings
    orch._ticketing_port = adapter
    store = InMemoryHandoffStore()
    bridge = HandoffBridge(store=store)
    await bridge.register("whatsapp-+60123", "42")
    bridge.publish = AsyncMock()  # type: ignore[method-assign]

    client = _client(orch, bridge, adapter)
    payload: dict[str, Any] = {
        "event": "message_created",
        "message_type": "outgoing",
        "private": False,
        "content": "Agent reply",
        "conversation": {"id": 42},
        "sender": {"name": "Aina"},
    }
    res = client.post("/webhooks/chatwoot", json=payload)
    assert res.status_code == 200
    assert bridge.publish.await_count == 1


@pytest.mark.asyncio
async def test_webhook_ignores_other_inbox_on_shared_account() -> None:
    # On a shared Chatwoot account, the account webhook fires for ALL inboxes.
    # Events for an inbox other than ours must be ignored — never run our AI on
    # another team's customers.
    settings = Settings(crm_provider="chatwoot", phone_enabled=False, chatwoot_inbox_id=7)
    adapter = ChatwootAdapter(settings)
    orch = MagicMock()
    orch._settings = settings
    orch._ticketing_port = adapter
    orch._chat_port = adapter
    orch.handle_turn = AsyncMock(return_value=MagicMock(reply="should-not-run"))
    bridge = HandoffBridge(store=InMemoryHandoffStore())

    client = _client(orch, bridge, adapter)
    payload: dict[str, Any] = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "hi",
        "conversation": {"id": 1, "inbox_id": 99},  # a different inbox
        "sender": {"id": 1},
    }
    res = client.post("/webhooks/chatwoot", json=payload)
    assert res.status_code == 200
    assert res.json() == {"status": "ignored_other_inbox"}
    orch.handle_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_processes_our_inbox_on_shared_account() -> None:
    # Chatwoot-as-customer-channel mode: the bot replies to incoming messages.
    settings = Settings(
        crm_provider="chatwoot",
        phone_enabled=False,
        chatwoot_inbox_id=7,
        chatwoot_bot_replies_to_incoming=True,
    )
    adapter = ChatwootAdapter(settings)
    orch = MagicMock()
    orch._settings = settings
    orch._ticketing_port = adapter
    orch._chat_port = adapter
    orch.handle_turn = AsyncMock(return_value=MagicMock(reply=None))
    bridge = HandoffBridge(store=InMemoryHandoffStore())

    client = _client(orch, bridge, adapter)
    payload: dict[str, Any] = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "hi",
        "conversation": {"id": 1, "inbox_id": 7},  # our inbox
        "sender": {"id": 1},
    }
    res = client.post("/webhooks/chatwoot", json=payload)
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    orch.handle_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_incoming_does_not_run_bot_by_default() -> None:
    # Handoff-console mode (default): an incoming message must NOT run the bot,
    # otherwise the escalation seed / forwarded customer messages re-escalate in a
    # loop. The agent still reads the message for its own sync.
    settings = Settings(crm_provider="chatwoot", phone_enabled=False, chatwoot_inbox_id=7)
    adapter = ChatwootAdapter(settings)
    orch = MagicMock()
    orch._settings = settings
    orch._chat_port = adapter
    orch.handle_turn = AsyncMock(return_value=MagicMock(reply="should-not-run"))
    bridge = HandoffBridge(store=InMemoryHandoffStore())

    client = _client(orch, bridge, adapter)
    payload: dict[str, Any] = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "My ring broke, I want a human",
        "conversation": {"id": 1, "inbox_id": 7},
        "sender": {"id": 1},
    }
    res = client.post("/webhooks/chatwoot", json=payload)
    assert res.status_code == 200
    assert res.json() == {"status": "ignored_incoming"}
    orch.handle_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversation_resolved_unpauses() -> None:
    settings = Settings(crm_provider="chatwoot", phone_enabled=False)
    adapter = ChatwootAdapter(settings)
    adapter._paused_sessions.add("whatsapp-+60123")
    orch = MagicMock()
    orch._settings = settings
    orch._ticketing_port = adapter
    orch.resume_ai = AsyncMock()
    store = InMemoryHandoffStore()
    bridge = HandoffBridge(store=store)
    # Seed the bridge mapping the handler reads (session_id_for(conv_id)).
    await bridge.register("whatsapp-+60123", "42")

    client = _client(orch, bridge, adapter)
    res = client.post(
        "/webhooks/chatwoot",
        json={"event": "conversation_resolved", "conversation": {"id": 42}, "sender": {"id": 1}},
    )
    assert res.status_code == 200
    # Both pause mechanisms must clear: adapter/web pause AND the ADK WhatsApp
    # handoff state (resume_ai), and the bridge mapping is torn down.
    assert "whatsapp-+60123" not in adapter._paused_sessions
    orch.resume_ai.assert_awaited_once_with("whatsapp-+60123")
    assert await bridge.session_id_for("42") is None


@pytest.mark.asyncio
async def test_flat_resolve_event_resumes_ai() -> None:
    # Chatwoot sends conversation_status_changed / conversation_resolved as a FLAT
    # payload — the conversation fields are at the top level ({event, id, status,
    # inbox_id}), with no nested `conversation` and no `sender`. Previously this
    # 422'd, so the AI never resumed after an agent resolved a conversation.
    settings = Settings(crm_provider="chatwoot", phone_enabled=False)
    adapter = ChatwootAdapter(settings)
    adapter._paused_sessions.add("whatsapp-+60123")
    orch = MagicMock()
    orch._settings = settings
    orch._ticketing_port = adapter
    orch.resume_ai = AsyncMock()
    bridge = HandoffBridge(store=InMemoryHandoffStore())
    await bridge.register("whatsapp-+60123", "42")

    client = _client(orch, bridge, adapter)
    res = client.post(
        "/webhooks/chatwoot",
        json={"event": "conversation_status_changed", "id": 42, "status": "resolved"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "resolved"}
    orch.resume_ai.assert_awaited_once_with("whatsapp-+60123")


@pytest.mark.asyncio
async def test_conversation_reopened_does_not_resume() -> None:
    # conversation_status_changed to a non-resolved status (agent re-opens) must
    # NOT hand control back to the AI.
    settings = Settings(crm_provider="chatwoot", phone_enabled=False)
    adapter = ChatwootAdapter(settings)
    orch = MagicMock()
    orch._settings = settings
    orch._ticketing_port = adapter
    orch.resume_ai = AsyncMock()
    store = InMemoryHandoffStore()
    bridge = HandoffBridge(store=store)
    await bridge.register("whatsapp-+60123", "42")

    client = _client(orch, bridge, adapter)
    res = client.post(
        "/webhooks/chatwoot",
        json={
            "event": "conversation_status_changed",
            "conversation": {"id": 42, "status": "open"},
            "sender": {"id": 1},
        },
    )
    assert res.status_code == 200
    # The transition is now logged to the audit trail (status "logged"), but a
    # re-open to a non-resolved status still must NOT hand control back to the AI.
    assert res.json() == {"status": "logged"}
    orch.resume_ai.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_token_query_param_blocks_without_token() -> None:
    """Without token (or header), a configured secret must return 401."""
    settings = Settings(
        crm_provider="chatwoot", phone_enabled=False, chatwoot_webhook_secret="s3cret"
    )
    adapter = ChatwootAdapter(settings)
    orch = MagicMock()
    orch._settings = settings
    orch._ticketing_port = adapter
    store = InMemoryHandoffStore()
    bridge = HandoffBridge(store=store)

    client = _client(orch, bridge, adapter)
    payload: dict[str, Any] = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "Hello",
        "conversation": {"id": 1},
        "sender": {"id": 1},
    }
    res = client.post("/webhooks/chatwoot", json=payload)
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_auth_enforced_without_human_agent_bridge() -> None:
    # When chatwoot_enabled=False the human_agent_bridge is None, but a configured
    # secret must STILL gate /webhooks/chatwoot — otherwise the incoming-customer
    # branch runs unauthenticated.
    settings = Settings(
        crm_provider="chatwoot", phone_enabled=False, chatwoot_webhook_secret="s3cret"
    )
    orch = MagicMock()
    orch._settings = settings
    app = create_app(settings)
    app.include_router(build_chat_router(orch))  # no bridges wired
    client = TestClient(app)
    payload: dict[str, Any] = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "Hello",
        "conversation": {"id": 1},
        "sender": {"id": 1},
    }
    res = client.post("/webhooks/chatwoot", json=payload)
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_auth_token_query_param_allows_with_token() -> None:
    """With ?token=<secret>, request must be processed (not 401)."""
    settings = Settings(
        crm_provider="chatwoot", phone_enabled=False, chatwoot_webhook_secret="s3cret"
    )
    adapter = ChatwootAdapter(settings)
    orch = MagicMock()
    orch._settings = settings
    orch._ticketing_port = adapter
    orch.handle_turn = AsyncMock(return_value=MagicMock(reply=None))
    store = InMemoryHandoffStore()
    bridge = HandoffBridge(store=store)

    client = _client(orch, bridge, adapter)
    payload: dict[str, Any] = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "Hello",
        "conversation": {"id": 1},
        "sender": {"id": 1},
    }
    res = client.post("/webhooks/chatwoot?token=s3cret", json=payload)
    assert res.status_code == 200
