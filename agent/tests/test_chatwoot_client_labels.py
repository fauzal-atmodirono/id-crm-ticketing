import json

import httpx
import respx

from app.clients.chatwoot import ChatwootClient

BASE = "http://chatwoot-rails:3000"


def _client() -> ChatwootClient:
    return ChatwootClient(base_url=BASE, api_access_token="t", account_id=1)


@respx.mock
async def test_add_labels_unions_with_existing():
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/5/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["existing"]})
    )
    post = respx.post(f"{BASE}/api/v1/accounts/1/conversations/5/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["existing", "category_battery"]})
    )
    client = _client()
    await client.add_labels(5, ["category_battery"])
    body = json.loads(post.calls.last.request.content)
    assert set(body["labels"]) == {"existing", "category_battery"}
    await client.aclose()


@respx.mock
async def test_add_labels_posts_new_when_get_fails():
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/9/labels").mock(
        return_value=httpx.Response(500)
    )
    post = respx.post(f"{BASE}/api/v1/accounts/1/conversations/9/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["category_sales"]})
    )
    client = _client()
    await client.add_labels(9, ["category_sales"])
    body = json.loads(post.calls.last.request.content)
    assert body["labels"] == ["category_sales"]
    await client.aclose()
