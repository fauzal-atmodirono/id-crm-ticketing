from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.phone.live_events import AudioOut, LiveEvent
from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import Settings, get_settings
from chatbot.platform.server import create_app


class _FakeLive:
    def __init__(self, scripted: list[LiveEvent]) -> None:
        self._scripted = scripted

    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(
        self, call_id: str, name: str, response: dict[str, object]
    ) -> None: ...

    async def events(self) -> AsyncIterator[LiveEvent]:
        for e in self._scripted:
            yield e


def _orch() -> MagicMock:
    orch = MagicMock()
    orch._settings = get_settings()
    # The `submit_csat` assertion below is about the CSAT tool set, so pin NPS
    # sampling off rather than inheriting the environment: the all-flags-ON
    # gate run exports NPS_SAMPLE_RATE=1.0, under which the call is correctly
    # offered `submit_nps` INSTEAD of `submit_csat` (never both). The sampled
    # phone path has its own coverage in test_nps_wiring.py.
    orch._settings.nps_sample_rate = 0.0
    orch._knowledge_port = MagicMock()
    orch._knowledge_port.search_kb = lambda _q, _limit=2: []
    orch._conversation_log_port = MagicMock()
    return orch


def test_stream_plays_live_audio_back_to_twilio() -> None:
    @asynccontextmanager
    async def factory(
        _settings: Any, _system_instruction: Any, _tools: Any
    ) -> AsyncIterator[_FakeLive]:
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


def test_stream_opens_live_with_all_three_tools_and_handoff_instruction() -> None:
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def factory(
        _settings: Any, system_instruction: Any, tools: Any
    ) -> AsyncIterator[_FakeLive]:
        captured["tools"] = tools
        captured["instruction"] = system_instruction
        yield _FakeLive([])

    orch = _orch()
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch, live_session_factory=factory))
    client = TestClient(app)
    with client.websocket_connect("/voice/phone/stream") as ws:
        ws.send_json({"event": "start", "start": {"streamSid": "S1", "callSid": "C1"}})

    names = [fd.name for tool in captured["tools"] for fd in (tool.function_declarations or [])]
    assert {"kb_search", "request_human_handoff", "submit_csat"} <= set(names)
    instruction = str(captured["instruction"]).lower()
    assert "human" in instruction and "rate" in instruction
    assert "do not ask for a rating if you handed off" in instruction


def _captured_instruction(settings: Any) -> str:
    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def factory(
        _settings: Any, system_instruction: Any, _tools: Any
    ) -> AsyncIterator[_FakeLive]:
        captured["instruction"] = system_instruction
        yield _FakeLive([])

    orch = _orch()
    orch._settings = settings
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch, live_session_factory=factory))
    client = TestClient(app)
    with client.websocket_connect("/voice/phone/stream") as ws:
        ws.send_json({"event": "start", "start": {"streamSid": "S1", "callSid": "C1"}})
    return str(captured["instruction"])


def test_handoff_paragraph_is_the_pre_package_wording_when_transfer_is_off() -> None:
    """Whole-branch review fix (Important 2): with phone_handoff_enabled off
    -- every deployed tenant -- `_attempt_transfer` always returns
    "ticket_created", so Task 6's transfer-aware wording would have the bot
    promise "Let me try to get a specialist for you now..." and then retract
    it on every single handoff. That is a customer-visible change with all
    flags off, which this package certifies cannot happen. Off must be the
    exact pre-package paragraph."""
    instruction = _captured_instruction(Settings(_env_file=None))
    assert (
        "If you cannot resolve the caller's issue, they ask for a human, or it is a "
        "complaint or sensitive matter, call request_human_handoff with a short reason "
        "and summary, then tell the caller a specialist will follow up. "
    ) in instruction
    assert "Let me try to get a specialist for you now" not in instruction
    assert "transferring" not in instruction


def test_handoff_paragraph_promises_a_transfer_only_when_the_flag_is_on() -> None:
    instruction = _captured_instruction(
        Settings(_env_file=None, phone_transcript_live_enabled=True, phone_handoff_enabled=True)
    )
    assert "Let me try to get a specialist for you now" in instruction
    assert '"transferring"' in instruction
    assert '"ticket_created"' in instruction
