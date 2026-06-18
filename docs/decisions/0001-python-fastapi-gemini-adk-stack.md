# 0001. Python + FastAPI + Gemini ADK Stack for Conversational AI Backend

**Date:** 2026-06-18
**Status:** Accepted

## Context

The project requires a backend that powers a conversational AI agent across two channels (text chatbot and voicebot) and integrates with multiple CRM/ticketing platforms (Zendesk, Chatwoot, Zammad). Functional requirements include:

- A single agent core shared by both channels.
- Tool-using LLM behavior (knowledge base search, ticket classification, human handoff).
- Async webhook ingestion from Chatwoot, Zendesk, and Twilio Voice without blocking the request loop.
- Speech-to-Text and Text-to-Speech adapters with mock variants for local development.
- Strict type safety and full unit-test coverage as a prerequisite for production hardening.

The stack choice is the first significant architectural decision in this repository and locks in the language, web framework, agent runtime, and configuration model for all subsequent work.

## Options Considered

### Option A: Python + FastAPI + Google ADK (Gemini)
- **Pros:** First-party Python SDK for the Gemini Agent Development Kit; FastAPI's `httpx`-based async ecosystem fits webhook ingestion; mature Pydantic-based settings; `google-cloud-speech` / `google-cloud-texttospeech` SDKs are Python-native; large pool of ML/LLM tooling.
- **Cons:** GIL limits CPU-bound parallelism (acceptable here — workload is I/O-bound); slower than Go/Rust per-request but well under SLO budget for webhook handling.
- **Effort:** Low — `project-structure-python-backend.md` already defines the layout.

### Option B: Go + chi + Gemini REST API
- **Pros:** Better concurrency primitives; smaller deployment footprint; statically typed.
- **Cons:** No first-party Gemini ADK in Go (would have to drive raw REST + roll our own tool-loop / handoff logic); GCP Speech/TTS SDKs are usable but less idiomatic; would re-implement what ADK already provides.
- **Effort:** High — agent loop, summarizer pipeline, and tool execution would all be from-scratch.

### Option C: TypeScript (Node.js) + Express/Fastify + Gemini SDK
- **Pros:** Shared language with potential frontend; Gemini SDK exists for JS.
- **Cons:** ADK's Python implementation is more feature-complete; Node's audio handling for STT/TTS streaming is weaker; Pydantic-equivalent runtime validation requires extra libraries (`zod`, etc.).
- **Effort:** Medium — comparable to Python for boilerplate but loses the ADK advantage.

## Decision

We chose **Option A: Python + FastAPI + Google ADK (Gemini)**.

The deciding factor is the first-party Python ADK: it already provides the tool-loop, structured output, and agent composition primitives this project needs. Building the same on Go or Node would mean re-implementing months of Google's tooling. FastAPI's async model and Pydantic Settings cleanly satisfy the I/O-bound webhook ingest pattern and the configuration requirements in [logging-and-observability-mandate.md](../../.agents/rules/logging-and-observability-mandate.md).

The layout follows [project-structure-python-backend.md](../../.agents/rules/project-structure-python-backend.md) — feature-sliced under `src/chatbot/features/chat/` with adapters behind protocol ports per [architectural-pattern.md](../../.agents/rules/architectural-pattern.md).

## Consequences

### Positive
- Agent loop, tool dispatch, and summarizer composition come from ADK rather than custom code.
- Strict `mypy --strict` + `ruff` enforce type and lint quality from day one (currently passing on 18 files).
- In-memory mock adapters allow the full test suite to run with zero external dependencies (9 tests passing in ~0.9s).
- Pydantic Settings provides typed env-var loading with `.env` support, satisfying [configuration-management-principles.md](../../.agents/rules/configuration-management-principles.md).

### Negative
- Python concurrency under load is bounded by the GIL — if future requirements add CPU-heavy audio processing, we may need to push that out to a worker process or rewrite hot paths.
- Two Python-side deprecation warnings surface in tests from upstream ADK (`BaseAgentConfig`) — we depend on Google maintaining the SDK; pinning major versions in `pyproject.toml` will be needed.
- `.venv` and `uv.lock` add a Python-toolchain dependency for every contributor.

### Risks
- **ADK API churn**: ADK is still maturing. Mitigation — pin versions, wrap ADK behind our own `agents.py` so swaps stay local.
- **GCP credential setup friction**: Application Default Credentials (ADC) work locally with `gcloud auth application-default login` but require service accounts in production. Mitigation — mock voice provider remains the default (`VOICE_PROVIDER=mock`).
- **Sunshine Conversations API deprecation**: Zendesk has signalled long-term shifts in their messaging API surface. Mitigation — the adapter pattern means we can swap `ZendeskAdapter` without touching `service.py`.

## Related

- [architectural-pattern.md](../../.agents/rules/architectural-pattern.md) — Testability-First, I/O behind ports
- [project-structure-python-backend.md](../../.agents/rules/project-structure-python-backend.md) — feature-slice layout
- [python-idioms-and-patterns.md](../../.agents/rules/python-idioms-and-patterns.md) — Pydantic, async, structlog
- [logging-and-observability-mandate.md](../../.agents/rules/logging-and-observability-mandate.md) — structured JSON logs
- [rugged-software-constitution.md](../../.agents/rules/rugged-software-constitution.md) — defense-in-depth posture
