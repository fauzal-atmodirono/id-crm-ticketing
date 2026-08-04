"""Customer-sent WhatsApp video reaches Gemini as a third media slot alongside
audio and image, and an oversized clip is skipped rather than breaking the turn.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from app.clients.proton import ProtonConfigClient
from app.config import get_settings
from app.services import orchestrator

PROTON = "http://proton-backend:8080"
CDN = "http://cw-assets"


def _make_proton_client() -> ProtonConfigClient:
    inner = httpx.AsyncClient(base_url=PROTON, headers={"x-api-key": "testkey"})
    return ProtonConfigClient(base_url=PROTON, api_key="testkey", client=inner, ttl=0.0)


def _message_with_video(content: str = "tengok video ni") -> list[dict]:
    return [
        {
            "id": 1,
            "content": content,
            "message_type": 0,
            "private": False,
            "attachments": [{"file_type": "video", "data_url": f"{CDN}/clip.mp4"}],
        }
    ]


class _FakeChatwoot:
    """Only the methods _process_via_chat_agent touches on the happy path."""

    async def get_inbox(self, inbox_id):
        return {"greeting_enabled": False}

    async def create_message(self, *args, **kwargs):
        return None

    async def toggle_status(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _enable_media(monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_media_understanding_enabled", True)


@respx.mock
async def test_video_attachment_is_sent_to_chat_turn(monkeypatch):
    respx.get(f"{CDN}/clip.mp4").mock(
        return_value=httpx.Response(200, content=b"MP4BYTES", headers={"content-type": "video/mp4"})
    )
    turn = respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "ok", "handoff": None, "products": []})
    )
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    await orchestrator._process_via_chat_agent(
        conversation_id=42,
        message_list=_message_with_video(),
        effective_mode="auto",
        chatwoot=_FakeChatwoot(),
        handoff_message="one moment",
        inbox_id=7,
    )

    body = turn.calls.last.request.content.decode()
    assert base64.b64encode(b"MP4BYTES").decode() in body
    assert "video/mp4" in body


@respx.mock
async def test_oversized_video_is_skipped_but_turn_proceeds(monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_video_max_bytes", 4)
    respx.get(f"{CDN}/clip.mp4").mock(
        return_value=httpx.Response(200, content=b"MUCHTOOBIG", headers={"content-type": "video/mp4"})
    )
    turn = respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "ok", "handoff": None, "products": []})
    )
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    await orchestrator._process_via_chat_agent(
        conversation_id=42,
        message_list=_message_with_video(),
        effective_mode="auto",
        chatwoot=_FakeChatwoot(),
        handoff_message="one moment",
        inbox_id=7,
    )

    body = turn.calls.last.request.content.decode()
    assert "video_base64" not in body
    assert "tengok video ni" in body


@respx.mock
async def test_flag_off_does_not_fetch_video(monkeypatch):
    monkeypatch.setattr(get_settings(), "whatsapp_media_understanding_enabled", False)
    fetch = respx.get(f"{CDN}/clip.mp4")
    respx.post(f"{PROTON}/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "ok", "handoff": None, "products": []})
    )
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    await orchestrator._process_via_chat_agent(
        conversation_id=42,
        message_list=_message_with_video(),
        effective_mode="auto",
        chatwoot=_FakeChatwoot(),
        handoff_message="one moment",
        inbox_id=7,
    )

    assert not fetch.called
