"""P6 task 2 -- custom statuses that mirror onto Chatwoot's native enum.

Two behaviours here decide whether custom statuses can be trusted:

`test_a_native_status_write_failure_does_not_append_a_misleading_event`: the
native Chatwoot write must happen FIRST, and the presence event is appended
only on success -- an event claiming an agent is on lunch while Chatwoot
still shows them online would make the dashboard and the router disagree.

`test_coaching_is_not_routable_but_does_not_count_as_unavailable` and
`test_toilet_is_not_routable_and_does_count_as_unavailable` pin the two-flag
design to concrete cases: Coaching is not routable but does not count as
unavailable (a supervisor does not want a 10-minute alert about a coaching
session they scheduled), while Toilet is not routable AND does count as
unavailable (that alert is exactly the point). A later "simplification" to
one flag must fail these loudly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chatbot.features.routing.custom_status import (
    SEED_STATUSES,
    CustomStatus,
    CustomStatusStore,
)
from chatbot.features.routing.presence_store import PresenceEvent
from chatbot.platform.config import get_settings


class _FakeDoc:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store, self._key = store, key

    def get(self) -> MagicMock:
        snap = MagicMock()
        snap.exists = self._key in self._store
        snap.to_dict.return_value = self._store.get(self._key)
        snap.id = self._key
        return snap

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._key] = data


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> _FakeDoc:
        return _FakeDoc(self._store, key)

    def stream(self) -> Any:
        for key in list(self._store):
            yield _FakeDoc(self._store, key).get()


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))


class _FakePresenceStore:
    """Enough of `PresenceEventStore` (`latest` + `append`) for `set_status`
    to depend on, without dragging Firestore mocking into the event side too
    -- the catalogue's own Firestore reads/writes are exercised separately
    via `_FakeFirestoreClient` above."""

    def __init__(self) -> None:
        self.appended: list[PresenceEvent] = []

    async def latest(self, agent_id: int) -> PresenceEvent | None:
        events = [e for e in self.appended if e.agent_id == agent_id]
        return events[-1] if events else None

    async def append(self, event: PresenceEvent) -> None:
        self.appended.append(event)


class _FakeAvailabilityWriter:
    """Stands in for the Chatwoot PATCH call. `succeed` toggles whether the
    simulated native write succeeds or fails."""

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.calls: list[tuple[int, str]] = []

    async def set_availability(self, agent_id: int, native: str) -> bool:
        self.calls.append((agent_id, native))
        return self.succeed


def _store(**overrides: Any) -> CustomStatusStore:
    settings = get_settings().model_copy(update=overrides)
    return CustomStatusStore(settings, _FakePresenceStore())


AGENT = 7


@pytest.mark.asyncio
async def test_the_eight_named_statuses_are_seeded():
    """§4.17 asks for eight named statuses. The catalogue also seeds a
    ninth (`acw`, for the concurrently-built After-Call-Work task) that no
    agent ever chooses directly, so this asserts "the eight names are
    present", not "the catalogue has exactly eight entries"."""
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        created = await store.seed()
        assert created == len(SEED_STATUSES)

        labels = {status.label for status in await store.list_all()}
        assert {
            "Available",
            "Busy",
            "Lunch",
            "Break",
            "Coaching",
            "Training",
            "Toilet",
            "Prayer",
        } <= labels


@pytest.mark.asyncio
async def test_setting_lunch_sets_the_native_chatwoot_status_to_busy():
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        writer = _FakeAvailabilityWriter(succeed=True)
        store = CustomStatusStore(get_settings(), _FakePresenceStore(), writer)
        await store.seed()

        ok = await store.set_status(AGENT, "lunch")

        assert ok is True
        assert writer.calls == [(AGENT, "busy")]


@pytest.mark.asyncio
async def test_setting_lunch_also_appends_a_presence_event():
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        presence = _FakePresenceStore()
        store = CustomStatusStore(get_settings(), presence, _FakeAvailabilityWriter(succeed=True))
        await store.seed()

        await store.set_status(AGENT, "lunch")

        assert len(presence.appended) == 1
        event = presence.appended[0]
        assert event.agent_id == AGENT
        assert event.status == "lunch"


@pytest.mark.asyncio
async def test_routable_and_counts_as_unavailable_are_independent_flags():
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.seed()

        available = await store.get("available")
        busy = await store.get("busy")
        lunch = await store.get("lunch")
        assert available is not None
        assert busy is not None
        assert lunch is not None

        # `busy` and `lunch` share routable=False but disagree on
        # counts_as_unavailable -- these are two separate flags, not one
        # restated as the other.
        assert busy.routable is False
        assert busy.counts_as_unavailable is False
        assert lunch.routable is False
        assert lunch.counts_as_unavailable is True
        assert available.routable is True
        assert available.counts_as_unavailable is False


@pytest.mark.asyncio
async def test_coaching_is_not_routable_but_does_not_count_as_unavailable():
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.seed()

        coaching = await store.get("coaching")
        assert coaching is not None
        assert coaching.routable is False
        assert coaching.counts_as_unavailable is False


@pytest.mark.asyncio
async def test_toilet_is_not_routable_and_does_count_as_unavailable():
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.seed()

        toilet = await store.get("toilet")
        assert toilet is not None
        assert toilet.routable is False
        assert toilet.counts_as_unavailable is True


