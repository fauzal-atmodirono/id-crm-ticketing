# WhatsApp Voice-Note + Image Understanding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Chatwoot-native WhatsApp agent-bot the ability to understand inbound voice notes and images, by forwarding them as Gemini multimodal Parts through `backend/`'s existing `/chat/turn` endpoint — no separate transcription step, reusing the exact Part-construction pattern the voice channel already uses.

**Architecture:** `backend/`'s `ChatTurnRequest`/`handle_turn` gain four optional fields (`audio_base64`, `audio_mime_type`, `image_base64`, `image_mime_type`) that, when present, get appended as extra `types.Part`s to the turn's `types.Content` — additive, `None`-default, byte-identical for every existing caller. `agent/`'s `ProtonConfigClient.chat_turn` gains matching optional params (mirroring the already-established `inbox_id` conditional-payload pattern exactly). `agent/`'s orchestrator, in `_process_via_chat_agent` (the only caller of `chat_turn`), detects attachments on incoming messages, fetches their bytes via a new plain-`httpx` fetcher, and passes them through — gated by one feature flag.

**Tech Stack:** FastAPI, Pydantic, `google-genai` (`types.Part.from_bytes`), httpx, respx (tests), pytest-asyncio.

## Global Constraints

- Every new field/param defaults to `None`/absent — zero behavior change for any existing caller of `/chat/turn` or `chat_turn()` until explicitly populated.
- `agent/`'s side is gated by one flag, `whatsapp_media_understanding_enabled` (default `False`).
- Attachment fetch failures never break the turn — degrade to omitting that field (today's exact behavior for that attachment).
- Out of scope: the legacy `gemini.decide()` router path (`chat_agent_enabled=False`), `backend/`'s standalone Twilio integration (`/webhooks/twilio-whatsapp`), video, `/voice/turn` (unrelated, multipart-upload based, not reused here).

---

### Task 1: Attachment fetcher (agent/)

**Files:**
- Create: `agent/app/services/media.py`
- Create: `agent/tests/test_media.py`

**Interfaces:**
- Produces: `async def fetch_attachment_bytes(data_url: str) -> tuple[bytes, str] | None` — `(bytes, mime_type)` on success, `None` on any failure. Consumed by Task 4.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_media.py
import httpx
import respx

from app.services.media import fetch_attachment_bytes


@respx.mock
async def test_fetch_attachment_bytes_success():
    respx.get("https://cdn.example.com/voice.ogg").mock(
        return_value=httpx.Response(200, content=b"fake-audio-bytes", headers={"Content-Type": "audio/ogg"})
    )
    result = await fetch_attachment_bytes("https://cdn.example.com/voice.ogg")
    assert result == (b"fake-audio-bytes", "audio/ogg")


@respx.mock
async def test_fetch_attachment_bytes_404_returns_none():
    respx.get("https://cdn.example.com/gone.jpg").mock(return_value=httpx.Response(404))
    assert await fetch_attachment_bytes("https://cdn.example.com/gone.jpg") is None


@respx.mock
async def test_fetch_attachment_bytes_network_error_returns_none():
    respx.get("https://cdn.example.com/timeout.jpg").mock(side_effect=httpx.ConnectError("down"))
    assert await fetch_attachment_bytes("https://cdn.example.com/timeout.jpg") is None


@respx.mock
async def test_fetch_attachment_bytes_missing_content_type_falls_back_to_octet_stream():
    respx.get("https://cdn.example.com/noheader.bin").mock(
        return_value=httpx.Response(200, content=b"data")
    )
    result = await fetch_attachment_bytes("https://cdn.example.com/noheader.bin")
    assert result is not None
    data, mime = result
    assert data == b"data"
    assert mime  # some non-empty fallback mime type, exact value not asserted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/pytest tests/test_media.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.media'`

- [ ] **Step 3: Implement `media.py`**

```python
# agent/app/services/media.py
"""Fetch inbound Chatwoot message attachment bytes for multimodal AI turns.

Chatwoot attachment data_urls are absolute, directly-fetchable URLs (either
pre-signed cloud storage or Chatwoot's own served asset route) — a plain,
unauthenticated client is used deliberately, NOT ChatwootClient, so the
account API token is never sent to an external host.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_MIME_TYPE = "application/octet-stream"


async def fetch_attachment_bytes(data_url: str) -> tuple[bytes, str] | None:
    """Download an attachment. Returns (bytes, mime_type), or None on any
    failure — a broken URL must never break the turn it's attached to."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(data_url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            mime_type = content_type or _DEFAULT_MIME_TYPE
            return response.content, mime_type
    except Exception:
        logger.warning("media: failed to fetch attachment %s", data_url, exc_info=True)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/pytest tests/test_media.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd agent
git add app/services/media.py tests/test_media.py
git commit -m "feat: fetch Chatwoot attachment bytes for multimodal WhatsApp turns"
```

---

### Task 2: `backend/`'s `/chat/turn` — optional multimodal input

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/router.py` (`ChatTurnRequest` at line 57-60, `chat_turn` handler at line 1064)
- Modify: `backend/apps/backend/src/chatbot/features/chat/service.py` (`handle_turn` at line 409, the `parts`/`new_message` construction at lines 459-463)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_router.py` and/or `test_service.py` (check which already covers `chat_turn`/`handle_turn` via `grep -n "handle_turn\|ChatTurnRequest" backend/apps/backend/src/chatbot/features/chat/test_*.py` first — extend whichever file already has this coverage rather than guessing)

**Interfaces:**
- Produces: `ChatTurnRequest` gains `audio_base64: str | None = None`, `audio_mime_type: str | None = None`, `image_base64: str | None = None`, `image_mime_type: str | None = None`. `handle_turn(self, session_id, text, inbox_id=None, audio_base64=None, audio_mime_type=None, image_base64=None, image_mime_type=None) -> TurnResult` — same signature extension. Consumed by Task 3 (the client that calls this HTTP endpoint).

- [ ] **Step 1: Write the failing test**

```python
# addition to whichever test file already covers handle_turn/ChatTurnRequest
import base64


async def test_handle_turn_with_audio_builds_multimodal_content(orchestrator_service, monkeypatch):
    captured = {}

    async def fake_run_support_agent(session_id, new_message):
        captured["parts"] = new_message.parts
        return "ok", set(), set()

    monkeypatch.setattr(orchestrator_service, "_run_support_agent", fake_run_support_agent)

    audio_b64 = base64.b64encode(b"fake-ogg-bytes").decode()
    await orchestrator_service.handle_turn(
        "session-1", "check this out",
        audio_base64=audio_b64, audio_mime_type="audio/ogg",
    )

    assert len(captured["parts"]) == 2  # text part + audio part


async def test_handle_turn_without_media_is_unchanged(orchestrator_service, monkeypatch):
    captured = {}

    async def fake_run_support_agent(session_id, new_message):
        captured["parts"] = new_message.parts
        return "ok", set(), set()

    monkeypatch.setattr(orchestrator_service, "_run_support_agent", fake_run_support_agent)

    await orchestrator_service.handle_turn("session-1", "just text")
    assert len(captured["parts"]) == 1  # text part only — unchanged from today


def test_chat_turn_request_accepts_media_fields():
    from chatbot.features.chat.router import ChatTurnRequest

    req = ChatTurnRequest(
        session_id="s1", text="hi",
        audio_base64="abc", audio_mime_type="audio/ogg",
    )
    assert req.audio_base64 == "abc"
    assert req.image_base64 is None
```

Adjust fixture names (`orchestrator_service`) to match whatever this file's existing `handle_turn` tests already use — read the file first, don't assume this fixture name is real.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/ -k "handle_turn_with_audio or handle_turn_without_media or chat_turn_request_accepts_media" -v`
Expected: FAIL — `TypeError: handle_turn() got an unexpected keyword argument 'audio_base64'` and `ChatTurnRequest` rejects the extra fields (pydantic ignores unknown fields by default unless configured strict, so the request-shape test may need `req.audio_base64` to raise `AttributeError` instead — check actual pydantic config; either way this fails before Step 3).

- [ ] **Step 3: Implement**

In `router.py`, extend `ChatTurnRequest` (lines 57-60):

```python
class ChatTurnRequest(BaseModel):
    session_id: str
    text: str
    inbox_id: int | None = None
    audio_base64: str | None = None
    audio_mime_type: str | None = None
    image_base64: str | None = None
    image_mime_type: str | None = None
```

In the `chat_turn` handler (around line 1064), pass the new fields through to `handle_turn` alongside the existing `session_id`/`text`/`inbox_id` args (read the handler's current body first to match its exact call-site style).

In `service.py`'s `handle_turn` (line 409), extend the signature:

```python
async def handle_turn(
    self,
    session_id: str,
    text: str,
    inbox_id: int | None = None,
    audio_base64: str | None = None,
    audio_mime_type: str | None = None,
    image_base64: str | None = None,
    image_mime_type: str | None = None,
) -> TurnResult:
```

Replace the `new_message` construction (lines 459-463):

```python
        # 4. Formulate the GenAI content structure
        parts: list[types.Part] = [types.Part.from_text(text=text)]
        if audio_base64 and audio_mime_type:
            try:
                parts.append(
                    types.Part.from_bytes(data=base64.b64decode(audio_base64), mime_type=audio_mime_type)
                )
            except Exception:
                _log.warning("handle_turn_audio_decode_failed", session_id=session_id)
        if image_base64 and image_mime_type:
            try:
                parts.append(
                    types.Part.from_bytes(data=base64.b64decode(image_base64), mime_type=image_mime_type)
                )
            except Exception:
                _log.warning("handle_turn_image_decode_failed", session_id=session_id)
        new_message = types.Content(role="user", parts=parts)
```

Add `import base64` at the top of `service.py` if not already present (check first).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/ -k "handle_turn or chat_turn" -v`
Expected: PASS, no regressions in existing `handle_turn`/`chat_turn` tests.

- [ ] **Step 5: Run the full chat test suite for regressions**

Run: `cd backend/apps/backend && export GEMINI_API_KEY=test-dummy-key GOOGLE_API_KEY=test-dummy-key && .venv/bin/pytest src/chatbot/features/chat/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/service.py src/chatbot/features/chat/test_*.py
git commit -m "feat(chat): accept optional audio/image on /chat/turn as multimodal Parts"
```

---

### Task 3: `agent/`'s `ProtonConfigClient.chat_turn` — forward media fields

**Files:**
- Modify: `agent/app/clients/proton.py` (`chat_turn`, line 315)
- Test: `agent/tests/test_chat_turn_inbox_id.py` (existing — the exact precedent for this exact kind of optional-field addition; extend it or add a sibling test file following its identical pattern)

**Interfaces:**
- Consumes: nothing new (still calls `backend/`'s `/chat/turn`, now with more optional fields — Task 2 makes them meaningful, but this task doesn't depend on Task 2 being deployed first, only on agreeing on field names, which this plan fixes).
- Produces: `chat_turn(self, session_id, text, inbox_id=None, audio_base64=None, audio_mime_type=None, image_base64=None, image_mime_type=None) -> dict | None`. Consumed by Task 4.

- [ ] **Step 1: Write the failing test**

```python
# addition to agent/tests/test_chat_turn_inbox_id.py — same file, same pattern
@respx.mock
async def test_chat_turn_includes_media_fields_when_set():
    route = respx.post("http://backend/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "hi"})
    )
    c = ProtonConfigClient(base_url="http://backend", api_key="k")
    await c.chat_turn(
        "crm-1", "check this",
        audio_base64="YWJj", audio_mime_type="audio/ogg",
    )
    body = route.calls.last.request.content.decode()
    assert "audio_base64" in body
    assert "audio_mime_type" in body
    assert "image_base64" not in body  # not passed -> omitted


@respx.mock
async def test_chat_turn_omits_media_fields_when_none():
    route = respx.post("http://backend/chat/turn").mock(
        return_value=httpx.Response(200, json={"reply": "hi"})
    )
    c = ProtonConfigClient(base_url="http://backend", api_key="k")
    await c.chat_turn("crm-1", "hello")
    body = route.calls.last.request.content.decode()
    assert "audio_base64" not in body
    assert "image_base64" not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && .venv/bin/pytest tests/test_chat_turn_inbox_id.py -v`
Expected: FAIL — `TypeError: chat_turn() got an unexpected keyword argument 'audio_base64'`

- [ ] **Step 3: Implement**

In `agent/app/clients/proton.py`, extend `chat_turn` (line 315), mirroring the exact `if inbox_id is not None:` conditional-payload pattern already there for each new field:

```python
    async def chat_turn(
        self,
        session_id: str,
        text: str,
        inbox_id: int | None = None,
        audio_base64: str | None = None,
        audio_mime_type: str | None = None,
        image_base64: str | None = None,
        image_mime_type: str | None = None,
    ) -> dict | None:
        """..."""  # keep existing docstring, unchanged
        try:
            payload: dict = {"session_id": session_id, "text": text}
            if inbox_id is not None:
                payload["inbox_id"] = inbox_id
            if audio_base64 is not None:
                payload["audio_base64"] = audio_base64
            if audio_mime_type is not None:
                payload["audio_mime_type"] = audio_mime_type
            if image_base64 is not None:
                payload["image_base64"] = image_base64
            if image_mime_type is not None:
                payload["image_mime_type"] = image_mime_type
            response = await self._client.post(
                "/chat/turn",
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.debug("proton_config: chat_turn failed", exc_info=True)
            return None
        if not isinstance(data, dict):
            return None
        return data
```

(Only the signature and the `payload` construction change — the try/except/return shape is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && .venv/bin/pytest tests/test_chat_turn_inbox_id.py -v`
Expected: PASS (4 tests: 2 pre-existing + 2 new)

- [ ] **Step 5: Commit**

```bash
cd agent
git add app/clients/proton.py tests/test_chat_turn_inbox_id.py
git commit -m "feat: forward optional audio/image fields on ProtonConfigClient.chat_turn"
```

---

### Task 4: Orchestrator wiring — config flag, attachment detection, silent-drop fix

**Files:**
- Modify: `agent/app/config.py` (near `chat_agent_enabled`, line 78)
- Modify: `agent/app/services/orchestrator.py` (`_process_via_chat_agent`, line 525; the empty-text short-circuit within it)
- Test: `agent/tests/test_orchestrator.py` or whichever file covers `_process_via_chat_agent` today (check via `grep -n "_process_via_chat_agent" agent/tests/*.py` first)

**Interfaces:**
- Consumes: `fetch_attachment_bytes` (Task 1), `ProtonConfigClient.chat_turn`'s new params (Task 3).
- Produces: attachment-aware `_process_via_chat_agent`.

- [ ] **Step 1: Add the config flag**

In `agent/app/config.py`, immediately after `chat_agent_enabled` (line 78):

```python
    # Voice-note + image understanding for the agent-bot's chat-agent path
    # (orchestrator.py's _process_via_chat_agent, chat_agent_enabled=True
    # only). Default False = today's text-only behavior, byte-identical.
    # When True, incoming WhatsApp attachments (audio/image) are downloaded
    # and forwarded to backend/'s /chat/turn as multimodal Parts alongside
    # the text.
    whatsapp_media_understanding_enabled: bool = False
```

- [ ] **Step 2: Read the current `_process_via_chat_agent` and its test file**

Run: `grep -n "_process_via_chat_agent" agent/tests/*.py`

Read the full current function body (orchestrator.py:525-601) and its existing tests before writing new ones — match existing fixture/mock conventions exactly (this file likely already mocks `chatwoot`/`proton` clients; find and reuse those fixtures).

- [ ] **Step 3: Write the failing tests**

```python
# addition to whichever file covers _process_via_chat_agent
async def test_process_via_chat_agent_forwards_audio_attachment_when_enabled(
    monkeypatch, chatwoot_stub, proton_stub  # match real fixture names from the file
):
    monkeypatch.setattr(settings, "whatsapp_media_understanding_enabled", True)
    monkeypatch.setattr(
        "app.services.orchestrator.fetch_attachment_bytes",
        AsyncMock(return_value=(b"ogg-bytes", "audio/ogg")),
    )
    # message_list's trailing incoming message has content="" and
    # attachments=[{"file_type": "audio", "data_url": "https://cdn/x.ogg"}]
    ...
    await _process_via_chat_agent(conversation_id, message_list, mode, chatwoot_stub, handoff_message, inbox_id=None)
    call_kwargs = proton_stub.chat_turn.call_args.kwargs
    assert call_kwargs.get("audio_base64")
    assert call_kwargs.get("audio_mime_type") == "audio/ogg"


async def test_process_via_chat_agent_ignores_attachments_when_flag_off(monkeypatch, chatwoot_stub, proton_stub):
    monkeypatch.setattr(settings, "whatsapp_media_understanding_enabled", False)
    # same attachment-only message_list as above
    ...
    await _process_via_chat_agent(conversation_id, message_list, mode, chatwoot_stub, handoff_message, inbox_id=None)
    call_kwargs = proton_stub.chat_turn.call_args.kwargs if proton_stub.chat_turn.called else {}
    assert "audio_base64" not in call_kwargs


async def test_process_via_chat_agent_logs_on_attachment_only_empty_text(monkeypatch, caplog, chatwoot_stub, proton_stub):
    monkeypatch.setattr(settings, "whatsapp_media_understanding_enabled", False)
    # attachment-only message, no caption
    ...
    await _process_via_chat_agent(conversation_id, message_list, mode, chatwoot_stub, handoff_message, inbox_id=None)
    assert "orchestrator_attachment_only_message_dropped" in caplog.text


async def test_process_via_chat_agent_fetch_failure_falls_back_gracefully(monkeypatch, chatwoot_stub, proton_stub):
    monkeypatch.setattr(settings, "whatsapp_media_understanding_enabled", True)
    monkeypatch.setattr(
        "app.services.orchestrator.fetch_attachment_bytes",
        AsyncMock(return_value=None),
    )
    # attachment-only message
    ...
    await _process_via_chat_agent(conversation_id, message_list, mode, chatwoot_stub, handoff_message, inbox_id=None)
    # must not raise; today's short-circuit / text-only behavior applies
```

These are illustrative — replace fixture setup with the file's real conventions once you've read Step 2's output. The four behaviors under test are fixed by the brief; the exact mock/fixture mechanics are yours to match to the file.

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd agent && .venv/bin/pytest tests/ -k "process_via_chat_agent" -v`
Expected: FAIL (new behavior not implemented yet).

- [ ] **Step 5: Implement**

In `orchestrator.py`, add the import: `from app.services.media import fetch_attachment_bytes`.

In `_process_via_chat_agent` (line 525), before the call to `proton.chat_turn(...)` (line 551):

1. Determine the trailing incoming message(s) the same way `_latest_incoming_text` does (reuse or lightly adapt its iteration — don't duplicate the whole greeting/lifecycle-filtering logic if it can be shared; if `_latest_incoming_text` doesn't already expose the raw message objects it selected, add a small local helper that mirrors its loop but collects messages instead of just `content` strings).
2. For each such message, when `settings.whatsapp_media_understanding_enabled` and `message.get("attachments")`: for the first audio-type and first image-type attachment found (one of each, not all), call `fetch_attachment_bytes(attachment["data_url"])`; on success, capture `(audio_base64, audio_mime_type)` / `(image_base64, image_mime_type)` via `base64.b64encode(data).decode()`.
3. Pass whichever media kwargs were populated into the `proton.chat_turn(...)` call at line 551.
4. Immediately before/within the existing empty-`text` short-circuit (find it near where `text` is checked after `_latest_incoming_text`): if `text` is empty AND any attachment was found on the trailing messages, log `logger.warning("orchestrator_attachment_only_message_dropped", extra={"conversation_id": conversation_id, ...})` (match this file's actual logging call convention — check whether it uses stdlib `logging` with `%s` args or structured `extra=`) before returning.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd agent && .venv/bin/pytest tests/ -k "process_via_chat_agent or media" -v`
Expected: PASS.

- [ ] **Step 7: Run the full agent test suite for regressions**

Run: `cd agent && .venv/bin/pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
cd agent
git add app/config.py app/services/orchestrator.py agent/tests/
git commit -m "feat: wire WhatsApp attachment understanding into the chat-agent path"
```

---

### Task 5: Document the new env var

**Files:**
- Modify: `deploy/tenants/example.env`

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add documentation**

Near `CHAT_AGENT_ENABLED` in `deploy/tenants/example.env`:

```bash
# Voice-note + image understanding for the chat-agent WhatsApp path (requires
# CHAT_AGENT_ENABLED=true). Downloads incoming WhatsApp attachments and
# forwards them to the backend as multimodal Gemini input. Default false =
# today's text-only behavior.
WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=false
```

- [ ] **Step 2: Commit**

```bash
git add deploy/tenants/example.env
git commit -m "docs: document WHATSAPP_MEDIA_UNDERSTANDING_ENABLED"
```

---

## Plan Self-Review Notes

- **Spec coverage:** all 5 numbered Design items in the spec map to Task 1 (item 2), Task 2 (item 3), Task 3 (the client layer the spec's item 4 implicitly requires but didn't call out as a separate file — added here since `_process_via_chat_agent` doesn't call `/chat/turn` directly, it goes through `ProtonConfigClient`), Task 4 (items 4 and 5), Task 5 (env docs, referenced in the spec's Rollout section).
- **Task 3 is a plan-level addition** beyond the spec's explicit "Design" numbering — the spec's Decision section says `_process_via_chat_agent` "fetches attachment bytes and forwards them" but the spec didn't explicitly enumerate the `ProtonConfigClient.chat_turn` layer as its own design item. Without Task 3, Task 4 would have nothing to call. Flagging this the same way Task 5 was flagged as a gap-closing addition in the case-categories plan.
- **No task touches the legacy `gemini.decide()` router or `backend/`'s standalone Twilio webhook** — matches the spec's explicit non-goals.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-02-whatsapp-voice-image-understanding.md`. Proceeding with Subagent-Driven execution (standing choice for this autonomous run, matching the prior two projects).
