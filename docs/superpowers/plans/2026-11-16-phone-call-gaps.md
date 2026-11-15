# Phone Call Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure and shorten the silent gaps a caller hears on the AI-first phone line: per-turn timing, a spoken filler and a time cap on knowledge-base lookups, and a notice that is ready on the first call after a restart.

**Architecture:** All changes are in the backend phone bridge (`features/chat/phone/`) and its router. Measurement first (logging only). Behaviour changes sit behind settings whose defaults keep today's behaviour byte-identical; proton switches them on by env after a rehearsal call proves them. The VAD end-of-speech tuning is env-only and deliberately left to after the demo (Task 5 is a runbook step, not code).

**Tech Stack:** Python 3.12, FastAPI, google-genai Live API, structlog, pytest (`asyncio_mode=auto`).

**Spec:** this conversation, 16 Nov 2026 — the gap analysis table (per-turn VAD wait ~1–1.5 s; silent `kb_search` 1–3 s; cold notice cache ~10 s on the first call after a restart; handoff ringing). Live evidence: `phone_call_timing first_model_audio_ms≈4230 live_connect_ms≈3170`, notice audio 9.3–9.5 s.

## Global Constraints

- Run everything from `backend/apps/backend`: `.venv/bin/pytest src/`, `.venv/bin/ruff check`, `.venv/bin/mypy src/ --strict` (wiring tests need `GOOGLE_API_KEY=test-dummy` in the environment).
- Every new setting goes in `src/chatbot/platform/config.py` AND `deploy/tenants/example.env` (commented, with its default), names verbatim.
- Defaults are byte-identical to today: a tenant that sets nothing sees no change in prompts, tool behaviour or TwiML.
- Logs never carry a phone number, a name, a query text or a transcript: only call_sid, tool name, durations, counts.
- Never `--build` proton from `/opt/platform` (it contains Logisly UC1 code since 12 Nov): deploy by layering changed files onto the running image, like `scripts/ops/proton_deploy_phone_start_frame_2026-11-16.sh`.
- Commit by explicit path; the main checkout may have unrelated untracked files.

## Review Focus

- A caller who talks over the assistant (barge-in → `Interrupted`) must not produce a negative or giant `gap_ms`: the gap is measured only from the caller's last fragment to the NEXT model audio, and reset on `Interrupted` (Task 1 test `test_turn_gap_resets_on_interruption`).
- A `kb_search` that raises (Vertex down) must still answer the tool call, or the model waits forever in silence (Task 2 test `test_kb_search_error_returns_empty_results`).
- The filler line must not produce a second spoken answer per turn — the 27 Oct double-answer bug came from injected realtime TEXT; the filler lives only in the system instruction (Task 3 test asserts no `send_text_hint` / extra greeting).
- Warming at startup with TTS down or no announcement configured must not fail startup (Task 4 test `test_startup_warm_never_raises`).
- The first turn of the call (the greeting) has no caller fragment before it: it must not log a turn gap (Task 1 test `test_greeting_logs_no_turn_gap`).

---

### Task 1: Per-turn and per-tool timing logs

**Files:**
- Modify: `src/chatbot/features/chat/phone/bridge.py` (`pump()` ~line 1508, `_forward_model_audio` ~1054, `_handle_tool_call` ~1349, `__init__` near `self._timing_logged` ~286)
- Test: `src/chatbot/features/chat/phone/test_bridge_turn_taking.py` (reuse its `_Live`, `_Clock`, `_bridge` helpers)

**Interfaces:**
- Produces: log events `phone_turn_timing` (fields `call_sid`, `turn`, `gap_ms`) and `phone_tool_timing` (fields `call_sid`, `name`, `ms`, `outcome` ∈ `ok|timeout|error`). Task 2 adds the `timeout` outcome.

- [ ] **Step 1: Write the failing tests**

