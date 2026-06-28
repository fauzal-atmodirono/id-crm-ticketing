# Phone / VOIP Real-Time Bridge (Core) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A caller (browser softphone over VOIP) holds a low-latency spoken conversation with the KB-grounded AI over a real-time Twilio Media Streams ⇄ Gemini Live bridge, and the call transcript is logged to a Zendesk ticket.

**Architecture:** Twilio Voice → TwiML `<Connect><Stream>` → a FastAPI WebSocket per call. A `PhoneBridge` pumps audio both ways through an `audio_codec` (μ-law 8 kHz ⇄ PCM), feeding a Gemini Live session (native audio in/out, built-in VAD + barge-in). The Live model grounds answers via a `kb_search` function tool backed by the existing `KnowledgePort`; on call end the accumulated transcript is written to Zendesk via `ConversationLogPort`. Business logic (codec, event normalization, tool dispatch, transcript/ticket, barge-in) is unit-tested behind interfaces; the streaming glue is thin and proven by a live smoke test.

**Tech Stack:** Python 3.12, FastAPI WebSockets, `google-genai` 2.8.0 Live API (`aio.live`), `twilio` (access tokens), `audioop` (μ-law + resample), Vue 3 + `@twilio/voice-sdk`, pytest-asyncio, ruff, mypy --strict.

## Global Constraints

- Backend commands run from `apps/backend/`. Quality gates per task: `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict`, `.venv/bin/pytest src/`.
- `pyproject.toml` sets `asyncio_mode = "auto"` — async tests need no `@pytest.mark.asyncio`.
- 3 pre-existing mypy errors (firestore_session_service.py, service.py, test_service.py) + 1 pre-existing ruff error (vertex_search.py PLC0415) live in unrelated files — do not fix; introduce no NEW ones.
- All external I/O (Gemini Live, Twilio WS, Zendesk) is wrapped in try/except + structlog; one failing call must never crash the server or other calls.
- New backend code lives under `apps/backend/src/chatbot/features/chat/phone/`. Frontend under `apps/frontend/src/features/phone/`.
- Verified Live API (google-genai 2.8.0): `client.aio.live.connect(model=..., config=LiveConnectConfig(...))` (async CM → `AsyncSession`); `session.send_realtime_input(audio=types.Blob(data=<pcm16 16kHz>, mime_type="audio/pcm;rate=16000"))`; `async for msg in session.receive()` where `msg` is `types.LiveServerMessage` with `.server_content` (`input_transcription`, `output_transcription`, `model_turn`, `interrupted`, `turn_complete`) and `.tool_call` (`.function_calls`); `session.send_tool_response(function_responses=[types.FunctionResponse(...)])`. Gemini Live input PCM = 16 kHz, output PCM = 24 kHz.
- Twilio Media Streams (bidirectional `<Connect><Stream>`): inbound WS JSON events `connected` / `start` (`start.streamSid`, `start.callSid`) / `media` (`media.payload` = base64 μ-law 8 kHz) / `stop`. Outbound JSON: `{"event":"media","streamSid":sid,"media":{"payload":<b64 μ-law>}}` to play audio; `{"event":"clear","streamSid":sid}` to flush playback (barge-in).
- Conventional commits. Never print secrets.
- `session_id = "phone-<CallSid>"`; the call's Zendesk ticket gets `external_id = session_id` (consistent with WhatsApp/email).
- The live model id, exact resample quality, and exact Live event field population are confirmed in the **live smoke test** (Task 10), not guessed — unit tests use fakes and constructed `types` objects, so they are independent of a live connection.

---

### Task 1: Dependencies + configuration

**Files:**
- Modify: `apps/backend/pyproject.toml` (add `twilio`)
- Modify: `apps/backend/src/chatbot/platform/config.py`
- Modify: `apps/frontend/package.json` (add `@twilio/voice-sdk`)
- Test: `apps/backend/src/chatbot/platform/test_config_phone.py` (create)

**Interfaces:**
- Produces `Settings` fields: `gemini_live_model: str`, `gemini_live_voice: str`, `twilio_api_key_sid: str`, `twilio_api_key_secret: str`, `twilio_twiml_app_sid: str`, `public_wss_base_url: str`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/platform/test_config_phone.py`:

```python
from chatbot.platform.config import get_settings


def test_phone_settings_have_defaults() -> None:
    s = get_settings()
    assert s.gemini_live_model  # non-empty default
    assert s.gemini_live_voice
    # secrets default to empty (set via env in deployment)
    assert hasattr(s, "twilio_api_key_sid")
    assert hasattr(s, "twilio_api_key_secret")
    assert hasattr(s, "twilio_twiml_app_sid")
    assert hasattr(s, "public_wss_base_url")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_phone.py -v`
Expected: FAIL (`AttributeError`/missing fields).

- [ ] **Step 3: Add config fields**

In `config.py`, in the `# Voice settings` area (after `gemini_tts_voice`):

```python
    # Phone (real-time Gemini Live) settings
    gemini_live_model: str = "gemini-live-2.5-flash-preview"
    gemini_live_voice: str = "Kore"
    # Browser-softphone access tokens (Twilio Voice grant)
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = ""
    twilio_twiml_app_sid: str = ""
    # Public wss base for the <Stream> URL; falls back to twilio_webhook_base_url with https->wss
    public_wss_base_url: str = ""
```

- [ ] **Step 4: Add the backend dependency**

Run: `uv add twilio`
Expected: `twilio` added to `pyproject.toml` + `uv.lock`; import works:
`.venv/bin/python -c "from twilio.jwt.access_token import AccessToken; from twilio.jwt.access_token.grants import VoiceGrant; print('ok')"` → `ok`.

- [ ] **Step 5: Add the frontend dependency**

From `apps/frontend/`: `npm install @twilio/voice-sdk`
Expected: added to `package.json` dependencies.

