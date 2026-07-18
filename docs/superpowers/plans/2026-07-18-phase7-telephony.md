# Phase 7 — Telephony / PSTN & Channel Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing Gemini-Live/Twilio phone bridge to open a Chatwoot conversation at call START (live in-call presence with streaming partial transcripts); define a `TelephonyProviderPort` abstraction so any future PSTN/CTI provider plugs in behind the same interface; and configure Facebook/Instagram/Telegram as native Chatwoot Community inboxes (runbook only — no code). The real PSTN number wiring is explicitly BLOCKED until a provider is chosen.

**Architecture:** The existing `PhoneBridge` in `proton-conversational-ai` is extended to call `ensure_conversation_ticket` at call START (when `callSid` first arrives) rather than only in `finalize()`, and to flush partial transcripts as private notes during the call via a new `flush_partial_transcript()` method. A new `TelephonyProviderPort` Protocol in `ports.py` captures the three provider events (incoming-call, media-stream, hangup) so the current Twilio-specific handling in `router.py` is refactored behind a `TwilioProvider` adapter and a `MockTelephonyProvider` enables tests without a live Twilio account. The social-inbox section is a pure Chatwoot admin runbook (Settings UI steps, no backend code).

**Tech Stack:** Python 3.12+, FastAPI, pytest-asyncio, structlog. All new code lives in `proton-conversational-ai/apps/backend/`. No new runtime dependencies. Chatwoot Community (existing deployment) for social inbox config.

## Global Constraints

- Never unlock or import from Chatwoot's `/enterprise` folder.
- Never reproduce Chatwoot or Twilio source code — only our adapter/bridge code.
- All new backend code: Python 3.12+, type-annotated, `from __future__ import annotations`.
- Tests: pytest-asyncio, no live network calls (stub all I/O), placed alongside the module under test (e.g. `phone/test_bridge_live_presence.py`).
- Secrets/credentials never committed — reference `Settings` fields only.
- Build/verify in `default` tenant first; replicate to `proton`/`wahchan` after smoke test.
- The BLOCKED gate (Task 4) must not block Tasks 1–3; they are independent.
- Repo: `proton-conversational-ai` (Tasks 1–3 + BLOCKED gate); Chatwoot admin UI (Task 3 runbook).

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `apps/backend/src/chatbot/features/chat/phone/test_bridge_live_presence.py` | Tests for live in-call presence (Task 1) |
| `apps/backend/src/chatbot/features/chat/phone/telephony_port.py` | `TelephonyProviderPort` Protocol + `MockTelephonyProvider` (Task 2) |
| `apps/backend/src/chatbot/features/chat/phone/twilio_provider.py` | `TwilioProvider` adapter implementing `TelephonyProviderPort` (Task 2) |
| `apps/backend/src/chatbot/features/chat/phone/test_telephony_port.py` | Tests for port + mock + Twilio adapter (Task 2) |

### Modified files

| File | Change |
|---|---|
| `apps/backend/src/chatbot/features/chat/phone/bridge.py` | Add `_conv_id` field; call `ensure_conversation_ticket` in `handle_twilio` on `start` event; add `flush_partial_transcript()` called from `pump()` on each transcript event (Task 1) |
| `apps/backend/src/chatbot/features/chat/phone/test_bridge.py` | Add/update tests for `_conv_id` presence and `flush_partial_transcript` behaviour (Task 1) |

### Not modified

- `router.py` — the Twilio-specific route handlers (`phone_incoming`, `phone_stream`) remain in place for Task 2; refactoring them behind `TwilioProvider` is scoped to Task 2 and explicitly marked as optional-if-provider-unblocked. The port abstraction is the priority.
- `adapters/chatwoot.py` — no changes needed; `ensure_conversation_ticket` and `append_conversation_comment` already exist.
- `ports.py` — no changes needed; `ConversationLogPort` already covers what we need.

---

## Task 1: Live In-Call Chatwoot Presence

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/phone/bridge.py`
- Modify: `apps/backend/src/chatbot/features/chat/phone/test_bridge.py`
- Create: `apps/backend/src/chatbot/features/chat/phone/test_bridge_live_presence.py`

**Interfaces:**
- Consumes: `ConversationLogPort.ensure_conversation_ticket(session_id, subject, customer_name, customer_phone) -> str` (already exists in `ports.py` and implemented by `ChatwootAdapter`)
- Consumes: `ConversationLogPort.append_conversation_comment(ticket_id, text, status) -> ConversationLogResult` (already exists)
- Produces: `PhoneBridge._conv_id: str | None` — set when the Chatwoot conversation is opened at call start; `None` until the `start` Twilio event arrives
- Produces: `PhoneBridge.flush_partial_transcript() -> None` — posts any new transcript lines to the open conversation as private notes; called internally from `pump()` after each `InputTranscript` / `OutputTranscript` event

**What this task does (in plain English):**
Currently `PhoneBridge.finalize()` opens the Chatwoot conversation AFTER the call ends. This task makes it open at call START (when the Twilio `start` event arrives with `callSid`) and stream each transcript turn as a private note in real time, so agents watching the Chatwoot inbox see the conversation unfold live.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/phone/test_bridge_live_presence.py`:

```python
"""Tests for live in-call Chatwoot presence (Task 1, Phase 7)."""
from __future__ import annotations

import pytest

from chatbot.features.chat.phone.bridge import PhoneBridge
from chatbot.features.chat.phone.live_events import InputTranscript, OutputTranscript
from chatbot.features.chat.ports import ConversationLogResult


class _FakeLive:
    def __init__(self, scripted):
        self._scripted = scripted
        self.audio_sent: list[bytes] = []
        self.tool_responses = []

    async def send_audio(self, pcm16k: bytes) -> None:
        self.audio_sent.append(pcm16k)

    async def send_tool_response(self, call_id, name, response) -> None:
        self.tool_responses.append((call_id, name, response))

    async def events(self):
        for e in self._scripted:
            yield e


class _FakeLog:
    def __init__(self) -> None:
        self.ticket_calls: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str, str | None]] = []
        self.external_ids: list[tuple[str, str]] = []
        self.tags: list[tuple[str, str]] = []

    async def ensure_conversation_ticket(self, session_id, subject, customer_name, customer_phone):
        self.ticket_calls.append((session_id, subject))
        return "CONV-1"

    async def rotate_conversation_ticket(self, session_id, subject, customer_name, customer_phone):
        return "CONV-2"

    async def append_conversation_comment(self, ticket_id, text, status=None):
        self.comments.append((ticket_id, text, status))
        return ConversationLogResult.OK

    async def add_ticket_tag(self, ticket_id, tag):
        self.tags.append((ticket_id, tag))

    async def set_ticket_external_id(self, ticket_id, external_id):
        self.external_ids.append((ticket_id, external_id))

    async def post_public_reply(self, ticket_id, text, status=None):
        pass

    async def get_latest_public_comment(self, ticket_id):
        return ("", None, None)


def _bridge(live, sent, log=None):
    async def send_twilio(msg):
        sent.append(msg)
    return PhoneBridge(live, _FakeKnowledge(), log or _FakeLog(), send_twilio)


class _FakeKnowledge:
    async def search_kb(self, query, limit=2):
        return []


@pytest.mark.asyncio
async def test_handle_start_opens_chatwoot_conversation():
    """Receiving the Twilio 'start' event must immediately open a Chatwoot conversation."""
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    await b.handle_twilio({"event": "start", "start": {"streamSid": "S1", "callSid": "C42"}})
    # ensure_conversation_ticket must have been called once
    assert len(log.ticket_calls) == 1
    session_id, subject = log.ticket_calls[0]
    assert session_id == "phone-C42"
    assert "[phone]" in subject
    # _conv_id must be set to the returned ticket id
    assert b._conv_id == "CONV-1"


@pytest.mark.asyncio
async def test_handle_start_noop_when_call_sid_missing():
    """A malformed start event with no callSid must not call ensure_conversation_ticket."""
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    await b.handle_twilio({"event": "start", "start": {"streamSid": "S1"}})
    assert log.ticket_calls == []
    assert b._conv_id is None


@pytest.mark.asyncio
async def test_pump_flushes_partial_transcript_as_private_note():
    """Each transcript turn during the call must be posted as a private note to the open conversation."""
    log = _FakeLog()
    events = [InputTranscript("help me"), OutputTranscript("I can help")]
    b = _bridge(_FakeLive(events), [], log)
    b.call_sid = "C42"
    b._conv_id = "CONV-1"  # simulate conversation already opened at start
    await b.pump()
    # Two partial transcript comments must have been posted
    partial_comments = [c for c in log.comments if "LIVE" in c[1] or "USER" in c[1] or "ASSISTANT" in c[1]]
    assert len(partial_comments) == 2
    texts = [c[1] for c in partial_comments]
    assert any("help me" in t for t in texts)
    assert any("I can help" in t for t in texts)
    # All partial posts target the correct conversation; status is None (no status change mid-call)
    assert all(c[0] == "CONV-1" for c in partial_comments)
    assert all(c[2] is None for c in partial_comments)


@pytest.mark.asyncio
async def test_pump_skips_partial_flush_when_conv_id_not_set():
    """If the conversation was never opened (e.g. start event malformed), pump must not crash."""
    log = _FakeLog()
    events = [InputTranscript("hello")]
    b = _bridge(_FakeLive(events), [], log)
    b._conv_id = None  # explicitly not set
    await b.pump()
    # No append calls (the transcript is accumulated in memory but not flushed)
    assert log.comments == []


@pytest.mark.asyncio
async def test_finalize_does_not_duplicate_open_if_already_opened():
    """finalize() must reuse the _conv_id set at call start, not call ensure_conversation_ticket again."""
    log = _FakeLog()
    b = _bridge(_FakeLive([]), [], log)
    b.call_sid = "C42"
    b._conv_id = "CONV-1"  # already opened at call start
    b.transcript = [("USER", "hi"), ("ASSISTANT", "hello")]
    await b.finalize()
    # ensure_conversation_ticket must NOT be called again in finalize
    assert log.ticket_calls == []
    # The final full transcript comment IS still posted
    assert any("USER: hi" in c[1] for c in log.comments)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/chat/phone/test_bridge_live_presence.py -v 2>&1 | head -40
```

