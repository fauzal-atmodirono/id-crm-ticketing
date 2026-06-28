from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.phone.live_events import AudioOut, LiveEvent
from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeLive:
    def __init__(self, scripted: list[LiveEvent]) -> None:
        self._scripted = scripted

    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(self, call_id: str, name: str, response: dict[str, object]) -> None: ...

    async def events(self) -> AsyncIterator[LiveEvent]:
        for e in self._scripted:
            yield e


def _orch() -> MagicMock:
    orch = MagicMock()
    orch._settings = get_settings()
    orch._knowledge_port = MagicMock()
    orch._knowledge_port.search_kb = lambda _q, _limit=2: []
    orch._conversation_log_port = MagicMock()
    return orch


def test_stream_plays_live_audio_back_to_twilio() -> None:
    @asynccontextmanager
    async def factory(_settings: Any, _system_instruction: Any, _tools: Any) -> AsyncIterator[_FakeLive]:
        yield _FakeLive([AudioOut(b"\x00\x00" * 240)])

    orch = _orch()
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch, live_session_factory=factory))
    client = TestClient(app)

    with client.websocket_connect("/voice/phone/stream") as ws:
        ws.send_json({"event": "start", "start": {"streamSid": "S1", "callSid": "C1"}})
        # the fake live emits one AudioOut → bridge should send a media frame back
        msg = ws.receive_json()
        assert msg["event"] == "media"
        assert msg["streamSid"] == "S1"