@pytest.mark.asyncio
async def test_an_operator_can_add_a_ninth_status_without_a_deploy():
    """The catalogue already seeds nine entries (the eight §4.17 names plus
    `acw`), so this adds a TENTH key -- the point is operator-extensibility
    (a document write, no enum/code change or deploy), not a literal count
    of nine."""
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.seed()

        new_status = CustomStatus(
            key="offsite_visit",
            label="Offsite Visit",
            color="#795548",
            routable=False,
            native="busy",
            counts_as_unavailable=True,
        )
        await store.add(new_status)

        assert await store.get("offsite_visit") == new_status


@pytest.mark.asyncio
async def test_a_native_status_write_failure_does_not_append_a_misleading_event():
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        presence = _FakePresenceStore()
        store = CustomStatusStore(get_settings(), presence, _FakeAvailabilityWriter(succeed=False))
        await store.seed()

        ok = await store.set_status(AGENT, "lunch")

        assert ok is False
        assert presence.appended == []


@pytest.mark.asyncio
async def test_a_native_online_status_resolves_to_the_available_entry():
    """The C1 defect in one assertion. The presence poller writes Chatwoot's
    native strings into the event log, and `online` is not a catalogue key --
    `available` is. `get` therefore answers `None`, which every consumer is
    contractually obliged to read as "no information", and that is why the
    threshold sweeper returned early on every event the product could
    actually produce. `resolve` is what closes the gap."""
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.seed()

        assert await store.get("online") is None  # the contract `resolve` exists to bridge

        resolved = await store.resolve("online")
        assert resolved is not None
        assert resolved.key == "available"
        assert resolved.routable is True
        assert resolved.counts_as_unavailable is False


@pytest.mark.asyncio
async def test_offline_is_catalogued_and_does_not_count_as_unavailable():
    """An agent going offline is off shift, not missing mid-shift -- the
    design derives login/logout from exactly these transitions. Alerting on
    it would page an admin ten minutes after every agent logs off, every
    night. Being *catalogued* is still the point: before this, `offline`
    resolved to `None`, i.e. "we know nothing", about the one transition the
    design says the most about."""
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.seed()

        offline = await store.resolve("offline")
        assert offline is not None
        assert offline.native == "offline"
        assert offline.routable is False
        assert offline.counts_as_unavailable is False


@pytest.mark.asyncio
async def test_a_status_resolves_from_the_shipped_defaults_when_the_seed_never_ran():
    """The hidden flag dependency (review finding I2), self-healed.

    The catalogue was only ever populated by a startup seed gated on
    `PRESENCE_CUSTOM_STATUSES_ENABLED`, so a tenant running `ACW_ENABLED`
    alone logged `custom_status_set_unknown_key` + `acw_enter_failed` on every
    completed call, forever, with no operator-visible symptom. `get` now falls
    back to the shipped definition -- a read, no Firestore write, so a tenant
    that never asked for this feature still gets nothing written."""
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        client = _FakeFirestoreClient()
        C.return_value = client
        presence = _FakePresenceStore()
        store = CustomStatusStore(get_settings(), presence, _FakeAvailabilityWriter(succeed=True))
        # NOTE: deliberately no `seed()` call.

        acw = await store.get("acw")
        assert acw is not None
        assert acw.native == "busy"
        assert await store.set_status(AGENT, "acw") is True
        assert [e.status for e in presence.appended] == ["acw"]
        assert client._collections.get("custom_statuses", {}) == {}  # nothing written


@pytest.mark.asyncio
async def test_a_stored_status_always_wins_over_the_shipped_default():
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        edited = CustomStatus(
            key="lunch",
            label="Makan",
            color="#ff0000",
            routable=False,
            native="busy",
            counts_as_unavailable=False,  # this tenant does not want lunch alerts
        )
        await store.add(edited)

        assert await store.get("lunch") == edited
        assert await store.resolve("lunch") == edited


@pytest.mark.asyncio
async def test_a_catalogue_outage_still_returns_none_rather_than_a_default():
    """The fail-open contract is unchanged by the defaults fallback: `None` on
    an outage is what stops a store failure from either halting all routing or
    silently overriding a stored operator edit with a shipped default."""

    class _ExplodingClient:
        def collection(self, name: str) -> Any:
            raise RuntimeError("firestore unavailable")

    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _ExplodingClient()
        store = _store()

        assert await store.get("lunch") is None
        assert await store.resolve("online") is None


@pytest.mark.asyncio
async def test_seeding_skips_a_status_it_cannot_read_rather_than_overwriting_it():
    """`seed()` reads documents directly, not through `get()`.

    Two reasons, both load-bearing. Through `get()` every status would look
    "already present" (the defaults fallback) and nothing would ever be
    persisted, leaving the admin catalogue page permanently blank. And a read
    failure must not read as "absent", or a Firestore hiccup at startup would
    overwrite an operator's edited status with the shipped default."""
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        client = _FakeFirestoreClient()

        class _FlakyClient:
            def __init__(self) -> None:
                self.reads = 0

            def collection(self, name: str) -> Any:
                self.reads += 1
                if self.reads == 1:  # the very first status read blows up
                    raise RuntimeError("firestore unavailable")
                return client.collection(name)

        C.return_value = _FlakyClient()
        store = _store()

        created = await store.seed()

        assert created == len(SEED_STATUSES) - 1  # the unreadable one was skipped, not written


@pytest.mark.asyncio
async def test_seeding_never_overwrites_an_operator_edited_status():
    with patch("chatbot.features.routing.custom_status.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.seed()

        edited = CustomStatus(
            key="lunch",
            label="Lunch (extended)",
            color="#ff0000",
            routable=False,
            native="busy",
            counts_as_unavailable=True,
        )
        await store.add(edited)

        created_on_reseed = await store.seed()

        assert created_on_reseed == 0
        assert (await store.get("lunch")) == edited
