# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — frontend UX & Zendesk webhook (2026-06-19)

- **Frontend chat autoscroll UX:** Added automated scrolling to the bottom of the chat container in `ChatLog.vue` when new messages (from the user, AI, or agent) are appended.
- **Zendesk Support Webhook route:** Added `/webhooks/zendesk-support` to backend router to synchronize agent public comments on standard Support tickets into the SSE stream (for backward compatibility when fallback ticketing is active).

### Fixed — duplicate tickets & immediate handoff routing (2026-06-19)

- **Ticket creation on human agent handoff:** Switched Sunshine Conversations handoff summary message author from `business` to `user` (using the session ID as external ID). This ensures Zendesk Messaging registers user activity immediately at handoff and creates the ticket in the agent workspace without requiring the customer to send another message.
- **Prevention of duplicate Support tickets:** Bypassed manual standard Support ticket creation in `OrchestratorService` when `live_chat_available` is true, since Sunshine Conversations automatically manages Messaging tickets.

### Added — handoff bridge, KB grounding, persistence (2026-06-19)

- **Sunshine Conversations bridge** for inline human-agent handoff. `SunshineConversationsAdapter` (httpx, v2 REST) opens a conversation per session and posts the AI summary + recent transcript as a business message; `forward_customer_message` relays subsequent customer turns. Reuses the existing `ZENDESK_KEY_ID` / `ZENDESK_SECRET_KEY` for HTTP Basic auth.
- **`HandoffBridge`** coordinator with per-session `asyncio.Queue` subscribers and a pluggable `HandoffStorePort` backing.
- **`GET /chat/stream/{session_id}`** — Server-Sent Events stream that yields `agent_message` events with a 30 s heartbeat.
- **`POST /webhooks/sunshine`** — HMAC-SHA256-verified webhook handler. Customer-authored events are dropped (we already showed them in the UI when the user pressed Send); business-authored events are published to the matching session's queue.
- **Frontend `ChatMessage.role` extended with `'agent'`** + purple-tinted card styling. `chat.store` opens an `EventSource` on handoff when `live_chat_available` is true, appends incoming agent messages to the same log, and closes the stream on `resetSession`.
- **`HandoffStatePort`** — persistence boundary for the bridge. Two implementations: `InMemoryHandoffStore` (dev/tests) and `FirestoreHandoffStore` (one doc per active handoff in `proton-db/handoff_sessions/<session_id>` with indexed reverse-lookup). The bridge keeps a read-through in-process cache; a backend restart costs one Firestore round-trip per session before warm.
- **`KNOWLEDGE_PROVIDER=vertex_search` path** — `VertexAISearchAdapter` queries the `proton-kb-engine` Discovery Engine over the `proton-kb` data store and reads `struct_data` (our authoritative fields) ahead of `derived_struct_data`. Falls back to the pre-extracted `body_excerpt` for content because Standard tier doesn't return snippets.
- **`scripts/scrape_proton.py` rewritten as a structured KB pipeline** — produces JSONL Documents (one per line) with `{id, struct_data: {title, link, language, body_excerpt, sections}, content: {uri, mime_type}}` from the Proton website sitemap, uploads JSONL + HTML to GCS, creates the data store + engine if missing, and imports with `data_schema="document"`. Discrete CLI stages (`--scrape-only`, `--upload-only`, `--import-only`, `--purge`).
- **Agent prompt** rewritten as a three-step playbook (Search → Answer → optionally Escalate). Explicit `NO_MATCHES` branching, Markdown-link citations from `Source URL`, replies in the customer's language (en / ms / zh). Grounds the agent in Proton's product domain.
- **Settings** — `SUNSHINE_WEBHOOK_SECRET`, `HANDOFF_STORE`, `FIRESTORE_PROJECT_ID`, `FIRESTORE_DATABASE_ID`, `FIRESTORE_HANDOFF_COLLECTION`, `KNOWLEDGE_PROVIDER`, `VERTEX_SEARCH_*`.
- **ADR-0003** — coupled decision for the Sunshine Conversations bridge, Vertex AI Search KB, and Firestore handoff persistence.

### Changed — handoff & KB (2026-06-19)

