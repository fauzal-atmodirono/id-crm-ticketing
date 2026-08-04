# Package C — Telephony: Live Transcript, Handoff, Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the ticket at the start of a call with a live transcript and a derived status, transfer a caller to a human when the AI cannot help, and record calls for QA.

**Architecture:** All three features hang off one missing capability — acting on the `CallSid` that `PhoneBridge` already captures but ignores. Task 1 builds a thin Twilio call-control client; everything after is a modest addition on top. Feature ordering is deliberate: transcript-to-ticket first because it is independent of Twilio call control and immediately useful, then recording, then handoff.

**Tech Stack:** Python 3.12, FastAPI, the `twilio` SDK (already a dependency — see `features/chat/phone/token.py`), Gemini Live, Twilio Programmable Voice + Media Streams, pytest with `respx`.

**Spec:** `docs/superpowers/specs/2026-08-04-pkg-c-telephony-handoff-transcript-busy-recording-design.md`

## Global Constraints

- **Every new setting defaults to today's behaviour.** With all flags off, the system must be byte-identical to the current build. Assert this explicitly.
- **A CRM or Twilio failure must never drop a live call.** Every new call path is fail-open: log and continue, never raise into the audio loop.
- New settings go in **both** `backend/apps/backend/src/chatbot/platform/config.py` and `backend/apps/backend/.env.example`.
- Run backend tests from `backend/apps/backend`: `.venv/bin/pytest src/`. Lint with `.venv/bin/ruff check . --fix` and `.venv/bin/ruff format .`; type-check with `.venv/bin/mypy src/ --strict`.
- Twilio credentials already exist in config: `twilio_account_sid`, `twilio_auth_token`, `twilio_phone_number`. Never log or echo the auth token.
- **Never place a real outbound call from a test.** The Twilio client is injected and stubbed in every test.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/chat/phone/call_control.py` | Create: Twilio REST call control — redirect a live call, start a recording |
| `backend/.../features/chat/phone/test_call_control.py` | Create: its tests |
| `backend/.../features/chat/phone/transcript_sink.py` | Create: batching of transcript fragments into Chatwoot messages |
| `backend/.../features/chat/phone/test_transcript_sink.py` | Create: its tests |
| `backend/.../features/chat/phone/bridge.py` | Modify: create the ticket at call start, flush transcript, trigger recording and handoff |
| `backend/.../features/chat/phone/handoff_target.py` | Create: `HandoffTargetResolver` returning a target descriptor |
| `backend/.../features/chat/phone/twiml.py` | Modify: add the dial TwiML builder |
| `backend/.../features/chat/router.py` | Modify: add `/webhooks/phone/dial-status` and `/webhooks/phone/recording-status` |
| `backend/.../platform/config.py` | Modify: the new phone settings |
| `backend/apps/backend/.env.example` | Modify: document them |

---

### Task 1: Twilio call-control client

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/call_control.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_call_control.py`

**Interfaces:**
- Consumes: `Settings.twilio_account_sid`, `Settings.twilio_auth_token`.
- Produces:
  - `class CallControl` with `__init__(self, settings: Settings, client: Any | None = None)`
  - `async def redirect(self, call_sid: str, twiml: str) -> bool`
  - `async def start_recording(self, call_sid: str, status_callback: str) -> str | None` returning the recording SID
  - Both return falsy on any failure and never raise. Tasks 4 and 6 depend on these exact names.

- [ ] **Step 1: Write the failing tests**

```python
"""Twilio call control. Every method is fail-open: a Twilio outage must degrade
the feature, never drop the live call it is attached to."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone.call_control import CallControl


class _FakeCalls:
    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.updated: list[tuple[str, str]] = []
        self.recorded: list[str] = []

    def __call__(self, call_sid: str):
        self.sid = call_sid
        return self

    def update(self, twiml: str):
        if self.raises:
            raise self.raises
        self.updated.append((self.sid, twiml))
        return object()

    @property
    def recordings(self):
        return self

    def create(self, **kwargs):
        if self.raises:
            raise self.raises
        self.recorded.append(self.sid)
        return type("R", (), {"sid": "RE123"})()


class _FakeTwilio:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls = _FakeCalls(raises)


async def test_redirect_updates_the_call_with_twiml(settings):
    fake = _FakeTwilio()
    cc = CallControl(settings, client=fake)
    ok = await cc.redirect("CA123", "<Response/>")
    assert ok is True
    assert fake.calls.updated == [("CA123", "<Response/>")]


async def test_redirect_returns_false_on_twilio_error(settings):
    cc = CallControl(settings, client=_FakeTwilio(raises=RuntimeError("boom")))
    assert await cc.redirect("CA123", "<Response/>") is False


async def test_start_recording_returns_sid(settings):
    fake = _FakeTwilio()
    cc = CallControl(settings, client=fake)
    assert await cc.start_recording("CA123", "https://x/cb") == "RE123"
    assert fake.calls.recorded == ["CA123"]


async def test_start_recording_returns_none_on_error(settings):
    cc = CallControl(settings, client=_FakeTwilio(raises=RuntimeError("boom")))
    assert await cc.start_recording("CA123", "https://x/cb") is None
```

