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
