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
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "custom_attributes": {}})
    )
    route = respx.post(
        f"{BASE}/api/v1/accounts/1/conversations/9/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = _client()
    await client.set_custom_attributes(9, {"lifecycle_state": "idle_warned"})
    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body == {"custom_attributes": {"lifecycle_state": "idle_warned"}}
    await client.aclose()


# --- set_custom_attributes merges, it does not replace ----------------------
# Chatwoot's custom-attributes endpoint ASSIGNS the whole object
# (ConversationsController#custom_attributes), so writing one key without
# reading first erases every other key on the conversation. This was a live
# clobber of real customers' case_category/vehicle_model via
# lifecycle._mirror_state and categorize, and it wiped the demo seeder's
# demo_seed marker off every seeded conversation (via
# sync.maybe_stamp_dealer_escalation) as soon as its labels were posted.


@respx.mock
async def test_set_custom_attributes_preserves_existing_keys():
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 9,
                "custom_attributes": {
                    "demo_seed": "seed-batch-1",
                    "case_category": "Sales",
                    "vehicle_model": "e.MAS 7",
                },
            },
        )
    )
    route = respx.post(
        f"{BASE}/api/v1/accounts/1/conversations/9/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = _client()
    await client.set_custom_attributes(9, {"dealer_escalated_at": "2026-08-04T00:00:00+00:00"})
    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body == {
        "custom_attributes": {
            "demo_seed": "seed-batch-1",
            "case_category": "Sales",
            "vehicle_model": "e.MAS 7",
            "dealer_escalated_at": "2026-08-04T00:00:00+00:00",
        }
    }
    await client.aclose()


@respx.mock
async def test_set_custom_attributes_new_values_win_on_conflict():
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(
            200, json={"id": 9, "custom_attributes": {"lifecycle_state": "idle_warned", "keep": "me"}}
        )
    )
    route = respx.post(
        f"{BASE}/api/v1/accounts/1/conversations/9/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = _client()
    await client.set_custom_attributes(9, {"lifecycle_state": "resolved"})
    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body == {"custom_attributes": {"lifecycle_state": "resolved", "keep": "me"}}
    await client.aclose()


@respx.mock
async def test_set_custom_attributes_falls_back_to_the_new_keys_when_the_read_fails():
    # Same fail-open posture as add_labels: a read failure must not turn into
    # "the attribute never gets set" for a best-effort background write.
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    route = respx.post(
        f"{BASE}/api/v1/accounts/1/conversations/9/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = _client()
    await client.set_custom_attributes(9, {"lifecycle_state": "idle_warned"})
    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body == {"custom_attributes": {"lifecycle_state": "idle_warned"}}
    await client.aclose()


@respx.mock
async def test_set_custom_attributes_tolerates_a_null_custom_attributes_on_read():
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "custom_attributes": None})
    )
    route = respx.post(
        f"{BASE}/api/v1/accounts/1/conversations/9/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = _client()
    await client.set_custom_attributes(9, {"lifecycle_state": "idle_warned"})
    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body == {"custom_attributes": {"lifecycle_state": "idle_warned"}}
    await client.aclose()
