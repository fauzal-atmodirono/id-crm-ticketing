import httpx
import respx

from app.clients.chatwoot import ChatwootClient

BASE = "http://chatwoot-rails:3000"


def _client() -> ChatwootClient:
    return ChatwootClient(base_url=BASE, api_access_token="t", account_id=1)


@respx.mock
async def test_list_conversations_passes_params_and_returns_json():
    route = respx.get(f"{BASE}/api/v1/accounts/1/conversations").mock(
        return_value=httpx.Response(200, json={"data": {"payload": [{"id": 7}]}})
    )
    client = _client()
    data = await client.list_conversations(status="open", assignee_type="all")
    assert data["data"]["payload"][0]["id"] == 7
    assert route.calls.last.request.url.params["status"] == "open"
    assert route.calls.last.request.url.params["assignee_type"] == "all"
    await client.aclose()


@respx.mock
async def test_get_inbox_returns_json():
    respx.get(f"{BASE}/api/v1/accounts/1/inboxes/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "channel_type": "Channel::Email"})
    )
    client = _client()
    inbox = await client.get_inbox(5)
    assert inbox["channel_type"] == "Channel::Email"
    await client.aclose()


@respx.mock
async def test_set_custom_attributes_posts_body():
    route = respx.post(
        f"{BASE}/api/v1/accounts/1/conversations/9/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = _client()
    await client.set_custom_attributes(9, {"lifecycle_state": "idle_warned"})
    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body == {"custom_attributes": {"lifecycle_state": "idle_warned"}}
    await client.aclose()