Add a `settings` fixture in this file returning the app `Settings` with `twilio_account_sid="AC1"` and `twilio_auth_token="tok"`, matching how `test_config_twilio.py` constructs settings.

- [ ] **Step 2: Run and watch fail**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_call_control.py -v`
Expected: FAIL — `ModuleNotFoundError: chatbot.features.chat.phone.call_control`.

- [ ] **Step 3: Implement**

```python
"""Twilio REST call control for the phone channel.

The Twilio SDK is synchronous, so every call runs through asyncio.to_thread —
blocking the event loop here would stall the Media Stream audio pump.

Every method is fail-open by design: this code sits in the path of a live
conversation, and a Twilio API failure must degrade the feature rather than
drop the caller.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class CallControl:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def _twilio(self) -> Any | None:
        if self._client is not None:
            return self._client
        sid = self._settings.twilio_account_sid
        token = self._settings.twilio_auth_token
        if not sid or not token:
            return None
        from twilio.rest import Client

        self._client = Client(sid, token)
        return self._client

    async def redirect(self, call_sid: str, twiml: str) -> bool:
        """Replace the in-progress call's TwiML. Ends the current <Connect><Stream>
        and runs the new verbs on the same call."""
        client = self._twilio()
        if client is None:
            _log.warning("call_control_unconfigured", call_sid=call_sid)
            return False
        try:
            await asyncio.to_thread(lambda: client.calls(call_sid).update(twiml=twiml))
            return True
        except Exception as e:
            _log.error("call_redirect_failed", call_sid=call_sid, error=str(e))
            return False

    async def start_recording(self, call_sid: str, status_callback: str) -> str | None:
        """Start a dual-channel recording on a live call. Returns the recording SID."""
        client = self._twilio()
        if client is None:
            return None
        try:
            rec = await asyncio.to_thread(
                lambda: client.calls(call_sid).recordings.create(
                    recording_channels="dual",
                    recording_status_callback=status_callback,
                )
            )
            return str(rec.sid)
        except Exception as e:
            _log.error("call_recording_start_failed", call_sid=call_sid, error=str(e))
            return None
```

- [ ] **Step 4: Run and watch pass**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/phone/test_call_control.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/call_control.py backend/apps/backend/src/chatbot/features/chat/phone/test_call_control.py
git commit -m "feat(phone): add fail-open Twilio call-control client"
```

---

### Task 2: Transcript batching sink

`PhoneBridge` already accumulates `self.transcript` as `(role, text)` pairs from Gemini Live. Gemini streams many small deltas, so one Chatwoot message per delta would be unusable and rate-limited. This task owns the batching rule in isolation so it can be tested without a live call.

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/transcript_sink.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_transcript_sink.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class TranscriptSink` with `__init__(self, flush_seconds: float, now: Callable[[], float])`
  - `def add(self, role: str, text: str) -> None`
  - `def take_if_due(self, *, force: bool = False) -> str | None` — returns the formatted block to post, or `None` when nothing is due
  - Task 3 uses both methods.

- [ ] **Step 1: Write the failing tests**

```python
"""Transcript fragments batch into readable blocks. Two triggers: a speaker
change (a completed turn), or the flush interval elapsing during a long
monologue. Time is injected so the tests are deterministic."""

from __future__ import annotations

from chatbot.features.chat.phone.transcript_sink import TranscriptSink


def _sink(clock):
    return TranscriptSink(flush_seconds=15.0, now=lambda: clock[0])


def test_nothing_is_due_before_a_turn_completes():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "hello ")
    s.add("USER", "there")
    assert s.take_if_due() is None


def test_speaker_change_flushes_the_completed_turn():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "hello there")
    s.add("ASSISTANT", "hi")
    block = s.take_if_due()
    assert block == "USER: hello there"


def test_long_monologue_flushes_on_the_timer():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "a very long complaint")
    clock[0] = 20.0
    assert s.take_if_due() == "USER: a very long complaint"


def test_taking_twice_does_not_repeat_content():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "one")
    s.add("ASSISTANT", "two")
    assert s.take_if_due() == "USER: one"
    assert s.take_if_due() is None


def test_force_flushes_whatever_remains():
    clock = [0.0]
    s = _sink(clock)
    s.add("USER", "trailing words")
    assert s.take_if_due(force=True) == "USER: trailing words"
```

- [ ] **Step 2: Run and watch fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_transcript_sink.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
"""Batches streamed transcript deltas into postable blocks.