```python
async def test_turn_gap_is_last_caller_fragment_to_first_model_audio() -> None:
    clock = _Clock()
    live = _Live([
        InputTranscript("How often"), lambda: setattr(clock, "t", clock.t + 0.5),
        InputTranscript(" should my X50 be serviced?"),
        lambda: setattr(clock, "t", clock.t + 1.8),
        AUDIO, AUDIO, TurnComplete(),
    ])
    with capture_logs() as logs:
        await _bridge(live, [], clock=clock).pump()
    turns = [e for e in logs if e["event"] == "phone_turn_timing"]
    assert [(t["turn"], t["gap_ms"]) for t in turns] == [(1, 1800)]

async def test_greeting_logs_no_turn_gap() -> None:  # AUDIO before any InputTranscript
    ...  # assert no phone_turn_timing event

async def test_turn_gap_resets_on_interruption() -> None:
    # InputTranscript, AUDIO (turn 1 logged), InputTranscript, Interrupted, +0.4 s, AUDIO
    ...  # assert turn 2 gap_ms == 400, measured from the fragment after the interruption

async def test_tool_timing_is_logged_for_kb_search() -> None:
    # ToolCall("c1", "kb_search", {"query": "x"}) with a fake knowledge port that advances the clock 1.2 s
    ...  # assert {"name": "kb_search", "ms": 1200, "outcome": "ok"} in phone_tool_timing events
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone/test_bridge_turn_taking.py -k "turn_gap or tool_timing or greeting_logs" -v`
Expected: FAIL (no `phone_turn_timing` events).

- [ ] **Step 3: Implement**

In `__init__`: `self._last_caller_at: float | None = None`, `self._turn_no = 0`.
`pump()`: on `InputTranscript` set `self._last_caller_at = self._now()`; on `Interrupted` leave it (the next fragment overwrites it). In `_forward_model_audio`, AFTER the drop-window check and before forwarding: if `self._last_caller_at is not None`, increment `_turn_no`, log `phone_turn_timing` with `gap_ms=round((now - last)*1000)`, set `_last_caller_at = None`. In `_handle_tool_call`, wrap the whole dispatch: `started = self._now()`, `try/except` around the branch, log `phone_tool_timing` in `finally` with `outcome="ok"` or `"error"` (re-raise is NOT allowed — an unanswered tool call stalls the call; send an `{"error": ...}` tool response on exception, matching the unknown-tool branch).

- [ ] **Step 4: Run the tests, then the whole phone suite**

Run: `.venv/bin/pytest src/chatbot/features/chat/phone -q`
Expected: all pass.

- [ ] **Step 5: Commit**

`git add src/chatbot/features/chat/phone/bridge.py src/chatbot/features/chat/phone/test_bridge_turn_taking.py && git commit -m "feat(phone): per-turn and per-tool timing logs"`

---

### Task 2: Time cap on kb_search

**Files:**
- Modify: `src/chatbot/features/chat/phone/kb_tool.py` (`dispatch_kb_search`)
- Modify: `src/chatbot/features/chat/phone/bridge.py` (pass the timeout; map the outcome into `phone_tool_timing`)
- Modify: `src/chatbot/platform/config.py` (next to `gemini_live_*` ~line 491), `deploy/tenants/example.env` (next to `GEMINI_LIVE_VAD_SILENCE_DURATION_MS`)
- Test: `src/chatbot/features/chat/phone/test_kb_tool.py` (create if absent)

