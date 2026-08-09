"""P5 task 1 — operator-editable targets for the control-item slide.

Two behaviours here decide whether the slide tells the truth.

`test_an_unknown_key_resolves_to_none_not_a_zero_target`: a Target(value=0)
would make every unconfigured metric render as "missed by everything" -- the
most alarming possible slide, produced entirely by absence of configuration.

`test_seeding_never_overwrites_an_operator_edit`: an operator who tightens the
complaint target must not have it silently reverted on the next restart.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chatbot.features.metrics.targets_store import (
    InvalidTarget,
    Target,
    TargetsStore,
)
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

    def stream(self):
        for key in list(self._store):
            yield _FakeDoc(self._store, key).get()


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str):
        return _FakeCollection(self._collections.setdefault(name, {}))


def _store(**overrides):
    settings = get_settings().model_copy(update=overrides)
    return TargetsStore(settings)


TENANT = Target(key="first_response", comparator="lte", value=120, unit="minutes")
SCOPED = Target(
    key="first_response", comparator="lte", value=60, unit="minutes", scope="sales"
)


@pytest.mark.asyncio
async def test_a_tenant_wide_target_resolves_for_any_scope():
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.set(TENANT)

        assert (await store.resolve("first_response", "anything")).value == 120


@pytest.mark.asyncio
async def test_a_scoped_target_beats_the_tenant_wide_one():
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.set(TENANT)
        await store.set(SCOPED)

        assert (await store.resolve("first_response", "sales")).value == 60
        assert (await store.resolve("first_response", "other")).value == 120


@pytest.mark.asyncio
async def test_an_unknown_key_resolves_to_none_not_a_zero_target():
    """A zero target makes every unconfigured metric render as missed."""
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        assert await _store().resolve("no_such_metric") is None


@pytest.mark.asyncio
async def test_a_working_hours_unit_round_trips():
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.set(Target("resolution", "lte", 8, "working_hours"))

        assert (await store.resolve("resolution")).unit == "working_hours"


@pytest.mark.asyncio
async def test_a_target_with_an_attainment_pct_round_trips():
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.set(Target("fr", "gte", 120, "minutes", attainment_pct=90.0))

        assert (await store.resolve("fr")).attainment_pct == 90.0


@pytest.mark.asyncio
async def test_an_unknown_unit_is_rejected_at_write_time():
    """A target in an unknown unit is a number the slide prints with the wrong
    label -- worse than a rejected edit."""
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        with pytest.raises(InvalidTarget) as excinfo:
            await _store().set(Target("x", "lte", 1, "furlongs"))
        assert "furlongs" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_unknown_comparator_is_rejected():
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        with pytest.raises(InvalidTarget):
            await _store().set(Target("x", "roughly", 1, "minutes"))


@pytest.mark.asyncio
async def test_the_store_seeds_from_resolution_sla_targets_json():
    seed = '{"Complaint": {"buckets_wh": [8, 24], "labels": ["a","b","c"]}}'
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store(resolution_sla_targets_json=seed)

        assert await store.seed_from_settings() == 1
        seeded = await store.resolve("resolution_complaint")
        assert seeded.value == 8
        assert seeded.unit == "working_hours"


@pytest.mark.asyncio
async def test_seeding_is_idempotent_and_never_overwrites_an_operator_edit():
    seed = '{"Complaint": {"buckets_wh": [8], "labels": ["a","b"]}}'
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store(resolution_sla_targets_json=seed)
        await store.seed_from_settings()

        # the operator tightens it
        await store.set(Target("resolution_complaint", "lte", 4, "working_hours"))
        assert await store.seed_from_settings() == 0

        assert (await store.resolve("resolution_complaint")).value == 4


@pytest.mark.asyncio
async def test_seeding_unparseable_json_creates_nothing_and_does_not_raise():
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        assert await _store(resolution_sla_targets_json="{not json").seed_from_settings() == 0


@pytest.mark.asyncio
async def test_a_store_outage_resolves_to_none_rather_than_raising():
    """Fail-open: a Firestore hiccup must not 500 the report."""
    with patch("chatbot.features.metrics.targets_store.firestore.Client", autospec=True) as C:
        C.side_effect = RuntimeError("firestore down")
        assert await _store().resolve("first_response") is None
