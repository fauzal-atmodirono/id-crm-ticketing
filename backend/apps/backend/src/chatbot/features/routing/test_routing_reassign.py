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
    assign_result: bool = True,
    previous_assignee: int | None = None,
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
    # Both return values are pinned rather than left to AsyncMock's truthy
    # auto-attribute: `assign` reports whether Chatwoot took the assignment
    # (review finding I5) and `resolve_assignee` supplies the audit row's
    # from_state (deferred Minor 7), so a test that cares must be able to say
    # what each one answered instead of inheriting a MagicMock.
    assigner.assign = AsyncMock(return_value=assign_result)
    assigner.resolve_assignee = AsyncMock(return_value=previous_assignee)

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


async def test_the_reassignment_is_audited_with_the_acting_user(tmp_path) -> None:
    """RBAC on and the caller's `routing.reassign` permission actually
    checks out (an "administrator" role, granted the full registry) --
    only then is x-chatwoot-uid Chatwoot-confirmed (via the /profile stub)
    rather than merely client-claimed, so only then may it become the
    audited actor. See test below for the non-RBAC (default) case, where
    the same header must NOT be trusted (review finding 3)."""
    settings = _settings(rbac_enabled=True)
    authz_repo = await _authz_repo(tmp_path, "actor_admin")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=77, role_id="administrator")
    validator = TokenValidator(settings)
    audit = AsyncMock()

    with respx.mock:
        respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
            return_value=httpx.Response(200, json={"id": 77})
        )
        client, _presence, _routing_svc, _assigner = _build(
            settings, audit=audit, authz_repo=authz_repo, validator=validator
        )

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


async def test_a_forged_uid_header_cannot_reach_the_audit_actor_without_rbac() -> None:
    """Review finding 3: with rbac_enabled=False (the default), the
    non-RBAC auth path never inspects the Chatwoot header triplet at all
    -- so x-chatwoot-uid is completely unauthenticated there. A caller who
    only holds the shared secret must not be able to forge
    `x-chatwoot-uid: <victim>` and have the audit row implicate that
    person; the actor must fall back to the honest "api-key" label."""
    settings = _settings()
    audit = AsyncMock()
    client, _presence, _routing_svc, _assigner = _build(settings, audit=audit)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY, "x-chatwoot-uid": "999-forged"},
    )

    assert resp.status_code == 200
    entry = audit.append.await_args.args[0]
    assert entry.actor == "api-key"
    assert entry.actor != "999-forged"


async def test_reassignment_bypasses_routing_enabled_while_auto_pick_stays_disabled() -> None:
    """Review finding 1: routing_enabled gates automatic SELECTION, not a
    supervisor's explicit choice. With routing_enabled=False (the default
    for every tenant), an explicit-agent_id reassignment must still
    succeed, while the agent_id-absent auto-pick path must still return
    the byte-identical `disabled` payload it always has."""
    settings = _settings(routing_enabled=False)
    client, presence, routing_svc, assigner = _build(settings)

    reassign_resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY},
    )
    assert reassign_resp.status_code == 200
    assert reassign_resp.json()["assigned_agent_id"] == 1
    assigner.assign.assert_awaited_once_with(5, 1)

    auto_pick_resp = client.post(
        "/routing/assign",
        json={"conversation_id": 6},
        headers={"x-api-key": API_KEY},
    )
    assert auto_pick_resp.status_code == 200
    assert auto_pick_resp.json() == {"assigned_agent_id": None, "disabled": True}
    routing_svc.pick_agent.assert_not_awaited()
    presence.fetch_agents.assert_awaited_once()  # only from the reassign call above


async def test_a_firestore_blip_does_not_turn_a_completed_reassignment_into_a_500() -> None:
    """Review finding 2: assigner.assign() has already landed by the time
    the audit write is attempted. An unguarded audit.append that raises
    must not 500 a request whose side effect already happened -- that
    would push a supervisor to retry and double-assign."""
    settings = _settings()
    audit = AsyncMock()
    audit.append = AsyncMock(side_effect=RuntimeError("firestore is down"))
    client, _presence, _routing_svc, assigner = _build(settings, audit=audit)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 200
    assert resp.json()["assigned_agent_id"] == 1
    assigner.assign.assert_awaited_once_with(5, 1)
    audit.append.assert_awaited_once()


