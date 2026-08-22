import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.deps import (
    is_platform_superadmin,
    require_permission,
    require_platform_superadmin,
)
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.platform.config import get_settings


def _app_with_endpoint(dep):
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(dep)])
    def protected():
        return {"ok": True}

    return TestClient(app)


def test_rbac_disabled_falls_back_to_shared_secret():
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "secret123"}
    )
    dep = require_permission("sla.manage", repo=None, validator=None, settings=settings)
    client = _app_with_endpoint(dep)

    assert client.get("/protected").status_code == 401
    assert client.get("/protected", headers={"x-api-key": "wrong"}).status_code == 401
    assert client.get("/protected", headers={"x-api-key": "secret123"}).status_code == 200


@pytest.mark.asyncio
async def test_rbac_enabled_allows_user_with_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=7, role_id="administrator")

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    res = client.get(
        "/protected",
        headers={
            "x-chatwoot-access-token": "tok-abc",
            "x-chatwoot-client": "client-1",
            "x-chatwoot-uid": "uid-1",
        },
    )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_rbac_enabled_denies_user_without_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test2.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=8, role_id="agent")  # agent lacks sla.manage

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 8})
    )
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    res = client.get(
        "/protected",
        headers={
            "x-chatwoot-access-token": "tok-abc",
            "x-chatwoot-client": "client-1",
            "x-chatwoot-uid": "uid-1",
        },
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_rbac_enabled_missing_token_denies(tmp_path):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test3.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    assert client.get("/protected").status_code == 401


@pytest.mark.asyncio
async def test_rbac_enabled_partial_credentials_denies(tmp_path):
    """access-token present but client/uid missing must still deny — a
    devise_token_auth session is only meaningful as the full triplet."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test3b.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    res = client.get("/protected", headers={"x-chatwoot-access-token": "tok-abc"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_rbac_enabled_invalid_token_denies(tmp_path, respx_mock):
    """A present-but-bad token (Chatwoot rejects it) must resolve to a 401
    deny through require_permission's own wiring, not just TokenValidator's
    unit tests."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_test4.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(
            401, json={"success": False, "errors": ["Invalid login credentials"]}
        )
    )
    validator = TokenValidator(settings)
    dep = require_permission("sla.manage", repo=repo, validator=validator, settings=settings)
    client = _app_with_endpoint(dep)

    res = client.get(
        "/protected",
        headers={
            "x-chatwoot-access-token": "tok-bogus",
            "x-chatwoot-client": "client-1",
            "x-chatwoot-uid": "uid-1",
        },
    )
    assert res.status_code == 401


async def test_identity_dependency_refuses_shared_secret_even_with_rbac_off():
    """require_permission falls back to a shared-secret check when RBAC is
    off. This variant must NOT: a shared secret identifies a service, not a
    person, and the token it guards is minted FOR a specific person."""
    import pytest  # noqa: PLC0415
    from fastapi import HTTPException  # noqa: PLC0415

    from chatbot.features.authz.deps import require_permission_with_identity  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "proton_backend_key": "shared-secret"}
    )
    check = require_permission_with_identity("voice.answer", settings=settings)
    with pytest.raises(HTTPException) as exc:
        await check(
            x_api_key="shared-secret",
            x_chatwoot_access_token=None,
            x_chatwoot_client=None,
            x_chatwoot_uid=None,
        )
    assert exc.value.status_code == 401


async def test_identity_dependency_returns_the_resolved_user_id():
    from unittest.mock import AsyncMock  # noqa: PLC0415

    from chatbot.features.authz.deps import require_permission_with_identity  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    validator = AsyncMock()
    validator.resolve_identity.return_value = (17, False)
    repo = AsyncMock()
    repo.permissions_for_user.return_value = {"voice.answer"}

    check = require_permission_with_identity(
        "voice.answer",
        repo=repo,
        validator=validator,
        settings=get_settings().model_copy(update={"rbac_enabled": True}),
    )
    assert (
        await check(
            x_api_key=None,
            x_chatwoot_access_token="tok",
            x_chatwoot_client="cli",
            x_chatwoot_uid="a@b.c",
        )
        == 17
    )


_SESSION_HEADERS = {
    "x-chatwoot-access-token": "tok-sa",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}


def test_user_one_is_always_a_platform_superadmin():
    """The floor. Id 1 set the platform up on every tenant, and hardcoding it
    means no administrative accident — a revoked role, a stripped SuperAdmin
    type — can lock the owner out of the switchboard."""
    assert is_platform_superadmin(1, False) is True


def test_chatwoot_super_admin_type_is_a_platform_superadmin():
    assert is_platform_superadmin(7, True) is True


def test_ordinary_user_is_not_a_platform_superadmin():
    assert is_platform_superadmin(7, False) is False


def test_platform_superadmin_requires_a_session():
    dep = require_platform_superadmin(validator=None, settings=get_settings())
    assert _app_with_endpoint(dep).get("/protected").status_code == 401


def test_platform_superadmin_ignores_the_shared_secret():
    """A shared secret identifies a service, not a person, and the switchboard
    changes what a tenant's product IS. There must be a person."""
    settings = get_settings().model_copy(update={"faq_admin_api_key": "secret123"})
    dep = require_platform_superadmin(validator=None, settings=settings)
    client = _app_with_endpoint(dep)
    assert client.get("/protected", headers={"x-api-key": "secret123"}).status_code == 401


@pytest.mark.asyncio
async def test_platform_superadmin_denies_an_ordinary_user(respx_mock):
    settings = get_settings()
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 7, "type": None})
    )
    dep = require_platform_superadmin(validator=TokenValidator(settings), settings=settings)
    client = _app_with_endpoint(dep)
    assert client.get("/protected", headers=_SESSION_HEADERS).status_code == 403


@pytest.mark.asyncio
async def test_platform_superadmin_allows_user_one_without_the_type(respx_mock):
    settings = get_settings()
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 1, "type": None})
    )
    dep = require_platform_superadmin(validator=TokenValidator(settings), settings=settings)
    client = _app_with_endpoint(dep)
    assert client.get("/protected", headers=_SESSION_HEADERS).status_code == 200


@pytest.mark.asyncio
async def test_platform_superadmin_allows_the_super_admin_type(respx_mock):
    settings = get_settings()
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9, "type": "SuperAdmin"})
    )
    dep = require_platform_superadmin(validator=TokenValidator(settings), settings=settings)
    client = _app_with_endpoint(dep)
    assert client.get("/protected", headers=_SESSION_HEADERS).status_code == 200


@pytest.mark.asyncio
async def test_super_admin_holds_every_permission_without_a_role(tmp_path, respx_mock):
    """Otherwise the platform owner cannot open Roles & Permissions on a tenant
    where nobody ever assigned them a role — which is most tenants. Note there
    is deliberately NO assign_role call here."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_superadmin.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9, "type": "SuperAdmin"})
    )
    dep = require_permission(
        "roles.manage", repo=repo, validator=TokenValidator(settings), settings=settings
    )
    client = _app_with_endpoint(dep)
    assert client.get("/protected", headers=_SESSION_HEADERS).status_code == 200


@pytest.mark.asyncio
async def test_ordinary_user_with_no_role_is_still_denied(tmp_path, respx_mock):
    """The regression guard for the bypass above: it must key on superadmin
    status, not merely on having survived token resolution."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/deps_noroleuser.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 42, "type": None})
    )
    dep = require_permission(
        "roles.manage", repo=repo, validator=TokenValidator(settings), settings=settings
    )
    client = _app_with_endpoint(dep)
    assert client.get("/protected", headers=_SESSION_HEADERS).status_code == 403