Gemini Live emits transcription as many small fragments. Posting one Chatwoot
message per fragment would be unreadable and would hit rate limits, so
fragments are concatenated per speaker and released either when the speaker
changes (the turn is complete) or when the flush interval elapses (so a long
monologue still appears during the call).
"""

from __future__ import annotations

from collections.abc import Callable


class TranscriptSink:
    def __init__(self, flush_seconds: float, now: Callable[[], float]) -> None:
        self._flush_seconds = flush_seconds
        self._now = now
        self._pending: list[tuple[str, str]] = []
        self._last_flush = now()

    def add(self, role: str, text: str) -> None:
        if self._pending and self._pending[-1][0] == role:
            prev_role, prev_text = self._pending[-1]
            self._pending[-1] = (prev_role, prev_text + text)
        else:
            self._pending.append((role, text))

    def take_if_due(self, *, force: bool = False) -> str | None:
        if not self._pending:
            return None
        turn_completed = len(self._pending) > 1
        timer_elapsed = (self._now() - self._last_flush) >= self._flush_seconds
        if not (force or turn_completed or timer_elapsed):
            return None
        # Keep the in-progress turn pending unless forced: releasing it would
        # split one sentence across two Chatwoot messages.
        release = self._pending if force or timer_elapsed else self._pending[:-1]
        self._pending = [] if (force or timer_elapsed) else self._pending[-1:]
        self._last_flush = self._now()
        if not release:
            return None
        return "\n".join(f"{role}: {text}" for role, text in release)
```

- [ ] **Step 4: Run and watch pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_transcript_sink.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/transcript_sink.py backend/apps/backend/src/chatbot/features/chat/phone/test_transcript_sink.py
git commit -m "feat(phone): add transcript batching sink"
```

---

### Task 3: Create the ticket at call start and stream the transcript

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/phone/bridge.py`
- Modify: `backend/apps/backend/src/chatbot/platform/config.py`
- Modify: `backend/apps/backend/.env.example`
- Modify: `backend/apps/backend/src/chatbot/features/chat/phone/test_bridge.py`

**Interfaces:**
- Consumes: `TranscriptSink` (Task 2), the existing `ConversationLogPort.ensure_conversation_ticket` and `append_conversation_comment`.
- Produces: `PhoneBridge.ticket_id: str | None`, populated during the call. Tasks 4 and 6 write attributes against it.

- [ ] **Step 1: Add the settings**

In `config.py`:

```python
    phone_transcript_live_enabled: bool = False
    phone_transcript_flush_seconds: float = 15.0
```

In `.env.example`:

```bash
# Create the Chatwoot conversation when the call starts and stream the
# transcript into it, instead of writing everything once the call ends.
PHONE_TRANSCRIPT_LIVE_ENABLED=false
PHONE_TRANSCRIPT_FLUSH_SECONDS=15
```

- [ ] **Step 2: Write the failing tests**

Append to `test_bridge.py`, following its existing fake-`LiveSession` and fake-log-port pattern:

```python
async def test_ticket_is_created_on_stream_start_when_enabled(settings, log_port, live):
    settings.phone_transcript_live_enabled = True
    bridge = PhoneBridge(live, knowledge_port, log_port, send_twilio, settings)
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert log_port.ensured == ["phone-CA1"]
    assert bridge.ticket_id is not None


async def test_ticket_is_not_created_when_flag_off(settings, log_port, live):
    settings.phone_transcript_live_enabled = False
    bridge = PhoneBridge(live, knowledge_port, log_port, send_twilio, settings)
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert log_port.ensured == []


async def test_ticket_creation_failure_does_not_break_the_call(settings, log_port, live):
    settings.phone_transcript_live_enabled = True
    log_port.ensure_raises = RuntimeError("chatwoot down")
    bridge = PhoneBridge(live, knowledge_port, log_port, send_twilio, settings)
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    assert bridge.ticket_id is None
    await bridge.handle_twilio({"event": "media", "media": {"payload": _silence_b64()}})


async def test_finalize_is_idempotent_after_live_creation(settings, log_port, live):
    settings.phone_transcript_live_enabled = True
    bridge = PhoneBridge(live, knowledge_port, log_port, send_twilio, settings)
    await bridge.handle_twilio({"event": "start", "start": {"streamSid": "MZ1", "callSid": "CA1"}})
    bridge.transcript = [("USER", "hi")]
    await bridge.finalize()
    assert log_port.ensured == ["phone-CA1"]
```

Extend the existing fake log port with an `ensured: list[str]` recorder and an `ensure_raises` hook.

- [ ] **Step 3: Run and watch fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_bridge.py -v`
Expected: the four new tests FAIL — `PhoneBridge` has no `ticket_id`.

- [ ] **Step 4: Implement**

In `PhoneBridge.__init__`, add `self.ticket_id: str | None = None` and construct the sink from settings. In `handle_twilio`, after `call_sid` is set on the `start` event, create the ticket as a background task when the flag is on, wrapped so failure only logs — `ensure_conversation_ticket` is keyed on `session_id`, so a later retry in `finalize()` returns the same conversation rather than a duplicate.

In `pump()`, feed each `InputTranscript` / `OutputTranscript` into the sink as well as the existing `_append_transcript`, and post any due block to `self.ticket_id`.

In `finalize()`, force a final flush, then keep today's behaviour — the whole-transcript comment and status update — so a call that dies mid-stream still ends consistent.

- [ ] **Step 5: Run and watch pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/ -v`
Expected: all PASS, including every pre-existing bridge test.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/bridge.py backend/apps/backend/src/chatbot/features/chat/phone/test_bridge.py backend/apps/backend/src/chatbot/platform/config.py backend/apps/backend/.env.example
git commit -m "feat(phone): create the ticket at call start and stream the transcript"
```

---

### Task 4: Derive case status and taxonomy from the transcript

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/transcript_classifier.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_transcript_classifier.py`
- Modify: `bridge.py` (`finalize`), `config.py`, `.env.example`

**Interfaces:**
- Consumes: `features/metrics/mapping.py` for the division/concern vocabulary — **reuse it, do not invent a second taxonomy**, or Package E's reporting will not aggregate these calls.
- Produces: `async def classify(transcript: str, gemini: Any) -> dict` returning `{"case_type", "division", "concern", "status"}`, all optional keys, `{}` on any failure.

- [ ] **Step 1: Add the setting**

`phone_transcript_classification_enabled: bool = False` in config, documented in `.env.example`.

- [ ] **Step 2: Write the failing tests**

Cover: a well-formed model response maps to the four keys; an unparseable response returns `{}`; a raised exception returns `{}`; and a returned division outside the `mapping.py` vocabulary is dropped rather than written through. That last one is the important test — an invented division silently corrupts reporting.

- [ ] **Step 3: Run and watch fail**, then implement, then re-run until green.

- [ ] **Step 4: Wire into `finalize()`**

When the flag is on, classify the full transcript and write the results as conversation custom attributes. Status resolution stays fail-open: on `{}`, fall back to today's exact binary rule — `open` if a handoff was requested, else `solved`.

- [ ] **Step 5: Run the full backend suite and commit**

```bash
.venv/bin/pytest src/ -q
git add -A backend/apps/backend/src/chatbot/features/chat/phone backend/apps/backend/src/chatbot/platform/config.py backend/apps/backend/.env.example
git commit -m "feat(phone): derive case type, division and status from the call transcript"
```

---

### Task 5: Call recording

**Files:**
- Modify: `bridge.py`, `router.py`, `config.py`, `.env.example`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_recording.py`

**Interfaces:**
- Consumes: `CallControl.start_recording` (Task 1), `PhoneBridge.ticket_id` (Task 3).
- Produces: `POST /webhooks/phone/recording-status`, and conversation custom attributes `recording_sid`, `recording_duration`, `recording_url`.

- [ ] **Step 1: Add the settings**

```python
    phone_recording_enabled: bool = False
    phone_recording_announcement: str = ""
    phone_recording_retention_days: int = 90
```

- [ ] **Step 2: Write the failing tests**

Cover: recording starts on stream start only when the flag is on; a `start_recording` failure does not affect the call; the status callback persists the three attributes; a callback for an unknown call is ignored rather than raising.

- [ ] **Step 3: Implement, run, and confirm green.**

- [ ] **Step 4: Add the PDPA announcement — this is not optional**

The caller must hear a recorded-line notice before recording begins, in English and Bahasa Melayu, and the text must be operator-configurable rather than hard-coded (it belongs alongside the existing lifecycle/persona messages). **If `phone_recording_enabled` is true and no announcement is configured, refuse to start recording and log a warning.** Recording customers without notice is a legal exposure, and a config mistake must fail closed here, unlike everywhere else in this package.

- [ ] **Step 5: Gate retrieval behind a permission**

Add `call_recording.listen` to `features/authz/seed.py` and require it on any endpoint exposing a recording. Never hand a raw Twilio URL to the browser.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(phone): record calls with a mandatory PDPA announcement and gated retrieval"
```

---

### Task 6: Real hand-off to a human

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/handoff_target.py`
- Modify: `twiml.py`, `bridge.py`, `router.py`, `config.py`, `.env.example`
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_handoff.py`

**Interfaces:**
- Consumes: `CallControl.redirect` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class HandoffTarget: kind: str; value: str` where `kind` is `"pstn"` or `"client"`
  - `class HandoffTargetResolver` with `async def resolve(self) -> HandoffTarget | None`
  - `def dial_twiml(target: HandoffTarget, action_url: str, timeout: int) -> str`

**Read before starting:** the target type is a **descriptor, not a phone number**, because Twilio cannot connect a WhatsApp call to any PSTN endpoint (spec §12.3). A `str` return type here would have to be unpicked later.

- [ ] **Step 1: Add the settings**

```python
    phone_handoff_enabled: bool = False
    phone_handoff_target_number: str = ""
    phone_handoff_timeout_seconds: int = 30
```

- [ ] **Step 2: Write the failing tests**

Cover: `request_human_handoff` issues exactly one call-update with well-formed dial TwiML; the tool response becomes `{"status": "transferring"}` rather than today's inaccurate `"ticket_created"`; a Twilio failure leaves the call alive and falls back to today's ticket-only behaviour; and each `<Dial action>` outcome (`completed`, `no-answer`, `busy`, `failed`) drives the right fallback.

- [ ] **Step 3: Implement the resolver, the TwiML builder, and the bridge wiring.**

Phase 1 resolves the static `phone_handoff_target_number` as `HandoffTarget(kind="pstn", value=...)`. The routing-backed per-agent implementation is a second implementation of the same interface, added when the §5.2 decision arrives.

- [ ] **Step 4: Handle the unanswered case explicitly**

On `no-answer` / `busy` / `failed`, respond with fallback TwiML that apologises and offers a callback, set the conversation `open`, and add an `unanswered_handoff` label. **Silently dropping the caller is the worst outcome available and is exactly what an untested implementation does.**

- [ ] **Step 5: Reuse the existing business-hours logic** for the out-of-hours path rather than adding a second notion of open hours.

- [ ] **Step 6: Run the full suite and commit**

```bash
.venv/bin/pytest src/ -q
git commit -m "feat(phone): transfer a live call to a human with explicit failure fallbacks"
```

---

### Task 7: Manual verification on a real number

None of the above proves a call works. Unit tests cannot catch a mis-built TwiML verb.

- [ ] **Step 1:** Deploy backend to `proton`, enable the flags one at a time.
- [ ] **Step 2:** Place a real call. Confirm the conversation exists **mid-call** with a growing transcript.
- [ ] **Step 3:** Force a handoff. Confirm audio connects both ways on the target phone.
- [ ] **Step 4:** Let a handoff go unanswered. Confirm the caller hears the fallback and the conversation carries `unanswered_handoff`.
- [ ] **Step 5:** Confirm the recording lands, plays, and is unreachable without `call_recording.listen`.
- [ ] **Step 6:** Confirm the transfer moment is not an abrupt silence — the AI should say a handover line before the stream tears down.
- [ ] **Step 7:** Update `docs/analysis/proton-demo-feedback-coverage-2026-07-28.md` items **#23** and **#27**, stating plainly which paths were exercised and which were not.

---

## Blocked — not planned here

**Feature 3, auto-busy (#21).** It requires knowing *which agent* was dialled, which only exists under the per-agent-numbers option of spec §5.2 — still an open decision with the client. Under the hunt-group option this feature **cannot work at all**, and the coverage document should say so rather than claiming it shipped. Re-plan once the decision lands; the work is then an `on_call` set consulted by `RoutingService.pick_agent` exactly as the concurrent-conversation cap already is, plus a stale-entry sweep so a missed callback cannot remove an agent from routing permanently.

**WhatsApp Business Calling.** Blocked on Meta Business Verification and a real WABA number; the current WhatsApp number is a Twilio sandbox. See spec §12.
