# WhatsApp voice-note transcription + image understanding (Chatwoot-native path)

**Date:** 2026-08-02
**Status:** Approved (autonomous — brainstormed and self-approved during an unattended run; no user available to review interactively, per standing authorization to proceed without interruption. Decisions and rationale documented inline for later human review.)
**Scope:** Roadmap item #4 in `docs/roadmap/2026-08-01-next-development-roadmap.md`. Add voice-note transcription and image understanding to `agent/`'s Chatwoot-native agent-bot flow (`/webhooks/chatwoot/bot`, `orchestrator.py`) — the WhatsApp integration actually live in production (a Chatwoot inbox backed by Twilio upstream), not `backend/`'s separate standalone Twilio integration.

## Problem

Chatwoot's native WhatsApp inbox already receives and displays inbound voice notes and images to human agents (stock Chatwoot/Twilio-channel behavior, confirmed no gap there). But `agent/`'s orchestrator — the AI agent-bot answering customer messages — reads only `message.get("content")` and ignores `attachments` entirely. An attachment-only message (voice note or uncaptioned image, empty `content`) causes `_latest_incoming_text` to return `""`, which short-circuits the whole turn (`if not text: return`) — **the AI silently does nothing**, no reply, no handoff, no log line. A captioned image's caption gets processed as text while the image itself is dropped.

## Decision (autonomous, documented for review)

- **Scope is the Chatwoot-native path only.** `backend/`'s standalone Twilio integration (which has more of this plumbing already built — `_transcribe_audio`, `handle_voice_turn`) is explicitly out of scope: it doesn't touch Chatwoot at all, so building there wouldn't move the needle for the actual product (agents already see the media; only the AI-side gap matters, and that gap is in `agent/`, not `backend/`). `backend/`'s functions are used here only as a **reference pattern** to copy (same `google.genai.Client`, same `types.Part.from_bytes` call shape) — no code is shared between the two services, consistent with this repo's existing agent/backend decoupling.
- **Audio → transcribe to text, then treat as if typed.** A new `_transcribe_whatsapp_audio(audio_bytes, mime_type) -> str` helper (mirrors `backend/`'s `_transcribe_audio`) converts a voice note to a transcript. That transcript is substituted for the message's empty `content` before `_build_context`/`_latest_incoming_text` run — so the rest of the pipeline (context building, `decide()`, reply posting) needs **no changes** for the audio case. This is simpler and lower-risk than threading raw audio bytes through `decide()`'s single flattened-string call shape.
- **Images → pass as a Gemini multimodal Part into `decide()`.** Unlike audio, an image can't be meaningfully flattened to text ahead of time (a photo of a damaged part needs visual understanding, not transcription). `gemini.py::decide()` and `generate()` get a new optional `media_parts: list[types.Part] | None = None` param; when present, `contents` switches from the plain string to `types.Content(role="user", parts=[types.Part.from_text(text=conversation_context), *media_parts])` — additive, byte-identical default when `media_parts` is `None`.
- **Attachment bytes are fetched with a plain, unauthenticated `httpx.AsyncClient`**, not `ChatwootClient` — Chatwoot's `data_url`s are absolute, directly-fetchable (pre-signed cloud storage or Chatwoot's own asset route), and reusing `ChatwootClient` would leak the account API token to that external host for no benefit.
- **One flag gates the whole feature**: `whatsapp_media_understanding_enabled` (agent/, default `False`, fail-open/default-preserving — today's text-only behavior when off).
- **Fix the silent-drop bug regardless of the flag.** Even with the feature disabled, an attachment-only message should not vanish with zero observability. When the flag is off and a message has attachments but no usable text, log it (structured, one line) and let the existing short-circuit stand (no customer-facing behavior change) — this makes the gap visible in logs/metrics instead of silent, without changing behavior for tenants that haven't opted in.

## Non-goals

- Not touching `backend/`'s standalone Twilio integration — separate, lower-priority path per the scope decision above.
- Not adding video support (roadmap doesn't ask for it; no existing pattern to lean on).
- Not building this for the legacy (`chat_agent_enabled=False`) router path — only the ADK chat-agent path (`_process_via_chat_agent`) gets media understanding; the legacy path keeps today's behavior (a smaller, lower-priority code path per the orchestrator's own structure — this repo is migrating toward the ADK chat-agent path per other work this session).
- Not solving Meta WhatsApp Business media-message verification/policy questions — external/business dependency, same category as the Customer 360 blocker in the roadmap doc; noted, not built.

## Design

### 1. Config flag

`agent/app/config.py`, near the Gemini/AI behavior block (`chat_agent_enabled`, etc.):

```python
# Voice-note transcription + image understanding for the agent-bot path
# (orchestrator.py, ADK chat-agent flow only). Default False = today's
# text-only behavior, byte-identical. When True, incoming WhatsApp
# attachments (audio/image) are downloaded and understood: audio is
# transcribed to text before context-building; images are sent to Gemini
# as multimodal Parts alongside the conversation context.
whatsapp_media_understanding_enabled: bool = False
```

Add to `deploy/tenants/example.env`: `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=false`, documented next to `CHAT_AGENT_ENABLED`.

### 2. Attachment fetcher (new)

`agent/app/services/media.py` (new file):

```python
async def fetch_attachment_bytes(data_url: str) -> tuple[bytes, str] | None:
    """Download a Chatwoot attachment. Returns (bytes, mime_type) or None on
    any failure (fail-open — a fetch error must never break the turn)."""
```

Uses a short-lived `httpx.AsyncClient()` (no Chatwoot auth headers), `GET`s `data_url`, derives mime type from the response's `Content-Type` header (falling back to the attachment's own `file_type` field — `"image"`/`"audio"` — mapped to a sensible default mime type if the header is missing/generic). Returns `None` and logs a warning on any exception, timeout, or non-2xx — never raises.

