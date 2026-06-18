# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `GET /sim` developer simulator UI with chat + voice channels. Gated on `DEBUG=true`.
- `POST /sim/chat` direct-turn endpoint used by the simulator; runs the orchestrator without the webhook envelope.
- `TWILIO_ESCALATION_PHONE` and `PUBLIC_BASE_URL` settings — replaces a hardcoded phone number in the TwiML Dial element and prefixes static audio URLs so Twilio can reach them from the public internet.

### Planned
- Live Chatwoot/Zammad webhook ingress smoke test
- Live Twilio Voice end-to-end call test
- Persistent session store (currently in-memory)
- Observability dashboards and alert wiring

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