async def test_a_routing_admin_key_only_tenant_can_reassign_without_rbac() -> None:
    """Review finding 4: require_permission's non-RBAC fallback doesn't
    accept routing_admin_api_key, but this router's own `_require_api_key`
    does -- and that (not the narrower fallback) is what must gate
    reassignment when RBAC is off, so a tenant configured with only
    ROUTING_ADMIN_API_KEY isn't locked out."""
    settings = _settings(proton_backend_key="", routing_admin_api_key="routing-only-key")
    client, _presence, _routing_svc, assigner = _build(settings)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": "routing-only-key"},
    )

    assert resp.status_code == 200
    assert resp.json()["assigned_agent_id"] == 1
    assigner.assign.assert_awaited_once_with(5, 1)


async def test_a_chatwoot_rejected_reassignment_fails_and_is_not_audited() -> None:
    """Review finding I5. `assigner.assign` swallowed every failure and
    returned None, and this path neither checked nor propagated -- so a
    Chatwoot 422 (the target is not a member of that inbox, say) answered
    200 {"assigned_agent_id": 1} AND wrote an audit row asserting
    `to_state="assigned:1"` for an assignment that never happened. Spec §3.7
    makes the audit this endpoint's entire justification; a trail that records
    unperformed actions is worse than no trail, because it gets believed.
    """
    settings = _settings()
    audit = AsyncMock()
    client, _presence, _routing_svc, assigner = _build(settings, audit=audit, assign_result=False)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 502
    assert "5" in resp.json()["detail"] and "1" in resp.json()["detail"]
    assigner.assign.assert_awaited_once_with(5, 1)
    audit.append.assert_not_awaited()


async def test_the_audit_row_records_who_the_case_was_taken_from() -> None:
    """Deferred Minor 7: `from_state` was hardcoded to `""`, so the row never
    said who the case was reassigned AWAY from -- half of what a reassignment
    audit answers. The previous assignee is read BEFORE the write, because
    afterwards the same lookup returns the agent we just assigned.
    """
    settings = _settings()
    audit = AsyncMock()
    client, _presence, _routing_svc, assigner = _build(settings, audit=audit, previous_assignee=4)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 200
    entry = audit.append.await_args.args[0]
    assert entry.from_state == "assigned:4"
    assert entry.to_state == "assigned:1"
    # Read before the write, not after -- otherwise it reports the new assignee.
    assert assigner.resolve_assignee.await_args_list[0].args == (5,)


async def test_an_unreadable_previous_assignee_is_labelled_honestly() -> None:
    """`resolve_assignee` is fail-open: None means "unassigned" OR "the lookup
    failed" and it cannot tell them apart, so the row must not claim nobody
    held the case. It says so instead -- the same rule the actor label follows
    on the non-RBAC path.
    """
    settings = _settings()
    audit = AsyncMock()
    client, _presence, _routing_svc, _assigner = _build(
        settings, audit=audit, previous_assignee=None
    )

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5, "agent_id": 1},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 200
    entry = audit.append.await_args.args[0]
    assert entry.from_state == "unassigned-or-unknown"


async def test_the_auto_pick_path_stays_fail_open_when_chatwoot_refuses() -> None:
    """The bool `assign` now returns is deliberately ADDITIVE: only the
    supervisor reassignment path reads it. Auto-pick (and `sweeper.py`, which
    shares the same collaborator) must keep answering exactly as before when
    Chatwoot refuses -- a blip there is logged by the assigner and retried on
    the next event/tick, never surfaced as a 502 to the live handoff path.
    """
    settings = _settings()
    client, _presence, _routing_svc, assigner = _build(settings, assign_result=False)

    resp = client.post(
        "/routing/assign",
        json={"conversation_id": 5},
        headers={"x-api-key": API_KEY},
    )

    assert resp.status_code == 200
    assert resp.json() == {"assigned_agent_id": 9, "channel": "whatsapp"}
    assigner.assign.assert_awaited_once_with(5, 9)
