from __future__ import annotations

import json
from typing import Any

import pytest

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.adapters.zammad import ZammadClient, ZammadTicket
from chatbot.features.chat.models import HandoffOpenPayload, Message
from chatbot.platform.config import Settings


class _FakeChatwoot:
    """Records Chatwoot _request calls; returns a conversation id on create."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/conversations"):
            return {"id": 99}
        return {}


_DEFAULT_TICKET = ZammadTicket(number="12034", id="777")


class _FakeZammad:
    """Fake ZammadClient recording create_ticket / add_note calls."""

    def __init__(self, ticket: ZammadTicket | None = _DEFAULT_TICKET) -> None:
        self.ticket = ticket
        self.create_calls: list[dict[str, Any]] = []
        self.note_calls: list[tuple[str, str]] = []

    async def create_ticket(
        self,
        title: str,
        body: str,
        customer_email: str,
        priority: str,
        group: str | None = None,
    ) -> ZammadTicket | None:
        self.create_calls.append(
            {
                "title": title,
                "body": body,
                "customer_email": customer_email,
                "priority": priority,
                "group": group,
            }
        )
        return self.ticket

    async def add_note(self, ticket_id: str, text: str) -> None:
        self.note_calls.append((ticket_id, text))


def _labels(calls: list[tuple[str, str, dict[str, Any] | None]]) -> list[list[str]]:
    return [pl["labels"] for _m, p, pl in calls if p.endswith("/labels") and pl]


def _private_notes(calls: list[tuple[str, str, dict[str, Any] | None]]) -> list[str]:
    return [
        str(pl.get("content"))
        for _m, p, pl in calls
        if p.endswith("/messages") and pl and pl.get("private") is True
    ]


def _complaint_payload(reason: str = "negative_sentiment") -> HandoffOpenPayload:
    return HandoffOpenPayload(
        session_id="sim-1",
        customer_name="Aina",
        customer_email="",
        ai_summary="Customer is furious about a defect.",
        transcript=(Message(role="user", text="my battery is dead"),),
        urgency="high",
        reason=reason,
    )


# --- ChatwootAdapter direct-ticketing branch ---


@pytest.mark.asyncio
async def test_complaint_direct_ticketing_creates_zammad_and_posts_note() -> None:
    fake_cw = _FakeChatwoot()
    fake_zam = _FakeZammad()
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
            zammad_direct_ticketing=True,
        ),
        zammad=fake_zam,  # type: ignore[arg-type]
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    await adapter.open_handoff(_complaint_payload())

    # Zammad ticket created exactly once with the synthesized email + high priority.
    assert len(fake_zam.create_calls) == 1
    call = fake_zam.create_calls[0]
    assert call["priority"] == "high"
    assert call["customer_email"] == adapter._synth_customer_email("sim-1")
    assert call["title"] == "Customer is furious about a defect."

    # A Chatwoot private note links to the Zammad ticket number.
    notes = _private_notes(fake_cw.calls)
    assert any("Zammad ticket #12034" in n for n in notes)
    assert any("/#ticket/zoom/777" in n for n in notes)

    # The `escalate` complaint label is NOT applied — only ai-escalation + dims.
    labels = _labels(fake_cw.calls)
    assert len(labels) == 1
    assert labels[0] == ["ai-escalation"]
    assert "escalate" not in labels[0]


@pytest.mark.asyncio
async def test_non_complaint_direct_ticketing_no_zammad_no_escalate() -> None:
    fake_cw = _FakeChatwoot()
    fake_zam = _FakeZammad()
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
            zammad_direct_ticketing=True,
        ),
        zammad=fake_zam,  # type: ignore[arg-type]
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-2",
        customer_name="Aina",
        customer_email="",
        ai_summary="Customer wants a human.",
        transcript=(Message(role="user", text="connect me with a human"),),
        urgency="medium",
        reason="help_request",
    )
    await adapter.open_handoff(payload)

    assert fake_zam.create_calls == []
    labels = _labels(fake_cw.calls)
    assert labels == [["ai-escalation"]]
    assert not _private_notes(fake_cw.calls) or all(
        "Zammad ticket" not in n for n in _private_notes(fake_cw.calls)
    )


@pytest.mark.asyncio
async def test_complaint_legacy_mode_applies_escalate_label_no_direct_call() -> None:
    fake_cw = _FakeChatwoot()
    fake_zam = _FakeZammad()
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
            zammad_direct_ticketing=False,  # legacy: label -> agent-service sync
        ),
        zammad=fake_zam,  # type: ignore[arg-type]
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    await adapter.open_handoff(_complaint_payload())

    assert fake_zam.create_calls == [], "legacy mode must NOT call Zammad directly"
    labels = _labels(fake_cw.calls)
    assert labels == [["ai-escalation", "escalate"]]
    assert all("Zammad ticket" not in n for n in _private_notes(fake_cw.calls))


@pytest.mark.asyncio
async def test_create_ticket_complaint_direct_ticketing_creates_zammad() -> None:
    fake_cw = _FakeChatwoot()
    fake_zam = _FakeZammad()
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
            zammad_direct_ticketing=True,
        ),
        zammad=fake_zam,  # type: ignore[arg-type]
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )

    assert len(fake_zam.create_calls) == 1
    assert any("Zammad ticket #12034" in n for n in _private_notes(fake_cw.calls))
    labels = _labels(fake_cw.calls)
    assert labels == [["ai-escalation"]], "escalate label suppressed in direct mode"


# --- ZammadClient unit tests (mock httpx) ---


class _FakeResp:
    def __init__(self, body: dict[str, Any]) -> None:
        self.content = json.dumps(body).encode()
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _mock_httpx(monkeypatch: pytest.MonkeyPatch, resp: _FakeResp | Exception) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: Any) -> bool:
            return False

        async def request(self, method: str, url: str, **kwargs: Any) -> _FakeResp:
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            captured["headers"] = kwargs.get("headers")
            if isinstance(resp, Exception):
                raise resp
            return resp

    monkeypatch.setattr(
        "chatbot.features.chat.adapters.zammad.httpx.AsyncClient",
        lambda *_a, **_k: _Client(),
    )
    return captured


@pytest.mark.asyncio
async def test_zammad_create_ticket_payload_and_returns_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _mock_httpx(monkeypatch, _FakeResp({"id": 777, "number": "12034"}))
    client = ZammadClient(
        Settings(
            zammad_enabled=True,
            zammad_api_url="https://zammad.example",
            zammad_api_token="tok",
            zammad_group="Users",
        )
    )
    ticket = await client.create_ticket(
        title="Battery fault",
        body="summary + transcript",
        customer_email="sim1@proton-demo.my",
        priority="high",
    )
    assert ticket == ZammadTicket(number="12034", id="777")
    assert captured["method"] == "POST"
    assert captured["url"] == "https://zammad.example/api/v1/tickets"
    assert captured["headers"]["Authorization"] == "Token token=tok"
    body = captured["json"]
    assert body["group"] == "Users"
    assert body["customer_id"] == "guess:sim1@proton-demo.my"
    assert body["priority_id"] == 3  # high
    assert body["state"] == "new"
    assert body["article"]["body"] == "summary + transcript"
    assert body["article"]["internal"] is False


@pytest.mark.asyncio
async def test_zammad_create_ticket_priority_map(monkeypatch: pytest.MonkeyPatch) -> None:
    for prio, expected in [("low", 1), ("medium", 2), ("high", 3), ("urgent", 3), ("weird", 2)]:
        captured = _mock_httpx(monkeypatch, _FakeResp({"id": 1, "number": "1"}))
        client = ZammadClient(Settings(zammad_enabled=True, zammad_api_token="t"))
        await client.create_ticket(title="t", body="b", customer_email="c@x.my", priority=prio)
        assert captured["json"]["priority_id"] == expected, prio


@pytest.mark.asyncio
async def test_zammad_create_ticket_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_httpx(monkeypatch, RuntimeError("boom"))
    client = ZammadClient(Settings(zammad_enabled=True, zammad_api_token="t"))
    ticket = await client.create_ticket(
        title="t", body="b", customer_email="c@x.my", priority="high"
    )
    assert ticket is None


@pytest.mark.asyncio
async def test_zammad_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _mock_httpx(monkeypatch, _FakeResp({"id": 1, "number": "1"}))
    client = ZammadClient(Settings(zammad_enabled=False, zammad_api_token="t"))
    ticket = await client.create_ticket(
        title="t", body="b", customer_email="c@x.my", priority="high"
    )
    assert ticket is None
    assert captured == {}, "no HTTP request when disabled"


@pytest.mark.asyncio
async def test_zammad_add_note_posts_internal_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _mock_httpx(monkeypatch, _FakeResp({"id": 9}))
    client = ZammadClient(Settings(zammad_enabled=True, zammad_api_token="t"))
    await client.add_note("777", "internal text")
    assert captured["url"].endswith("/api/v1/ticket_articles")
    assert captured["json"]["ticket_id"] == 777
    assert captured["json"]["internal"] is True


@pytest.mark.asyncio
async def test_zammad_create_ticket_missing_ids_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_httpx(monkeypatch, _FakeResp({"id": 777}))  # no "number"
    client = ZammadClient(Settings(zammad_enabled=True, zammad_api_token="t"))
    ticket = await client.create_ticket(
        title="t", body="b", customer_email="c@x.my", priority="high"
    )
    assert ticket is None