- [ ] **Step 6: Run test + gates**

Run (from `apps/backend/`): `.venv/bin/pytest src/chatbot/platform/test_config_phone.py -v && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: PASS, no new errors.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/src/chatbot/platform/config.py apps/backend/src/chatbot/platform/test_config_phone.py apps/frontend/package.json apps/frontend/package-lock.json
git commit -m "feat(phone): deps (twilio, @twilio/voice-sdk) + Gemini Live / softphone config"
```

---

### Task 2: Audio codec (pure μ-law ⇄ PCM)

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/phone/__init__.py` (empty)
- Create: `apps/backend/src/chatbot/features/chat/phone/audio_codec.py`
- Test: `apps/backend/src/chatbot/features/chat/phone/test_audio_codec.py`

**Interfaces:**
- Produces: `def mulaw8k_to_pcm16k(mulaw: bytes) -> bytes` (Twilio inbound → Gemini input); `def pcm24k_to_mulaw8k(pcm: bytes) -> bytes` (Gemini output → Twilio).

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/phone/test_audio_codec.py`:

```python
import audioop

from chatbot.features.chat.phone.audio_codec import (
    mulaw8k_to_pcm16k,
    pcm24k_to_mulaw8k,
)


def test_mulaw8k_to_pcm16k_doubles_sample_count() -> None:
    # 160 μ-law bytes = 160 samples @ 8kHz (20ms). PCM16 @16kHz = 320 samples * 2 bytes.
    mulaw = b"\xff" * 160
    pcm = mulaw8k_to_pcm16k(mulaw)
    assert len(pcm) == 640  # 320 samples * 2 bytes


def test_pcm24k_to_mulaw8k_thirds_sample_count() -> None:
    # 240 samples @24kHz PCM16 (480 bytes, 10ms) -> 80 samples @8kHz μ-law (80 bytes).
    pcm = b"\x00\x00" * 240
    mulaw = pcm24k_to_mulaw8k(pcm)
    assert len(mulaw) == 80


def test_roundtrip_is_audio_shaped() -> None:
    # A non-trivial tone survives a round trip without raising and stays non-empty.
    pcm16k = audioop.lin2lin(b"\x10\x20" * 160, 2, 2)
    mulaw = pcm24k_to_mulaw8k(audioop.ratecv(pcm16k, 2, 1, 16000, 24000, None)[0])
    assert len(mulaw) > 0
    back = mulaw8k_to_pcm16k(mulaw)
    assert len(back) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_audio_codec.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the codec**

Create `apps/backend/src/chatbot/features/chat/phone/audio_codec.py`:

```python
"""Pure audio conversions between Twilio (μ-law 8 kHz) and Gemini Live (PCM).

Gemini Live input is 16 kHz PCM16; output is 24 kHz PCM16. Twilio Media Streams
is 8 kHz μ-law. Stateless per-frame resampling (audioop.ratecv state=None) is
sufficient for short 20 ms frames in this POC.

NOTE: `audioop` is deprecated in 3.12 and removed in 3.13. If the runtime moves
to 3.13, swap to the `audioop-lts` backport (drop-in) or a numpy implementation.
"""

from __future__ import annotations

import audioop

_WIDTH = 2  # 16-bit PCM
_CHANNELS = 1


def mulaw8k_to_pcm16k(mulaw: bytes) -> bytes:
    """Twilio inbound μ-law 8 kHz → PCM16 16 kHz for Gemini Live input."""
    pcm8k = audioop.ulaw2lin(mulaw, _WIDTH)
    pcm16k, _ = audioop.ratecv(pcm8k, _WIDTH, _CHANNELS, 8000, 16000, None)
    return pcm16k


def pcm24k_to_mulaw8k(pcm: bytes) -> bytes:
    """Gemini Live output PCM16 24 kHz → μ-law 8 kHz for Twilio playback."""
    pcm8k, _ = audioop.ratecv(pcm, _WIDTH, _CHANNELS, 24000, 8000, None)
    return audioop.lin2ulaw(pcm8k, _WIDTH)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_audio_codec.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/phone/__init__.py src/chatbot/features/chat/phone/audio_codec.py src/chatbot/features/chat/phone/test_audio_codec.py
git commit -m "feat(phone): pure μ-law/PCM audio codec for Twilio↔Gemini Live"
```

> Note: `mypy --strict` may report `audioop` as untyped; if so add `# type: ignore[import-untyped]` on the `import audioop` line (and nowhere else).

---

### Task 3: TwiML helper + `POST /voice/phone/incoming`

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/phone/twiml.py`
- Modify: `apps/backend/src/chatbot/features/chat/router.py` (route + handler)
- Test: `apps/backend/src/chatbot/features/chat/phone/test_twiml.py`, `apps/backend/src/chatbot/features/chat/test_phone_incoming.py`

**Interfaces:**
- Produces: `def connect_stream_twiml(wss_url: str) -> str`; route `POST /voice/phone/incoming` returning `application/xml`.
- Consumes: `Settings.public_wss_base_url`, `Settings.twilio_webhook_base_url`.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/phone/test_twiml.py`:

```python
from chatbot.features.chat.phone.twiml import connect_stream_twiml


def test_connect_stream_twiml_embeds_wss_url() -> None:
    xml = connect_stream_twiml("wss://example.test/voice/phone/stream")
    assert xml.startswith("<?xml")
    assert "<Connect>" in xml and "<Stream" in xml
    assert 'url="wss://example.test/voice/phone/stream"' in xml
```

Create `apps/backend/src/chatbot/features/chat/test_phone_incoming.py`:

```python
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_incoming_returns_connect_stream_twiml() -> None:
    orch = MagicMock()
    s = get_settings()
    s.public_wss_base_url = "wss://tunnel.test"
    orch._settings = s
    res = _client(orch).post("/voice/phone/incoming")
    assert res.status_code == 200
    assert "application/xml" in res.headers["content-type"]
    assert 'url="wss://tunnel.test/voice/phone/stream"' in res.text
    assert "<Connect>" in res.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_twiml.py src/chatbot/features/chat/test_phone_incoming.py -v`
