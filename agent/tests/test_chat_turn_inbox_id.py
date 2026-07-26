"""Tests: ProtonConfigClient.chat_turn forwards inbox_id to /chat/turn.

TDD test for Task 5: inbox_id is included in the POST body when set,
and omitted (or not equal to 3) when None (default-preserving).
"""
import httpx
import respx

from app.clients.proton import ProtonConfigClient


@respx.mock
async def test_chat_turn_includes_inbox_id_when_set():
    route = respx.post("http://backend/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "hi"})
    )
    c = ProtonConfigClient(base_url="http://backend", api_key="k")
    await c.chat_turn("crm-1", "hello", inbox_id=3)
    body = route.calls.last.request.content.decode()
    assert '"inbox_id": 3' in body or '"inbox_id":3' in body


@respx.mock
async def test_chat_turn_omits_or_nulls_inbox_id_when_none():
    route = respx.post("http://backend/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "hi"})
    )
    c = ProtonConfigClient(base_url="http://backend", api_key="k")
    await c.chat_turn("crm-1", "hello")
    # default None -> key omitted entirely; must NOT send a bogus inbox
    body = route.calls.last.request.content.decode()
    assert '"inbox_id": 3' not in body
    assert "inbox_id" not in body
