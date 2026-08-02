import httpx
import pytest

from chatbot.features.authz.chatwoot_role_mirror import (
    ChatwootRoleMirror,
    ChatwootRoleMirrorError,
)
from chatbot.platform.config import get_settings


@pytest.fixture
def settings():
    return get_settings()


async def test_ensure_custom_role_creates_when_no_existing_id(settings, respx_mock):
    respx_mock.post(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles"
    ).mock(return_value=httpx.Response(200, json={"id": 7}))
    mirror = ChatwootRoleMirror(settings)
    result = await mirror.ensure_custom_role(None, "Leader", "desc", ["conversation_manage"])
    assert result == 7


async def test_ensure_custom_role_updates_when_existing_id(settings, respx_mock):
    respx_mock.patch(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles/7"
    ).mock(return_value=httpx.Response(200, json={"id": 7}))
    mirror = ChatwootRoleMirror(settings)
    result = await mirror.ensure_custom_role(7, "Leader", "desc", ["contact_manage"])
    assert result == 7


async def test_ensure_custom_role_raises_on_http_error(settings, respx_mock):
    respx_mock.post(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles"
    ).mock(return_value=httpx.Response(500))
    mirror = ChatwootRoleMirror(settings)
    with pytest.raises(ChatwootRoleMirrorError):
        await mirror.ensure_custom_role(None, "Leader", "desc", [])


async def test_ensure_custom_role_raises_on_missing_id_in_2xx_response(settings, respx_mock):
    respx_mock.post(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles"
    ).mock(return_value=httpx.Response(200, json={"name": "Leader"}))
    mirror = ChatwootRoleMirror(settings)
    with pytest.raises(ChatwootRoleMirrorError):
        await mirror.ensure_custom_role(None, "Leader", "desc", [])


async def test_ensure_custom_role_raises_on_non_numeric_id_in_2xx_response(settings, respx_mock):
    respx_mock.post(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles"
    ).mock(return_value=httpx.Response(200, json={"id": "not-a-number"}))
    mirror = ChatwootRoleMirror(settings)
    with pytest.raises(ChatwootRoleMirrorError):
        await mirror.ensure_custom_role(None, "Leader", "desc", [])


async def test_delete_custom_role_raises_on_http_error(settings, respx_mock):
    respx_mock.delete(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles/7"
    ).mock(return_value=httpx.Response(404))
    mirror = ChatwootRoleMirror(settings)
    with pytest.raises(ChatwootRoleMirrorError):
        await mirror.delete_custom_role(7)


async def test_delete_custom_role_succeeds(settings, respx_mock):
    respx_mock.delete(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/custom_roles/7"
    ).mock(return_value=httpx.Response(200))
    mirror = ChatwootRoleMirror(settings)
    await mirror.delete_custom_role(7)  # no exception


async def test_set_agent_custom_role_sends_top_level_param(settings, respx_mock):
    route = respx_mock.patch(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/agents/9"
    ).mock(return_value=httpx.Response(200, json={}))
    mirror = ChatwootRoleMirror(settings)
    await mirror.set_agent_custom_role(9, 7)
    assert route.calls.last.request.content
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == {"custom_role_id": 7}


async def test_set_agent_custom_role_clears_with_none(settings, respx_mock):
    route = respx_mock.patch(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/agents/9"
    ).mock(return_value=httpx.Response(200, json={}))
    mirror = ChatwootRoleMirror(settings)
    await mirror.set_agent_custom_role(9, None)
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == {"custom_role_id": None}


async def test_set_agent_custom_role_raises_on_http_error(settings, respx_mock):
    respx_mock.patch(
        f"{settings.chatwoot_api_url}/api/v1/accounts/{settings.chatwoot_account_id}/agents/9"
    ).mock(return_value=httpx.Response(422))
    mirror = ChatwootRoleMirror(settings)
    with pytest.raises(ChatwootRoleMirrorError):
        await mirror.set_agent_custom_role(9, 7)