Expected: FAIL (module/route missing).

- [ ] **Step 3: Implement the TwiML helper**

Create `apps/backend/src/chatbot/features/chat/phone/twiml.py`:

```python
"""Pure TwiML builders for the phone channel."""

from __future__ import annotations

from xml.sax.saxutils import quoteattr


def connect_stream_twiml(wss_url: str) -> str:
    """TwiML that bridges the call into a bidirectional Media Stream."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f"<Stream url={quoteattr(wss_url)} />"
        "</Connect></Response>"
    )
```

- [ ] **Step 4: Add the route + handler**

In `router.py` `ChatRouter.__init__` (with the other routes):

```python
        self.router.add_api_route(
            "/voice/phone/incoming", self.phone_incoming, methods=["POST"]
        )
```

Add a helper + handler to `ChatRouter` (import at top: `from chatbot.features.chat.phone.twiml import connect_stream_twiml`):

```python
    def _phone_wss_url(self) -> str:
        base = self.orchestrator._settings.public_wss_base_url
        if not base:
            https = self.orchestrator._settings.twilio_webhook_base_url
            base = https.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base.rstrip('/')}/voice/phone/stream"

    async def phone_incoming(self) -> Response:
        """Twilio Voice webhook: bridge the call into a Media Stream."""
        xml = connect_stream_twiml(self._phone_wss_url())
        return Response(content=xml, media_type="application/xml")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_twiml.py src/chatbot/features/chat/test_phone_incoming.py -v`
Expected: PASS.

- [ ] **Step 6: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/phone/twiml.py src/chatbot/features/chat/phone/test_twiml.py src/chatbot/features/chat/router.py src/chatbot/features/chat/test_phone_incoming.py
git commit -m "feat(phone): TwiML <Connect><Stream> + /voice/phone/incoming"
```

---

### Task 4: `POST /voice/phone/token` (browser softphone access token)

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/phone/token.py`
- Modify: `apps/backend/src/chatbot/features/chat/router.py`
- Test: `apps/backend/src/chatbot/features/chat/test_phone_token.py`

**Interfaces:**
- Produces: `def mint_voice_token(settings, identity: str) -> str`; route `POST /voice/phone/token` returning `{"token": str, "identity": str}`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_phone_token.py`:

```python
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_token_endpoint_returns_jwt_and_identity() -> None:
    orch = MagicMock()
    s = get_settings()
    s.twilio_account_sid = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    s.twilio_api_key_sid = "SKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    s.twilio_api_key_secret = "secretsecretsecretsecretsecretse"
    s.twilio_twiml_app_sid = "APxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    orch._settings = s
    res = _client(orch).post("/voice/phone/token")
    assert res.status_code == 200
    body = res.json()
    assert body["identity"]
    assert body["token"].count(".") == 2  # a JWT has three dot-separated parts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_phone_token.py -v`
Expected: FAIL (route missing).

- [ ] **Step 3: Implement the token minter**

Create `apps/backend/src/chatbot/features/chat/phone/token.py`:

```python
"""Mint short-lived Twilio Voice access tokens for the browser softphone."""

from __future__ import annotations

from typing import TYPE_CHECKING

from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

if TYPE_CHECKING:
    from chatbot.platform.config import Settings


def mint_voice_token(settings: Settings, identity: str) -> str:
    """Access token granting outgoing calls to our TwiML app."""
    token = AccessToken(
        settings.twilio_account_sid,
        settings.twilio_api_key_sid,
        settings.twilio_api_key_secret,
        identity=identity,
    )
    token.add_grant(
        VoiceGrant(
            outgoing_application_sid=settings.twilio_twiml_app_sid,
            incoming_allow=False,
        )
    )
    return token.to_jwt()
```

- [ ] **Step 4: Add the route + handler**

In `router.py` `__init__`:

```python
        self.router.add_api_route("/voice/phone/token", self.phone_token, methods=["POST"])
```

Import at top: `from chatbot.features.chat.phone.token import mint_voice_token`. Handler:

```python
    async def phone_token(self) -> dict[str, str]:
        """Mint a Twilio Voice access token for the browser softphone."""
        identity = "proton-web-caller"
        token = mint_voice_token(self.orchestrator._settings, identity)
        return {"token": token, "identity": identity}
```

- [ ] **Step 5: Run test + gates**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_phone_token.py -v && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/phone/token.py src/chatbot/features/chat/router.py src/chatbot/features/chat/test_phone_token.py
git commit -m "feat(phone): /voice/phone/token Twilio Voice access token for softphone"
```

---

### Task 5: Live event model + Gemini Live adapter

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/phone/live_events.py`
- Create: `apps/backend/src/chatbot/features/chat/phone/gemini_live.py`
- Test: `apps/backend/src/chatbot/features/chat/phone/test_live_events.py`

**Interfaces:**
- Produces normalized events (frozen dataclasses): `AudioOut(pcm: bytes)`, `InputTranscript(text: str)`, `OutputTranscript(text: str)`, `ToolCall(id: str, name: str, args: dict[str, object])`, `Interrupted()`, `TurnComplete()`; type alias `LiveEvent`.
- Produces: `def normalize_server_message(msg: types.LiveServerMessage) -> list[LiveEvent]` (pure).
- Produces: `class LiveSession(Protocol)` with `async def send_audio(self, pcm16k: bytes) -> None`, `async def send_tool_response(self, call_id: str, name: str, response: dict[str, object]) -> None`, `def events(self) -> AsyncIterator[LiveEvent]`; and `connect_live(settings, system_instruction, tools) -> AsyncContextManager[LiveSession]`.

