"""Task 8 (P6): supervisor-facing reassignment on POST /routing/assign.

An explicit `agent_id` in the request body is a team leader/supervisor
choosing who handles a case -- distinct from the existing auto-pick flow
(`agent_id` absent) that the live handoff path depends on. See
`router.py::build_routing_router` for the auth-layering rationale (outer
`_require_api_key` unconditionally, `routing.reassign` gated additionally
and only inside the handler for this branch).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine as build_authz_engine
from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
from chatbot.features.authz.db import init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.features.routing.presence import AgentRecord
from chatbot.features.routing.router import build_routing_router
from chatbot.platform.config import Settings

API_KEY = "k"

CHATWOOT_HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "77",
}


async def _authz_repo(tmp_path, name: str) -> AuthzRepository:
    engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(engine)
    return AuthzRepository(build_authz_session_maker(engine))


def _settings(**overrides) -> Settings:
    # proton_backend_key (not routing_admin_api_key) is used deliberately:
    # it's the one shared-secret candidate accepted BOTH by this router's
    # own `_require_api_key` (the decorator-level check, unchanged) and by
    # `require_permission`'s non-RBAC fallback (`features/authz/deps.py`'s
    # `_shared_secret_check`, which does not list routing_admin_api_key) --
    # so a single header satisfies both layers for the reassignment path.
    defaults = {
        "proton_backend_key": API_KEY,
        "routing_enabled": True,
        "chatwoot_api_url": "http://cw",
        "chatwoot_account_id": 1,
        "chatwoot_api_token": "t",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _agents() -> list[AgentRecord]:
    return [
        AgentRecord(id=1, name="Alice", availability_status="online"),
        AgentRecord(id=2, name="Bob (lunch)", availability_status="busy"),
    ]


def _build(
    settings: Settings,
    *,
    audit=None,
    authz_repo=None,
    validator=None,
    fetch_agents_result: list[AgentRecord] | None = None,
):
    store = AsyncMock()
    presence = AsyncMock()
    presence.fetch_agents = AsyncMock(
        return_value=_agents() if fetch_agents_result is None else fetch_agents_result
    )
    routing_svc = AsyncMock()
    routing_svc.pick_agent = AsyncMock(return_value=9)
    assigner = AsyncMock()
    assigner.resolve_channel = AsyncMock(return_value="whatsapp")
    assigner.assign = AsyncMock()

    app = FastAPI()
    app.include_router(
        build_routing_router(
            settings,
            store,
            presence,
            routing_svc,
            assigner,
            audit=audit,
            authz_repo=authz_repo,
            validator=validator,
        )
    )
    return TestClient(app), presence, routing_svc, assigner


async def test_an_explicit_agent_id_is_honoured_and_bypasses_selection() -> None:
    settings = _settings()
    client, _presence, routing_svc, assigner = _build(settings)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 200
    assert resp.json()["assigned_agent_id"] == 1
    assigner.assign.assert_awaited_once_with(5, 1)
    routing_svc.pick_agent.assert_not_awaited()
    assigner.resolve_channel.assert_not_awaited()


async def test_an_absent_agent_id_auto_picks_exactly_as_today() -> None:
    settings = _settings()
    client, presence, _routing_svc, assigner = _build(settings)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 200
    assert resp.json() == {"assigned_agent_id": 9, "channel": "whatsapp"}
    assigner.assign.assert_awaited_once_with(5, 9)
    presence.fetch_agents.assert_not_awaited()


async def test_an_unknown_agent_id_is_rejected_with_a_useful_message() -> None:
    settings = _settings()
    client, _presence, _routing_svc, assigner = _build(settings)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 999},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 400
    assert "999" in resp.json()["detail"]
    assigner.assign.assert_not_awaited()


async def test_unknown_agent_id_is_also_rejected_when_chatwoot_is_unreachable() -> None:
    """fetch_agents() is fail-open ([] on a Chatwoot blip) everywhere else in
    this module, but validation deliberately does NOT inherit that: silently
    assigning to an unvalidated id is worse than refusing during a blip."""
    settings = _settings()
    client, _presence, _routing_svc, assigner = _build(settings, fetch_agents_result=[])

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 400
    assigner.assign.assert_not_awaited()


async def test_assigning_to_a_non_routable_agent_succeeds_with_a_warning() -> None:
    settings = _settings()
    client, _presence, _routing_svc, assigner = _build(settings)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 2},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned_agent_id"] == 2
    assert "warning" in body
    assert "2" in body["warning"] or "Bob" in body["warning"]
    assigner.assign.assert_awaited_once_with(5, 2)


async def test_an_unauthorised_caller_is_rejected(tmp_path) -> None:
    """RBAC on: a caller who is a valid general API caller for this endpoint
    (has the shared secret) but whose Chatwoot identity lacks the
    `routing.reassign` permission must be rejected -- auto-pick's auth alone
    is not enough to let anyone reassign."""
    settings = _settings(rbac_enabled=True)
    authz_repo = await _authz_repo(tmp_path, "no_perm")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=77, role_id="agent")  # lacks routing.reassign
    validator = TokenValidator(settings)

    with respx.mock:
        respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
            return_value=httpx.Response(200, json={"id": 77})
        )
        client, _presence, _routing_svc, assigner = _build(
            settings, authz_repo=authz_repo, validator=validator
        )

        resp = client.post(
            "/routing/assign",
            json={"conversation_id": 5, "agent_id": 1},
            headers={"x-api-key": API_KEY, **CHATWOOT_HEADERS},
        )

    assert resp.status_code == 403
    assigner.assign.assert_not_awaited()


async def test_the_reassignment_is_audited_with_the_acting_user() -> None:
    settings = _settings()
    audit = AsyncMock()
    client, _presence, _routing_svc, _assigner = _build(settings, audit=audit)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY, **CHATWOOT_HEADERS},
    )

    assert resp.status_code == 200
    audit.append.assert_awaited_once()
    entry = audit.append.await_args.args[0]
    assert entry.actor == "77"
    assert entry.ticket_id == "5"
    assert "1" in entry.to_state


async def test_reassignment_audit_falls_back_to_api_key_label_without_rbac() -> None:
    settings = _settings()
    audit = AsyncMock()
    client, _presence, _routing_svc, _assigner = _build(settings, audit=audit)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 200
    entry = audit.append.await_args.args[0]
    assert entry.actor == "api-key"
