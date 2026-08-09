"""P9 task 1 -- the alert-rule store.

`test_new_inbound_defaults_to_toast_only` is a design assertion, not a detail:
see `rules_store`'s module docstring for why. If this test starts failing
because someone added `sound` to `new_inbound`'s default, that is the bug,
not the test.

`test_a_store_outage_falls_back_to_the_seeded_defaults` is the other one that
matters more than its name suggests: a rule-store outage must degrade to the
built-in defaults, never to silence and never to "everything on".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chatbot.features.alerts.rules_store import (
    BUILT_IN_DEFAULTS,
    AlertRule,
    AlertRuleStore,
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

    def delete(self) -> None:
        self._store.pop(self._key, None)


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> _FakeDoc:
        return _FakeDoc(self._store, key)


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))


class _RaisingDoc:
    def get(self) -> Any:
        raise RuntimeError("firestore unavailable")

    def set(self, data: dict[str, Any]) -> None:
        raise RuntimeError("firestore unavailable")

    def delete(self) -> None:
        raise RuntimeError("firestore unavailable")


class _RaisingFirestoreClient:
    def collection(self, name: str) -> Any:
        collection = MagicMock()
        collection.document.return_value = _RaisingDoc()
        return collection


def _store() -> AlertRuleStore:
    return AlertRuleStore(get_settings())


@pytest.mark.asyncio
async def test_the_six_default_rules_are_seeded():
    with patch("chatbot.features.alerts.rules_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        created = await store.seed()

        assert created == 6
        for event in BUILT_IN_DEFAULTS:
            stored = await store.get_account_rule(event)
            assert stored == BUILT_IN_DEFAULTS[event]

        # Seeding again must not overwrite an operator's later edit.
        await store.set_account_rule(
            AlertRule(event="new_inbound", scope="my_inbox", modalities=("sound", "toast"))
        )
        assert await store.seed() == 0
        edited = await store.get_account_rule("new_inbound")
        assert edited is not None
        assert edited.modalities == ("sound", "toast")


@pytest.mark.asyncio
async def test_new_inbound_defaults_to_toast_only():
    with patch("chatbot.features.alerts.rules_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        rule = await store.resolve(agent_id=1, event="new_inbound")

        assert rule.modalities == ("toast",)
        assert rule.enabled is True
        assert rule.scope == "my_inbox"


@pytest.mark.asyncio
async def test_sla_breach_defaults_to_all_three_modalities():
    with patch("chatbot.features.alerts.rules_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        rule = await store.resolve(agent_id=1, event="sla_breach")

        assert set(rule.modalities) == {"sound", "desktop", "toast"}
        assert rule.enabled is True


@pytest.mark.asyncio
async def test_a_per_agent_override_beats_the_account_default():
    with patch("chatbot.features.alerts.rules_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.set_account_rule(
            AlertRule(event="sla_warn", scope="mine", modalities=("toast",))
        )
        await store.set_agent_override(
            42, AlertRule(event="sla_warn", scope="mine", modalities=("sound", "toast"))
        )

        rule = await store.resolve(agent_id=42, event="sla_warn")

        assert set(rule.modalities) == {"sound", "toast"}

        # A different agent, with no override, still gets the account default.
        other = await store.resolve(agent_id=99, event="sla_warn")
        assert other.modalities == ("toast",)


@pytest.mark.asyncio
async def test_an_agent_with_no_override_gets_the_account_default():
    with patch("chatbot.features.alerts.rules_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.set_account_rule(
            AlertRule(event="escalated", scope="my_team", modalities=("sound", "toast"))
        )

        rule = await store.resolve(agent_id=7, event="escalated")

        # Not the built-in default (toast only) -- the account's own default.
        assert set(rule.modalities) == {"sound", "toast"}
        assert rule.scope == "my_team"


@pytest.mark.asyncio
async def test_a_disabled_rule_resolves_to_no_modalities():
    with patch("chatbot.features.alerts.rules_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()
        await store.set_agent_override(
            5,
            AlertRule(event="anomaly", scope="all", modalities=("desktop", "sound"), enabled=False),
        )

        rule = await store.resolve(agent_id=5, event="anomaly")

        assert rule.modalities == ()
        assert rule.enabled is False


@pytest.mark.asyncio
async def test_an_unknown_event_resolves_to_none_and_alerts_nothing():
    with patch("chatbot.features.alerts.rules_store.firestore.Client", autospec=True) as C:
        C.return_value = _FakeFirestoreClient()
        store = _store()

        rule = await store.resolve(agent_id=1, event="something_nobody_defined")

        assert rule.modalities == ()
        assert rule.enabled is False


@pytest.mark.asyncio
async def test_a_store_outage_falls_back_to_the_seeded_defaults():
    with patch("chatbot.features.alerts.rules_store.firestore.Client", autospec=True) as C:
        C.return_value = _RaisingFirestoreClient()
        store = _store()

        rule = await store.resolve(agent_id=1, event="sla_breach")

        assert rule == BUILT_IN_DEFAULTS["sla_breach"]

        toast_only = await store.resolve(agent_id=1, event="new_inbound")
        assert toast_only == BUILT_IN_DEFAULTS["new_inbound"]
