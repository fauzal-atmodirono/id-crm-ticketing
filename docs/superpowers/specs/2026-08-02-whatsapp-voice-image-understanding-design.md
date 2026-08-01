# WhatsApp voice-note transcription + image understanding (Chatwoot-native path)

**Date:** 2026-08-02
**Status:** Approved (autonomous — brainstormed and self-approved during an unattended run; no user available to review interactively, per standing authorization to proceed without interruption. Decisions and rationale documented inline for later human review.)
**Scope:** Roadmap item #4 in `docs/roadmap/2026-08-01-next-development-roadmap.md`. Add voice-note transcription and image understanding to `agent/`'s Chatwoot-native agent-bot flow (`/webhooks/chatwoot/bot`, `orchestrator.py`) — the WhatsApp integration actually live in production (a Chatwoot inbox backed by Twilio upstream), not `backend/`'s separate standalone Twilio integration.

## Problem

Chatwoot's native WhatsApp inbox already receives and displays inbound voice notes and images to human agents (stock Chatwoot/Twilio-channel behavior, confirmed no gap there). But `agent/`'s orchestrator — the AI agent-bot answering customer messages — reads only `message.get("content")` and ignores `attachments` entirely. An attachment-only message (voice note or uncaptioned image, empty `content`) causes `_latest_incoming_text` to return `""`, which short-circuits the whole turn (`if not text: return`) — **the AI silently does nothing**, no reply, no handoff, no log line. A captioned image's caption gets processed as text while the image itself is dropped.

## Decision (autonomous, documented for review)

- **Scope is the Chatwoot-native path only, specifically the `chat_agent_enabled=True` branch** (`_process_via_chat_agent`, which proxies to `backend/`'s `POST /chat/turn`) — **not** the legacy local `gemini.decide()` router (`_process_conversation`'s `chat_agent_enabled=False` branch), and **not** `backend/`'s separate standalone Twilio integration. Two corrections made while grounding this design against the actual code (no user available to review — self-caught before finalizing):
  1. `_process_via_chat_agent` does not call `agent/`'s local `gemini.py` at all — it HTTP-POSTs to `backend/`'s `/chat/turn`. So the media support must be added to **`backend/`'s `/chat/turn` request/response and `handle_turn`**, not to `agent/app/ai/gemini.py`.
  2. Given that, audio does **not** need a separate pre-transcription step. `backend/`'s `handle_turn` (`service.py:460-463`) already builds a single-`Part` `types.Content` from text; extending it to accept raw audio/image bytes as additional `Part`s reuses the *exact* multimodal pattern `handle_voice_turn` already uses for the voice channel — Gemini understands audio directly within one multimodal turn, no separate transcription call required for the reply itself.
