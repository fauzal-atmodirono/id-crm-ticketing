"""The token endpoint is the one place a billable, call-receiving credential
is issued, so these tests are mostly about who can get one and whose name is
on it."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.phone.softphone_router import build_softphone_router


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    return get_settings().model_copy(
        update={
            "rbac_enabled": True,
            "phone_agent_softphone_enabled": True,
            "twilio_account_sid": "AC" + "0" * 32,
            "twilio_api_key_sid": "SK" + "0" * 32,
            "twilio_api_key_secret": "secret-value",
            "twilio_twiml_app_sid": "AP" + "0" * 32,
        }
    )


@pytest.fixture
def registry():
    reg = AsyncMock()
    reg.registered_ids.return_value = set()
    return reg


def _client(settings, registry, user_id=17, perms=frozenset({"voice.answer"})):
    validator = AsyncMock()
    validator.resolve_user_id.return_value = user_id
    repo = AsyncMock()
    repo.permissions_for_user.return_value = set(perms)
    app = FastAPI()
    app.include_router(build_softphone_router(settings, registry, repo=repo, validator=validator))
    return TestClient(app)


_AUTH = {
    "x-chatwoot-access-token": "tok",
    "x-chatwoot-client": "cli",
    "x-chatwoot-uid": "agent@proton.local",
}


def test_token_identity_comes_from_the_session_not_the_body(settings, registry):
    """The attack this blocks: an authenticated agent asking for a token in a
    COLLEAGUE's name, which would let them receive that colleague's
    transferred customer calls."""
    res = _client(settings, registry).post(
        "/voice/agent/token", json={"identity": "agent_999"}, headers=_AUTH
    )
    assert res.status_code == 200
    assert res.json()["identity"] == "agent_17"


def test_token_requires_the_permission(settings, registry):
    res = _client(settings, registry, perms=frozenset()).post(
        "/voice/agent/token", json={}, headers=_AUTH
    )
    assert res.status_code == 403


def test_token_requires_a_session(settings, registry):
    res = _client(settings, registry).post("/voice/agent/token", json={})
    assert res.status_code == 401


def test_heartbeat_registers_the_session_user(settings, registry):
    res = _client(settings, registry).post("/voice/agent/heartbeat", json={}, headers=_AUTH)
    assert res.status_code == 200
    registry.heartbeat.assert_awaited_once_with(17)


def test_unregister_removes_the_session_user(settings, registry):
    res = _client(settings, registry).post("/voice/agent/unregister", json={}, headers=_AUTH)
    assert res.status_code == 200
    registry.unregister.assert_awaited_once_with(17)


def test_routes_404_when_the_feature_is_off(registry):
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    off = get_settings().model_copy(
        update={"rbac_enabled": True, "phone_agent_softphone_enabled": False}
    )
    res = _client(off, registry).post("/voice/agent/token", json={}, headers=_AUTH)
    assert res.status_code == 404
