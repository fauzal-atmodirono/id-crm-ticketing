from __future__ import annotations

import httpx
import respx

from chatbot.features.assist.chatwoot_context import ChatwootContextClient
from chatbot.platform.config import Settings


def _settings() -> Settings:
    return Settings(
        chatwoot_api_url="http://cw.local",
        chatwoot_api_token="tok",
        chatwoot_account_id=1,
        chatwoot_enabled=True,
    )


def _base() -> str:
    return "http://cw.local/api/v1/accounts/1"


@respx.mock
async def test_get_transcript_includes_private_notes() -> None:
    respx.get(f"{_base()}/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"sender": {"id": 7}},
                "messages": [
                    {"content": "my battery died", "message_type": 0, "private": False},
                    {"content": "internal: check warranty", "message_type": 1, "private": True},
                    {"content": "we will help", "message_type": 1, "private": False},
                    {"content": "conversation opened", "message_type": 2, "private": False},
                ],
            },
        )
    )
    client = ChatwootContextClient(_settings())
    turns = await client.get_transcript("42")
    # activity messages (type 2) are dropped; private notes kept and flagged
    assert turns == [
        {"sender": "customer", "private": False, "content": "my battery died"},
        {"sender": "agent", "private": True, "content": "internal: check warranty"},
        {"sender": "agent", "private": False, "content": "we will help"},
    ]


@respx.mock
async def test_get_contact_returns_attributes() -> None:
    respx.get(f"{_base()}/conversations/42").mock(
        return_value=httpx.Response(200, json={"meta": {"sender": {"id": 7}}, "messages": []})
    )
    respx.get(f"{_base()}/contacts/7").mock(
        return_value=httpx.Response(
            200,
            json={"payload": {
                "id": 7, "name": "Aiman", "email": "a@x.my", "phone_number": "+60",
                "custom_attributes": {"plan": "premium"},
            }},
        )
    )
    client = ChatwootContextClient(_settings())
    contact = await client.get_contact("42")
    assert contact["name"] == "Aiman"
    assert contact["custom_attributes"] == {"plan": "premium"}


@respx.mock
async def test_list_contact_conversations_excludes_current() -> None:
    respx.get(f"{_base()}/conversations/42").mock(
        return_value=httpx.Response(200, json={"meta": {"sender": {"id": 7}}, "messages": []})
    )
    respx.get(f"{_base()}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200,
            json={"payload": [
                {"id": 42, "status": "open", "last_activity_at": 100, "labels": []},
                {"id": 30, "status": "resolved", "last_activity_at": 90, "labels": ["billing"]},
            ]},
        )
    )
    client = ChatwootContextClient(_settings())
    convs = await client.list_contact_conversations("42")
    assert [c["id"] for c in convs] == [30]


async def test_disabled_short_circuits() -> None:
    s = Settings(chatwoot_enabled=False)
    client = ChatwootContextClient(s)
    assert await client.get_transcript("42") == []
    assert await client.get_contact("42") == {}
    assert await client.list_contact_conversations("42") == []


@respx.mock
async def test_http_failure_returns_empty() -> None:
    respx.get(f"{_base()}/conversations/42").mock(side_effect=httpx.ConnectError("boom"))
    client = ChatwootContextClient(_settings())
    assert await client.get_transcript("42") == []
    assert await client.get_contact("42") == {}
    assert await client.list_contact_conversations("42") == []