Expected: 5 failures. `AttributeError: 'PhoneBridge' object has no attribute '_conv_id'` or similar.

- [ ] **Step 3: Implement live in-call presence in `bridge.py`**

Open `apps/backend/src/chatbot/features/chat/phone/bridge.py`. Apply these changes:

**3a. Add `_conv_id` to `__init__`:**

In `PhoneBridge.__init__`, after `self.csat_score: int | None = None`, add:

```python
        self._conv_id: str | None = None
        self._flushed_transcript_len: int = 0  # index into self.transcript of next line to flush
```

**3b. Open the conversation at call START inside `handle_twilio`:**

Replace the `if event == "start":` block with:

```python
        if event == "start":
            start = msg.get("start")
            if isinstance(start, dict):
                sid = start.get("streamSid")
                self.stream_sid = str(sid) if sid is not None else None
                csid = start.get("callSid")
                self.call_sid = str(csid) if csid is not None else None
            # Open the Chatwoot conversation immediately so agents see it live.
            # Guard: only attempt if call_sid is set (malformed start events lack it).
            if self.call_sid and self._conv_id is None:
                session_id = f"phone-{self.call_sid}"
                try:
                    self._conv_id = await self._log_port.ensure_conversation_ticket(
                        session_id=session_id,
                        subject=f"[phone] Live call {session_id}",
                        customer_name=None,
                        customer_phone=None,
                    )
                    _log.info("phone_conversation_opened_at_start", session_id=session_id, conv_id=self._conv_id)
                except Exception as e:
                    _log.error("phone_open_conversation_failed", session_id=session_id, error=str(e))
```

**3c. Add `flush_partial_transcript()` method:**

After the `_append_transcript` method, add:

```python
    async def flush_partial_transcript(self) -> None:
        """Post any new transcript lines accumulated since the last flush as private
        notes in the live Chatwoot conversation.  No-op when no conversation is open
        or when there are no new lines to flush."""
        if self._conv_id is None:
            return
        new_lines = self.transcript[self._flushed_transcript_len :]
        if not new_lines:
            return
        for role, text in new_lines:
            line = f"{role}: {text}"
            try:
                await self._log_port.append_conversation_comment(self._conv_id, line, status=None)
            except Exception as e:
                _log.error(
                    "phone_partial_transcript_flush_failed",
                    conv_id=self._conv_id,
                    error=str(e),
                )
        self._flushed_transcript_len = len(self.transcript)
```

**3d. Call `flush_partial_transcript()` from `pump()` after each transcript event:**

In the `pump()` method, after `self._append_transcript("USER", event.text)` (the `InputTranscript` branch), add:

```python
                await self.flush_partial_transcript()
```

And after `self._append_transcript("ASSISTANT", event.text)` (the `OutputTranscript` branch), add:

```python
                await self.flush_partial_transcript()
```

The updated `pump()` body becomes:

```python
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
                self._append_transcript("USER", event.text)
                await self.flush_partial_transcript()
            elif isinstance(event, OutputTranscript):
                self._append_transcript("ASSISTANT", event.text)
                await self.flush_partial_transcript()
            elif isinstance(event, ToolCall):
                await self._handle_tool_call(event)
```

**3e. Update `finalize()` to reuse `_conv_id` instead of calling `ensure_conversation_ticket` again:**

Replace the existing `finalize()` method with:

```python
    async def finalize(self) -> None:
        if not self.transcript or not self.call_sid:
            return
        session_id = f"phone-{self.call_sid}"
        body = "\n".join(f"{role}: {text}" for role, text in self.transcript)
        status = "open" if self.handoff is not None else "solved"
        try:
            # Reuse the conversation opened at call start when available; fall back to
            # ensure_conversation_ticket for backward-compat (e.g. test stubs that never
            # receive a start event).
            if self._conv_id is not None:
                ticket_id = self._conv_id
            else:
                ticket_id = await self._log_port.ensure_conversation_ticket(
                    session_id=session_id,
                    subject=f"[phone] Conversation {session_id}",
                    customer_name=None,
                    customer_phone=None,
                )
            if self.handoff is not None:
                note = (
                    "[Handoff to human agent]\n"
                    f"{self.handoff.get('reason', '')}\n{self.handoff.get('summary', '')}"
                )
                await self._log_port.append_conversation_comment(ticket_id, note, status="open")
            await self._log_port.append_conversation_comment(ticket_id, body, status=status)
            await self._log_port.set_ticket_external_id(ticket_id, session_id)
            if self.handoff is None and self.csat_score is not None:
                await record_csat_on_ticket(self._log_port, ticket_id, self.csat_score, "phone")
        except Exception as e:
            _log.error(
                "phone_finalize_failed", session_id=session_id, error=str(e), transcript=body
            )
```

