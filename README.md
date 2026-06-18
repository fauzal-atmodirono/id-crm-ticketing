# Proton Conversational AI

A Python backend for a Gemini-powered conversational AI agent that serves both **text chatbot** and **voicebot** channels through a single shared agent core. Integrates with **Zendesk** (commercial) and **Chatwoot + Zammad** (open-source) CRM/ticketing platforms via a pluggable adapter pattern.

Built with FastAPI, Google's Agent Development Kit (ADK) over Gemini, and Google Cloud Speech-to-Text / Text-to-Speech.

---

## Features

- **Unified agent core** — one instruction set and tool pipeline drives both channels.
- **Voice & text parity** — voicebot transcribes input, runs the same agent loop, then synthesizes the reply.
- **Pluggable CRM** — swap Zendesk for Chatwoot/Zammad via configuration; no code change required.
- **Tool-using LLM** — knowledge-base search, ticket classification, and human escalation as Gemini tools.
- **Automated handoff** — keyword/sentiment-triggered escalation produces a structured JSON summary for the receiving human agent.
- **Local-first development** — in-memory mock adapters and a mock voice provider let the full test suite run with zero external dependencies.

---

## Architecture

The project is a single-app Python backend organized as a vertical feature slice. The full layout and stack rationale live in:

- [docs/decisions/0001-python-fastapi-gemini-adk-stack.md](docs/decisions/0001-python-fastapi-gemini-adk-stack.md) — stack decision record
- [CLAUDE.md](CLAUDE.md) — directory map and commands
- [.agents/rules/architectural-pattern.md](.agents/rules/architectural-pattern.md) — testability-first / ports-and-adapters rules

```
src/chatbot/
├── main.py                  FastAPI bootstrap & DI
├── platform/                Config, structlog, ASGI setup
└── features/chat/
    ├── ports.py             ChatPort, TicketingPort, KnowledgePort, STT/TTS ports
    ├── service.py           Conversation orchestrator
    ├── agents.py            Gemini ADK support + summarizer agents
    ├── router.py            FastAPI webhook routes
    └── adapters/
        ├── mock.py          In-memory adapters for tests
        ├── chatwoot_zammad.py
        ├── zendesk.py       Sunshine Conversations + Support + Guide
        └── gcp_voice.py     Google Cloud STT / TTS
```

I/O sits behind protocol ports; business logic is pure and dependency-free, per `architectural-pattern.md`.

---

## Endpoints

| Method | Path                                | Purpose                                         |
|--------|-------------------------------------|-------------------------------------------------|
| GET    | `/`                                 | Health check (returns providers + model)        |
| POST   | `/webhooks/chatwoot`                | Chatwoot inbound message webhook                |
| POST   | `/webhooks/zendesk`                 | Zendesk / Sunshine Conversations webhook        |
| POST   | `/webhooks/voice/twilio`            | Twilio Voice — call start (returns TwiML)       |
| POST   | `/webhooks/voice/twilio/process`    | Twilio Voice — speech result (returns TwiML)    |

---

> For a full walkthrough — webhook payloads, live-provider setup, troubleshooting — see [docs/USAGE.md](docs/USAGE.md).

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- (Optional, for live voice) GCP project with Speech-to-Text + Text-to-Speech enabled
- (Optional, for live CRM) Zendesk / Chatwoot / Zammad credentials

### Setup

```bash
# Clone and enter
git clone https://github.com/Yudaadi-devo/proton-conversational-ai.git
cd proton-conversational-ai

# Install dependencies (creates .venv/)
uv sync

# Copy env template and fill in your values
cp .env.example .env
# edit .env — see "Configuration" below
```

### Run

```bash
.venv/bin/uvicorn chatbot.main:app --reload
```

Visit `http://localhost:8000/` — you should see:

```json
{
  "status": "healthy",
  "crm_provider": "chatwoot",
  "voice_provider": "mock",
  "model": "gemini-2.5-flash"
}
```

---

## Configuration

All settings load via Pydantic from `.env`. The full template is in [.env.example](.env.example); the most important variables:

| Variable                    | Purpose                                                | Default                         |
|-----------------------------|--------------------------------------------------------|---------------------------------|
| `CRM_PROVIDER`              | `chatwoot` or `zendesk`                                | `chatwoot`                      |
| `VOICE_PROVIDER`            | `mock` or `gcp`                                        | `mock`                          |
| `GEMINI_MODEL`              | Gemini model id                                        | `gemini-2.5-flash`              |
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` to route Gemini calls via Vertex AI             | `false`                         |
| `VERTEX_PROJECT_ID`         | Required if `GOOGLE_GENAI_USE_VERTEXAI=true`           | —                               |
| `ZENDESK_SUBDOMAIN`         | e.g. `acme` for `acme.zendesk.com`                     | —                               |
| `ZENDESK_EMAIL`             | Email tied to the API token (Support API auth)         | —                               |
| `ZENDESK_API_TOKEN`         | Zendesk Support API token                              | —                               |
| `ZENDESK_APP_ID` / `_KEY_ID` / `_SECRET_KEY` | Sunshine Conversations credentials    | —                               |
| `CHATWOOT_API_URL` / `_TOKEN` / `_ACCOUNT_ID` | Chatwoot self-hosted credentials     | `http://localhost:3000`         |
| `ZAMMAD_API_URL` / `_TOKEN` | Zammad credentials                                     | `http://localhost:3000`         |

### GCP credentials (live voice)

The GCP voice adapter uses [Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials). For local development:

```bash
gcloud auth application-default login
```

No service-account JSON file is required. In production, attach a service account to the workload runtime.

---

## Development

### Commands

```bash
# Lint & format
.venv/bin/ruff format .
.venv/bin/ruff check . --fix

# Strict type checking
.venv/bin/mypy src/ --strict

# Tests (uses in-memory mocks — no network/credentials needed)
.venv/bin/pytest src/

# Dev server with auto-reload
.venv/bin/uvicorn chatbot.main:app --reload
```

All three checks must pass before commit. The project ships with 9 unit + route tests covering happy-path turns, AI pause/takeover, escalation triggers, and Twilio TwiML generation.

### Project conventions

This repo uses the [`.agents/`](.agents/) framework — a structured prompt library defining rules, agent personas, skills, and multi-agent workflows for AI-assisted development. See [CLAUDE.md](CLAUDE.md) for the rule hierarchy, workflow entry points, and parallel-dispatch protocol.

Key rules to know before contributing:

- Organize by **feature**, not by technical layer. Vertical slices only.
- I/O behind ports; business logic depends only on protocols.
- Strict `mypy` + `ruff` enforced on every commit.
- Conventional commits: `<type>(<scope>): <description>`.
- New architectural decisions get an ADR under `docs/decisions/`.

---

## Roadmap

The following are known follow-ups, not yet implemented:

- Live Chatwoot/Zammad webhook ingress smoke test
- Live Twilio Voice end-to-end call test
- Persistent session store (currently in-memory)
- Observability dashboards + alert wiring per `logging-and-observability-mandate.md`

See [CHANGELOG.md](CHANGELOG.md) for the version history.

---

## License

Not yet specified. Until a license is added, all rights are reserved by the repository owner.