- **BREAKING — handoff UX is no longer the Zendesk Messenger widget.** `<script id="ze-snippet">`, `zESettings`, `plugins/zendesk.ts`, and the `HandoffStatus.vue` full-panel swap are removed. Handoff now keeps the existing chat log + input visible and renders agent replies inline as a new `'agent'` message role. Deployments that depended on the embedded widget must switch to registering a Sunshine Conversations webhook integration pointing at `POST /webhooks/sunshine`.
- **BREAKING — `ChatTurnResponse` adds `forwarded_to_agent: bool`** and `HandoffPayload` adds `live_chat_available: bool`. The voice `X-Handoff-*` headers gain `X-Handoff-Live-Chat`.
- **`OrchestratorService` constructor** takes new optional `human_agent_bridge: HumanAgentBridgePort | None` and `handoff_bridge: HandoffBridge | None` parameters. When both are present and the session is already handed off, `/chat/turn` text is forwarded to Sunshine Conversations instead of Gemini.
- **Zendesk Support tickets** are now attributed to a session-scoped pseudo-user (`Proton AI Customer (<session-id>)` with email `<session-id>@proton.devoteam.example`) instead of defaulting to the API token owner. Stops escalation emails from landing in the admin's inbox.
- **`InMemoryKnowledgeAdapter`** is now a development fallback only — the production path is `KNOWLEDGE_PROVIDER=vertex_search`.
- **Agent tool set** — `classify_ticket_tool` is dropped from the registered tools because its result was stored only in `tool_context.state` (unread downstream) and Gemini was treating the classify call as the turn's terminal action, silently skipping the customer-facing text reply. The function stays defined so it can be re-enabled once its state is wired into the handoff payload.

### Fixed — handoff & KB (2026-06-19)

- **Empty replies on grounded questions.** Root cause: Gemini 2.5 Flash treating `classify_ticket_tool` as the terminal call. Fixed by dropping the tool from the registered set.
- **GCS object URI surfaced to the customer as the citation link.** Vertex's `derived_struct_data.link` overrides our canonical Proton URL with the bucket URI. Adapter now prefers our explicit `struct_data` before falling back to derived.
- **Two-ticket fragmentation on handoff.** The Zendesk Support ticket (with AI transcript) and the Messenger conversation (live chat) are still distinct records, but now share a `session-<id>` tag and external_id linkage so agents can correlate. The user-facing UI no longer shows two chat surfaces.
- **Handoff state lost on backend restart.** `HandoffBridge`'s `session_id ↔ conversation_id` map is now persisted in Firestore (`HANDOFF_STORE=firestore`). Subscribers (SSE queues) are still in-process; clients reconnect after a restart.
- **Sunshine webhook signature verification rejected all real v2 traffic.** `verify_webhook_signature` only computed an HMAC-SHA256 of the body and compared it to `X-Api-Key`, but Sunshine Conversations v2 sends the **raw shared secret** directly in `X-Api-Key` — so every genuine webhook would have returned `401`. The check now accepts the request when `X-Api-Key` matches the shared secret (constant-time) **or** an HMAC-SHA256 of the body (legacy Smooch v1 back-compat); both still require knowledge of the secret. Added `adapters/test_sunshine_conversations.py` covering both accept paths plus rejection.
- **KB citations rendered as raw markdown in the chat UI.** `ChatLog.vue` interpolated the reply as plain text, so the agent's grounded `[label](url)` citations showed literally instead of as clickable links. Added `features/chat/markdown.ts` (`marked` → `DOMPurify`) and switched assistant/agent messages to sanitized `v-html`; links open in a new tab with `rel="noopener noreferrer"`. User/system messages stay literal. The LLM-authored text is treated as untrusted and sanitized before rendering.

### Added
- **Monorepo structure** (`apps/backend/` + `apps/frontend/`) per `project-structure-vue-frontend.md`.
- **Vue 3 frontend** at `apps/frontend/` (Vite + TypeScript + Pinia, `<script setup lang="ts">` exclusively). Vertical feature slices for `chat/` and `voice/`, each with its own `api/`, `store/`, `components/`, and `types/`.
- **`useVoiceCapture` composable** — `getUserMedia` + `AnalyserNode` + `MediaRecorder` with built-in **voice activity detection** (sustained-silence auto-stop, configurable RMS floor / min-speech / trailing-silence). On stop, the captured Opus blob is decoded via `AudioContext.decodeAudioData`, mixed to mono, linearly resampled to 16 kHz, and packed as 16-bit PCM WAV — Gemini accepts WAV natively, so Chrome's webm/opus output is no longer rejected.
- **`WaveformDisplay.vue`** — 48-bar canvas waveform driven by the live analyser; switches palette when speech is detected.
- **`VoiceRecorder.vue`** rewritten as **tap-to-talk** with a four-phase status machine (`idle → listening → processing → speaking`), pulsing halo locked to mic level, mid-turn manual-stop, and a <kbd>Space</kbd>-key shortcut.
- **`phase` state in `useVoiceStore`** — `'idle' | 'listening' | 'processing' | 'speaking'`. Assistant audio autoplays and flips back to `idle` on `ended`.
- **`POST /chat/turn`** — JSON in, JSON out frontend chat endpoint (returns `{reply, language, sentiment, handoff}`).
- **`POST /voice/turn`** — multipart audio in (now `audio/wav`), `audio/mpeg` out with `X-Reply-Text` (URL-encoded) and `X-Handoff-Reason` response headers.
- **CORS middleware** on the backend; new `FRONTEND_ORIGINS` setting accepts a JSON list of allowed origins and defaults to Vite's port fallbacks (`5173`–`5180`).
- **Vertex AI env mirroring** — `bootstrap_application` now copies `vertex_project_id` / `vertex_location` into `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` so the `google-genai` SDK actually sees them when `GOOGLE_GENAI_USE_VERTEXAI=true`. A single `.env` now drives both layers.
- **`GEMINI_TTS_MODEL` / `GEMINI_TTS_VOICE`** settings selecting the Gemini TTS model and voice (defaults: `gemini-2.5-flash-tts`, `Kore`).
- **ADR 0002** documenting the monorepo, Vue frontend, and Gemini end-to-end audio decisions together.