- **`backend/`'s `ChatTurnRequest`/`handle_turn` gain optional media fields.** `ChatTurnRequest` (`router.py:57-60`) gets `audio_base64: str | None`, `audio_mime_type: str | None`, `image_base64: str | None`, `image_mime_type: str | None` — base64 because this is a JSON HTTP request, not a multipart upload (unlike `/voice/turn`, which is out of scope here). `handle_turn` gains matching optional params and appends `types.Part.from_bytes(data=base64.b64decode(...), mime_type=...)` to the `parts` list built at `service.py:460-463`, one per attachment present. Default `None` on every field = byte-identical to today for every existing caller (the legacy WhatsApp-standalone path, web chat, etc.) — purely additive.
- **`agent/`'s orchestrator fetches attachment bytes and forwards them.** In `_process_via_chat_agent`, when the trailing incoming message(s) have `attachments` (and the feature flag is on): fetch each attachment's bytes via a new, plain unauthenticated `httpx.AsyncClient` (not `ChatwootClient` — Chatwoot's `data_url`s are absolute, directly-fetchable, pre-signed or Chatwoot-served URLs; reusing `ChatwootClient` would leak the account API token to that external host for no benefit), base64-encode, and include in the `/chat/turn` POST body.
- **One flag gates the whole feature**: `whatsapp_media_understanding_enabled` (agent/, default `False`, fail-open/default-preserving — today's text-only behavior when off). `backend/`'s side needs no flag — the new fields are optional and no-op when absent, matching this codebase's "additive, default-preserving" convention throughout.
- **Fix the silent-drop bug regardless of the flag.** Even with the feature disabled, an attachment-only message should not vanish with zero observability. When the flag is off (or fetch fails) and a message has attachments but no usable text, log it (structured, one line) and let the existing short-circuit stand (no customer-facing behavior change) — this makes the gap visible in logs/metrics instead of silent, without changing behavior for tenants that haven't opted in.

## Non-goals

- Not touching `backend/`'s standalone Twilio integration — separate, lower-priority path per the scope decision above.
- Not adding video support (roadmap doesn't ask for it; no existing pattern to lean on).
- Not building this for the legacy (`chat_agent_enabled=False`) router path — only the ADK chat-agent path (`_process_via_chat_agent`) gets media understanding; the legacy path keeps today's behavior (a smaller, lower-priority code path per the orchestrator's own structure — this repo is migrating toward the ADK chat-agent path per other work this session).
- Not solving Meta WhatsApp Business media-message verification/policy questions — external/business dependency, same category as the Customer 360 blocker in the roadmap doc; noted, not built.

## Design

### 1. Config flag

`agent/app/config.py`, near the Gemini/AI behavior block (`chat_agent_enabled`, etc.):

```python
# Voice-note + image understanding for the agent-bot's chat-agent path
# (orchestrator.py's _process_via_chat_agent, chat_agent_enabled=True only).
# Default False = today's text-only behavior, byte-identical. When True,
# incoming WhatsApp attachments (audio/image) are downloaded and forwarded
# to backend/'s /chat/turn as multimodal Parts alongside the text.
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

### 3. `backend/`'s `/chat/turn` — optional multimodal input

`ChatTurnRequest` (`backend/apps/backend/src/chatbot/features/chat/router.py:57-60`) gains:

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

`chat_turn` (`router.py:1064`) passes the four new fields through to `handle_turn`. `handle_turn` (`service.py:409`) gains matching optional params and extends the `parts` list built at `service.py:460-463`:

```python
parts = [types.Part.from_text(text=text)]
if audio_base64 and audio_mime_type:
    parts.append(types.Part.from_bytes(data=base64.b64decode(audio_base64), mime_type=audio_mime_type))
if image_base64 and image_mime_type:
    parts.append(types.Part.from_bytes(data=base64.b64decode(image_base64), mime_type=image_mime_type))
new_message = types.Content(role="user", parts=parts)
```

All four fields default to `None` — every existing caller (web chat, the legacy WhatsApp-standalone integration, anything else hitting `/chat/turn`) is unaffected; this is purely additive, matching `handle_voice_turn`'s already-established multimodal-Part pattern one endpoint over.

### 4. Orchestrator wiring

`agent/app/services/orchestrator.py`'s `_process_via_chat_agent` (the only path that calls `/chat/turn` — see Decision above for why the legacy `gemini.decide()` router is explicitly out of scope):

- Before building the request to `/chat/turn`, scan the trailing run of incoming messages (same messages `_latest_incoming_text` already walks) for `message.get("attachments")`. For each attachment, only when `settings.whatsapp_media_understanding_enabled`:
  - `file_type == "audio"` → `fetch_attachment_bytes` → base64-encode → set `audio_base64`/`audio_mime_type` on the outgoing request.
  - `file_type == "image"` → same → `image_base64`/`image_mime_type`.
- Include whichever fields were populated in the POST body to `/chat/turn`; when nothing was fetched (flag off, no attachments, or fetch failed), the request is identical to today's (all four fields absent/`None`).

### 5. Silent-drop observability fix (flag-independent)

In `_process_via_chat_agent`'s existing empty-text short-circuit: when the effective text is empty AND the latest incoming message(s) had at least one attachment, log one structured warning (e.g. `orchestrator_attachment_only_message_dropped`, with conversation id and attachment file_types) before returning. No behavior change — purely closes the "silent" part of "silent-drop" so this is observable in logs/metrics regardless of whether the media-understanding flag is on.

## Error handling

- `fetch_attachment_bytes` never raises — any failure (timeout, non-2xx, malformed URL) returns `None` and logs a warning. A fetch failure means that attachment's field(s) are simply omitted from the `/chat/turn` request — degrades to today's exact behavior for that attachment (text-only, or the empty-text short-circuit if there was no caption), never breaks the turn.
- `handle_turn`'s new `base64.b64decode(...)` calls are wrapped so a malformed base64 payload (shouldn't happen given `agent/` controls the encoding, but defense-in-depth) degrades to the text-only `Content` rather than raising into the turn.

## Testing

- `test_media.py` (new, `agent/tests/`): `fetch_attachment_bytes` — success, 404, timeout, malformed URL (mirrors `respx`-based test conventions already used in `agent/tests/`).
- `backend/`'s `test_router.py`/`test_service.py` (existing files, extend): `ChatTurnRequest` with `audio_base64`/`image_base64` set produces a multimodal `types.Content` with the extra `Part`(s); all fields absent (today's only caller shape) produces the identical single-`Part` `Content` as before (regression guard) — mirrors whatever test already covers `handle_voice_turn`'s multimodal-Part construction, if one exists, for the assertion style.
- `orchestrator.py` tests: attachment-only audio message + flag on → `/chat/turn` POST body includes `audio_base64`. Attachment-only image message + flag on → POST body includes `image_base64`. Flag off → attachments ignored, today's short-circuit still fires, but the new warning log line is emitted. Flag on + fetch failure → falls back to today's short-circuit/text-only behavior, no crash.

## Rollout

Both `agent/` and `backend/` redeploy (no Chatwoot fork/image change — this is backend-only wiring, no SPA surface). Default off — zero behavior change for any tenant until `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` is set on `agent/`; `backend/`'s new `/chat/turn` fields are optional and inert for every other caller regardless of that flag. Meta WhatsApp Business media-message policy/verification is an external prerequisite outside this repo's control, same category as other external blockers already noted in the roadmap doc.