### 3. Audio transcription (new)

`agent/app/services/media.py`, alongside the fetcher:

```python
async def transcribe_whatsapp_audio(audio_bytes: bytes, mime_type: str, *, client=None) -> str | None:
    """Transcribe a voice note via Gemini. Returns the transcript, or None on
    any failure (fail-open — audio the model can't transcribe just drops back
    to today's behavior for that message)."""
```

Mirrors `backend/`'s `_transcribe_audio` call shape exactly (`types.Content(parts=[Part.from_bytes(...), Part.from_text(text="Transcribe this audio verbatim...")])` via the same `google.genai.Client` `agent/`'s `gemini.py` already constructs), adapted to `agent/`'s sync-call-via-`asyncio.to_thread` convention (matching `decide()`'s existing pattern, not `backend/`'s native-async client).

### 4. `gemini.py` — optional multimodal input

`decide()` and `generate()` (`agent/app/ai/gemini.py`) gain `media_parts: list[types.Part] | None = None`. When `None` (the default — every existing call site, unchanged), behavior is byte-identical to today (`contents=conversation_context`, a plain string). When present:

```python
contents = types.Content(
    role="user",
    parts=[types.Part.from_text(text=conversation_context), *media_parts],
)
```

### 5. Orchestrator wiring

`agent/app/services/orchestrator.py`'s `_process_via_chat_agent` (the ADK path — where this feature lands per the non-goals above):

- Before calling `_latest_incoming_text`, scan the trailing run of incoming messages for `message.get("attachments")`. For each attachment (only when `settings.whatsapp_media_understanding_enabled`):
  - `file_type == "audio"` → `fetch_attachment_bytes` → `transcribe_whatsapp_audio` → if a transcript comes back, treat it as that message's effective text (feed into `_latest_incoming_text`'s join, same as if the customer had typed it).
  - `file_type == "image"` → `fetch_attachment_bytes` → build a `types.Part.from_bytes(data=..., mime_type=...)`, collected into a `media_parts` list for this turn.
- Pass the collected `media_parts` (if any) through to the `decide(...)` call.
- When the flag is off (or no attachments): behavior is completely unchanged — including the silent-drop fix in Design item 6 below, which fires independent of the flag.

### 6. Silent-drop observability fix (flag-independent)

In `_process_via_chat_agent`'s existing `if not text: return` short-circuit: when the effective text is empty AND the latest incoming message(s) had at least one attachment, log one structured warning (e.g. `orchestrator_attachment_only_message_dropped`, with conversation id and attachment file_types) before returning. No behavior change — purely closes the "silent" part of "silent-drop" so this is observable in logs/metrics regardless of whether the media-understanding flag is on.

## Error handling

- `fetch_attachment_bytes` and `transcribe_whatsapp_audio` never raise — any failure returns `None`/falls back, logged as a warning. A broken attachment URL or a Gemini transcription failure degrades to "treat this message as if it had no usable text" (today's exact behavior for an attachment-only message, just now logged per item 6), never breaks the turn.
- `decide()`/`generate()` with `media_parts` follow the exact same existing exception handling as the plain-text path (already fail-open per the codebase's established convention — `decide()`'s function-calling contract falls back to `handoff_to_human` on any anomaly).

## Testing

- `test_media.py` (new): `fetch_attachment_bytes` — success, 404, timeout, malformed URL (mirrors `respx`-based test conventions already used in `agent/tests/`). `transcribe_whatsapp_audio` — success (mocked Gemini response), failure (exception → `None`).
- `gemini.py` tests: `decide()`/`generate()` called with `media_parts=None` produce the identical `contents=` string call as before (regression guard); called with a `media_parts` list produce the `types.Content(...)` shape.
- `orchestrator.py` tests: attachment-only audio message → transcript flows into context → `decide()` called with the transcript as if typed. Attachment-only image message → `decide()` called with `media_parts` containing the image `Part`. Flag off → attachments ignored, today's short-circuit still fires, but the new warning log line is emitted. Flag on + fetch failure → falls back to today's short-circuit behavior, no crash.

## Rollout

Agent redeploy only (no backend/ or Chatwoot fork change). Default off — zero behavior change for any tenant until `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` is set. Meta WhatsApp Business media-message policy/verification is an external prerequisite outside this repo's control, same category as other external blockers already noted in the roadmap doc.