### Changed
- **BREAKING — voice pipeline rewritten end-to-end through Gemini.**
  - `OrchestratorService.handle_voice_turn` now sends the audio bytes directly as a multimodal `Part` to the ADK runner. No separate transcription step.
  - `GcpTextToSpeechAdapter` replaced by `GeminiTextToSpeechAdapter` — uses Cloud TTS with `model_name="gemini-2.5-flash-tts"`.
  - `MockVoiceAdapter` now implements only `TextToSpeechPort`.
- **Frontend upload format**: voice turns now post `audio/wav` (16 kHz mono PCM-16) instead of `audio/ogg`/`audio/webm`. The wire MIME is consumed verbatim by the ADK `Part`.
- **Settings — `frontend_origin: str` → `frontend_origins: list[str]`** (BREAKING for anyone overriding `.env`). `.env.example` updated; old `FRONTEND_ORIGIN=…` keys must be migrated to `FRONTEND_ORIGINS=["…"]`.
- **CORS consolidated to one middleware** in `bootstrap_application` (the duplicate `allow_origins=["*"]` in `create_app` was removed). Stacking two `CORSMiddleware` layers caused the restrictive one to win for preflight while the permissive one shaped simple-request responses — symptoms were "Failed to fetch" on `/chat/turn` from Vite fallback ports.
- **Frontend replaces the old `/sim` simulator.** The inline-HTML `features/sim/` package was deleted; the Vue app at `apps/frontend/` is the new UI surface.
- **CLAUDE.md, README, USAGE** updated for the new layout and `cd apps/backend` / `cd apps/frontend` workflow.

