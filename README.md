# Proton Conversational AI

A Gemini-powered conversational AI agent for customer support, serving **text chat** and **voice** through a single agent core, with pluggable CRM integration (**Zendesk** or **Chatwoot + Zammad**).

Two apps in one repo:

- **`apps/backend/`** — Python / FastAPI / Google ADK (over Gemini) — webhook handlers + frontend API.
- **`apps/frontend/`** — Vue 3 / Vite / TypeScript / Pinia — browser UI with text chat and microphone-driven voice.

Voice goes end-to-end through Gemini: the browser captures audio with `MediaRecorder`, the backend forwards it as a multimodal `Part` to the same ADK runner used for text turns, and Gemini TTS (`gemini-2.5-flash-tts`) synthesizes the reply. No Twilio, no separate Speech-to-Text step.

---

## Highlights

- **Unified agent core** — one Gemini instruction + tool pipeline drives both channels.
- **Gemini native audio** — `audio/ogg` blob → ADK Part → Gemini TTS MP3 → browser playback. One model, one round trip.
- **Pluggable CRM** — swap Zendesk for Chatwoot/Zammad via configuration.
- **Tool-using LLM** — knowledge-base search, ticket classification, and human escalation as Gemini tools.
- **Automated handoff** — keyword/sentiment-triggered escalation produces a structured JSON summary for the receiving human agent.
- **Local-first** — in-memory mock adapters let the full backend test suite run with zero external dependencies.

---

## Architecture

Decisions live as ADRs:

- [docs/decisions/0001-python-fastapi-gemini-adk-stack.md](docs/decisions/0001-python-fastapi-gemini-adk-stack.md) — backend stack
- [docs/decisions/0002-monorepo-vue-frontend-gemini-audio.md](docs/decisions/0002-monorepo-vue-frontend-gemini-audio.md) — monorepo, Vue, Gemini end-to-end audio
- [.agents/rules/architectural-pattern.md](.agents/rules/architectural-pattern.md) — testability-first ports & adapters

```
proton-conversational-ai/
├── apps/
│   ├── backend/                          # Python FastAPI service
│   │   └── src/chatbot/
│   │       ├── main.py                   FastAPI bootstrap, DI, CORS
│   │       ├── platform/                 Config, structlog, ASGI setup
│   │       └── features/chat/
│   │           ├── ports.py              ChatPort, TicketingPort, KnowledgePort, TextToSpeechPort
│   │           ├── service.py            handle_turn + handle_voice_turn orchestrator
│   │           ├── agents.py             ADK support + summarizer agents
│   │           ├── router.py             /webhooks/*, /chat/turn, /voice/turn
│   │           └── adapters/             mock, chatwoot_zammad, zendesk, gcp_voice (Gemini TTS)
│   └── frontend/                         # Vue 3 + Vite + Pinia
│       └── src/
│           ├── features/chat/            # vertical slice: api, store, components, types
│           ├── features/voice/           # vertical slice: + composables/useMediaRecorder.ts
│           ├── components/ui/            # ChannelTabs
│           ├── components/layout/        # AppHeader
│           ├── layouts/MainLayout.vue
│           ├── views/HomeView.vue
│           └── plugins/api.ts
└── docs/
```

---

## Endpoints

All backend routes:

| Method | Path                    | Purpose                                              |
|--------|-------------------------|------------------------------------------------------|
| GET    | `/`                     | Health check (providers + model)                     |
| POST   | `/chat/turn`            | Text turn — JSON in, JSON out (used by the Vue app)  |
| POST   | `/voice/turn`           | Voice turn — multipart audio in, `audio/mpeg` out    |
| POST   | `/webhooks/chatwoot`    | Chatwoot inbound message webhook                     |
| POST   | `/webhooks/zendesk`     | Zendesk / Sunshine Conversations webhook             |

`/voice/turn` adds two response headers: `X-Reply-Text` (URL-encoded reply text) and `X-Handoff-Reason` (set when escalation triggers).

> For a full walkthrough — webhook payloads, live-provider setup, troubleshooting — see [docs/USAGE.md](docs/USAGE.md).

---