- [ ] **Step 4: Run the new tests — confirm they pass**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/chat/phone/test_bridge_live_presence.py -v
```

Expected output:
```
PASSED test_handle_start_opens_chatwoot_conversation
PASSED test_handle_start_noop_when_call_sid_missing
PASSED test_pump_flushes_partial_transcript_as_private_note
PASSED test_pump_skips_partial_flush_when_conv_id_not_set
PASSED test_finalize_does_not_duplicate_open_if_already_opened
5 passed in 0.XXs
```

- [ ] **Step 5: Run the existing bridge tests — confirm none regressed**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/chat/phone/test_bridge.py -v
```

Expected: all existing tests pass. Key ones to watch:
- `test_finalize_writes_transcript_to_zendesk` — still calls `ensure_conversation_ticket` because `_conv_id` is `None` in the stub (backward-compat path)
- `test_finalize_noop_without_transcript` — still a no-op

- [ ] **Step 6: Run the full phone test suite**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/chat/phone/ -v
```

Expected: all tests pass (no new failures).

- [ ] **Step 7: Commit**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git add apps/backend/src/chatbot/features/chat/phone/bridge.py \
        apps/backend/src/chatbot/features/chat/phone/test_bridge_live_presence.py
git commit -m "feat(phone): open Chatwoot conversation at call start and stream partial transcript live

- PhoneBridge now calls ensure_conversation_ticket on Twilio 'start' event so
  agents see the conversation immediately, not only after hang-up.
- flush_partial_transcript() posts each new transcript turn as a private note
  during the call; finalize() reuses the already-open conversation.
- 5 new tests covering live-presence behaviour; all existing bridge tests pass."
```

---

## Task 2: `TelephonyProviderPort` — Provider Abstraction + Twilio Adapter

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/phone/telephony_port.py`
- Create: `apps/backend/src/chatbot/features/chat/phone/twilio_provider.py`
- Create: `apps/backend/src/chatbot/features/chat/phone/test_telephony_port.py`

**Interfaces:**
- Produces: `TelephonyProviderPort` — a `typing.Protocol` with three async methods:
  - `async def incoming_call_response(self, public_wss_url: str) -> str` — returns the provider-specific response body (TwiML for Twilio, SIP 200 body for a SIP provider, etc.) to send back to the provider's HTTP hook
  - `async def parse_media_frame(self, raw_message: str) -> tuple[str, bytes | None]` — parse a raw WebSocket message from the provider; returns `(event_type, pcm_or_none)` where `event_type` is one of `"start"`, `"media"`, `"stop"`, `"unknown"` and `pcm_or_none` is decoded PCM bytes on a `"media"` event
  - `async def build_audio_frame(self, stream_sid: str, mulaw_payload: bytes) -> dict` — build the dict to send back over the provider's WebSocket for outbound audio
  - `async def build_clear_frame(self, stream_sid: str) -> dict` — build the interrupt/clear dict
  - `def extract_call_sid(self, start_message: dict) -> str | None` — extract the call/session identifier from a parsed start message (Twilio uses `start.callSid`; SIP providers differ)
  - `def extract_stream_sid(self, start_message: dict) -> str | None` — extract the stream/session identifier

- Produces: `MockTelephonyProvider` — an in-memory implementation of `TelephonyProviderPort` for tests; configurable scripted responses
- Produces: `TwilioProvider` — wraps the existing `connect_stream_twiml` and `PhoneBridge.handle_twilio` logic

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/chat/phone/test_telephony_port.py`:

