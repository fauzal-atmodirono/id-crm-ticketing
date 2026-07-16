from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.platform.config import Settings


class _FakeClient:
    """Records requests and returns canned responses keyed by (method, path substring)."""

    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._responses = responses

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append((method, path, payload))
        for (m, sub), resp in self._responses.items():
            if m == method and sub in path:
                return resp
        return {}


def _adapter(fake: _FakeClient) -> ChatwootAdapter:
    a = ChatwootAdapter(
        Settings(chatwoot_account_id=1, chatwoot_inbox_id=7, chatwoot_agent_team_id=3)
    )
    a._request = fake._request  # type: ignore[method-assign]
    return a


@pytest.mark.asyncio
async def test_send_message_posts_outgoing() -> None:
    fake = _FakeClient({})
    await _adapter(fake).send_message(conversation_id="42", text="hi")
    method, path, payload = fake.calls[-1]
    assert method == "POST"
    assert "/conversations/42/messages" in path
    assert payload == {"content": "hi", "message_type": "outgoing"}


@pytest.mark.asyncio
async def test_create_ticket_creates_prioritizes_labels_assigns() -> None:
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    ticket_id = await _adapter(fake).create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    assert ticket_id == "99"
    paths = [p for _, p, _ in fake.calls]
    assert any(p.endswith("/conversations") for p in paths)  # create POST
    assert any("toggle_priority" in p for p in paths)
    assert any("assignments" in p for p in paths)
    assert any("labels" in p for p in paths)


@pytest.mark.asyncio
async def test_create_ticket_creates_contact_before_conversation() -> None:
    # Chatwoot's Application API POST /conversations needs a contact_inbox: without
    # a contact_id the ConversationBuilder errors. So we must create/find a contact
    # first and pass its id (source_id stays == session_id so the mapping holds).
    fake = _FakeClient(
        {
            ("POST", "/contacts"): {"payload": {"contact": {"id": 55}}},
            ("POST", "/conversations"): {"id": 99},
        }
    )
    adapter = _adapter(fake)
    ticket_id = await adapter.create_ticket(
        session_id="whatsapp-+60123",
        title="Refund",
        body="help",
        urgency="high",
        customer_name="Aina",
        customer_phone="+60123",
    )
    assert ticket_id == "99"

    methods_paths = [(m, p) for m, p, _ in fake.calls]
    contact_idx = next(
        i for i, (m, p) in enumerate(methods_paths) if m == "POST" and p.endswith("/contacts")
    )
    conv_idx = next(
        i for i, (m, p) in enumerate(methods_paths) if m == "POST" and p.endswith("/conversations")
    )
    assert contact_idx < conv_idx, "contact must be created before the conversation"

    conv_payload = next(
        pl for m, p, pl in fake.calls if m == "POST" and p.endswith("/conversations")
    )
    assert conv_payload is not None
    assert conv_payload["contact_id"] == 55
    assert conv_payload["source_id"] == "whatsapp-+60123"
    assert "contact" not in conv_payload  # the old undocumented inline key is gone


@pytest.mark.asyncio
async def test_create_ticket_falls_back_to_contact_search_on_duplicate() -> None:
    # A repeat escalation for the same identifier 422s on create (Chatwoot enforces
    # identifier uniqueness); the adapter recovers by searching for the contact.
    fake = _FakeClient(
        {
            # POST /contacts returns {} (simulated 422 -> _request None-ish path)
            ("GET", "/contacts/search"): {"payload": [{"id": 77, "identifier": "whatsapp-+60123"}]},
            ("POST", "/conversations"): {"id": 42},
        }
    )
    adapter = _adapter(fake)
    ticket_id = await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    assert ticket_id == "42"
    assert any("/contacts/search" in p for _m, p, _pl in fake.calls)
    conv_payload = next(
        pl for m, p, pl in fake.calls if m == "POST" and p.endswith("/conversations")
    )
    assert conv_payload is not None
    assert conv_payload["contact_id"] == 77


@pytest.mark.asyncio
async def test_reuses_existing_active_conversation_after_cache_loss() -> None:
    # After a restart the in-memory cache is empty. If the contact still has an
    # OPEN conversation we must reuse it, not create a duplicate.
    fake = _FakeClient(
        {
            ("POST", "/contacts"): {},  # duplicate identifier -> no id
            ("GET", "/contacts/search"): {"payload": [{"id": 55, "identifier": "whatsapp-+60123"}]},
            ("GET", "/contacts/55/conversations"): {
                "payload": [{"id": 99, "status": "open", "inbox_id": 7}]
            },
        }
    )
    adapter = _adapter(fake)
    conv_id = await adapter._find_or_create_conversation("whatsapp-+60123")
    assert conv_id == "99"
    assert not any(m == "POST" and p.endswith("/conversations") for m, p, _ in fake.calls), (
        "must not create a duplicate conversation"
    )


@pytest.mark.asyncio
async def test_starts_new_conversation_when_prior_is_resolved() -> None:
    # A prior RESOLVED conversation is a closed ticket — the next contact opens a
    # fresh conversation rather than re-escalating into the resolved thread.
    fake = _FakeClient(
        {
            ("POST", "/contacts"): {},
            ("GET", "/contacts/search"): {"payload": [{"id": 55, "identifier": "whatsapp-+60123"}]},
            ("GET", "/contacts/55/conversations"): {
                "payload": [{"id": 99, "status": "resolved", "inbox_id": 7}]
            },
            ("POST", "/conversations"): {"id": 123},
        }
    )
    adapter = _adapter(fake)
    conv_id = await adapter._find_or_create_conversation("whatsapp-+60123")
    assert conv_id == "123"
    assert any(m == "POST" and p.endswith("/conversations") for m, p, _ in fake.calls)


