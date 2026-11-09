"""Brief 3 item 2/3: one-click dealer escalation on every channel.

The agent adds `escalate`; the agent service's maybe_escalate (with
ESCALATION_ALL_CHANNELS_ENABLED) calls /escalation/notify with the channel
type and the dealer_<slug> label if there is one. With HANDOFF_NOTE_ENABLED:
- no dealer label -> the dealer the customer confirmed (caller_dealer) is
  used, and dealer_<slug> is applied at that moment (stamp first, so the
  label's own conversation_updated echo is not a second escalation);
- a phone call (API inbox) gets no written customer ack; WhatsApp does.
Flag off: unchanged (pinned by test_escalation_router.py).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.platform.config import Settings

_H = {"x-api-key": "k"}


def _client(conv: dict[str, Any] | None, *, on: bool = True, stamp_sticks: bool = True):
    notifier = AsyncMock()
    calls: list[tuple[str, Any, Any]] = []

    async def cw(method: str, path: str, payload: Any = None) -> dict | None:  # noqa: ARG001
        return conv

    async def merge(ticket_id: str, attrs: dict[str, Any]) -> None:
        calls.append(("attrs", ticket_id, attrs))
        if conv is not None and stamp_sticks:
            conv.setdefault("custom_attributes", {}).update(attrs)

    async def add_label(ticket_id: str, tag: str) -> None:
        calls.append(("label", ticket_id, tag))

    settings = Settings(_env_file=None, proton_backend_key="k", handoff_note_enabled=on)
    app = FastAPI()
    app.include_router(
        build_escalation_router(notifier, cw, settings, merge_attributes=merge, add_label=add_label)
    )
    return TestClient(app), notifier, calls


def _phone_conv(**attrs: Any) -> dict[str, Any]:
    return {
        "custom_attributes": {"external_id": "phone-CA1", **attrs},
        "meta": {"sender": {"phone_number": "+60000000101"}},
    }


def _notify(client: TestClient, **over: Any):
    body = {"conversation_id": "42", "title": "t", "body": "b", "channel_type": "Channel::Api"}
    body.update(over)
    return client.post("/escalation/notify", headers=_H, json=body)


def test_phone_escalation_reaches_the_dealer_with_no_customer_ack() -> None:
    client, notifier, calls = _client(_phone_conv(caller_dealer="petaling_jaya"))
    assert _notify(client).status_code == 200
    kw = notifier.notify_escalation.await_args.kwargs
    assert kw["dealer"] == "petaling_jaya"
    assert kw["ack_transport"] == "none"
    # stamp first, then the label (its echo then sees the stamp)
    assert calls[0][0] == "attrs" and "escalation_notified_at" in calls[0][2]
    assert calls[1] == ("label", "42", "dealer_petaling_jaya")


def test_whatsapp_escalation_reaches_the_dealer_and_acks_in_the_thread() -> None:
    conv = {"custom_attributes": {"caller_dealer": "caroline_motor"}, "meta": {}}
    client, notifier, calls = _client(conv)
    _notify(client, channel_type="Channel::Whatsapp")
    kw = notifier.notify_escalation.await_args.kwargs
    assert kw["dealer"] == "caroline_motor"
    assert kw["ack_transport"] == "conversation"
    assert ("label", "42", "dealer_caroline_motor") in calls


def test_an_existing_dealer_label_wins_and_nothing_is_relabelled() -> None:
    client, notifier, calls = _client(_phone_conv(caller_dealer="petaling_jaya"))
    _notify(client, dealer="komang_motor")
    assert notifier.notify_escalation.await_args.kwargs["dealer"] == "komang_motor"
    assert calls == []


def test_no_confirmed_dealer_means_no_dealer_leg() -> None:
    # caller_dealer_text alone (unmatched) is never used.
    client, notifier, calls = _client(_phone_conv(caller_dealer_text="Shah Alam"))
    _notify(client)
    assert notifier.notify_escalation.await_args.kwargs["dealer"] is None
    assert calls == []


def test_non_phone_api_conversation_still_acks_in_the_thread() -> None:
    client, notifier, _ = _client({"custom_attributes": {"external_id": "crm-9"}})
    _notify(client)
    assert notifier.notify_escalation.await_args.kwargs["ack_transport"] == "conversation"


def test_flag_off_is_unchanged() -> None:
    client, notifier, calls = _client(_phone_conv(caller_dealer="petaling_jaya"), on=False)
    _notify(client)
    kw = notifier.notify_escalation.await_args.kwargs
    assert kw["dealer"] is None
    assert kw["ack_transport"] == "conversation"
    assert calls == []


def test_conversation_read_failure_falls_back_to_today() -> None:
    client, notifier, calls = _client(None)
    _notify(client)
    kw = notifier.notify_escalation.await_args.kwargs
    assert kw["dealer"] is None and kw["ack_transport"] == "conversation"
    assert calls == []


# --- fix round 1 ---------------------------------------------------------------


def test_a_stamp_that_did_not_stick_adds_no_dealer_label() -> None:
    """I1: the merge writer swallows Chatwoot errors. If the stamp is not
    there on re-read, adding dealer_<slug> would let the agent's echo
    escalate a second time -- so no label (the email already went)."""
    client, notifier, calls = _client(
        _phone_conv(caller_dealer="petaling_jaya"), stamp_sticks=False
    )
    _notify(client)
    assert notifier.notify_escalation.await_args.kwargs["dealer"] == "petaling_jaya"
    assert not any(c[0] == "label" for c in calls)


def test_a_department_escalation_never_reaches_the_dealer() -> None:
    """I3 ruling: escalate + dept_<x> is an internal escalation."""
    client, notifier, calls = _client(_phone_conv(caller_dealer="petaling_jaya"))
    _notify(client, department="aftersales")
    assert notifier.notify_escalation.await_args.kwargs["dealer"] is None
    assert calls == []