- [ ] **Step 1: Write the failing test (pure normalizer)**

Create `apps/backend/src/chatbot/features/chat/phone/test_live_events.py`:

```python
from google.genai import types

from chatbot.features.chat.phone.live_events import (
    AudioOut,
    Interrupted,
    OutputTranscript,
    ToolCall,
    normalize_server_message,
)


def _audio_part(pcm: bytes) -> types.Part:
    return types.Part(inline_data=types.Blob(data=pcm, mime_type="audio/pcm;rate=24000"))


def test_normalize_extracts_audio_from_model_turn() -> None:
    msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(parts=[_audio_part(b"\x01\x02")])
        )
    )
    events = normalize_server_message(msg)
    assert AudioOut(b"\x01\x02") in events


def test_normalize_extracts_output_transcript_and_interrupt() -> None:
    msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            output_transcription=types.Transcription(text="hello"),
            interrupted=True,
        )
    )
    events = normalize_server_message(msg)
    assert OutputTranscript("hello") in events
    assert Interrupted() in events


def test_normalize_extracts_tool_call() -> None:
    msg = types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(
            function_calls=[types.FunctionCall(id="c1", name="kb_search", args={"query": "x"})]
        )
    )
    events = normalize_server_message(msg)
    assert ToolCall(id="c1", name="kb_search", args={"query": "x"}) in events
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_live_events.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the event model + normalizer**

Create `apps/backend/src/chatbot/features/chat/phone/live_events.py`:

```python
"""Normalized Gemini Live events, decoupling the bridge from the raw SDK shape."""

from __future__ import annotations

from dataclasses import dataclass, field

from google.genai import types


@dataclass(frozen=True)
class AudioOut:
    pcm: bytes  # PCM16 24 kHz from Gemini


@dataclass(frozen=True)
class InputTranscript:
    text: str


@dataclass(frozen=True)
class OutputTranscript:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Interrupted:
    pass


@dataclass(frozen=True)
class TurnComplete:
    pass


LiveEvent = AudioOut | InputTranscript | OutputTranscript | ToolCall | Interrupted | TurnComplete


def normalize_server_message(msg: types.LiveServerMessage) -> list[LiveEvent]:
    """Map one raw LiveServerMessage to zero or more normalized events."""
    events: list[LiveEvent] = []
    sc = msg.server_content
    if sc is not None:
        if sc.model_turn is not None:
            for part in sc.model_turn.parts or []:
                blob = part.inline_data
                if blob is not None and blob.data:
                    events.append(AudioOut(blob.data))
        if sc.input_transcription is not None and sc.input_transcription.text:
            events.append(InputTranscript(sc.input_transcription.text))
        if sc.output_transcription is not None and sc.output_transcription.text:
            events.append(OutputTranscript(sc.output_transcription.text))
        if sc.interrupted:
            events.append(Interrupted())
        if sc.turn_complete:
            events.append(TurnComplete())
    if msg.tool_call is not None:
        for fc in msg.tool_call.function_calls or []:
            events.append(ToolCall(id=fc.id or "", name=fc.name or "", args=dict(fc.args or {})))
    return events
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_live_events.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Implement the live adapter (thin, no unit test — exercised by smoke test)**

Create `apps/backend/src/chatbot/features/chat/phone/gemini_live.py`:

```python
"""Thin wrapper over the google-genai Live API exposing a testable Protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Protocol

import structlog
from google import genai
from google.genai import types

from chatbot.features.chat.phone.live_events import LiveEvent, normalize_server_message

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class LiveSession(Protocol):
    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(
        self, call_id: str, name: str, response: dict[str, object]
    ) -> None: ...
    def events(self) -> AsyncIterator[LiveEvent]: ...


class _GeminiLiveSession:
    def __init__(self, session: genai.live.AsyncSession) -> None:
        self._session = session

    async def send_audio(self, pcm16k: bytes) -> None:
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm16k, mime_type="audio/pcm;rate=16000")
        )

    async def send_tool_response(
        self, call_id: str, name: str, response: dict[str, object]
    ) -> None:
        await self._session.send_tool_response(
            function_responses=[types.FunctionResponse(id=call_id, name=name, response=response)]
        )

    async def events(self) -> AsyncIterator[LiveEvent]:
        async for msg in self._session.receive():
            for event in normalize_server_message(msg):
                yield event


@asynccontextmanager
async def connect_live(
    settings: Settings,
    system_instruction: str,
    tools: list[types.Tool],
) -> AsyncIterator[LiveSession]:
    """Open a Gemini Live session configured for telephony audio + KB tools."""
    client = genai.Client(vertexai=True) if settings.voice_provider == "gcp" else genai.Client()
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system_instruction,
        tools=tools,
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.gemini_live_voice)
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    async with client.aio.live.connect(model=settings.gemini_live_model, config=config) as session:
        yield _GeminiLiveSession(session)
```

- [ ] **Step 6: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/phone/live_events.py src/chatbot/features/chat/phone/gemini_live.py src/chatbot/features/chat/phone/test_live_events.py
git commit -m "feat(phone): Gemini Live event model + session adapter"
```

> Note: the `genai.Client()` construction here mirrors how the rest of the codebase builds clients — if the existing code uses a shared client/auth helper, use that instead (the implementer should check `adapters/gcp_voice.py` and match it). The exact `gemini_live_model` id is verified in the smoke test (Task 10).

---

### Task 6: `kb_search` tool (schema + dispatch)

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/phone/kb_tool.py`
- Test: `apps/backend/src/chatbot/features/chat/phone/test_kb_tool.py`

**Interfaces:**
- Consumes: `KnowledgePort.search_kb(query: str, limit: int = 2) -> list[KbArticle]` (KbArticle has `.title`, `.content`, `.url`).
- Produces: `KB_SEARCH_TOOL: types.Tool`; `async def dispatch_kb_search(args: dict[str, object], knowledge_port: KnowledgePort) -> dict[str, object]`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/phone/test_kb_tool.py`:

```python
from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.phone.kb_tool import KB_SEARCH_TOOL, dispatch_kb_search