@pytest.mark.asyncio
async def test_contact_search_fallback_url_encodes_session_id() -> None:
    # WhatsApp session ids contain '+' (e.g. whatsapp-+60123); a raw '+' in a
    # query string decodes to a space server-side and breaks the recovery search.
    fake = _FakeClient({("GET", "/contacts/search"): {"payload": []}})
    adapter = _adapter(fake)
    await adapter._find_or_create_contact("whatsapp-+60123")
    search_paths = [p for m, p, _ in fake.calls if m == "GET" and "/contacts/search" in p]
    assert search_paths, "expected a /contacts/search fallback call"
    assert "whatsapp-%2B60123" in search_paths[0]
    assert "+60123" not in search_paths[0]


@pytest.mark.asyncio
async def test_create_ticket_applies_multiple_escalation_labels() -> None:
    # A comma-separated escalation label lets one escalation carry both our marker
    # (ai-escalation) AND the label that triggers the CTO's Zammad sync (escalate).
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation, escalate",
        )
    )
    adapter._request = fake._request  # type: ignore[method-assign]
    await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    labels_payload = next(pl for _m, p, pl in fake.calls if p.endswith("/labels"))
    assert labels_payload == {"labels": ["ai-escalation", "escalate"]}


@pytest.mark.asyncio
async def test_create_ticket_does_not_cache_on_failed_create() -> None:
    # /conversations returns no id (e.g. network/auth failure) -> fallback used,
    # nothing cached so a later attempt can retry.
    fake = _FakeClient({})  # every request returns {} (no "id")
    adapter = _adapter(fake)
    ticket_id = await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    assert ticket_id == "whatsapp-+60123"
    assert adapter._conv_by_session == {}


@pytest.mark.asyncio
async def test_unpause_evicts_conversation_cache() -> None:
    # Resolving (unpause) closes the ticket. The cached conversation is now
    # resolved, so it must be forgotten — the next contact opens a fresh one
    # rather than re-escalating into a resolved (queue-invisible) thread.
    fake = _FakeClient({})
    adapter = _adapter(fake)
    adapter._conv_by_session["whatsapp-+60123"] = "99"
    await adapter.unpause_ai_for_session("whatsapp-+60123")
    assert "whatsapp-+60123" not in adapter._conv_by_session


@pytest.mark.asyncio
async def test_request_sends_dash_and_underscore_token_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Some reverse proxies strip request headers with underscores, so the token
    # must ALSO be sent as `Api-Access-Token` (dashes) — Rails maps both forms to
    # the same value, so the dash header survives the proxy.
    captured: dict[str, Any] = {}

    class _FakeResp:
        content = b'{"ok": true}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"ok": True}

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> bool:
            return False

        async def request(self, method: str, url: str, **kwargs: Any) -> _FakeResp:
            captured["headers"] = kwargs.get("headers")
            return _FakeResp()

    monkeypatch.setattr(
        "chatbot.features.chat.adapters.chatwoot.httpx.AsyncClient",
        lambda *_a, **_k: _FakeClient(),
    )
    adapter = ChatwootAdapter(Settings(chatwoot_api_token="tok123", chatwoot_enabled=True))
    await adapter._request("GET", "/profile")
    assert captured["headers"]["Api-Access-Token"] == "tok123"
    assert captured["headers"]["api_access_token"] == "tok123"


@pytest.mark.asyncio
async def test_contact_created_with_synthesized_email() -> None:
    # Downstream ticketing (Chatwoot->Zammad) keys customers by email; web/WhatsApp
    # contacts have none, so we synthesize a deterministic, format-valid one.
    fake = _FakeClient(
        {
            ("POST", "/contacts"): {"payload": {"contact": {"id": 55}}},
            ("POST", "/conversations"): {"id": 99},
        }
    )
    adapter = _adapter(fake)
    await adapter._find_or_create_conversation("whatsapp-+60123")
    contact_payload = next(pl for m, p, pl in fake.calls if m == "POST" and p.endswith("/contacts"))
    assert contact_payload is not None
    email = contact_payload["email"]
    assert email.endswith("@" + adapter._settings.chatwoot_customer_email_domain)
    assert "+" not in email  # local part sanitized


@pytest.mark.asyncio
async def test_create_ticket_posts_incoming_message_before_labels() -> None:
    # The escalation must leave an INCOMING customer message on the conversation
    # (so a downstream sync can identify the customer), posted before the labels
    # that fire the webhook.
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    await _adapter(fake).create_ticket(
        session_id="s1", title="Broken clasp", body="details", urgency="high"
    )
    calls = fake.calls
    inc = [
        i
        for i, (_m, p, pl) in enumerate(calls)
        if p.endswith("/messages") and (pl or {}).get("message_type") == "incoming"
    ]
    lbl = [i for i, (_m, p, _pl) in enumerate(calls) if p.endswith("/labels")]
    assert inc, "expected an incoming customer message"
    assert inc[0] < lbl[0], "incoming message must precede the label (webhook trigger)"
    inc_payload = calls[inc[0]][2]
    assert inc_payload is not None and inc_payload["content"] == "Broken clasp"


@pytest.mark.asyncio
async def test_add_private_note_sets_private_true() -> None:
    fake = _FakeClient({})
    await _adapter(fake).add_private_note(ticket_id="99", text="internal")
    method, path, payload = fake.calls[-1]
    assert method == "POST"
    assert "/conversations/99/messages" in path
    assert payload == {"content": "internal", "message_type": "outgoing", "private": True}