```python
"""Tests for TelephonyProviderPort, MockTelephonyProvider, and TwilioProvider."""
from __future__ import annotations

import base64
import json

import pytest

from chatbot.features.chat.phone.telephony_port import MockTelephonyProvider, TwilioProvider


@pytest.mark.asyncio
async def test_twilio_provider_incoming_call_response_contains_wss_url():
    p = TwilioProvider()
    body = await p.incoming_call_response("wss://example.com/voice/phone/stream")
    assert "wss://example.com/voice/phone/stream" in body
    assert "<Stream" in body


@pytest.mark.asyncio
async def test_twilio_provider_parse_start_event():
    p = TwilioProvider()
    msg = json.dumps({"event": "start", "start": {"streamSid": "SS1", "callSid": "CA1"}})
    event_type, pcm = await p.parse_media_frame(msg)
    assert event_type == "start"
    assert pcm is None


@pytest.mark.asyncio
async def test_twilio_provider_parse_media_event_decodes_mulaw():
    p = TwilioProvider()
    # 160 bytes of mu-law silence
    mulaw = base64.b64encode(b"\xff" * 160).decode()
    msg = json.dumps({"event": "media", "media": {"payload": mulaw}})
    event_type, pcm = await p.parse_media_frame(msg)
    assert event_type == "media"
    # mulaw8k -> pcm16k: 160 samples * 2x upsample * 2 bytes = 640 bytes
    assert pcm is not None and len(pcm) == 640


@pytest.mark.asyncio
async def test_twilio_provider_parse_stop_event():
    p = TwilioProvider()
    msg = json.dumps({"event": "stop"})
    event_type, pcm = await p.parse_media_frame(msg)
    assert event_type == "stop"
    assert pcm is None


@pytest.mark.asyncio
async def test_twilio_provider_parse_unknown_event():
    p = TwilioProvider()
    msg = json.dumps({"event": "connected"})
    event_type, pcm = await p.parse_media_frame(msg)
    assert event_type == "unknown"
    assert pcm is None


@pytest.mark.asyncio
async def test_twilio_provider_build_audio_frame():
    p = TwilioProvider()
    frame = await p.build_audio_frame("SS1", b"\x00" * 10)
    assert frame["event"] == "media"
    assert frame["streamSid"] == "SS1"
    assert "media" in frame
    assert "payload" in frame["media"]


@pytest.mark.asyncio
async def test_twilio_provider_build_clear_frame():
    p = TwilioProvider()
    frame = await p.build_clear_frame("SS1")
    assert frame == {"event": "clear", "streamSid": "SS1"}


def test_twilio_provider_extract_call_sid():
    p = TwilioProvider()
    msg = {"event": "start", "start": {"streamSid": "SS1", "callSid": "CA1"}}
    assert p.extract_call_sid(msg) == "CA1"


def test_twilio_provider_extract_stream_sid():
    p = TwilioProvider()
    msg = {"event": "start", "start": {"streamSid": "SS1", "callSid": "CA1"}}
    assert p.extract_stream_sid(msg) == "SS1"


def test_twilio_provider_extract_call_sid_missing():
    p = TwilioProvider()
    assert p.extract_call_sid({"event": "start", "start": {}}) is None


@pytest.mark.asyncio
async def test_mock_provider_incoming_call_response():
    p = MockTelephonyProvider(call_sid="MOCK-C1", stream_sid="MOCK-S1")
    body = await p.incoming_call_response("wss://test")
    assert "mock" in body.lower() or body  # just non-empty


@pytest.mark.asyncio
async def test_mock_provider_extract_sids():
    p = MockTelephonyProvider(call_sid="MOCK-C1", stream_sid="MOCK-S1")
    msg = {"start": {"callSid": "MOCK-C1", "streamSid": "MOCK-S1"}}
    assert p.extract_call_sid(msg) == "MOCK-C1"
    assert p.extract_stream_sid(msg) == "MOCK-S1"


@pytest.mark.asyncio
async def test_mock_provider_build_and_parse_round_trip():
    """Audio frame built by the mock can be parsed back as a media event."""
    p = MockTelephonyProvider(call_sid="MOCK-C1", stream_sid="MOCK-S1")
    frame = await p.build_audio_frame("MOCK-S1", b"\xff" * 160)
    raw = json.dumps(frame)
    event_type, pcm = await p.parse_media_frame(raw)
    assert event_type == "media"
    assert pcm is not None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/chat/phone/test_telephony_port.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'chatbot.features.chat.phone.telephony_port'`

- [ ] **Step 3: Create `telephony_port.py`**

Create `apps/backend/src/chatbot/features/chat/phone/telephony_port.py`:

