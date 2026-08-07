"""The SLA alert callback receives the conversation's labels."""

from __future__ import annotations

from chatbot.features.chat.sla import _fire


class _Audit:
    def __init__(self) -> None:
        self.entries: list = []

    async def append(self, entry):
        self.entries.append(entry)

    async def list_for_ticket(self, ticket_id):
        return list(self.entries)


async def test_fire_passes_labels_to_the_alert():
    from datetime import UTC, datetime

    seen: list = []

    async def _alert(ticket_id, to_state, remark, labels):
        seen.append((ticket_id, to_state, labels))

    await _fire(
        _Audit(),
        ticket_id="42",
        session_id="s",
        to_state="SLA_BREACH_NO_RESPONSE",
        remark="r",
        clock=datetime.now(UTC),
        alert=_alert,
        labels=["dept_sales", "escalate"],
    )

    assert seen == [("42", "SLA_BREACH_NO_RESPONSE", ["dept_sales", "escalate"])]


async def test_fire_survives_an_alert_that_raises():
    from datetime import UTC, datetime

    async def _alert(ticket_id, to_state, remark, labels):
        raise RuntimeError("twilio down")

    entry = await _fire(
        _Audit(),
        ticket_id="42",
        session_id="s",
        to_state="SLA_BREACH_UNRESOLVED",
        remark="r",
        clock=datetime.now(UTC),
        alert=_alert,
        labels=[],
    )

    assert entry.to_state == "SLA_BREACH_UNRESOLVED"