### Fixed
- **Chat "Failed to fetch" from Vite fallback ports** (e.g., `:5174`, `:5176`) — the old single-origin allowlist returned `400 Disallowed CORS origin` on preflight. Replaced with the multi-origin `FRONTEND_ORIGINS` list.
- **Voice `Maaf, terjadi kendala teknis…` fallback in Chrome** — Gemini does not accept `audio/webm;codecs=opus`, so every Chrome capture hit the exception handler. Re-encoding to WAV in the browser fixes the round trip natively.
- **`google-genai` not honouring Settings on Vertex** — declared but never propagated, so even with `GOOGLE_GENAI_USE_VERTEXAI=true` the SDK fell back to AI Studio (and 401'd without `GOOGLE_API_KEY`). Bootstrap now mirrors the values into the env vars the SDK actually reads.

### Removed
- **`useMediaRecorder` composable** — superseded by `useVoiceCapture` (which adds VAD + WAV encoding). Hold-to-talk semantics are gone; tap-to-talk replaces them.
- **Duplicate `CORSMiddleware`** registration in `chatbot/platform/server.py` (the permissive `allow_origins=["*"]` one).
- **BREAKING — Twilio integration removed entirely.**
  - Routes `/webhooks/voice/twilio` and `/webhooks/voice/twilio/process` deleted.
  - All TwiML generation and the `static/audio/` mount removed.
  - Settings `TWILIO_*`, `PUBLIC_BASE_URL`, and `TWILIO_ESCALATION_PHONE` removed.
  - Deps `twilio`, `google-cloud-speech` and the `twilio.*` mypy override removed from `apps/backend/pyproject.toml`.
- `SpeechToTextPort` protocol and `VoiceTranscript` model deleted — Gemini consumes audio natively.
- `GcpSpeechToTextAdapter` deleted.

### Planned
- Safari support for the voice capture path (Safari's `MediaRecorder` is feature-limited; needs a polyfill or a WebAudio-only PCM capture path).
- Continuous "conversation mode" — auto-resume listening after the assistant finishes speaking.
- Frontend unit tests via Vitest (with the WAV encoder + VAD timing as first targets).
- Persistent session store (currently in-memory).
- Webhook signature verification on `/webhooks/zendesk` and `/webhooks/chatwoot`.
- Observability dashboards and alert wiring.

---

## [0.1.0] — 2026-06-18

Initial release. Single-app Python backend providing a unified conversational AI agent across text and voice channels with pluggable CRM/ticketing integration.

### Added

#### Agent core
- Google ADK integration over Gemini (`gemini-2.5-flash` default) in `features/chat/agents.py`.
- Tool-using support agent with three closures: `search_kb_tool`, `classify_ticket_tool`, `emit_handoff_tool`.
- Summarizer agent producing structured JSON handoff payloads on escalation.
- Escalation triggers: negative sentiment, keyword match, retry limit.

#### Platform layer (`src/chatbot/platform/`)
- `config.py` — Pydantic Settings loader for env + `.env` (typed, cached).
- `logger.py` — `structlog` JSON logging per `logging-and-observability-mandate.md`.
- `server.py` — FastAPI ASGI bootstrap with CORS and observability middleware.
- `main.py` — Dependency injection wiring; `GET /` health endpoint.

#### Feature slice (`src/chatbot/features/chat/`)
- `ports.py` — Protocols: `ChatPort`, `TicketingPort`, `KnowledgePort`, `SpeechToTextPort`, `TextToSpeechPort`.
- `models.py` — Immutable dataclasses (`Message`, `TurnResult`, `HandoffPayload`, `KbArticle`, `VoiceTranscript`).
- `service.py` — Conversation orchestrator with `handle_text_turn` and `handle_voice_turn`.
- `router.py` — Webhook endpoints:
  - `POST /webhooks/chatwoot`
  - `POST /webhooks/zendesk`
  - `POST /webhooks/voice/twilio` (welcome — returns TwiML)
  - `POST /webhooks/voice/twilio/process` (speech result — returns TwiML)
- `prompts.py`, `schemas.py` — model instruction templates and structured-output schemas.

#### Adapters (`features/chat/adapters/`)
- `mock.py` — In-memory `InMemoryChatAdapter`, `InMemoryTicketingAdapter`, knowledge and voice mocks. Enables zero-dependency unit tests.
- `chatwoot_zammad.py` — Async `httpx` client for Chatwoot Messaging + Zammad Ticketing.
- `zendesk.py` — Async client covering Sunshine Conversations (chat), Support API (ticketing), and Guide (knowledge base).
- `gcp_voice.py` — `GcpSpeechToTextAdapter` + `GcpTextToSpeechAdapter` using Google Cloud client libraries with Application Default Credentials.

#### Testing
- 9 tests across `test_service.py` (text + voice turn logic, pause/takeover, escalation) and `test_router.py` (webhook ingestion, Twilio TwiML generation).
- Strict `mypy --strict` passes on 18 source files.
- `ruff` passes (rules: `E F I B UP SIM RET ARG PL RUF ASYNC S C4`).

#### Tooling & configuration
- `pyproject.toml` — `hatchling` build backend, dev deps (`pytest`, `pytest-asyncio`, `ruff`, `mypy`), strict `mypy` config, `ruff` selections, `pytest` asyncio auto-mode.
- `uv.lock` — reproducible dependency lockfile.
- `.env.example` — env template covering provider, GCP, Zendesk, Chatwoot, and Zammad keys.
- `.gitignore` — excludes `.env`, virtualenv, lint/test caches, agent worktrees, local Claude settings.

#### Documentation
- `README.md` — project overview, quick-start, endpoint table, configuration reference.
- `docs/decisions/0001-python-fastapi-gemini-adk-stack.md` — first ADR recording the stack choice with options and consequences.
- `CLAUDE.md` — directory map and dev commands for AI-assisted contribution.
- `.agents/` — operations framework (15 personas, ~40 rules, ~50 skills, workflows).

### Changed
- `ZENDESK_EMAIL` promoted from a `getattr(settings, "zendesk_email", "admin@example.com")` fallback in `zendesk.py` to a first-class `Settings` field. The previous fallback would have silently authenticated against the wrong identity in production.

### Security
- `.env` is git-ignored from the first commit; only `.env.example` (placeholder values) is tracked.
- All third-party SDK calls use async clients (`httpx`, `google-cloud-*` async variants where available) to keep the ASGI loop unblocked under load.

[Unreleased]: https://github.com/Yudaadi-devo/proton-conversational-ai/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Yudaadi-devo/proton-conversational-ai/releases/tag/v0.1.0