**Interfaces:**
- Consumes: Task 1's `phone_tool_timing` (`outcome`).
- Produces: `async def dispatch_kb_search(args: dict[str, object], knowledge_port: KnowledgePort, *, timeout_seconds: float = 0.0) -> dict[str, Any]`; on timeout returns `{"results": [], "status": "timeout"}`, on exception `{"results": [], "status": "error"}`; setting `phone_kb_search_timeout_seconds: float = 0.0` (0 = no cap, today's behaviour).

- [ ] **Step 1: Write the failing tests**

```python
async def test_kb_search_over_the_cap_returns_empty_with_timeout_status() -> None:
    slow = _Knowledge(delay=0.2)
    assert await dispatch_kb_search({"query": "x"}, slow, timeout_seconds=0.05) == {"results": [], "status": "timeout"}

async def test_zero_cap_waits_as_before() -> None:
    assert (await dispatch_kb_search({"query": "x"}, _Knowledge(delay=0.05), timeout_seconds=0.0))["results"]

async def test_kb_search_error_returns_empty_results() -> None:
    assert await dispatch_kb_search({"query": "x"}, _Knowledge(raises=True)) == {"results": [], "status": "error"}
```

- [ ] **Step 2: Run to see them fail** — `.venv/bin/pytest src/chatbot/features/chat/phone/test_kb_tool.py -v` → FAIL (unexpected keyword / exception propagates).
- [ ] **Step 3: Implement** with `asyncio.wait_for` when `timeout_seconds > 0`; the bridge passes `self._settings.phone_kb_search_timeout_seconds` and logs `outcome` from `status` (missing status = `ok`). example.env text: `# PHONE_KB_SEARCH_TIMEOUT_SECONDS=0   # cap on the phone's knowledge-base lookup; 0 = no cap. Over the cap the AI answers without the KB instead of staying silent. Proton: 1.5 after the rehearsal.`
- [ ] **Step 4: Run** `.venv/bin/pytest src/chatbot/features/chat/phone -q` → pass.
- [ ] **Step 5: Commit** kb_tool.py, bridge.py, config.py, example.env, test_kb_tool.py — `feat(phone): optional time cap on kb_search`.

---

### Task 3: Spoken filler before a knowledge lookup

**Files:**
- Modify: `src/chatbot/features/chat/router.py` (system instruction, the `"Use the kb_search tool to ground answers in the "` sentence ~line 2115)
- Modify: `config.py`, `example.env`
- Test: `src/chatbot/features/chat/test_phone_fast_start.py` (reuse `_app`, `_incoming`, `_start`, `CONNECTED`, `MEDIA`; `cap["instruction"]`)

**Interfaces:**
- Produces: setting `phone_kb_filler_enabled: bool = False`; module constant `_KB_FILLER_INSTRUCTION` in router.py.

Exact copy (the spec fixes it):
`_KB_FILLER_INSTRUCTION = "Before you call kb_search, first say one short phrase in the caller's language, such as 'Let me check that for you.', then call it; never say it twice in one answer and never use it when you answer without kb_search. "`

- [ ] **Step 1: Failing tests** — `test_filler_line_only_with_the_flag` (flag on → `_KB_FILLER_INSTRUCTION in cap["instruction"]`; flag off → not in, and the instruction equals today's) and `test_filler_adds_no_injected_turn` (flag on → `cap["live"].text_hints == []` and `len(cap["live"].greetings) <= 1`).
- [ ] **Step 2: Run** `.venv/bin/pytest src/chatbot/features/chat/test_phone_fast_start.py -k filler -v` → FAIL.
- [ ] **Step 3: Implement** — append `_KB_FILLER_INSTRUCTION` right after the kb_search sentence when the flag is on. example.env: `# PHONE_KB_FILLER_ENABLED=false   # the AI says a short "let me check" before a knowledge lookup instead of going silent`.
- [ ] **Step 4: Run** `.venv/bin/pytest src/chatbot/features/chat -q` → pass.
- [ ] **Step 5: Commit** — `feat(phone): optional spoken filler before kb_search`.

---

### Task 4: Synthesize the recording notice at startup

**Files:**
- Modify: `src/chatbot/features/chat/router.py` (`ChatRouter.__init__` after `self.router = APIRouter(...)` ~line 582)
- Test: `src/chatbot/features/chat/test_phone_fast_start.py`

**Interfaces:**
- Consumes: `AnnouncementAudio.warm(text)` / `.get(text)` (`phone/call_start_cache.py`, unchanged).
- Produces: `async def _warm_phone_notice(self) -> None` on `ChatRouter`, registered with `self.router.add_event_handler("startup", self._warm_phone_notice)` (FastAPI merges an included router's startup handlers into the app; `main.py` already uses `on_event("startup")`).

- [ ] **Step 1: Failing tests**

```python
def test_first_call_after_startup_gets_the_streamed_notice() -> None:
    svc, cap, tts = _Service(), {}, _Tts(b"\xff" * 8000)
    with TestClient(_app(_settings(), svc, tts, cap)) as client:   # startup runs here
        # let the warm task finish
        params = _params(_incoming(client, FORM))
    assert params.get("notice") == "stream"          # today: the FIRST call gets <Say>
    assert tts.calls == [(NOTICE, ANY)]               # synthesised exactly once

def test_startup_warm_is_skipped_without_fast_start_or_notice() -> None:
    # phone_fast_start_enabled=False, or phone_recording_announcement="" -> tts.calls == []

def test_startup_warm_never_raises() -> None:
    # _Tts(None) raises inside synth -> TestClient startup completes, first call falls back to <Say>
```
Startup must finish only after synthesis, so the first call is deterministic: `_warm_phone_notice` awaits a new public `async def prime(self, text: str) -> None` on `AnnouncementAudio`, bounded by `asyncio.wait_for(..., timeout=10)`. A fire-and-forget `warm()` would race the first call.

- [ ] **Step 2: Run** `.venv/bin/pytest src/chatbot/features/chat/test_phone_fast_start.py -k startup -v` → FAIL.
- [ ] **Step 3: Implement** `AnnouncementAudio.prime(text)` (awaits the synthesis once, never raises) and `_warm_phone_notice` (only when `phone_fast_start_enabled and phone_recording_enabled and phone_recording_announcement`; wrapped in `asyncio.wait_for(..., timeout=10)`; any exception → `_log.warning("phone_announcement_prime_failed", error_type=...)`). Add a `prime` unit test in `phone/test_call_start_caches.py`.
- [ ] **Step 4: Run** `GOOGLE_API_KEY=test-dummy .venv/bin/pytest src/chatbot -q` → all pass; ruff + mypy on the touched files show no NEW errors (router.py already has one pre-existing mypy error at the `AbandonRecorder.record` line and an I001 import-order warning).
- [ ] **Step 5: Commit** — `feat(phone): synthesize the PDPA notice at startup`.

---

### Task 5: Proton rollout and VAD trial (ops, no code)

**Files:**
- Create: `scripts/ops/proton_deploy_phone_gaps_<date>.sh` + `scripts/ops/backend-phone-gaps.patch` — copy the pattern of `scripts/ops/proton_deploy_phone_start_frame_2026-11-16.sh` (patched files copied out of the running container, layered on it, `latest` restored, `--dry-run/--apply/--rollback`). Generate the patch with `rtk proxy git diff` (plain `git diff` is filtered by RTK and produced a malformed patch on 16 Nov). The patch now touches several files: patch each one inside the work dir and `COPY` each.

- [ ] **Step 1:** Deploy with all flags at default (measurement only). Make three rehearsal calls (Ahmad: a greeting, a knowledge question, a handoff). Collect: `docker logs --since 15m proton-backend | grep -E "\] phone_(turn|tool|call)_timing"`.
- [ ] **Step 2:** Decide from the numbers. If `kb_search` `ms` p50 > 800: set `PHONE_KB_FILLER_ENABLED=true` and `PHONE_KB_SEARCH_TIMEOUT_SECONDS=1.5` in `proton.env` (the user runs the env edit — auto mode blocks it), recreate backend only, re-run the three calls.
- [ ] **Step 3 (after the 17 Nov demo only):** VAD trial — `GEMINI_LIVE_VAD_SILENCE_DURATION_MS=650` with a hesitant-speaker script (pause mid-sentence for 1 s). Keep it only if the assistant never answers mid-pause in 5 calls; otherwise back to 900. Record the result in the memory file.

Expected: `phone_turn_timing gap_ms` median drops by the filler/timeout effect; `phone_call_timing notice=stream` on the FIRST call after a restart.
