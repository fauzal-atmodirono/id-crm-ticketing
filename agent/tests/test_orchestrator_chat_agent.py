"""Tests for the chat-agent brain-swap path (`chat_agent_enabled=True`): the
orchestrator routes the decision through the backend `/chat/turn` ADK agent
instead of the local `gemini.decide` router, and maps the structured result
(reply / handoff / forwarded_to_agent / failure) onto Chatwoot.

Proton client is injected the same way as test_orchestrator_proton.py: a real
httpx client against the respx-mocked PROTON base URL, patched into the
orchestrator module namespace.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.ai import gemini
from app.clients.proton import ProtonConfigClient
from app.config import get_settings
from app.services import orchestrator

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"

CONVERSATION_RESPONSE = {"id": 42, "inbox_id": 7, "status": "pending"}
MESSAGES_RESPONSE = {
    "payload": [
        {
            "id": 1,
            "content": "ada detail spesification?",
            "message_type": 0,
            "private": False,
            "created_at": 1_700_000_000,
            "sender": {"id": 55, "name": "Alice", "email": "alice@example.com"},
        }
    ]
}
INBOXES = {
    "auto": {"inboxes": [{"inbox_id": 7, "name": "i", "mode": "auto", "source": "manual"}]},
    "suggest": {"inboxes": [{"inbox_id": 7, "name": "i", "mode": "suggest", "source": "manual"}]},
}
SETTINGS_RESPONSE = {"settings": {"debounce_seconds": {"value": 0.0, "source": "t"}}}


def _payload(conversation_id=42, status="pending", sender_type="contact"):
    return {
        "event": "message_created",
        "id": 501,
        "content": "ada detail spesification?",
        "message_type": "incoming",
        "private": False,
        "conversation": {"id": conversation_id, "status": status},
        "sender": {"id": 55, "name": "Alice", "email": "alice@example.com", "type": sender_type},
    }


def _make_proton_client() -> ProtonConfigClient:
    inner = httpx.AsyncClient(base_url=PROTON, headers={"x-api-key": "testkey"})
    return ProtonConfigClient(base_url=PROTON, api_key="testkey", client=inner, ttl=0.0)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(orchestrator, "DEBOUNCE_SECONDS", 0)
    orchestrator._pending_tasks.clear()
    monkeypatch.setattr(get_settings(), "chat_agent_enabled", True)
    # gemini.decide must NEVER be called on this path — trip a failure if it is.
    async def _boom_decide(*args, **kwargs):
        raise AssertionError("gemini.decide must not run when chat_agent_enabled")

    monkeypatch.setattr(gemini, "decide", _boom_decide)
    yield
    orchestrator._pending_tasks.clear()


def _mock_common_routes(mode_key: str = "auto", channel: str = "Channel::TwilioSms"):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    # Channel resolution for outbound formatting (WhatsApp vs raw).
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "channel_type": channel})
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES[mode_key])
    )
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(200, json=SETTINGS_RESPONSE)
    )


async def _run(monkeypatch):
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)
    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task
    await client.aclose()


@respx.mock
async def test_chat_agent_auto_posts_kb_reply_and_stays_pending(monkeypatch):
    _mock_common_routes("auto")
    chat_turn = respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(
            200,
            json={
                "reply": "The Proton X70 comes with a 1.5L TGDi engine...",
                "handoff": None,
                "products": [],
                "forwarded_to_agent": False,
            },
        )
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status")

    await _run(monkeypatch)

    assert chat_turn.called
    sent = chat_turn.calls.last.request.content
    assert b"crm-42" in sent  # session id maps to the conversation
    assert b"spesification" in sent  # the customer's actual question is sent
    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"1.5L TGDi engine" in body
    assert b'"private": false' in body or b'"private":false' in body
    assert not toggle_status.called  # auto stays pending, no human handoff


@respx.mock
async def test_chat_agent_auto_splits_long_reply_for_whatsapp(monkeypatch):
    # A reply longer than the Twilio WhatsApp limit must be split into multiple
    # in-limit messages, or Twilio rejects the whole thing (status=failed) and the
    # customer gets nothing.
    import json

    from app.services import whatsapp_format

    _mock_common_routes("auto", channel="Channel::TwilioSms")
    long_reply = ("Spesifikasi Proton S70 sangat lengkap. " * 80).strip()  # ~3100 chars
    respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(
            200,
            json={
                "reply": long_reply,
                "handoff": None,
                "products": [],
                "forwarded_to_agent": False,
            },
        )
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))

    await _run(monkeypatch)

    assert create_message.call_count >= 2
    for call in create_message.calls:
        body = json.loads(call.request.content)
        assert len(body["content"]) <= whatsapp_format.WHATSAPP_BODY_LIMIT


@respx.mock
async def test_chat_agent_whatsapp_converts_markdown(monkeypatch):
    # On a WhatsApp inbox the Markdown reply is converted to WhatsApp-native
    # formatting so the customer doesn't see literal ** and - .
    import json

    _mock_common_routes("auto", channel="Channel::TwilioSms")
    respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(
            200,
            json={
                "reply": "**Proton S70**\n- Cepat\n- Cekap",
                "handoff": None,
                "products": [],
                "forwarded_to_agent": False,
            },
        )
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))

    await _run(monkeypatch)

    content = json.loads(create_message.calls.last.request.content)["content"]
    assert "*Proton S70*" in content and "**" not in content
    assert "• Cepat" in content


@respx.mock
async def test_chat_agent_non_whatsapp_keeps_raw_markdown(monkeypatch):
    # A non-WhatsApp channel (web widget) keeps the raw Markdown reply intact —
    # the frontend renders it; no WhatsApp conversion/splitting.
    _mock_common_routes("auto", channel="Channel::WebWidget")
    respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(
            200,
            json={
                "reply": "**Proton S70**",
                "handoff": None,
                "products": [],
                "forwarded_to_agent": False,
            },
        )
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))

    await _run(monkeypatch)

    assert b"**Proton S70**" in create_message.calls.last.request.content


@respx.mock
async def test_chat_agent_handoff_signal_acks_and_reopens(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "handoff_default_message", "Connecting you to a human agent.")
    _mock_common_routes("auto")
    respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(
            200,
            json={
                "reply": None,
                "handoff": {"reason": "help_request", "language": "en"},
                "products": [],
                "forwarded_to_agent": False,
            },
        )
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    await _run(monkeypatch)

    assert create_message.call_count == 1
    assert b"Connecting you to a human agent." in create_message.calls.last.request.content
    assert toggle_status.call_count == 1  # reopened for a human


@respx.mock
async def test_chat_agent_backend_error_fails_open_to_handoff(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "handoff_default_message", "One moment please.")
    _mock_common_routes("auto")
    respx.post(f"{PROTON}/chat/turn").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    await _run(monkeypatch)

    # Never silent: a backend failure degrades to a Chatwoot handoff.
    assert create_message.call_count == 1
    assert b"One moment please." in create_message.calls.last.request.content
    assert toggle_status.call_count == 1


@respx.mock
async def test_chat_agent_forwarded_to_agent_is_noop(monkeypatch):
    _mock_common_routes("auto")
    respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(
            200,
            json={"reply": None, "handoff": None, "products": [], "forwarded_to_agent": True},
        )
    )
    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages")
    toggle_status = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status")

    await _run(monkeypatch)

    # Already handed off — the human owns it; the bot must stay silent.
    assert not create_message.called
    assert not toggle_status.called


@respx.mock
async def test_chat_agent_suggest_mode_posts_private_note_and_reopens(monkeypatch):
    _mock_common_routes("suggest")
    respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(
            200,
            json={
                "reply": "Suggested KB answer.",
                "handoff": None,
                "products": [],
                "forwarded_to_agent": False,
            },
        )
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    await _run(monkeypatch)

    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"Suggested reply" in body
    assert b"Suggested KB answer." in body
    assert b'"private": true' in body or b'"private":true' in body
    assert toggle_status.call_count == 1  # suggest reopens for the human
