"""P6 task 1 -- the presence-event store.

Two behaviours here decide whether every later P6 task (named statuses,
durations, threshold alerts, the workforce dashboard, ACW, fair-share
assignment) can trust what this store hands back:

`test_an_agent_with_no_events_returns_none_not_a_zero_duration`: "no events"
and "zero seconds in status" are different claims -- a dashboard that shows
a brand-new agent as "0 min in Available" is asserting a transition that
never happened.

`test_the_store_is_append_only_and_exposes_no_update_method`: if an
`update`/`set_status`-style mutator existed, someone would eventually use
it and the event log would stop being trustworthy as history.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from chatbot.features.routing.presence_store import (
    PresenceEvent,
    PresenceEventStore,
)
from chatbot.platform.config import get_settings


class _FakeDocRef:
    def __init__(self, collection_store: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._collection_store = collection_store
        self._doc_id = doc_id

    def update(self, fields: dict[str, Any]) -> None:
        self._collection_store[self._doc_id].update(fields)


class _FakeDocSnapshot:
    def __init__(
        self, doc_id: str, data: dict[str, Any], collection_store: dict[str, dict[str, Any]]
    ) -> None:
        self.id = doc_id
        self._data = data
        self._collection_store = collection_store

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    @property
    def reference(self) -> _FakeDocRef:
        return _FakeDocRef(self._collection_store, self.id)


class _FakeQuery:
    """Enough of `firestore.Query` to support `.where(...).stream()`."""

    def __init__(
        self, collection_store: dict[str, dict[str, Any]], docs: list[tuple[str, dict[str, Any]]]
    ) -> None:
        self._collection_store = collection_store
        self._docs = docs

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        assert op == "==", "the store only ever issues equality filters"
        filtered = [(doc_id, data) for doc_id, data in self._docs if data.get(field) == value]
        return _FakeQuery(self._collection_store, filtered)

    def stream(self) -> list[_FakeDocSnapshot]:
        return [
            _FakeDocSnapshot(doc_id, data, self._collection_store) for doc_id, data in self._docs
        ]


class _FakeCollection:
    """Wraps the shared per-collection dict. A fresh `_FakeCollection` is
    built on every `_client().collection(...)` call (mirroring how the real
    store builds a fresh `firestore.Client()` per operation), so doc ids
    must be unique across instances, not per instance -- real Firestore
    assigns globally unique ids the same way regardless of client identity.
    """

    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def add(self, data: dict[str, Any]) -> tuple[None, _FakeDocRef]:
        doc_id = uuid.uuid4().hex
        self._store[doc_id] = dict(data)
        return None, _FakeDocRef(self._store, doc_id)

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        query = _FakeQuery(self._store, list(self._store.items()))
        return query.where(field, op, value)

    def stream(self) -> list[_FakeDocSnapshot]:
        return [_FakeDocSnapshot(doc_id, data, self._store) for doc_id, data in self._store.items()]


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))


def _store(**overrides: Any) -> PresenceEventStore:
    settings = get_settings().model_copy(update=overrides)
    return PresenceEventStore(settings)


def _dt(hour: int, minute: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


AGENT = 42


def _event(status: str, at: datetime, previous: str | None, source: str = "agent") -> PresenceEvent:
    return PresenceEvent(agent_id=AGENT, status=status, at=at, source=source, previous=previous)


@pytest.mark.asyncio
async def test_appending_an_event_makes_it_the_latest():
    with patch("chatbot.features.routing.presence_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        await store.append(_event("available", _dt(8), previous=None))
        await store.append(_event("on_call", _dt(9), previous="available"))

        latest = await store.latest(AGENT)
        assert latest is not None
        assert latest.status == "on_call"
        assert latest.previous == "available"


@pytest.mark.asyncio
async def test_elapsed_is_computed_from_the_latest_event_not_stored():
    """Elapsed time is derived fresh from `latest()`, never a value cached
    on some earlier event -- appending a newer event must shift what
    `elapsed_in_current_status` returns."""
    with patch("chatbot.features.routing.presence_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        await store.append(_event("available", _dt(8), previous=None))
        await store.append(_event("on_call", _dt(9, 30), previous="available"))

        now = _dt(10)
        elapsed = await store.elapsed_in_current_status(AGENT, now)

        assert elapsed == now - _dt(9, 30)
        assert elapsed != now - _dt(8)


@pytest.mark.asyncio
async def test_an_agent_with_no_events_returns_none_not_a_zero_duration():
    with patch("chatbot.features.routing.presence_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        assert await store.latest(AGENT) is None
        assert await store.elapsed_in_current_status(AGENT, _dt(12)) is None


@pytest.mark.asyncio
async def test_since_returns_events_in_chronological_order():
    with patch("chatbot.features.routing.presence_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        # Appended out of chronological order on purpose.
        await store.append(_event("on_call", _dt(9), previous="available"))
        await store.append(_event("available", _dt(8), previous=None))
        await store.append(_event("wrap_up", _dt(10), previous="on_call"))

        events = await store.since(AGENT, _dt(0))

        assert [e.status for e in events] == ["available", "on_call", "wrap_up"]
        assert [e.at for e in events] == sorted(e.at for e in events)


@pytest.mark.asyncio
async def test_two_appends_at_the_same_instant_are_both_retained():
    same_instant = _dt(9)
    with patch("chatbot.features.routing.presence_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        await store.append(_event("available", same_instant, previous=None, source="poll"))
        await store.append(_event("on_call", same_instant, previous="available", source="agent"))

        events = await store.since(AGENT, _dt(0))

        assert len(events) == 2
        assert {e.status for e in events} == {"available", "on_call"}


@pytest.mark.asyncio
async def test_the_store_is_append_only_and_exposes_no_update_method():
    """Design assertion: no general mutator exists on the store. `stamp_alert`
    is the one sanctioned, narrowly-scoped exception -- it patches only
    `alerts_sent`, never `status`/`at`/`previous`."""
    disallowed_names = {"update", "set", "set_status", "edit", "replace", "delete", "patch"}
    public_methods = {
        name
        for name, _member in inspect.getmembers(PresenceEventStore, predicate=inspect.isfunction)
        if not name.startswith("_")
    }

    assert not (public_methods & disallowed_names)
    assert public_methods == {
        "append",
        "since",
        "latest",
        "elapsed_in_current_status",
        "time_in_status_since",
        "stamp_alert",
    }


@pytest.mark.asyncio
async def test_todays_time_in_each_status_is_derivable_from_the_event_list():
    with patch("chatbot.features.routing.presence_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        start_of_day = _dt(0)
        await store.append(_event("available", _dt(8), previous=None))
        await store.append(_event("on_call", _dt(9, 30), previous="available"))
        await store.append(_event("available", _dt(10), previous="on_call"))

        now = _dt(11)
        totals = await store.time_in_status_since(AGENT, start_of_day, now)

        assert totals["available"] == timedelta(hours=1, minutes=30) + timedelta(hours=1)
        assert totals["on_call"] == timedelta(minutes=30)


@pytest.mark.asyncio
async def test_stamp_alert_only_touches_alerts_sent_on_the_latest_event():
    with patch("chatbot.features.routing.presence_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        await store.append(_event("available", _dt(8), previous=None))
        await store.append(_event("unavailable", _dt(9), previous="available"))

        await store.stamp_alert(AGENT, "10min_unavailable")
        latest = await store.latest(AGENT)

        assert latest is not None
        assert latest.status == "unavailable"
        assert latest.previous == "available"
        assert latest.at == _dt(9)
        assert "10min_unavailable" in latest.alerts_sent

        # Stamping is idempotent under a repeat fire, and never rewrites the
        # earlier event's status/at/previous.
        await store.stamp_alert(AGENT, "10min_unavailable")
        events = await store.since(AGENT, _dt(0))
        assert events[0].status == "available"
        assert events[0].alerts_sent == frozenset()


@pytest.mark.asyncio
async def test_a_store_outage_fails_open_rather_than_raising():
    with patch("chatbot.features.routing.presence_store.firestore.Client", autospec=True) as C:
        C.side_effect = RuntimeError("firestore down")
        store = _store()

        assert await store.latest(AGENT) is None
        assert await store.since(AGENT, _dt(0)) == []
        assert await store.elapsed_in_current_status(AGENT, _dt(1)) is None
        assert await store.time_in_status_since(AGENT, _dt(0), _dt(1)) == {}
        # None of these may raise, including the write path and the alert stamp.
        await store.append(_event("available", _dt(8), previous=None))
        await store.stamp_alert(AGENT, "x")
