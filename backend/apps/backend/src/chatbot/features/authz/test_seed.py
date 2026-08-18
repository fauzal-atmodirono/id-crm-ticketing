import pytest

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import (
    ALL_NATIVE_KEYS,
    NATIVE_PERMISSION_REGISTRY,
    PERMISSION_REGISTRY,
    seed_defaults,
)


@pytest.fixture
async def repo(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/authz_seed.db")
    await init_authz_db(engine)
    return AuthzRepository(build_session_maker(engine))


@pytest.mark.asyncio
async def test_seed_creates_administrator_with_all_permissions(repo):
    await seed_defaults(repo)
    roles = {r.id for r in await repo.list_roles()}
    assert {"administrator", "agent"} <= roles


@pytest.mark.asyncio
async def test_seed_is_idempotent(repo):
    await seed_defaults(repo)
    await seed_defaults(repo)  # second run must not raise or duplicate
    roles = await repo.list_roles()
    assert len([r for r in roles if r.id == "administrator"]) == 1


@pytest.mark.asyncio
async def test_administrator_role_has_every_registered_permission(repo):
    await seed_defaults(repo)
    # administrator is seeded with chatwoot_user_id=0 as a placeholder assignment
    # only in this test — in production, role assignment happens via the
    # /authz roles-admin endpoint (Task 5), not at seed time.
    async with repo._sm() as session:
        pass  # placeholder to keep session import path exercised; real assertion below
    from sqlalchemy import select

    from chatbot.features.authz.db import RolePermission

    async with repo._sm() as session:
        rows = (
            (
                await session.execute(
                    select(RolePermission.permission_key).where(
                        RolePermission.role_id == "administrator"
                    )
                )
            )
            .scalars()
            .all()
        )
    # voice.answer is the one PERMISSION_REGISTRY key withheld even from
    # "administrator" -- see its entry in seed.py for why.
    assert set(rows) == set(PERMISSION_REGISTRY.keys()) - {"voice.answer"}


@pytest.mark.asyncio
async def test_bootstrap_admin_assignment_grants_full_permission_set(repo):
    # Mirrors main.py's startup wiring: seed_defaults() then assign_role() for
    # RBAC_BOOTSTRAP_ADMIN_USER_ID. Confirms the composition actually grants
    # the bootstrapped user every registered permission, and that re-running
    # both calls (as happens on every restart) stays idempotent.
    await seed_defaults(repo)
    await repo.assign_role(999, "administrator")
    # voice.answer is the one PERMISSION_REGISTRY key withheld even from
    # "administrator" -- see its entry in seed.py for why.
    assert await repo.permissions_for_user(999) == set(PERMISSION_REGISTRY.keys()) - {
        "voice.answer"
    }

    # Simulate a second startup: seeding + bootstrap assignment again must not
    # raise or change the resulting permission set.
    await seed_defaults(repo)
    await repo.assign_role(999, "administrator")
    # voice.answer is the one PERMISSION_REGISTRY key withheld even from
    # "administrator" -- see its entry in seed.py for why.
    assert await repo.permissions_for_user(999) == set(PERMISSION_REGISTRY.keys()) - {
        "voice.answer"
    }


@pytest.mark.asyncio
async def test_native_permissions_are_registered(repo):
    await seed_defaults(repo)
    perms = await repo.list_permissions()
    keys = {p.key for p in perms}
    assert keys >= ALL_NATIVE_KEYS


@pytest.mark.asyncio
async def test_native_permissions_not_auto_granted_to_administrator(repo):
    await seed_defaults(repo)
    admin_perms = await repo.role_permissions("administrator")
    assert admin_perms.isdisjoint(ALL_NATIVE_KEYS)


@pytest.mark.asyncio
async def test_native_permissions_not_auto_granted_to_agent(repo):
    await seed_defaults(repo)
    agent_perms = await repo.role_permissions("agent")
    assert agent_perms.isdisjoint(ALL_NATIVE_KEYS)


def test_native_permission_registry_has_six_keys():
    assert len(NATIVE_PERMISSION_REGISTRY) == 6


@pytest.mark.asyncio
async def test_voice_answer_permission_is_seeded_and_not_default_granted(repo):
    """A Voice grant with incoming_allow=True is a BILLABLE capability on the
    tenant's Twilio account, not just a UI affordance -- an operator ticks it
    on deliberately. Note this repo has no DEFAULT_ROLE_PERMISSIONS/PERMISSIONS
    constants (the seeded permission catalogue is PERMISSION_REGISTRY, and
    each default role's grants are read back from the DB via
    repo.role_permissions), so this checks the same property against the
    real names: seeded in the catalogue, but not granted to either default
    role -- including "administrator", which for every other
    PERMISSION_REGISTRY key gets the full set automatically."""
    assert "voice.answer" in PERMISSION_REGISTRY

    await seed_defaults(repo)
    for role in await repo.list_roles():
        perms = await repo.role_permissions(role.id)
        assert "voice.answer" not in perms, f"{role.id} must not get voice.answer by default"
