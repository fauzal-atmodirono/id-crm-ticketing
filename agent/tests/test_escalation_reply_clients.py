"""Client support for the escalation reply loop."""

import httpx
import respx

from app.clients.chatwoot import ChatwootClient
from app.clients.proton import ProtonConfigClient

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


@respx.mock
async def test_get_escalation_contacts_maps_email_to_name():
    respx.get(f"{PROTON}/escalation/contacts").mock(
        return_value=httpx.Response(
            200,
            json={"contacts": [
                {"email": "Pic@Test", "name": "Aduy", "kind": "pic"},
                {"email": "a@test", "name": "komang", "kind": "dealer"},
            ]},
        )
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_escalation_contacts() == {"pic@test": "Aduy", "a@test": "komang"}
    await client.aclose()


@respx.mock
async def test_get_escalation_contacts_returns_none_on_error():
    respx.get(f"{PROTON}/escalation/contacts").mock(return_value=httpx.Response(500))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_escalation_contacts() is None
    await client.aclose()


@respx.mock
async def test_suggest_reply_returns_draft():
    respx.post(f"{PROTON}/assist/suggest").mock(
        return_value=httpx.Response(200, json={"draft": "Dear customer, ...", "sources": []})
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.suggest_reply("42", ["hello"]) == "Dear customer, ..."
    await client.aclose()


@respx.mock
async def test_suggest_reply_returns_none_on_error():
    respx.post(f"{PROTON}/assist/suggest").mock(return_value=httpx.Response(503))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.suggest_reply("42", ["hello"]) is None
    await client.aclose()


@respx.mock
async def test_create_message_sends_message_type_when_given():
    route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    client = ChatwootClient(CHATWOOT, "token", 1)
    await client.create_message(42, "hi", private=False, message_type="incoming")
    assert route.calls.last.request.read().decode().count("incoming") == 1
    await client.aclose()


@respx.mock
async def test_create_message_omits_message_type_by_default():
    route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 7})
    )
    client = ChatwootClient(CHATWOOT, "token", 1)
    await client.create_message(42, "hi")
    assert "message_type" not in route.calls.last.request.read().decode()
    await client.aclose()
