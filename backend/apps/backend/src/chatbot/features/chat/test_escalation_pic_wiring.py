# features/chat/test_escalation_pic_wiring.py
"""Tests for Task 5: EscalationNotifier wired into ChatwootAdapter (Phase 2).

Covers:
- PIC-resolved Zammad group passed to create_ticket
- PIC team_id used for Chatwoot team assignment
- WA alert sent to PIC's WhatsApp number
- Fallback when no PIC found (group=None, no WA alert)
- ZammadClient.assign_owner: user lookup + PATCH
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.adapters.zammad import ZammadClient, ZammadTicket
from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.models import HandoffOpenPayload, Message
from chatbot.features.chat.pic_registry import build_pic_registry
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "chatwoot_account_id": 1,
        "chatwoot_inbox_id": 7,
        "chatwoot_escalation_label": "ai-escalation",
        "chatwoot_complaint_label": "escalate",
        "zammad_direct_ticketing": True,
        "escalation_email_enabled": False,  # email off so only WA + attr tested here
        "escalation_cc_pic": False,
        "pic_map_json": json.dumps({
            "apps": {
                "pic_name": "Alice Tan",
                "pic_email": "alice@proton.my",
                "pic_whatsapp": "+60123456789",
                "zammad_group": "Apps-Support",
                "chatwoot_team_id": 3,
            }
        }),
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


class _FakeCW:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/conversations"):
            return {"id": 99}
        return {}


class _FakeZammad:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.assign_calls: list[tuple[str, str]] = []
        self.ticket = ZammadTicket(number="42", id="999")

    async def create_ticket(self, title: str, body: str, customer_email: str,
                             priority: str, group: str | None = None) -> ZammadTicket | None:
        self.create_calls.append({"group": group})
        return self.ticket

    async def add_note(self, ticket_id: str, text: str) -> None:
        pass

    async def assign_owner(self, ticket_id: str, owner_email: str) -> None:
        self.assign_calls.append((ticket_id, owner_email))


class _FakeTwilio:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, conversation_id: str, text: str) -> None:
        self.sent.append((conversation_id, text))


async def test_open_handoff_complaint_uses_pic_zammad_group() -> None:
    s = _settings()
    fake_cw = _FakeCW()
    fake_zam = _FakeZammad()
    fake_twilio = _FakeTwilio()

    registry = build_pic_registry(s)
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=None,  # type: ignore[arg-type]  email disabled in settings
        twilio_adapter=fake_twilio,  # type: ignore[arg-type]
        chatwoot_request=fake_cw._request,
    )

    adapter = ChatwootAdapter(
        settings=s,
        zammad=fake_zam,  # type: ignore[arg-type]
        pic_registry=registry,
        escalation_notifier=notifier,
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-1",
        customer_name="Budi",
        customer_email="",
        ai_summary="App crashes on login",
        transcript=(Message(role="user", text="the app crashes"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
    )
    await adapter.open_handoff(payload)

    # Zammad ticket uses the PIC's group
    assert len(fake_zam.create_calls) == 1
    assert fake_zam.create_calls[0]["group"] == "Apps-Support"

    # Chatwoot team assignment uses the PIC's team_id
    team_calls = [pl for _, p, pl in fake_cw.calls
                  if "/assignments" in p and pl and pl.get("team_id") == 3]
    assert len(team_calls) == 1

    # WA alert sent to PIC
    assert len(fake_twilio.sent) == 1
    wa_to, _ = fake_twilio.sent[0]
    assert "+60123456789" in wa_to


async def test_open_handoff_no_pic_falls_back_to_default_group() -> None:
    s = _settings(pic_map_json="")  # empty → no PIC for any dept
    fake_cw = _FakeCW()
    fake_zam = _FakeZammad()

    registry = build_pic_registry(s)
    adapter = ChatwootAdapter(
        settings=s,
        zammad=fake_zam,  # type: ignore[arg-type]
        pic_registry=registry,
        escalation_notifier=None,
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-2",
        customer_name="C",
        customer_email="",
        ai_summary="s",
        transcript=(Message(role="user", text="help"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
    )
    await adapter.open_handoff(payload)

    # Falls back to the settings-configured default group
    assert fake_zam.create_calls[0]["group"] is None  # no override when no PIC


async def test_open_handoff_applies_pic_label_on_escalation() -> None:
    """Fix 2: open_handoff must apply a ``pic_<slug>`` label when a PIC is resolved.

    The label ``pic_alice_tan`` must appear in the labels POST alongside the
    escalation labels so agents can filter/route by PIC in Chatwoot.
    """
    s = _settings()
    fake_cw = _FakeCW()
    fake_zam = _FakeZammad()
    fake_twilio = _FakeTwilio()

    registry = build_pic_registry(s)
    notifier = EscalationNotifier(
        settings=s,
        pic_registry=registry,
        email_sender=None,  # type: ignore[arg-type]  email disabled in settings
        twilio_adapter=fake_twilio,
        chatwoot_request=fake_cw._request,
    )

    adapter = ChatwootAdapter(
        settings=s,
        zammad=fake_zam,  # type: ignore[arg-type]
        pic_registry=registry,
        escalation_notifier=notifier,
    )
    adapter._request = fake_cw._request  # type: ignore[method-assign]

    payload = HandoffOpenPayload(
        session_id="sim-pic-label",
        customer_name="Budi",
        customer_email="",
        ai_summary="App crashes on login",
        transcript=(Message(role="user", text="the app crashes"),),
        urgency="high",
        reason="negative_sentiment",
        department="apps",
    )
    await adapter.open_handoff(payload)

    # Find the labels POST call and assert pic_alice_tan is present.
    label_calls = [
        pl["labels"]
        for _, path, pl in fake_cw.calls
        if "/labels" in path and pl and "labels" in pl
    ]
    assert label_calls, "Expected at least one labels POST"
    all_labels = label_calls[-1]  # the final labels merge call
    assert any(lbl.startswith("pic_") for lbl in all_labels), (
        f"Expected a pic_* label in {all_labels}"
    )
    assert "pic_alice_tan" in all_labels


async def test_zammad_assign_owner_calls_user_lookup_and_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []

    class _FakeResp:
        def __init__(self, body: Any) -> None:
            self._body = body
            self.content = json.dumps(body).encode()
        def raise_for_status(self) -> None: pass
        def json(self) -> Any: return self._body

    class _Client:
        async def __aenter__(self) -> _Client: return self
        async def __aexit__(self, *_: Any) -> bool: return False
        async def request(self, method: str, url: str, **kw: Any) -> _FakeResp:
            calls.append((method, url, kw.get("json")))
            if "users" in url:
                return _FakeResp([{"id": 5, "email": "alice@proton.my"}])
            return _FakeResp({})

    monkeypatch.setattr(
        "chatbot.features.chat.adapters.zammad.httpx.AsyncClient",
        lambda *_a, **_k: _Client(),
    )

    client = ZammadClient(Settings(_env_file=None, zammad_enabled=True,
                                    zammad_api_url="https://zam.example", zammad_api_token="t"))
    await client.assign_owner("999", "alice@proton.my")

    methods = [c[0] for c in calls]
    assert "GET" in methods
    assert "PUT" in methods
    put = next(c for c in calls if c[0] == "PUT")
    assert put[2] == {"owner_id": 5}
