"""Feature B wiring: the contact write merges, and the orchestrator dispatches."""

import json as _json

import httpx
import respx

from app.clients.chatwoot import ChatwootClient


def _client() -> ChatwootClient:
    return ChatwootClient(
        base_url="http://cw.test", api_access_token="t", account_id=1
    )


@respx.mock
async def test_merge_preserves_attributes_it_was_not_given():
    respx.get("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": {
                    "id": 7,
                    "custom_attributes": {
                        "risk_profile": "Konservatif",
                        "holdings": "BBCA, BBRI",
                    },
                }
            },
        )
    )
    route = respx.put("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    wrote = await client.merge_contact_attributes(7, {"investor_horizon": "> 10 tahun"})
    await client.aclose()

    assert wrote is True
    body = _json.loads(route.calls[0].request.content)["custom_attributes"]
    # The portfolio the contact already carried must survive the write.
    assert body["risk_profile"] == "Konservatif"
    assert body["holdings"] == "BBCA, BBRI"
    assert body["investor_horizon"] == "> 10 tahun"


@respx.mock
async def test_merge_writes_nothing_when_given_nothing():
    get = respx.get("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={"payload": {"id": 7}})
    )
    put = respx.put("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    wrote = await client.merge_contact_attributes(7, {})
    await client.aclose()

    assert wrote is False
    assert not put.called
    assert not get.called


@respx.mock
async def test_merge_survives_a_contact_with_no_attributes_yet():
    respx.get("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={"payload": {"id": 7}})
    )
    route = respx.put("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    wrote = await client.merge_contact_attributes(7, {"investor_experience": "Pemula"})
    await client.aclose()

    assert wrote is True
    body = _json.loads(route.calls[0].request.content)["custom_attributes"]
    assert body == {"investor_experience": "Pemula"}