## Quick Start

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm (or pnpm)
- (Optional, for live Gemini calls) GCP project with the Generative Language API + Cloud Text-to-Speech enabled
- (Optional, for live CRM) Zendesk / Chatwoot / Zammad credentials

### Run the backend

```bash
cd apps/backend
uv sync
cp .env.example .env       # then edit .env with your values

.venv/bin/uvicorn chatbot.main:app --reload
# → http://localhost:8000
```

`GET /` returns:

```json
{ "status": "healthy", "crm_provider": "chatwoot", "voice_provider": "mock", "model": "gemini-2.5-flash" }
```

### Run the frontend

In a second terminal:

```bash
cd apps/frontend
npm install
npm run dev
# → http://localhost:5173
```

The header badges show the current backend providers. Switch the **Chat** / **Voice** tab to use either channel. The Voice tab uses your microphone — Chrome and Firefox supported (Safari isn't yet, see ADR 0002).

---

## Configuration

Backend settings load from `apps/backend/.env`. See [.env.example](apps/backend/.env.example) for the full template; key vars:

| Variable                    | Purpose                                                | Default                         |
|-----------------------------|--------------------------------------------------------|---------------------------------|
| `CRM_PROVIDER`              | `chatwoot` or `zendesk`                                | `chatwoot`                      |
| `VOICE_PROVIDER`            | `mock` or `gcp`                                        | `mock`                          |
| `FRONTEND_ORIGIN`           | CORS origin allowed by the backend                     | `http://localhost:5173`         |
| `GEMINI_MODEL`              | Gemini model id                                        | `gemini-2.5-flash`              |
| `GEMINI_TTS_MODEL`          | TTS model (via Cloud TTS API)                          | `gemini-2.5-flash-tts`          |
| `GEMINI_TTS_VOICE`          | Voice name (e.g. `Kore`, `Charon`, `Callirrhoe`)       | `Kore`                          |
| `VOICE_DEFAULT_LANG`        | BCP-47 code for TTS output                             | `en-US`                         |
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` to route Gemini via Vertex AI                   | `false`                         |
| `VERTEX_PROJECT_ID`         | Required if `GOOGLE_GENAI_USE_VERTEXAI=true`           | —                               |
| `ZENDESK_*`                 | Sunshine Conversations + Support API + email           | —                               |
| `CHATWOOT_*` / `ZAMMAD_*`   | self-hosted CRM credentials                            | `http://localhost:3000`         |

Frontend reads `VITE_API_BASE_URL` at build/dev time (default `http://localhost:8000`).

### GCP credentials (live voice)

The Gemini TTS adapter uses [Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials):

```bash
gcloud auth application-default login
```

In production, attach a service account to the workload runtime.

---

## Development

### Backend

```bash
cd apps/backend
.venv/bin/ruff format .
.venv/bin/ruff check . --fix
.venv/bin/mypy src/ --strict
.venv/bin/pytest src/
.venv/bin/uvicorn chatbot.main:app --reload
```

### Frontend

```bash
cd apps/frontend
npm run type-check      # vue-tsc strict
npm run build           # production build
npm run dev             # Vite dev server
```

All checks (backend ruff/mypy/pytest, frontend vue-tsc) must pass before commit.

### Project conventions

This repo uses the [`.agents/`](.agents/) framework — a structured prompt library defining rules, agent personas, skills, and multi-agent workflows for AI-assisted development. See [CLAUDE.md](CLAUDE.md) for the rule hierarchy and workflow entry points.

Key rules:

- Organize by **feature** (vertical slices), not by technical layer.
- I/O behind ports; business logic depends only on protocols.
- `<script setup lang="ts">` exclusively on the frontend.
- Strict `mypy` + `ruff` (backend) and `vue-tsc` strict (frontend) on every commit.
- Conventional commits: `<type>(<scope>): <description>`.
- New architectural decisions get an ADR under `docs/decisions/`.

---

## Roadmap

- Safari `MediaRecorder` support (transcoder or polyfill)
- Persistent session store (currently in-memory)
- Frontend unit tests via Vitest
- Observability dashboards + alert wiring

See [CHANGELOG.md](CHANGELOG.md) for the version history.

---

## License

Not yet specified. Until a license is added, all rights are reserved by the repository owner.