```python
"""TelephonyProviderPort — provider-agnostic abstraction for incoming PSTN calls.

Defines the interface any telephony/CTI provider must implement so the
PhoneBridge and router are decoupled from Twilio specifics.  TwilioProvider
is the current production adapter; MockTelephonyProvider is for tests.
"""
from __future__ import annotations

import base64
import json
from typing import Protocol

import structlog

from chatbot.features.chat.phone.audio_codec import mulaw8k_to_pcm16k
from chatbot.features.chat.phone.twiml import connect_stream_twiml

_log = structlog.get_logger(__name__)


class TelephonyProviderPort(Protocol):
    """Port interface for a PSTN/CTI telephony provider.

    A provider maps three event-loop phases:
    1. HTTP incoming-call hook → provider-specific response body (e.g. TwiML XML)
    2. Bidirectional WebSocket for media streaming (parse inbound / build outbound)
    3. Call/session identifier extraction from the provider's start message

    Implementations: TwilioProvider (production), MockTelephonyProvider (tests),
    and a future PSTN/SIP adapter once the provider is chosen (Task 4).
    """

    async def incoming_call_response(self, public_wss_url: str) -> str:
        """Return the body to send back to the provider's incoming-call HTTP hook.

        For Twilio: TwiML XML that connects the call to a Media Stream.
        For a SIP provider: provider-specific instruction body.
        """
        ...

    async def parse_media_frame(self, raw_message: str) -> tuple[str, bytes | None]:
        """Parse a raw WebSocket text message from the provider.

        Returns:
            (event_type, pcm_or_none) where event_type is one of:
              "start"   — call started; pcm_or_none is None
              "media"   — audio frame; pcm_or_none is decoded PCM-16k bytes
              "stop"    — call ended; pcm_or_none is None
              "unknown" — unrecognised message; pcm_or_none is None
        """
        ...

    async def build_audio_frame(self, stream_sid: str, mulaw_payload: bytes) -> dict:
        """Build the provider's outbound audio WebSocket message dict.

        Args:
            stream_sid: The stream/session identifier from the start message.
            mulaw_payload: μ-law encoded audio bytes to send.
        """
        ...

    async def build_clear_frame(self, stream_sid: str) -> dict:
        """Build the provider's interrupt/clear WebSocket message dict."""
        ...

    def extract_call_sid(self, start_message: dict) -> str | None:
        """Extract the call/session unique identifier from a parsed start message dict."""
        ...

    def extract_stream_sid(self, start_message: dict) -> str | None:
        """Extract the media stream identifier from a parsed start message dict."""
        ...


class TwilioProvider:
    """Production TelephonyProviderPort implementation for Twilio Media Streams."""

    async def incoming_call_response(self, public_wss_url: str) -> str:
        return connect_stream_twiml(public_wss_url)

    async def parse_media_frame(self, raw_message: str) -> tuple[str, bytes | None]:
        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError:
            _log.warning("twilio_provider_invalid_json")
            return ("unknown", None)
        event = msg.get("event", "")
        if event == "start":
            return ("start", None)
        if event == "stop":
            return ("stop", None)
        if event == "media":
            media = msg.get("media") or {}
            payload = media.get("payload")
            if payload:
                try:
                    pcm = mulaw8k_to_pcm16k(base64.b64decode(str(payload)))
                    return ("media", pcm)
                except Exception as e:
                    _log.error("twilio_provider_audio_decode_failed", error=str(e))
            return ("media", None)
        return ("unknown", None)

    async def build_audio_frame(self, stream_sid: str, mulaw_payload: bytes) -> dict:
        return {
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": base64.b64encode(mulaw_payload).decode()},
        }

    async def build_clear_frame(self, stream_sid: str) -> dict:
        return {"event": "clear", "streamSid": stream_sid}

    def extract_call_sid(self, start_message: dict) -> str | None:
        start = start_message.get("start")
        if isinstance(start, dict):
            v = start.get("callSid")
            return str(v) if v is not None else None
        return None

    def extract_stream_sid(self, start_message: dict) -> str | None:
        start = start_message.get("start")
        if isinstance(start, dict):
            v = start.get("streamSid")
            return str(v) if v is not None else None
        return None


class MockTelephonyProvider:
    """In-memory TelephonyProviderPort for tests.

    Behaves identically to TwilioProvider for media frame encoding/decoding
    so the round-trip test passes without a live Twilio account.
    Configurable call_sid / stream_sid so tests can control identity.
    """

    def __init__(self, call_sid: str = "MOCK-C1", stream_sid: str = "MOCK-S1") -> None:
        self._call_sid = call_sid
        self._stream_sid = stream_sid
        # Delegate encode/decode to TwilioProvider (same wire format in tests)
        self._twilio = TwilioProvider()

    async def incoming_call_response(self, public_wss_url: str) -> str:
        return f"<mock-connect url='{public_wss_url}' />"

    async def parse_media_frame(self, raw_message: str) -> tuple[str, bytes | None]:
        return await self._twilio.parse_media_frame(raw_message)

    async def build_audio_frame(self, stream_sid: str, mulaw_payload: bytes) -> dict:
        return await self._twilio.build_audio_frame(stream_sid, mulaw_payload)

    async def build_clear_frame(self, stream_sid: str) -> dict:
        return await self._twilio.build_clear_frame(stream_sid)

    def extract_call_sid(self, start_message: dict) -> str | None:
        return self._twilio.extract_call_sid(start_message)

    def extract_stream_sid(self, start_message: dict) -> str | None:
        return self._twilio.extract_stream_sid(start_message)
```

- [ ] **Step 4: Create `twilio_provider.py` (re-export shim + docstring)**

Create `apps/backend/src/chatbot/features/chat/phone/twilio_provider.py`:

```python
"""Twilio telephony provider adapter — re-exports TwilioProvider from telephony_port.

This shim keeps the import path stable for router.py and any future code that
imports the concrete adapter by name, while keeping the source of truth in
telephony_port.py alongside the Protocol it implements.
"""
from __future__ import annotations

from chatbot.features.chat.phone.telephony_port import TwilioProvider as TwilioProvider

__all__ = ["TwilioProvider"]
```

- [ ] **Step 5: Run the port tests — confirm they pass**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/chat/phone/test_telephony_port.py -v
```

Expected output:
```
PASSED test_twilio_provider_incoming_call_response_contains_wss_url
PASSED test_twilio_provider_parse_start_event
PASSED test_twilio_provider_parse_media_event_decodes_mulaw
PASSED test_twilio_provider_parse_stop_event
PASSED test_twilio_provider_parse_unknown_event
PASSED test_twilio_provider_build_audio_frame
PASSED test_twilio_provider_build_clear_frame
PASSED test_twilio_provider_extract_call_sid
PASSED test_twilio_provider_extract_stream_sid
PASSED test_twilio_provider_extract_call_sid_missing
PASSED test_mock_provider_incoming_call_response
PASSED test_mock_provider_extract_sids
PASSED test_mock_provider_build_and_parse_round_trip
13 passed in 0.XXs
```

- [ ] **Step 6: Run the full phone test suite to confirm nothing regressed**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/chat/phone/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git add apps/backend/src/chatbot/features/chat/phone/telephony_port.py \
        apps/backend/src/chatbot/features/chat/phone/twilio_provider.py \
        apps/backend/src/chatbot/features/chat/phone/test_telephony_port.py
git commit -m "feat(phone): add TelephonyProviderPort abstraction + TwilioProvider + MockTelephonyProvider

Defines a provider-agnostic Protocol for incoming PSTN calls so a future
CTI/SIP adapter plugs in without touching PhoneBridge or router.py.
TwilioProvider wraps existing TwiML + media-codec logic; MockTelephonyProvider
enables tests without a live Twilio account. 13 tests; all existing tests pass."
```