class _FakeKnowledge:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        self.queries.append(query)
        return [KbArticle(title="Warranty", content="5 years", url="https://x/y")]


def test_tool_declares_kb_search() -> None:
    names = [fd.name for fd in (KB_SEARCH_TOOL.function_declarations or [])]
    assert "kb_search" in names


async def test_dispatch_runs_search_and_returns_results() -> None:
    kb = _FakeKnowledge()
    out = await dispatch_kb_search({"query": "battery warranty"}, kb)
    assert kb.queries == ["battery warranty"]
    assert out["results"]
    assert out["results"][0]["title"] == "Warranty"


async def test_dispatch_handles_missing_query() -> None:
    kb = _FakeKnowledge()
    out = await dispatch_kb_search({}, kb)
    assert out["results"] == []
    assert kb.queries == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_kb_tool.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the tool**

Create `apps/backend/src/chatbot/features/chat/phone/kb_tool.py`:

```python
"""The kb_search function tool the Live model calls for KB grounding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.genai import types

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort

KB_SEARCH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="kb_search",
            description="Search the Proton knowledge base for facts to answer the caller.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="The caller's question or keywords to look up.",
                    )
                },
                required=["query"],
            ),
        )
    ]
)


async def dispatch_kb_search(
    args: dict[str, object], knowledge_port: KnowledgePort
) -> dict[str, object]:
    """Run kb_search and shape the result for send_tool_response."""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"results": []}
    articles = await knowledge_port.search_kb(query, limit=3)
    return {
        "results": [
            {"title": a.title, "content": a.content, "url": a.url} for a in articles
        ]
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_kb_tool.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/phone/kb_tool.py src/chatbot/features/chat/phone/test_kb_tool.py
git commit -m "feat(phone): kb_search Live tool (schema + KnowledgePort dispatch)"
```

---

### Task 7: PhoneBridge (the orchestration core)

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/phone/bridge.py`
- Test: `apps/backend/src/chatbot/features/chat/phone/test_bridge.py`

**Interfaces:**
- Consumes: `LiveSession` (Task 5), `audio_codec` (Task 2), `dispatch_kb_search` (Task 6), `KnowledgePort`, `ConversationLogPort` (`ensure_conversation_ticket`, `append_conversation_comment(status=...)`, `set_ticket_external_id`).
- Produces: `class PhoneBridge` with `async def handle_twilio(self, msg: dict[str, object]) -> None`, `async def pump(self) -> None`, `async def finalize(self) -> None`.
- Constructor: `PhoneBridge(live: LiveSession, knowledge_port: KnowledgePort, conversation_log_port: ConversationLogPort, send_twilio: Callable[[dict[str, object]], Awaitable[None]])`.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/phone/test_bridge.py`:

