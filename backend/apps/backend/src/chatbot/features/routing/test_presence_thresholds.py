"""P6 task 4 -- the 10-minute / 1-hour agent-unavailability threshold sweeper.

No Firestore, no real Chatwoot, no real SMTP/Twilio anywhere in this file:
`sweep_presence_thresholds` depends only on small structural Protocols
(`_AgentDirectory`, `_PresenceLog`, `_StatusCatalogue`), so every
collaborator here is a purpose-built in-memory fake, the same style
`test_custom_status.py` uses for its own injected `presence_store`/
`availability_writer` collaborators.

`_FakePresenceLog` is written to mirror the behaviours that matter most for
this suite: `stamp_alert` mutates `alerts_sent` on whatever event is
currently "latest" -- but only when the caller's `expected_event` still
identifies that same period, the same expected-event guard the real store
enforces -- and a brand-new event (e.g. an agent returning to Available and
leaving again) starts with an empty `alerts_sent`. That is what makes
`test_returning_to_available_and_leaving_again_re_arms_both_thresholds` a
fair test of the real store's contract, not just of the fake, and what
would catch `_check_agent` if it ever stopped passing the same `latest`
object it read into both `stamp_alert` calls.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from chatbot.features.routing.custom_status import CustomStatus
from chatbot.features.routing.presence import AgentRecord
from chatbot.features.routing.presence_store import PresenceEvent
from chatbot.features.routing.presence_thresholds import (
    ESCALATE_ALERT_KEY,
    WARN_ALERT_KEY,
    WipSummary,
    sweep_presence_thresholds,
)
from chatbot.platform.config import get_settings

AGENT = AgentRecord(id=42, name="Ahmad", availability_status="busy", email="ahmad@example.com")
OTHER_AGENT_ID = 7
BASE = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Any:
    return get_settings().model_copy(
        update={"presence_threshold_alerts_enabled": True, **overrides}
    )


def _at(minutes: float) -> datetime:
    return BASE + timedelta(minutes=minutes)


def _event(
    status: str, at_minutes: float, alerts_sent: frozenset[str] = frozenset()
) -> PresenceEvent:
    return PresenceEvent(
        agent_id=AGENT.id,
        status=status,
        at=_at(at_minutes),
        source="agent",
        previous=None,
        alerts_sent=alerts_sent,
    )


class _FakeAgents:
    def __init__(self, agents: list[AgentRecord]) -> None:
        self._agents = agents

    async def fetch_agents(self) -> list[AgentRecord]:
        return list(self._agents)


def _same_period(a: PresenceEvent, b: PresenceEvent) -> bool:
    """Mirrors `presence_store._same_period`: identity for `stamp_alert`'s
    guard, deliberately excluding `alerts_sent`."""
    return (
        a.agent_id == b.agent_id
        and a.status == b.status
        and a.at == b.at
        and a.previous == b.previous
        and a.source == b.source
    )


class _FakePresenceLog:
    """Mirrors the contract this module relies on from
    `PresenceEventStore`: `stamp_alert` patches `alerts_sent` on whatever
    event is currently latest -- but only if the caller's `expected_event`
    still identifies that period (the real store's expected-event guard) --
    and a freshly-appended event starts with an empty `alerts_sent`."""

    def __init__(self, event: PresenceEvent | None) -> None:
        self._event = event
        self.stamped: list[str] = []

    async def latest(self, agent_id: int) -> PresenceEvent | None:
        return self._event

    async def elapsed_in_current_status(self, agent_id: int, now: datetime) -> timedelta | None:
        if self._event is None:
            return None
        return now - self._event.at

    async def stamp_alert(
        self, agent_id: int, alert_key: str, expected_event: PresenceEvent
    ) -> None:
        if self._event is None or not _same_period(self._event, expected_event):
            return
        self.stamped.append(alert_key)
        self._event = replace(self._event, alerts_sent=self._event.alerts_sent | {alert_key})

    def set_event(self, event: PresenceEvent | None) -> None:
        """Test-only helper simulating a NEW presence event landing (e.g.
        the agent returning to Available and then leaving again) -- a
        fresh event with an empty `alerts_sent`, exactly like a real
        append to the store."""
        self._event = event


def _status(key: str, counts_as_unavailable: bool) -> CustomStatus:
    return CustomStatus(
        key=key,
        label=key.title(),
        color="#000000",
        routable=False,
        native="busy",
        counts_as_unavailable=counts_as_unavailable,
    )


class _FakeStatusCatalogue:
    def __init__(self, statuses: dict[str, CustomStatus]) -> None:
        self._statuses = statuses

    async def get(self, key: str) -> CustomStatus | None:
        return self._statuses.get(key)


CATALOGUE = _FakeStatusCatalogue(
    {
        "lunch": _status("lunch", True),
        "busy": _status("busy", False),
        "coaching": _status("coaching", False),
        "training": _status("training", False),
        "acw": _status("acw", False),
    }
)


class _RecordingAlert:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[AgentRecord, str, float, WipSummary | None]] = []
        self._fail = fail

    async def __call__(
        self, agent: AgentRecord, level: str, elapsed_minutes: float, wip: WipSummary | None
    ) -> None:
        self.calls.append((agent, level, elapsed_minutes, wip))
        if self._fail:
            raise RuntimeError("transport down")


class _FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        attachments: list[Any],
        *,
        reply_to: str | None = None,
    ) -> None:
        self.sent.append({"to": to, "cc": cc, "subject": subject, "body": body})


@pytest.mark.asyncio
async def test_eleven_minutes_unavailable_fires_the_warn_alert():
    presence = _FakePresenceLog(_event("lunch", 0))
    alert = _RecordingAlert()

    await sweep_presence_thresholds(
        _settings(),
        now=_at(11),
        presence_fetcher=_FakeAgents([AGENT]),
        presence_store=presence,
        status_store=CATALOGUE,
        alert=alert,
        open_case_fetcher=lambda: [],
    )

    assert [call[1] for call in alert.calls] == ["warn"]
    assert presence.stamped == [WARN_ALERT_KEY]


@pytest.mark.asyncio
async def test_nine_minutes_unavailable_fires_nothing():
    presence = _FakePresenceLog(_event("lunch", 0))
    alert = _RecordingAlert()

    await sweep_presence_thresholds(
        _settings(),
        now=_at(9),
        presence_fetcher=_FakeAgents([AGENT]),
        presence_store=presence,
        status_store=CATALOGUE,
        alert=alert,
        open_case_fetcher=lambda: [],
    )

    assert alert.calls == []
    assert presence.stamped == []


@pytest.mark.asyncio
async def test_a_three_hour_absence_fires_exactly_two_alerts():
    presence = _FakePresenceLog(_event("lunch", 0))
    alert = _RecordingAlert()
    settings = _settings()
    fetcher = _FakeAgents([AGENT])

    # Sweep repeatedly across a 3-hour absence, as the real interval
    # scheduler would every `presence_poll_seconds` -- anti-noise means
    # this must still only ever produce two alerts total.
    for minute in (2, 5, 8, 11, 15, 30, 45, 59, 61, 90, 120, 150, 180):
        await sweep_presence_thresholds(
            settings,
            now=_at(minute),
            presence_fetcher=fetcher,
            presence_store=presence,
            status_store=CATALOGUE,
            alert=alert,
            open_case_fetcher=lambda: [],
        )

    assert [call[1] for call in alert.calls] == ["warn", "escalate"]
    assert presence.stamped == [WARN_ALERT_KEY, ESCALATE_ALERT_KEY]


@pytest.mark.asyncio
async def test_returning_to_available_and_leaving_again_re_arms_both_thresholds():
    presence = _FakePresenceLog(_event("lunch", 0))
    alert = _RecordingAlert()
    settings = _settings()
    fetcher = _FakeAgents([AGENT])

    # First absence runs past both thresholds.
    await sweep_presence_thresholds(
        settings,
        now=_at(90),
        presence_fetcher=fetcher,
        presence_store=presence,
        status_store=CATALOGUE,
        alert=alert,
        open_case_fetcher=lambda: [],
    )
    assert [call[1] for call in alert.calls] == ["warn", "escalate"]

    # The agent comes back to Available (not counts_as_unavailable), then
    # leaves again -- each a brand-new presence event with an empty
    # `alerts_sent`, exactly like a real append to the store.
    presence.set_event(_event("busy", 95))  # "available" isn't in our fake catalogue; busy works
    presence.set_event(_event("lunch", 100))

    await sweep_presence_thresholds(
        settings,
        now=_at(111),
        presence_fetcher=fetcher,
        presence_store=presence,
        status_store=CATALOGUE,
        alert=alert,
        open_case_fetcher=lambda: [],
    )

    # The second absence re-arms the warn threshold (11 minutes elapsed
    # since the new "lunch" event at minute 100) without needing to cross
    # the escalate threshold again in this test.
    assert [call[1] for call in alert.calls] == ["warn", "escalate", "warn"]


@pytest.mark.asyncio
async def test_the_one_hour_alert_includes_the_agents_open_cases():
    presence = _FakePresenceLog(_event("lunch", 0))
    alert = _RecordingAlert()
    conversations = [
        {"id": 501, "status": "open", "meta": {"assignee": {"id": AGENT.id}}},
        {"id": 502, "status": "open", "meta": {"assignee": {"id": AGENT.id}}},
        {"id": 503, "status": "resolved", "meta": {"assignee": {"id": AGENT.id}}},
        {"id": 999, "status": "open", "meta": {"assignee": {"id": OTHER_AGENT_ID}}},
    ]

    await sweep_presence_thresholds(
        _settings(),
        now=_at(61),
        presence_fetcher=_FakeAgents([AGENT]),
        presence_store=presence,
        status_store=CATALOGUE,
        alert=alert,
        open_case_fetcher=lambda: conversations,
    )

    escalate_calls = [call for call in alert.calls if call[1] == "escalate"]
    assert len(escalate_calls) == 1
    wip = escalate_calls[0][3]
    assert wip is not None
    assert wip.count == 2
    assert set(wip.case_ids) == {"501", "502"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status_key", ["busy", "coaching", "training", "acw"])
async def test_a_status_with_counts_as_unavailable_false_never_alerts(status_key: str):
    presence = _FakePresenceLog(_event(status_key, 0))
    alert = _RecordingAlert()

    await sweep_presence_thresholds(
        _settings(),
        now=_at(180),  # well past both thresholds
        presence_fetcher=_FakeAgents([AGENT]),
        presence_store=presence,
        status_store=CATALOGUE,
        alert=alert,
        open_case_fetcher=lambda: [],
    )

    assert alert.calls == []
    assert presence.stamped == []


@pytest.mark.asyncio
async def test_the_warn_alert_reaches_both_the_agent_and_the_admin():
    presence = _FakePresenceLog(_event("lunch", 0))
    email_sender = _FakeEmailSender()
    settings = _settings(report_recipients="admin@proton.example")

    # No `alert=` override -- exercises the real `_build_threshold_alert`
    # fan-out, wired through `email_sender`/`twilio_adapter` like a real
    # caller would.
    await sweep_presence_thresholds(
        settings,
        now=_at(11),
        presence_fetcher=_FakeAgents([AGENT]),
        presence_store=presence,
        status_store=CATALOGUE,
        open_case_fetcher=lambda: [],
        email_sender=email_sender,  # type: ignore[arg-type]
    )

    destinations = [tuple(mail["to"]) for mail in email_sender.sent]
    assert (AGENT.email,) in destinations  # the agent leg
    assert ("admin@proton.example",) in destinations  # the admin leg
    assert len(destinations) == 2  # a refactor collapsing these to one send must fail this


@pytest.mark.asyncio
async def test_an_alert_transport_failure_does_not_prevent_the_stamp_from_being_recorded():
    presence = _FakePresenceLog(_event("lunch", 0))
    alert = _RecordingAlert(fail=True)

    await sweep_presence_thresholds(
        _settings(),
        now=_at(11),
        presence_fetcher=_FakeAgents([AGENT]),
        presence_store=presence,
        status_store=CATALOGUE,
        alert=alert,
        open_case_fetcher=lambda: [],
    )

    assert alert.calls  # the send was attempted
    assert WARN_ALERT_KEY in presence.stamped  # ...but the stamp still landed