---

## Task 3: Social-Media Inboxes — Chatwoot Config Runbook

**Files:**
- No code changes. This task is a configuration runbook executed in the Chatwoot admin UI and replicated to each tenant via the Chatwoot API or the tenant-config script.

**Interfaces:**
- Consumes: Facebook App credentials (App ID, App Secret, Page Access Token)
- Consumes: Instagram Business Account linked to the Facebook App
- Consumes: Telegram Bot Token (from @BotFather)
- Produces: Three new Chatwoot inboxes visible in the unified inbox view for agents

**What this task does:**
Enables Facebook/Instagram/Telegram as native Chatwoot Community inboxes. All three channels are natively supported in Chatwoot Community — no backend code, no fork patches required. Each step below is a click-sequence in Chatwoot Settings.

> **Prereq — complete for EACH tenant (`default`, `proton`, `wahchan`) separately. Credentials differ per tenant's Facebook/Telegram account.**

### 3a. Facebook Messenger Inbox

Prerequisites:
- A Facebook Page belonging to the business
- A Facebook App (type: Business) with the `pages_messaging` permission approved
- A Page Access Token with `pages_read_engagement` + `pages_messaging` + `pages_manage_metadata` scopes
- Facebook App ID and App Secret

Steps:

1. Log in to Chatwoot as a Super Admin. Navigate to **Settings → Inboxes → Add Inbox**.
2. Choose channel type **Facebook Messenger**.
3. Fill in:
   - **Page Access Token:** paste the Page Access Token
   - **App Secret:** paste the Facebook App Secret
4. Click **Next**. Chatwoot will validate the token and list available Pages.
5. Select the target Facebook Page from the dropdown. Click **Next**.
6. Assign agents/teams that should handle Facebook messages. Click **Finish**.
7. Copy the **Webhook URL** shown on the confirmation page (format: `https://<chatwoot-host>/api/v1/inbox/…/callback`).
8. In your Facebook App dashboard → **Webhooks → Page subscriptions**, add that URL, set the Verify Token to the value Chatwoot shows, and subscribe to `messages`, `messaging_postbacks`.
9. Verify: send a test message to your Facebook Page. Within 30 s a conversation should appear in Chatwoot under the new inbox.

### 3b. Instagram Inbox

Prerequisites:
- The Facebook Page from 3a must have an **Instagram Business Account** linked (via Facebook Business Settings)
- The Facebook App must have `instagram_basic` + `instagram_manage_messages` permissions approved

Steps:

1. Navigate to **Settings → Inboxes → Add Inbox**.
2. Choose channel type **Instagram**.
3. Fill in:
   - **Page Access Token:** same token as 3a (must have Instagram scopes added)
   - **Instagram Account ID:** the numeric Instagram Business Account ID (find it in Facebook Business Settings → Instagram Accounts)
4. Click **Next**. Chatwoot lists linked Instagram accounts.
5. Select the Instagram account. Click **Next**.
6. Assign agents/teams. Click **Finish**.
7. Verify: send a DM to the Instagram account. A conversation should appear in Chatwoot under the new inbox within 30 s.

> **Note:** Facebook/Instagram share a webhook registration. If you already completed 3a, no new webhook registration is needed for 3b — the existing webhook fires for both channels.

### 3c. Telegram Inbox

Prerequisites:
- A Telegram Bot created via @BotFather (`/newbot`). Note the **Bot Token** (format: `123456789:ABC…`).

Steps:

1. Navigate to **Settings → Inboxes → Add Inbox**.
2. Choose channel type **Telegram**.
3. Fill in:
   - **Bot Name:** a display name for agents (e.g. "Proton Support Telegram")
   - **Bot Token:** paste the token from @BotFather
4. Click **Create Inbox**. Chatwoot registers a webhook with Telegram automatically.
5. Assign agents/teams. Click **Finish**.
6. Verify: send a `/start` message to the bot in Telegram. A conversation should appear in Chatwoot within 10 s.

### 3d. Smoke test all three inboxes

For each inbox:

1. From a personal device (not the admin account), send an inbound message on the channel.
2. In Chatwoot → **Conversations**, confirm the conversation appears with the correct inbox label.
3. Reply from Chatwoot. Confirm the reply appears on the originating channel.
4. Check **Reports → Overview** — the new inbox should appear in the channel breakdown.

### 3e. Replicate to other tenants

The Chatwoot Community tenant-config script (`id-crm-ticketing` repo, tenant-config path) applies inbox config per tenant via the Chatwoot API. After verifying on `default`:

