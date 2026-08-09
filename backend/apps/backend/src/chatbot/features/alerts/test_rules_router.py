"""P9 task 1/6 -- the alert-rule router.

Mirrors `features/authz/test_router.py`'s fixture shape: a real RBAC database
(sqlite via `aiosqlite`), a real `TokenValidator` with Chatwoot's
`/api/v1/profile` stubbed via `respx`, and the router's own store with
Firestore replaced by an in-memory fake -- nothing here talks to a real
Firestore or a real Chatwoot.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.alerts.rules_router import build_rules_router
from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import PERMISSION_REGISTRY, seed_defaults
from chatbot.platform.config import get_settings

_ADMIN_HEADERS = {
    "x-chatwoot-access-token": "admin-tok",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}
_AGENT_HEADERS = {
    "x-chatwoot-access-token": "agent-tok",
    "x-chatwoot-client": "client-2",
    "x-chatwoot-uid": "uid-2",
}


class _FakeDoc:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store, self._key = store, key

    def get(self) -> Any:
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


@pytest.fixture
async def client(tmp_path, respx_mock, monkeypatch):
    settings = get_settings().model_copy(update={"rbac_enabled": True, "alert_rules_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/rules_router_test.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=1, role_id="administrator")
    await repo.assign_role(chatwoot_user_id=2, role_id="agent")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        side_effect=lambda request: httpx.Response(
            200, json={"id": 1 if request.headers["access-token"] == "admin-tok" else 2}
        )
    )
    validator = TokenValidator(settings)

    fake_client = _FakeFirestoreClient()
    monkeypatch.setattr(
        "chatbot.features.alerts.rules_store.firestore.Client",
        lambda *_args, **_kwargs: fake_client,
    )

    app = FastAPI()
    app.include_router(build_rules_router(settings, repo, validator))
    return TestClient(app)


def test_an_agent_can_read_and_set_their_own_overrides(client):
    mine = client.get("/alerts/rules/mine", headers=_AGENT_HEADERS)
    assert mine.status_code == 200, mine.text
    # No override exists yet -- resolves to the built-in default.
    assert mine.json()["rules"]["sla_warn"]["modalities"] == ["toast"]

    put = client.put(
        "/alerts/rules/mine/sla_warn",
        json={"scope": "mine", "modalities": ["sound", "toast"], "enabled": True},
        headers=_AGENT_HEADERS,
    )
    assert put.status_code == 200, put.text

    mine_again = client.get("/alerts/rules/mine", headers=_AGENT_HEADERS)
    assert set(mine_again.json()["rules"]["sla_warn"]["modalities"]) == {"sound", "toast"}

    # A different agent's own overrides are untouched.
    other = client.get("/alerts/rules/mine?agent_id=99", headers=_ADMIN_HEADERS)
    assert other.status_code == 200
    # agent_id is ignored when RBAC is on -- the caller's own session wins.
    assert other.json()["agent_id"] == 1


def test_an_agent_cannot_change_the_account_defaults(client):
    res = client.put(
        "/alerts/rules/defaults/sla_warn",
        json={"scope": "mine", "modalities": ["sound"], "enabled": True},
        headers=_AGENT_HEADERS,
    )
    assert res.status_code == 403


def test_an_admin_can_change_the_account_defaults(client):
    res = client.put(
        "/alerts/rules/defaults/new_inbound",
        json={"scope": "my_inbox", "modalities": ["toast", "desktop"], "enabled": True},
        headers=_ADMIN_HEADERS,
    )
    assert res.status_code == 200, res.text

    defaults = client.get("/alerts/rules/defaults", headers=_ADMIN_HEADERS)
    assert set(defaults.json()["defaults"]["new_inbound"]["modalities"]) == {"toast", "desktop"}

    # And an agent with no override of their own now inherits the new default.
    mine = client.get("/alerts/rules/mine", headers=_AGENT_HEADERS)
    assert set(mine.json()["rules"]["new_inbound"]["modalities"]) == {"toast", "desktop"}


def test_resetting_an_override_returns_the_agent_to_the_account_default(client):
    admin_set = client.put(
        "/alerts/rules/defaults/escalated",
        json={"scope": "my_team", "modalities": ["sound", "toast"], "enabled": True},
        headers=_ADMIN_HEADERS,
    )
    assert admin_set.status_code == 200

    agent_override = client.put(
        "/alerts/rules/mine/escalated",
        json={"scope": "mine", "modalities": ["desktop"], "enabled": True},
        headers=_AGENT_HEADERS,
    )
    assert agent_override.status_code == 200
    assert client.get("/alerts/rules/mine", headers=_AGENT_HEADERS).json()["rules"]["escalated"][
        "modalities"
    ] == ["desktop"]

    reset = client.delete("/alerts/rules/mine/escalated", headers=_AGENT_HEADERS)
    assert reset.status_code == 200, reset.text
    assert set(reset.json()["rule"]["modalities"]) == {"sound", "toast"}

    mine = client.get("/alerts/rules/mine", headers=_AGENT_HEADERS)
    assert set(mine.json()["rules"]["escalated"]["modalities"]) == {"sound", "toast"}


def test_the_permission_appears_in_the_permission_registry():
    assert "alerts.manage" in PERMISSION_REGISTRY
    assert "alerts.set_own_preferences" in PERMISSION_REGISTRY
