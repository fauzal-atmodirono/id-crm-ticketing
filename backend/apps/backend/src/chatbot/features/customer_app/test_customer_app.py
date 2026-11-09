"""Mock "Proton e.MAS customer app" (demo): persona allow-list + chat routes.

Chatwoot is mocked with respx; nothing here reaches a live system. All
numbers are the fake +600000001xx persona numbers.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.customer_app.channel import CustomerAppChannel
from chatbot.features.customer_app.personas import (
    PERSONAS,
    allowed_persona,
    contact_name,
)
from chatbot.features.customer_app.router import build_customer_app_router, customer_view
from chatbot.platform.config import Settings

CW = "http://chatwoot.test/api/v1/accounts/1"
ORIGIN = "https://console.test"
REPO = Path(__file__).resolve().parents[7]
# The marker the app stamps on the conversations it opens (its own only).
OWN: dict[str, Any] = {
    "additional_attributes": {"demo_customer_app": "ahmad"},
    "labels": ["demo_persona", "audit_test"],
}


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "demo_customer_app_enabled": True,
        "chatwoot_api_url": "http://chatwoot.test",
        "chatwoot_api_token": "test-token",
        "chatwoot_account_id": 1,
        "chatwoot_inbox_id": 1,
        "frontend_origins": [ORIGIN],
    }
    base.update(over)
    return Settings(**base)


def _client(settings: Settings | None = None) -> TestClient:
    s = settings or _settings()
    app = FastAPI()
    app.include_router(build_customer_app_router(s, CustomerAppChannel(s)))
    return TestClient(app, headers={"Origin": ORIGIN})


def _contact(
    cid: int = 7, name: str = "[DEMO] Ahmad", phone: str = "+60000000101"
) -> dict[str, Any]:
    return {"id": cid, "name": name, "phone_number": phone}


def _no_writes(router: respx.Router) -> None:
    writes = [c for c in router.calls if c.request.method != "GET"]
    assert writes == []


# --------------------------------------------------------------------------
# Persona allow-list
# --------------------------------------------------------------------------


def test_allow_list_is_the_personas_with_a_number() -> None:
    ids = {p.id for p in PERSONAS}
    assert ids == {"ahmad", "nurul", "lim", "farid", "new"}
    assert allowed_persona("ahmad") is not None
    assert allowed_persona("ahmad").number == "+60000000101"  # type: ignore[union-attr]
    for bad in ("unknown", "custom", "", "+60000000101", "AHMAD ", None):
        assert allowed_persona(bad) is None


def test_every_persona_number_is_fake() -> None:
    for p in PERSONAS:
        assert re.fullmatch(r"\+600000001\d\d", p.number), p.id


def test_contact_name_always_carries_the_demo_tag() -> None:
    for p in PERSONAS:
        assert contact_name(p).startswith("[DEMO] ")
    assert contact_name(allowed_persona("new")) == "[DEMO] New caller"  # type: ignore[arg-type]
    assert contact_name(allowed_persona("ahmad")) == "[DEMO] Ahmad"  # type: ignore[arg-type]


def test_server_copy_matches_the_shared_personas_file() -> None:
    shared = REPO / ".superpowers/sdd/2026-11-09-phone-caller-context/personas.json"
    if not shared.exists():
        pytest.skip("shared personas.json not in this checkout")
    here = Path(__file__).with_name("personas.json")
    assert json.loads(here.read_text()) == json.loads(shared.read_text())


def test_server_copy_matches_the_phone_tab_personas_ts() -> None:
    ts = REPO / "backend/apps/frontend/src/features/phone/personas.ts"
    if not ts.exists():
        pytest.skip("frontend not in this checkout")
    pairs = dict(re.findall(r"id: '([a-z]+)',.*?number: '([^']*)'", ts.read_text(), re.S))
    assert {p.id: p.number for p in PERSONAS} == {k: v for k, v in pairs.items() if v}


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def test_flag_off_is_404() -> None:
    c = _client(_settings(demo_customer_app_enabled=False))
    assert c.get("/customer-app/messages", params={"persona": "ahmad"}).status_code == 404
    assert (
        c.post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"}).status_code == 404
    )


@respx.mock
@pytest.mark.parametrize("persona", ["unknown", "custom", "+60000000101", "bob"])
def test_persona_outside_the_allow_list_is_refused(persona: str) -> None:
    c = _client()
    assert c.get("/customer-app/messages", params={"persona": persona}).status_code == 403
    res = c.post("/customer-app/messages", data={"persona": persona, "text": "hi"})
    assert res.status_code == 403
    assert respx.calls.call_count == 0


@respx.mock
def test_foreign_origin_is_refused() -> None:
    c = _client()
    res = c.post(
        "/customer-app/messages",
        data={"persona": "ahmad", "text": "hi"},
        headers={"Origin": "https://evil.test"},
    )
    assert res.status_code == 403
    res = c.post(
        "/customer-app/messages", data={"persona": "ahmad", "text": "hi"}, headers={"Origin": ""}
    )
    assert res.status_code == 403
    assert respx.calls.call_count == 0


@respx.mock
def test_no_inbox_or_token_is_503() -> None:
    c = _client(_settings(chatwoot_inbox_id=0))
    assert (
        c.post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"}).status_code == 503
    )
    c = _client(_settings(chatwoot_api_token=""))
    assert (
        c.post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"}).status_code == 503
    )
    assert respx.calls.call_count == 0


# --------------------------------------------------------------------------
# Posting as the persona
# --------------------------------------------------------------------------


@respx.mock
def test_first_message_creates_the_demo_contact_and_a_pending_conversation() -> None:
    respx.get(f"{CW}/contacts/search").mock(return_value=httpx.Response(200, json={"payload": []}))
    create_contact = respx.post(f"{CW}/contacts").mock(
        return_value=httpx.Response(200, json={"payload": {"contact": _contact()}})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    open_conv = respx.post(f"{CW}/conversations").mock(
        return_value=httpx.Response(200, json={"id": 55})
    )
    post_msg = respx.post(f"{CW}/conversations/55/messages").mock(
        return_value=httpx.Response(200, json={"id": 900})
    )
    labels = respx.post(f"{CW}/conversations/55/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["demo_persona", "audit_test"]})
    )

    res = _client().post("/customer-app/messages", data={"persona": "ahmad", "text": " Hi there "})

    assert res.status_code == 200
    assert res.json() == {"conversation_id": 55}
    body = json.loads(create_contact.calls.last.request.content)
    assert body["name"] == "[DEMO] Ahmad"
    assert body["phone_number"] == "+60000000101"
    assert body["custom_attributes"] == {
        "vehicle_no": "DMO1101",
        "vehicle_model": "Proton X50",
        "demo_seed": "demo_persona",
    }
    conv = json.loads(open_conv.calls.last.request.content)
    assert conv["inbox_id"] == 1 and conv["contact_id"] == 7 and conv["status"] == "pending"
    assert conv["source_id"] == "demo-customer-app-ahmad"
    assert conv["custom_attributes"] == {"demo_seed": "demo_persona"}
    assert conv["additional_attributes"] == {"demo_customer_app": "ahmad"}
    assert json.loads(labels.calls.last.request.content) == {
        "labels": ["demo_persona", "audit_test"]
    }
    msg = json.loads(post_msg.calls.last.request.content)
    assert msg == {"content": "Hi there", "message_type": "incoming"}
    assert create_contact.calls.last.request.headers["api_access_token"] == "test-token"


@respx.mock
def test_new_persona_contact_has_a_demo_name_and_no_attributes() -> None:
    respx.get(f"{CW}/contacts/search").mock(return_value=httpx.Response(200, json={"payload": []}))
    create_contact = respx.post(f"{CW}/contacts").mock(
        return_value=httpx.Response(
            200, json={"payload": {"contact": _contact(9, "[DEMO] New caller")}}
        )
    )
    respx.get(f"{CW}/contacts/9/conversations").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CW}/conversations").mock(return_value=httpx.Response(200, json={"id": 56}))
    respx.post(f"{CW}/conversations/56/messages").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{CW}/conversations/56/labels").mock(return_value=httpx.Response(200, json={}))

    assert (
        _client().post("/customer-app/messages", data={"persona": "new", "text": "hi"}).status_code
        == 200
    )
    body = json.loads(create_contact.calls.last.request.content)
    assert body["name"] == "[DEMO] New caller"
    assert body["custom_attributes"] == {"demo_seed": "demo_persona"}


@respx.mock
def test_existing_contact_and_open_conversation_are_reused() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(
            200,
            json={"payload": [_contact(3, "[DEMO] Someone", "+60000000999"), _contact(7)]},
        )
    )
    create_contact = respx.post(f"{CW}/contacts")
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": [
                    {"id": 40, "inbox_id": 1, "status": "open"},
                    {"id": 44, "inbox_id": 1, "status": "open", "labels": ["demo_persona"]},
                    {"id": 41, "inbox_id": 1, **OWN, "status": "pending"},
                    {"id": 42, "inbox_id": 1, **OWN, "status": "resolved"},
                    {"id": 43, "inbox_id": 9, "status": "open"},
                ]
            },
        )
    )
    open_conv = respx.post(f"{CW}/conversations")
    post_msg = respx.post(f"{CW}/conversations/41/messages").mock(
        return_value=httpx.Response(200, json={})
    )

    res = _client().post("/customer-app/messages", data={"persona": "ahmad", "text": "again"})

    assert res.json() == {"conversation_id": 41}
    assert not create_contact.called and not open_conv.called
    assert post_msg.called


@respx.mock
@pytest.mark.parametrize("name", ["Real Customer", "Ahmad [DEMO]", "[DEMO]Ahmad", "[demo] Ahmad"])
def test_a_number_owned_by_a_non_demo_contact_is_never_used(name: str) -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact(7, name)]})
    )
    res = _client().post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"})
    assert res.status_code == 409
    _no_writes(respx.mock)


@respx.mock
def test_seeded_phone_or_other_open_conversations_are_never_reused() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": [
                    # demo-console seed (labelled, open), a phone call, another
                    # persona's app conversation: none of them is Ahmad's app chat.
                    {
                        "id": 50,
                        "inbox_id": 1,
                        "status": "open",
                        "labels": ["demo_persona"],
                        "custom_attributes": {"demo_seed": "demo_persona"},
                    },
                    {
                        "id": 51,
                        "inbox_id": 1,
                        "status": "open",
                        "additional_attributes": {"session_id": "phone-CA1"},
                    },
                    {
                        "id": 52,
                        "inbox_id": 1,
                        "status": "open",
                        "additional_attributes": {"demo_customer_app": "nurul"},
                    },
                ]
            },
        )
    )
    open_conv = respx.post(f"{CW}/conversations").mock(
        return_value=httpx.Response(200, json={"id": 70})
    )
    respx.post(f"{CW}/conversations/70/labels").mock(return_value=httpx.Response(200, json={}))
    post_msg = respx.post(f"{CW}/conversations/70/messages").mock(
        return_value=httpx.Response(200, json={})
    )

    res = _client().post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"})

    assert res.json() == {"conversation_id": 70}
    assert open_conv.called and post_msg.called


@respx.mock
def test_two_fast_posts_open_only_one_conversation() -> None:
    created: list[dict[str, Any]] = []

    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        side_effect=lambda _request: httpx.Response(200, json={"payload": list(created)})
    )

    async def _create(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)  # the window two unlocked posts would both fall into
        created.append({"id": 80 + len(created), "inbox_id": 1, **OWN, "status": "pending"})
        return httpx.Response(200, json={"id": created[-1]["id"]})

    open_conv = respx.post(f"{CW}/conversations").mock(side_effect=_create)
    respx.post(url__regex=rf"{CW}/conversations/8\d/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(url__regex=rf"{CW}/conversations/8\d/messages").mock(
        return_value=httpx.Response(200, json={})
    )

    settings = _settings()
    app = FastAPI()
    app.include_router(build_customer_app_router(settings, CustomerAppChannel(settings)))

    async def _both() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://app.test", headers={"Origin": ORIGIN}
        ) as client:
            results = await asyncio.gather(
                *(
                    client.post("/customer-app/messages", data={"persona": "ahmad", "text": t})
                    for t in ("one", "two")
                )
            )
        return [r.json()["conversation_id"] for r in results]

    ids = asyncio.run(_both())  # ASGITransport is not intercepted by respx

    assert open_conv.call_count == 1
    assert ids == [80, 80]


@respx.mock
def test_photo_is_posted_as_a_multipart_attachment() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 41, "inbox_id": 1, **OWN, "status": "pending"}]}
        )
    )
    post_msg = respx.post(f"{CW}/conversations/41/messages").mock(
        return_value=httpx.Response(200, json={})
    )

    res = _client().post(
        "/customer-app/messages",
        data={"persona": "ahmad", "text": "this light"},
        files={"file": ("dash.jpg", b"\xff\xd8fakejpeg", "image/jpeg")},
    )

    assert res.status_code == 200
    req = post_msg.calls.last.request
    assert req.headers["content-type"].startswith("multipart/form-data")
    raw = req.content
    assert b'name="attachments[]"; filename="dash.jpg"' in raw
    assert b"fakejpeg" in raw and b"this light" in raw and b"incoming" in raw


@respx.mock
def test_voice_note_wav_is_accepted() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 41, "inbox_id": 1, **OWN, "status": "open"}]}
        )
    )
    post_msg = respx.post(f"{CW}/conversations/41/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    res = _client().post(
        "/customer-app/messages",
        data={"persona": "ahmad"},
        files={"file": ("voice-note.wav", b"RIFFxxxxWAVE", "audio/wav")},
    )
    assert res.status_code == 200
    assert post_msg.called


@respx.mock
def test_upload_type_size_and_empty_checks() -> None:
    c = _client()
    res = c.post(
        "/customer-app/messages",
        data={"persona": "ahmad"},
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert res.status_code == 415
    for bad in ("image/gif", "image/svg+xml", "video/x-msvideo", "audio/flac", "application/pdf"):
        res = c.post(
            "/customer-app/messages",
            data={"persona": "ahmad"},
            files={"file": ("x.bin", b"x", bad)},
        )
        assert res.status_code == 415, bad
    big = b"0" * (15 * 1024 * 1024 + 1)
    res = c.post(
        "/customer-app/messages",
        data={"persona": "ahmad"},
        files={"file": ("big.jpg", big, "image/jpeg")},
    )
    assert res.status_code == 413
    assert (
        c.post("/customer-app/messages", data={"persona": "ahmad", "text": "  "}).status_code == 422
    )
    assert respx.calls.call_count == 0


@respx.mock
def test_chatwoot_failure_is_502() -> None:
    respx.get(f"{CW}/contacts/search").mock(return_value=httpx.Response(500))
    res = _client().post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"})
    assert res.status_code == 502


@respx.mock
def test_dedicated_inbox_setting_wins_over_chatwoot_inbox_id() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 41, "inbox_id": 1, **OWN, "status": "open"}]}
        )
    )
    open_conv = respx.post(f"{CW}/conversations").mock(
        return_value=httpx.Response(200, json={"id": 60})
    )
    respx.post(f"{CW}/conversations/60/messages").mock(return_value=httpx.Response(200, json={}))
    respx.post(f"{CW}/conversations/60/labels").mock(return_value=httpx.Response(200, json={}))
    c = _client(_settings(demo_customer_app_inbox_id=5))
    assert c.post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"}).json() == {
        "conversation_id": 60
    }
    assert json.loads(open_conv.calls.last.request.content)["inbox_id"] == 5


# --------------------------------------------------------------------------
# Reading the thread
# --------------------------------------------------------------------------


@respx.mock
def test_reading_never_creates_anything() -> None:
    respx.get(f"{CW}/contacts/search").mock(return_value=httpx.Response(200, json={"payload": []}))
    res = _client().get("/customer-app/messages", params={"persona": "ahmad"})
    assert res.status_code == 200
    assert res.json() == {"conversation_id": None, "messages": []}
    _no_writes(respx.mock)


@respx.mock
def test_reading_without_an_origin_header_is_allowed_same_origin_get() -> None:
    respx.get(f"{CW}/contacts/search").mock(return_value=httpx.Response(200, json={"payload": []}))
    res = _client().get(
        "/customer-app/messages", params={"persona": "ahmad"}, headers={"Origin": ""}
    )
    assert res.status_code == 200


@respx.mock
def test_thread_shows_customer_and_proton_messages_only() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 41, "inbox_id": 1, **OWN, "status": "open"}]}
        )
    )
    respx.get(f"{CW}/conversations/41/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": [
                    {
                        "id": 1,
                        "message_type": 0,
                        "content": "hi",
                        "created_at": 10,
                        "private": False,
                    },
                    {
                        "id": 2,
                        "message_type": 1,
                        "content": "Hello Ahmad",
                        "created_at": 11,
                        "private": False,
                        "sender": {"type": "agent_bot", "name": "Bot"},
                    },
                    {"id": 3, "message_type": 1, "content": "internal", "private": True},
                    {"id": 4, "message_type": 2, "content": "Assigned to Ali", "private": False},
                    {
                        "id": 5,
                        "message_type": 1,
                        "content": "I can help",
                        "private": False,
                        "sender": {"type": "user", "name": "Agent Ali"},
                        "attachments": [
                            {
                                "file_type": "image",
                                "data_url": "http://cw/x.jpg",
                                "thumb_url": "http://cw/t.jpg",
                            }
                        ],
                    },
                ]
            },
        )
    )
    res = _client().get("/customer-app/messages", params={"persona": "ahmad"})
    body = res.json()
    assert body["conversation_id"] == 41
    assert [m["id"] for m in body["messages"]] == [1, 2, 5]
    assert [m["from"] for m in body["messages"]] == ["customer", "proton", "proton"]
    assert [m["by"] for m in body["messages"]] == ["customer", "ai", "agent"]
    assert body["messages"][2]["attachments"] == [
        {"type": "image", "url": "http://cw/x.jpg", "thumb": "http://cw/t.jpg"}
    ]
    # Agent names are not shown to the customer.
    assert "Ali" not in json.dumps(body["messages"][2])


def test_customer_view_drops_notes_and_activity() -> None:
    assert customer_view({"message_type": 1, "private": True}) is None
    assert customer_view({"message_type": 2}) is None


@respx.mock
def test_a_failed_labels_post_does_not_lose_the_message() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CW}/conversations").mock(return_value=httpx.Response(200, json={"id": 90}))
    respx.post(f"{CW}/conversations/90/labels").mock(return_value=httpx.Response(500))
    post_msg = respx.post(f"{CW}/conversations/90/messages").mock(
        return_value=httpx.Response(200, json={})
    )

    res = _client().post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"})

    assert res.status_code == 200
    assert post_msg.called


@respx.mock
def test_missing_demo_labels_are_merged_back_on_the_next_post() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    own = {**OWN, "labels": ["dealer_petaling_jaya"]}
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 91, "inbox_id": 1, **own, "status": "open"}]}
        )
    )
    respx.get(f"{CW}/conversations/91/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["dealer_petaling_jaya", "audit_test"]})
    )
    labels = respx.post(f"{CW}/conversations/91/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(f"{CW}/conversations/91/messages").mock(return_value=httpx.Response(200, json={}))

    res = _client().post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"})

    assert res.status_code == 200
    assert json.loads(labels.calls.last.request.content) == {
        "labels": ["dealer_petaling_jaya", "audit_test", "demo_persona"]
    }


@respx.mock
def test_labels_already_present_are_not_rewritten() -> None:
    respx.get(f"{CW}/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [_contact()]})
    )
    respx.get(f"{CW}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 92, "inbox_id": 1, **OWN, "status": "open"}]}
        )
    )
    respx.post(f"{CW}/conversations/92/messages").mock(return_value=httpx.Response(200, json={}))

    assert (
        _client()
        .post("/customer-app/messages", data={"persona": "ahmad", "text": "hi"})
        .status_code
        == 200
    )
    assert not [c for c in respx.calls if c.request.url.path.endswith("/labels")]