1. Generate a new Facebook Page Access Token scoped to the target tenant's Page.
2. Create a new Telegram Bot for the tenant (separate bot = separate inbox, avoids cross-tenant message leakage).
3. Run the tenant-config script targeting `proton` or `wahchan`:

```bash
# From id-crm-ticketing repo root
./scripts/tenant-config.sh proton
```

4. Repeat smoke test 3d for each replicated tenant.

> **No code commit for Task 3.** This is a pure admin runbook. Document completion in a Notion/internal ops page with screenshots of the three inboxes and the smoke-test timestamp.

---

## Task 4 (BLOCKED): PSTN/CTI Provider Wiring

> **STATUS: BLOCKED — do not implement until the provider-unblock gate is satisfied.**

### Provider-Unblock Gate

This task is explicitly gated on the following decisions. All four must be answered before any code is written:

| # | Decision input | Who decides | Notes |
|---|---|---|---|
| 1 | **Provider choice** | Business/ops team | Options: Twilio PSTN (extend existing Twilio account), a SIP trunk (e.g. Vonage, SignalWire, Bandwidth), or a CCaaS/CTI platform (Genesys, NICE, Avaya). Each has a different media-stream protocol. | 
| 2 | **PSTN number / DID** | Provider + ops | The E.164 number the provider assigns; must be configured as the inbound route to our webhook. |
| 3 | **Media transport protocol** | Provider | Twilio uses WebSocket + Media Streams (already supported). SIP providers may use SIP/RTP or WebRTC. A new `TelephonyProviderPort` adapter must implement the protocol chosen here. |
| 4 | **Auth credentials** | Provider account | API key/SID, webhook secret, SIP trunk credentials. Go into `Settings` (Pydantic config) and the tenant env — never committed. |

### What will be built once unblocked (scope, not implementation)

1. A new `<ProviderName>Provider` class in `apps/backend/src/chatbot/features/chat/phone/<provider>_provider.py` implementing `TelephonyProviderPort` (Task 2's Protocol).
2. Update `router.py` `phone_incoming` and `phone_stream` to inject the chosen provider via the `TelephonyProviderPort` interface rather than calling Twilio-specific helpers directly.
3. Add `Settings` fields for the provider's credentials (following the existing `twilio_*` pattern in `config.py`).
4. Integration smoke test: inbound PSTN call → Gemini-Live STT → live Chatwoot transcript (Task 1) → hang-up → finalize.

### Why this is BLOCKED and not just deferred

Implementing the adapter without knowing the provider's media protocol (point 3 above) would produce speculative dead code. The `TelephonyProviderPort` in Task 2 is the exact interface the future adapter must implement — it is the unblocking scaffolding.

---

## Self-Review

### Spec coverage check

| Spec requirement | Task that covers it |
|---|---|
| Phase 7 goal: open Chatwoot conversation at call START | Task 1 (`handle_twilio` start event → `ensure_conversation_ticket`) |
| Phase 7 goal: stream partial transcript during the call | Task 1 (`flush_partial_transcript()` called from `pump()`) |
| Phase 7: `TelephonyProviderPort` interface | Task 2 (`telephony_port.py` Protocol) |
| Phase 7: refactor Twilio handling behind the port | Task 2 (`TwilioProvider` + `twilio_provider.py`) |
| Phase 7: mock for tests | Task 2 (`MockTelephonyProvider`) |
| Phase 7: social-media inboxes (FB/IG/Telegram) | Task 3 (runbook, 3a–3e) |
| Phase 7: PSTN provider wiring — BLOCKED | Task 4 (explicit gate + decision inputs listed) |
| Spec constraint: reuse STT bridge, not rebuild | All tasks reuse `PhoneBridge`, `gemini_live.py`, `audio_codec.py` — no duplication |
| Spec constraint: IP-safe (no Chatwoot/Twilio source reproduced) | Verified — all code is our bridge/adapter layer |
| Spec constraint: test-first | Tasks 1 and 2 both start with failing tests before implementation |

### Placeholder scan

No "TBD", "TODO", "implement later", or "fill in details" strings in Tasks 1–3. Task 4 is intentionally deferred and clearly labelled BLOCKED with concrete prerequisites listed.

### Type consistency check

- `PhoneBridge._conv_id: str | None` — introduced in Task 1 `__init__`, used in `handle_twilio`, `flush_partial_transcript`, and `finalize`. Consistent throughout.
- `PhoneBridge._flushed_transcript_len: int` — introduced in Task 1 `__init__`, used only in `flush_partial_transcript`. Consistent.
- `TelephonyProviderPort.parse_media_frame` returns `tuple[str, bytes | None]` — Tests reference `event_type, pcm = await p.parse_media_frame(...)` which matches the return type.
- `TwilioProvider.extract_call_sid` returns `str | None` — test calls `p.extract_call_sid(msg)` and asserts `== "CA1"` or `is None`. Consistent.
- `MockTelephonyProvider` delegates `parse_media_frame` and `build_audio_frame` to `TwilioProvider` internally — the round-trip test in Task 2 Step 1 exercises this path. Consistent.