```python
import base64
from collections.abc import AsyncIterator

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.phone.bridge import PhoneBridge
from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    LiveEvent,
    OutputTranscript,
    ToolCall,
)


class _FakeLive:
    def __init__(self, scripted: list[LiveEvent]) -> None:
        self._scripted = scripted
        self.audio_sent: list[bytes] = []
        self.tool_responses: list[tuple[str, str, dict]] = []

    async def send_audio(self, pcm16k: bytes) -> None:
        self.audio_sent.append(pcm16k)

    async def send_tool_response(self, call_id: str, name: str, response: dict) -> None:
        self.tool_responses.append((call_id, name, response))

    async def events(self) -> AsyncIterator[LiveEvent]:
        for e in self._scripted:
            yield e


class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        return [KbArticle(title="W", content="5y", url="https://x")]


class _FakeLog:
    def __init__(self) -> None:
        self.ticket_calls: list = []
        self.comments: list = []
        self.external_ids: list = []

    async def ensure_conversation_ticket(self, session_id, subject, customer_name, customer_phone):
        self.ticket_calls.append((session_id, subject))
        return "T-1"

    async def append_conversation_comment(self, ticket_id, text, status=None):
        self.comments.append((ticket_id, text, status))

    async def add_ticket_tag(self, ticket_id, tag):  # unused here
        ...

    async def set_ticket_external_id(self, ticket_id, external_id):
        self.external_ids.append((ticket_id, external_id))

    async def post_public_reply(self, ticket_id, text, status=None):  # unused here
        ...

    async def get_latest_public_comment(self, ticket_id):  # unused here
        return ("", None, None)


def _bridge(live: _FakeLive, sent: list[dict], log: _FakeLog | None = None) -> PhoneBridge:
    async def send_twilio(msg: dict) -> None:
        sent.append(msg)

    return PhoneBridge(live, _FakeKnowledge(), log or _FakeLog(), send_twilio)


async def test_handle_start_records_sids() -> None:
    live = _FakeLive([])
    b = _bridge(live, [])
    await b.handle_twilio({"event": "start", "start": {"streamSid": "S1", "callSid": "C1"}})
    assert b.stream_sid == "S1"
    assert b.call_sid == "C1"


async def test_handle_media_forwards_decoded_audio_to_live() -> None:
    live = _FakeLive([])
    b = _bridge(live, [])
    payload = base64.b64encode(b"\xff" * 160).decode()
    await b.handle_twilio({"event": "media", "media": {"payload": payload}})
    assert len(live.audio_sent) == 1
    assert len(live.audio_sent[0]) == 640  # 160 μ-law → 320 samples @16k * 2 bytes


async def test_pump_audio_out_sends_twilio_media_frame() -> None:
    live = _FakeLive([AudioOut(b"\x00\x00" * 240)])  # 240 samples @24k
    sent: list[dict] = []
    b = _bridge(live, sent)
    b.stream_sid = "S1"
    await b.pump()
    media = [m for m in sent if m.get("event") == "media"]
    assert media and media[0]["streamSid"] == "S1"
    assert media[0]["media"]["payload"]  # base64 μ-law


async def test_pump_tool_call_dispatches_kb_and_responds() -> None:
    live = _FakeLive([ToolCall(id="c1", name="kb_search", args={"query": "warranty"})])
    b = _bridge(live, [])
    await b.pump()
    assert live.tool_responses
    call_id, name, response = live.tool_responses[0]
    assert name == "kb_search"
    assert response["results"][0]["title"] == "W"


async def test_pump_interrupted_sends_twilio_clear() -> None:
    live = _FakeLive([Interrupted()])
    sent: list[dict] = []
    b = _bridge(live, sent)
    b.stream_sid = "S1"
    await b.pump()
    assert {"event": "clear", "streamSid": "S1"} in sent


async def test_pump_accumulates_transcript() -> None:
    live = _FakeLive([InputTranscript("hi"), OutputTranscript("hello there")])
    b = _bridge(live, [])
    await b.pump()
    assert ("USER", "hi") in b.transcript
    assert ("ASSISTANT", "hello there") in b.transcript


async def test_finalize_writes_transcript_to_zendesk() -> None:
    log = _FakeLog()
    live = _FakeLive([])
    b = _bridge(live, [], log)
    b.call_sid = "C1"
    b.transcript = [("USER", "hi"), ("ASSISTANT", "hello")]
    await b.finalize()
    assert log.ticket_calls and log.ticket_calls[0][0] == "phone-C1"
    assert log.external_ids == [("T-1", "phone-C1")]
    assert log.comments and log.comments[0][2] == "solved"
    assert "USER: hi" in log.comments[0][1]


async def test_finalize_noop_without_transcript() -> None:
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C1"
    await b.finalize()
    assert log.ticket_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_bridge.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement PhoneBridge**

Create `apps/backend/src/chatbot/features/chat/phone/bridge.py`:

```python
"""Orchestrates a single phone call: Twilio Media Stream ⇄ Gemini Live."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog

from chatbot.features.chat.phone.audio_codec import mulaw8k_to_pcm16k, pcm24k_to_mulaw8k
from chatbot.features.chat.phone.kb_tool import dispatch_kb_search
from chatbot.features.chat.phone.live_events import (
    AudioOut,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    ToolCall,
)

if TYPE_CHECKING:
    from chatbot.features.chat.phone.gemini_live import LiveSession
    from chatbot.features.chat.ports import ConversationLogPort, KnowledgePort

_log = structlog.get_logger(__name__)


class PhoneBridge:
    def __init__(
        self,
        live: LiveSession,
        knowledge_port: KnowledgePort,
        conversation_log_port: ConversationLogPort,
        send_twilio: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        self._live = live
        self._knowledge = knowledge_port
        self._log_port = conversation_log_port
        self._send_twilio = send_twilio
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.transcript: list[tuple[str, str]] = []

    async def handle_twilio(self, msg: dict[str, object]) -> None:
        event = msg.get("event")
        if event == "start":
            start = msg.get("start") or {}
            self.stream_sid = start.get("streamSid")  # type: ignore[assignment]
            self.call_sid = start.get("callSid")  # type: ignore[assignment]
        elif event == "media":
            media = msg.get("media") or {}
            payload = media.get("payload")  # type: ignore[union-attr]
            if payload:
                pcm = mulaw8k_to_pcm16k(base64.b64decode(payload))
                await self._live.send_audio(pcm)
        # "stop"/"connected" need no action here; finalize() runs on socket close.

    async def pump(self) -> None:
        async for event in self._live.events():
            if isinstance(event, AudioOut):
                if self.stream_sid:
                    mulaw = pcm24k_to_mulaw8k(event.pcm)
                    await self._send_twilio(
                        {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": base64.b64encode(mulaw).decode()},
                        }
                    )
            elif isinstance(event, Interrupted):
                if self.stream_sid:
                    await self._send_twilio({"event": "clear", "streamSid": self.stream_sid})
            elif isinstance(event, InputTranscript):
                self.transcript.append(("USER", event.text))
            elif isinstance(event, OutputTranscript):
                self.transcript.append(("ASSISTANT", event.text))
            elif isinstance(event, ToolCall):
                if event.name == "kb_search":
                    result = await dispatch_kb_search(event.args, self._knowledge)
                    await self._live.send_tool_response(event.id, event.name, result)

    async def finalize(self) -> None:
        if not self.transcript or not self.call_sid:
            return
        session_id = f"phone-{self.call_sid}"
        body = "\n".join(f"{role}: {text}" for role, text in self.transcript)
        try:
            ticket_id = await self._log_port.ensure_conversation_ticket(
                session_id=session_id,
                subject=f"[phone] Conversation {session_id}",
                customer_name=None,
                customer_phone=None,
            )
            await self._log_port.append_conversation_comment(ticket_id, body, status="solved")
            await self._log_port.set_ticket_external_id(ticket_id, session_id)
        except Exception as e:
            _log.error("phone_finalize_failed", session_id=session_id, error=str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_bridge.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/phone/bridge.py src/chatbot/features/chat/phone/test_bridge.py
git commit -m "feat(phone): PhoneBridge — Twilio↔Gemini Live pump, kb tool, barge-in, transcript ticket"
```

---

### Task 8: `WS /voice/phone/stream` endpoint

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py` (WS route + handler + optional `live_session_factory` injection)
- Test: `apps/backend/src/chatbot/features/chat/test_phone_stream.py`

**Interfaces:**
- Consumes: `PhoneBridge` (Task 7), `connect_live` (Task 5), `KB_SEARCH_TOOL` (Task 6), the agent system instruction.
- Produces: WebSocket route `/voice/phone/stream`; `build_chat_router(..., live_session_factory=None)` optional injection for tests.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/features/chat/test_phone_stream.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.phone.live_events import AudioOut, LiveEvent
from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


class _FakeLive:
    def __init__(self, scripted: list[LiveEvent]) -> None:
        self._scripted = scripted

    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(self, call_id, name, response) -> None: ...

    async def events(self) -> AsyncIterator[LiveEvent]:
        for e in self._scripted:
            yield e


def _orch() -> MagicMock:
    orch = MagicMock()
    orch._settings = get_settings()
    orch._knowledge_port = MagicMock()
    orch._knowledge_port.search_kb = lambda q, limit=2: []
    orch._conversation_log_port = MagicMock()
    return orch


def test_stream_plays_live_audio_back_to_twilio() -> None:
    @asynccontextmanager
    async def factory(settings, system_instruction, tools) -> AsyncIterator[_FakeLive]:
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_phone_stream.py -v`
Expected: FAIL (no WS route / no `live_session_factory` param).

- [ ] **Step 3: Wire the WebSocket handler**

In `router.py`: import `WebSocket`, `WebSocketDisconnect` from `fastapi`; `import asyncio`, `json` (json already imported); import `connect_live`, `PhoneBridge`, `KB_SEARCH_TOOL`. Add `live_session_factory` to `ChatRouter.__init__` and `build_chat_router` (default `None` → use the real `connect_live`):

```python
        self._live_session_factory = live_session_factory or connect_live
        self.router.add_api_websocket_route("/voice/phone/stream", self.phone_stream)
```

Handler (the system instruction: reuse the agent persona; for the core spec a concise spoken-style instruction is acceptable — pull the existing support prompt if one is exposed, else this string):

```python
    async def phone_stream(self, websocket: WebSocket) -> None:
        await websocket.accept()
        system_instruction = (
            "You are Proton's friendly phone support agent. Answer spoken questions "
            "concisely and naturally. Use the kb_search tool to ground answers in the "
            "Proton knowledge base before giving facts. No markdown — this is spoken aloud."
        )
        try:
            async with self._live_session_factory(
                self.orchestrator._settings, system_instruction, [KB_SEARCH_TOOL]
            ) as live:
                bridge = PhoneBridge(
                    live,
                    self.orchestrator._knowledge_port,
                    self.orchestrator._conversation_log_port,
                    websocket.send_json,
                )

                async def from_twilio() -> None:
                    while True:
                        raw = await websocket.receive_text()
                        await bridge.handle_twilio(json.loads(raw))

                pump_task = asyncio.create_task(bridge.pump())
                twilio_task = asyncio.create_task(from_twilio())
                done, pending = await asyncio.wait(
                    {pump_task, twilio_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                await bridge.finalize()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            _log.error("phone_stream_failed", error=str(e))
```

> The test drives a finite fake (one `AudioOut`, then `events()` ends → `pump_task` completes → the handler tears down). In production the streams run until the caller hangs up (`WebSocketDisconnect`) or the stop event arrives.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_phone_stream.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite + gates**

Run: `.venv/bin/pytest src/ && .venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict`
Expected: PASS, clean (report the count).

- [ ] **Step 6: Commit**

```bash
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_phone_stream.py
git commit -m "feat(phone): /voice/phone/stream WebSocket wiring Twilio Media Streams to Gemini Live"
```

---

### Task 9: Frontend browser softphone widget

**Files:**
- Create: `apps/frontend/src/features/phone/api/phone.api.ts`
- Create: `apps/frontend/src/features/phone/components/PhoneCall.vue`
- Create: `apps/frontend/src/features/phone/index.ts`
- Modify: a view/layout to mount `<PhoneCall>` (e.g. `apps/frontend/src/views/HomeView.vue` or the channel tabs) — match how the existing voice widget is mounted.

**Interfaces:**
- Consumes: `POST /voice/phone/token` → `{ token, identity }`.
- Produces: a "Call support" button that connects/disconnects a Twilio `Device` call.

- [ ] **Step 1: Token API client**

Create `apps/frontend/src/features/phone/api/phone.api.ts`:

```typescript
import { apiBaseUrl } from '@/plugins/api'

export interface PhoneToken {
  token: string
  identity: string
}

export async function fetchPhoneToken(): Promise<PhoneToken> {
  const res = await fetch(`${apiBaseUrl}/voice/phone/token`, { method: 'POST' })
  if (!res.ok) throw new Error(`token request failed: ${res.status}`)
  return (await res.json()) as PhoneToken
}
```

> Check `apps/frontend/src/plugins/api.ts` for the exact exported base-URL symbol and match it (e.g. `apiBaseUrl` vs a default export). Adjust the import to the real name.

- [ ] **Step 2: The softphone component**

Create `apps/frontend/src/features/phone/components/PhoneCall.vue`:

```vue
<script setup lang="ts">
import { ref, onBeforeUnmount } from 'vue'
import { Device, type Call } from '@twilio/voice-sdk'
import { fetchPhoneToken } from '../api/phone.api'

const status = ref<'idle' | 'connecting' | 'in-call' | 'error'>('idle')
let device: Device | null = null
let call: Call | null = null

async function startCall() {
  try {
    status.value = 'connecting'
    const { token } = await fetchPhoneToken()
    device = new Device(token)
    call = await device.connect()
    call.on('disconnect', endCall)
    status.value = 'in-call'
  } catch {
    status.value = 'error'
  }
}

function endCall() {
  call?.disconnect()
  device?.destroy()
  call = null
  device = null
  status.value = 'idle'
}

onBeforeUnmount(endCall)
</script>

<template>
  <div class="phone-call">
    <button v-if="status === 'idle' || status === 'error'" @click="startCall">📞 Call support</button>
    <button v-else-if="status === 'in-call'" @click="endCall">⛔ Hang up</button>
    <span v-else>Connecting…</span>
    <span v-if="status === 'error'" class="err">Call failed</span>
  </div>
</template>
```

- [ ] **Step 3: Barrel export + mount**

Create `apps/frontend/src/features/phone/index.ts`:

```typescript
export { default as PhoneCall } from './components/PhoneCall.vue'
```

Mount `<PhoneCall />` somewhere visible (match the existing voice widget's placement — check where `VoiceRecorder`/the voice feature is mounted and add `PhoneCall` alongside it).

- [ ] **Step 4: Type-check**

Run (from `apps/frontend/`): `npm run type-check`
Expected: PASS (no vue-tsc errors).

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/src/features/phone/ apps/frontend/src/views/HomeView.vue
git commit -m "feat(phone-ui): browser softphone widget (Twilio Voice SDK)"
```

---

### Task 10: Live smoke-test plan

**Files:**
- Create: `docs/testing/phone-channel-smoke-test.md`

- [ ] **Step 1: Write the smoke-test doc**

Create `docs/testing/phone-channel-smoke-test.md` covering: prerequisites (a Twilio account, an **API Key** (`twilio_api_key_sid`/`secret`), a **TwiML App** whose Voice URL = `<PUBLIC_BASE>/voice/phone/incoming`, the env vars, a public tunnel that passes **WebSocket upgrades**, and `gemini_live_model` set/confirmed); the verification steps:
1. Open the web app, click **Call support** → grant mic → confirm you hear the AI greet you with low latency.
2. Ask a KB question (e.g. battery warranty) → confirm the answer is correct/grounded (the model called `kb_search`).
3. **Interrupt** the AI mid-sentence → confirm it stops promptly (barge-in / Twilio `clear`).
4. Hang up → confirm a Zendesk ticket exists with `external_id = phone-<CallSid>`, status **solved**, and the USER/ASSISTANT transcript in a comment.
And troubleshooting: model-id errors, no audio (sample-rate/codec), WS not upgrading through the tunnel, token errors (API key/TwiML app SID), choppy audio (stateless resample).

- [ ] **Step 2: Commit**

```bash
git add docs/testing/phone-channel-smoke-test.md
git commit -m "docs(phone): live smoke-test plan"
```

---

### Task 11: Docs — CHANGELOG, README, .env.example

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `apps/backend/.env.example`

- [ ] **Step 1: CHANGELOG** — add under `## [Unreleased]` (or a dated `## [2026-06-28]`) an *Added* entry:

```markdown
- **Phone / VOIP channel (real-time)** — callers reach the AI over a real-time
  Twilio Media Streams ⇄ Gemini Live bridge (native audio, built-in VAD + barge-in).
  KB-grounded via a `kb_search` tool; the call transcript is logged to a Zendesk ticket
  (`session_id = phone-<CallSid>`). Tested via an in-app browser softphone (Twilio Voice
  SDK) — no phone number / no PSTN cost. Handoff + CSAT are a follow-up.
```

- [ ] **Step 2: README** — add **Phone (real-time, via Twilio)** to the channel list/Highlights; add `POST /voice/phone/incoming`, `POST /voice/phone/token`, and `WS /voice/phone/stream` to the Endpoints table; add `GEMINI_LIVE_MODEL`, `GEMINI_LIVE_VOICE`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_TWIML_APP_SID`, `PUBLIC_WSS_BASE_URL` to the Configuration table.

- [ ] **Step 3: .env.example** — add:

```bash
# Phone channel (real-time Gemini Live + Twilio Media Streams)
GEMINI_LIVE_MODEL=gemini-live-2.5-flash-preview
GEMINI_LIVE_VOICE=Kore
# Browser softphone (Twilio Voice access tokens)
TWILIO_API_KEY_SID=
TWILIO_API_KEY_SECRET=
TWILIO_TWIML_APP_SID=
# Public wss base for the <Stream> URL (falls back to TWILIO_WEBHOOK_BASE_URL with https->wss)
PUBLIC_WSS_BASE_URL=
```

- [ ] **Step 4: Final full suite + gates**

Run (from `apps/backend/`): `.venv/bin/pytest src/ && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`; (from `apps/frontend/`): `npm run type-check`.
Expected: PASS; only the documented pre-existing mypy/ruff errors remain.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md apps/backend/.env.example
git commit -m "docs(phone): changelog, readme, env example"
```

---

## Self-Review Notes

- **Spec coverage:** TwiML incoming → Task 3; Media Streams WS → Task 8; PhoneBridge (transcode + pump + barge-in) → Tasks 2/7; Gemini Live session → Task 5; kb_search grounding → Tasks 6/7; transcript → Zendesk → Task 7; browser softphone (token + widget) → Tasks 4/9; config → Task 1; smoke test → Task 10; docs → Task 11. Out-of-scope (handoff, CSAT, warm transfer, DTMF, PSTN, dashboard) — none built. All covered.
- **Type consistency:** `LiveSession` protocol (`send_audio`/`send_tool_response`/`events`) used identically in Tasks 5/7/8; `PhoneBridge(live, knowledge_port, conversation_log_port, send_twilio)` consistent; `mulaw8k_to_pcm16k`/`pcm24k_to_mulaw8k`, `dispatch_kb_search(args, knowledge_port)`, `KB_SEARCH_TOOL`, `normalize_server_message`, `connect_live(settings, system_instruction, tools)` consistent across tasks; `session_id="phone-<CallSid>"` + `set_ticket_external_id` consistent with the WhatsApp/email mechanism.
- **Live-dependent unknowns** (model id, exact audio fidelity, real Live event population) are isolated to the live adapter (Task 5 thin wrapper) and the smoke test (Task 10); every unit test uses fakes or constructed `types` objects, so the suite is green without a live connection.
- **Risk:** the `genai.Client()` construction (Task 5) and the system-instruction source (Task 8) should match existing patterns (`adapters/gcp_voice.py`, the agent persona) — flagged inline for the implementer.
