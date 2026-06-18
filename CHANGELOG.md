# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
