import time

import httpx
import pytest
import respx

from chatbot.features.authz.identity import TokenValidator
from chatbot.platform.config import get_settings


@pytest.mark.asyncio
@respx.mock
async def test_resolve_user_id_valid_token():
    settings = get_settings()
    route = respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 7, "name": "Agent Smith"})
    )
    validator = TokenValidator(settings)
    user_id = await validator.resolve_user_id("tok-abc", "client-1", "uid-1")
    assert user_id == 7
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_resolve_user_id_invalid_token_returns_none():
    settings = get_settings()
    respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(401)
    )
    validator = TokenValidator(settings)
    assert await validator.resolve_user_id("bad-token", "client-1", "uid-1") is None


@pytest.mark.asyncio
@respx.mock
async def test_resolve_user_id_network_error_returns_none_not_raises():
    settings = get_settings()
    respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        side_effect=httpx.ConnectError("down")
    )
    validator = TokenValidator(settings)
    assert await validator.resolve_user_id("tok-abc", "client-1", "uid-1") is None


@pytest.mark.asyncio
@respx.mock
async def test_result_is_cached_within_ttl():
    settings = get_settings()
    route = respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    validator = TokenValidator(settings, cache_ttl_seconds=60)
    await validator.resolve_user_id("tok-abc", "client-1", "uid-1")
    await validator.resolve_user_id("tok-abc", "client-1", "uid-1")
    assert route.call_count == 1  # second call served from cache


@pytest.mark.asyncio
@respx.mock
async def test_different_client_or_uid_is_not_cache_hit():
    settings = get_settings()
    route = respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    validator = TokenValidator(settings, cache_ttl_seconds=60)
    await validator.resolve_user_id("tok-abc", "client-1", "uid-1")
    await validator.resolve_user_id("tok-abc", "client-2", "uid-1")
    assert route.call_count == 2  # different (token, client, uid) triplet, not a cache hit


@pytest.mark.asyncio
@respx.mock
async def test_cache_expires_after_ttl(monkeypatch):
    settings = get_settings()
    route = respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    validator = TokenValidator(settings, cache_ttl_seconds=0.01)
    await validator.resolve_user_id("tok-abc", "client-1", "uid-1")
    time.sleep(0.02)
    await validator.resolve_user_id("tok-abc", "client-1", "uid-1")
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_resolve_identity_reports_super_admin_type():
    settings = get_settings()
    respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 7, "type": "SuperAdmin"})
    )
    validator = TokenValidator(settings)
    assert await validator.resolve_identity("tok-a", "client-1", "uid-1") == (7, True)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_identity_treats_null_type_as_not_super_admin():
    """A regular Chatwoot user's `type` is null, NOT the string "User". A
    truthiness check would pass here and hand the switchboard to everyone."""
    settings = get_settings()
    respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9, "type": None})
    )
    validator = TokenValidator(settings)
    assert await validator.resolve_identity("tok-b", "client-1", "uid-1") == (9, False)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_identity_omitted_type_is_not_super_admin():
    """Some Chatwoot versions omit the key entirely rather than sending null."""
    settings = get_settings()
    respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    validator = TokenValidator(settings)
    assert await validator.resolve_identity("tok-c", "client-1", "uid-1") == (9, False)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_identity_rejects_a_truthy_non_superadmin_type():
    """The test that actually separates equality from truthiness. A
    `bool(payload.get("type"))` implementation passes every other test in
    this file — None and "SuperAdmin" agree under both readings — and fails
    only here. Chatwoot emits only null/"SuperAdmin" today, so this guards
    the invariant rather than a live payload."""
    settings = get_settings()
    respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 11, "type": "Agent"})
    )
    validator = TokenValidator(settings)
    assert await validator.resolve_identity("tok-g", "client-1", "uid-1") == (11, False)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_identity_caches_both_halves():
    settings = get_settings()
    route = respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 1, "type": "SuperAdmin"})
    )
    validator = TokenValidator(settings)
    first = await validator.resolve_identity("tok-d", "client-1", "uid-1")
    second = await validator.resolve_identity("tok-d", "client-1", "uid-1")
    assert route.call_count == 1
    assert first == second == (1, True)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_user_id_still_returns_a_bare_int():
    """Existing callers pass an int straight into repo.permissions_for_user."""
    settings = get_settings()
    respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 4, "type": None})
    )
    validator = TokenValidator(settings)
    assert await validator.resolve_user_id("tok-e", "client-1", "uid-1") == 4


@pytest.mark.asyncio
@respx.mock
async def test_resolve_identity_returns_none_on_http_failure():
    settings = get_settings()
    respx.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(401)
    )
    validator = TokenValidator(settings)
    assert await validator.resolve_identity("tok-f", "client-1", "uid-1") is None
