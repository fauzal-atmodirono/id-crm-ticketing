# Proton Conversational AI

A Gemini-powered conversational AI agent for customer support, serving **text chat**, **voice**, **WhatsApp** (via Twilio), and **Email** (via Zendesk) through a single agent core, with pluggable CRM integration (**Zendesk** or **Chatwoot + Zammad**), Knowledge-Base grounding via **Vertex AI Search**, and **inline human-agent handoff** — over Server-Sent Events in the web UI, or two-way through a single Zendesk ticket on WhatsApp/Email, closing with a CSAT survey.

Two apps in one repo:

- **`apps/backend/`** — Python / FastAPI / Google ADK (over Gemini) — webhook handlers + frontend API.
- **`apps/frontend/`** — Vue 3 / Vite / TypeScript / Pinia — browser UI with text chat and microphone-driven voice.

Voice goes end-to-end through Gemini: the browser captures audio with `MediaRecorder`, decodes the Opus stream client-side and re-encodes to 16 kHz mono WAV (Gemini accepts WAV natively), the backend forwards it as a multimodal `Part` to the same ADK runner used for text turns, and Gemini TTS (`gemini-2.5-flash-tts`) synthesizes the reply. The voice path itself needs no separate Speech-to-Text step. **WhatsApp** is a separate inbound channel: Twilio posts to a signature-verified webhook, the same agent core answers, and replies go back out via the Twilio REST API (see Highlights).

---

## Highlights

- **Unified agent core** — one Gemini instruction + tool pipeline drives both channels.
- **Gemini native audio** — 16 kHz mono WAV blob → ADK Part → Gemini TTS MP3 → browser playback. One model, one round trip.
- **Interactive voice UX** — tap-to-talk with live canvas waveform, auto-stop on silence (built-in VAD), and a four-state status machine (`idle → listening → processing → speaking`). Keyboard `Space` toggles the mic.
- **Grounded answers via Vertex AI Search** — the agent queries the `proton-kb` Discovery Engine data store on every turn and cites the source URL in Markdown link form. Empty searches return the literal `NO_MATCHES` token so the agent never invents facts.
- **Inline human-agent handoff** — on escalation the backend opens a Sunshine Conversations conversation, posts the AI transcript as a business message, and the customer keeps typing in the same Vue chat UI. Agent replies stream back over Server-Sent Events and render inline alongside the AI turns. No separate Zendesk widget panel.
- **Persistent handoff state** — `HandoffBridge` is backed by Firestore (`proton-db / handoff_sessions`), so a uvicorn reload or deploy roll preserves the `session_id ↔ conversation_id` mapping. Restart-safe routing of customer messages to the agent's conversation.
- **Pluggable CRM** — swap Zendesk for Chatwoot/Zammad via configuration.
- **Tool-using LLM** — knowledge-base search and human escalation as Gemini tools, with explicit prompt rules for `NO_MATCHES`, direct human requests, and negative sentiment.
- **Automated handoff** — keyword/sentiment-triggered escalation produces a structured JSON summary, attaches it to a Zendesk Support ticket attributed to a session-scoped pseudo-user, AND opens the Sunshine Conversations conversation for live chat.
- **WhatsApp channel (Twilio)** — `POST /webhooks/twilio-whatsapp` (HMAC-SHA1 signature-verified) routes `whatsapp-{E164}` sessions through the same agent core; replies (text, flattened product list, and image cards via Twilio media) go back over the Twilio REST API. Every conversation is mirrored into **one** Zendesk Support ticket via a detection gate — actionable → `open`, routine → `solved` log — keyed by `external_id = session_id`, deduped and restart-safe.
- **Single-ticket two-way WhatsApp handoff** — on handoff the AI pauses and the customer is told a human is taking over; customer messages become private notes and the agent's public reply (via `POST /webhooks/zendesk-support`) relays back to WhatsApp. A persisted `active → paused → awaiting_survey → active` state machine routes each message. The web/Sunshine path is untouched.
- **Channel-agnostic CSAT** — when the agent resolves the ticket (`POST /webhooks/zendesk-handback`), the customer gets a 1–5 survey: text on WhatsApp, a `CsatSurvey.vue` widget (driven by a `survey` SSE event + `POST /chat/csat`) in the web UI. Both share one `record_csat` path — `⭐` comment, `csat_N` tag, `csat_score` — then the AI resumes.
- **Email channel (Zendesk-native)** — inbound customer emails become Zendesk tickets; a Zendesk trigger delivers each end-user comment to `POST /webhooks/zendesk-email`. The AI auto-replies as a public comment (Zendesk emails it), gated by the detection gate, with email loop guards. Handoff pauses the AI and a human takes the ticket; on Solve the same CSAT path fires (`record_csat(channel="email")`). Set `EMAIL_DRAFT_ASSIST=true` to have the AI post replies as private notes for an agent to review before sending.
- **Phone channel (real-time, via Twilio)** — in-app browser softphone ("Phone" tab → "Call support") using the Twilio Voice JS SDK; no PSTN number or SIM required in dev. Calls connect via Twilio Media Streams (`WS /voice/phone/stream`) to a Gemini Live session that streams µ-law audio bidirectionally, enabling sub-second barge-in. KB-grounded via `kb_search` (same Vertex AI Search engine as the other channels). On hang-up the call transcript is captured to a Zendesk ticket keyed by `session_id = phone-<CallSid>`. Handoff + CSAT are a follow-up.
- **Local-first** — in-memory mock adapters let the full backend test suite run with zero external dependencies. `HANDOFF_STORE=memory` keeps Firestore optional in dev.

---

## Architecture

Decisions live as ADRs:

- [docs/decisions/0001-python-fastapi-gemini-adk-stack.md](docs/decisions/0001-python-fastapi-gemini-adk-stack.md) — backend stack
- [docs/decisions/0002-monorepo-vue-frontend-gemini-audio.md](docs/decisions/0002-monorepo-vue-frontend-gemini-audio.md) — monorepo, Vue, Gemini end-to-end audio
- [docs/decisions/0003-sunshine-conversations-bridge-vertex-search-firestore.md](docs/decisions/0003-sunshine-conversations-bridge-vertex-search-firestore.md) — inline human-agent bridge, Vertex AI Search KB, Firestore handoff persistence
- [.agents/rules/architectural-pattern.md](.agents/rules/architectural-pattern.md) — testability-first ports & adapters

```
proton-conversational-ai/
├── apps/
│   ├── backend/                          # Python FastAPI service
│   │   ├── src/scraper/                   # Render-first KB scraper → JSONL Documents → Vertex AI Search
│   │   └── src/chatbot/
│   │       ├── main.py                   FastAPI bootstrap, DI, CORS, store/bridge wiring
│   │       ├── platform/                 Config, structlog, ASGI setup
│   │       └── features/chat/
│   │           ├── ports.py              Chat/Ticketing/Knowledge/TTS/HumanAgentBridge/HandoffStore
│   │           ├── service.py            handle_turn + handle_voice_turn orchestrator
│   │           ├── agents.py             ADK support + summarizer agents
│   │           ├── prompts.py            Proton-grounded instruction with citation + NO_MATCHES rules
│   │           ├── handoff_bridge.py     session ↔ conversation map + per-session SSE queues
│   │           ├── router.py             /webhooks/*, /chat/turn, /chat/stream, /voice/turn
│   │           └── adapters/             mock, chatwoot_zammad, zendesk, gcp_voice (Gemini TTS),
│   │                                     sunshine_conversations, vertex_search, handoff_store
│   └── frontend/                         # Vue 3 + Vite + Pinia
│       └── src/
│           ├── features/chat/            # vertical slice: api, store, components, types
│           ├── features/voice/           # vertical slice: + composables/useVoiceCapture.ts, components/WaveformDisplay.vue
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

| Method | Path                          | Purpose                                                      |
|--------|-------------------------------|--------------------------------------------------------------|
| GET    | `/`                           | Health check (providers + model)                             |
| POST   | `/chat/turn`                  | Text turn — JSON in, JSON out (used by the Vue app). Returns `{reply, language, sentiment, handoff, forwarded_to_agent}`. |
| GET    | `/chat/stream/{session_id}`   | Server-Sent Events stream of `agent_message` and `survey` events for a handed-off session. |
| POST   | `/chat/csat`                  | Web CSAT submission — `{session_id, score}` (1–5) → records the rating. |
| POST   | `/voice/turn`                 | Voice turn — multipart audio in, `audio/mpeg` out + `X-Reply-Text` / `X-Handoff-*` headers. |
| POST   | `/voice/phone/incoming`       | Twilio Voice webhook — returns TwiML `<Connect><Stream>` to initiate a Media Stream to the backend. |
| POST   | `/voice/phone/token`          | Issues a Twilio Voice access token for the in-app browser softphone.         |
| WS     | `/voice/phone/stream`         | WebSocket Media Stream bridge — real-time µ-law ↔ Gemini Live bidirectional audio with barge-in. |
| POST   | `/webhooks/twilio-whatsapp`   | Inbound WhatsApp message from Twilio (signature-verified). Runs the agent, replies via Twilio, captures to Zendesk. |
| POST   | `/webhooks/zendesk-support`   | Public agent reply on a Support ticket → relays to the SSE stream (web) or WhatsApp (Twilio). |
| POST   | `/webhooks/zendesk-handback`  | Ticket solved/closed → unpauses the AI and starts the CSAT survey. |
| POST   | `/webhooks/zendesk-email`     | Inbound customer email (Zendesk trigger) → AI auto-reply as public comment. |
| POST   | `/webhooks/chatwoot`          | Chatwoot inbound message webhook.                            |
| POST   | `/webhooks/zendesk`           | Zendesk Sunshine Conversations webhook (incoming-to-bot direction). |
| POST   | `/webhooks/sunshine`          | Sunshine Conversations message events (agent → customer direction; HMAC-verified). |

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

The header badges show the current backend providers. Switch the **Chat** / **Voice** tab to use either channel. The Voice tab uses your microphone — **tap the mic** to start talking, and the agent auto-replies (text + synthesized speech) when you pause for ~1.2 s. Press <kbd>Space</kbd> to toggle the mic without using the mouse. Chrome and Firefox supported (Safari isn't yet, see ADR 0002).

---

## Configuration

Backend settings load from `apps/backend/.env`. See [.env.example](apps/backend/.env.example) for the full template; key vars:

| Variable                    | Purpose                                                | Default                         |
|-----------------------------|--------------------------------------------------------|---------------------------------|
| `CRM_PROVIDER`              | `chatwoot` or `zendesk`                                | `chatwoot`                      |
| `VOICE_PROVIDER`            | `mock` or `gcp`                                        | `mock`                          |
| `FRONTEND_ORIGINS`          | JSON list of CORS origins (covers Vite port fallbacks) | `["http://localhost:5173",…5180]`|
| `KNOWLEDGE_PROVIDER`        | `mock`, `zendesk`, or `vertex_search`                  | `mock`                          |
| `VERTEX_SEARCH_*`           | Project / location / engine / data store ids           | —                               |
| `SUNSHINE_WEBHOOK_SECRET`   | HMAC secret to verify inbound `/webhooks/sunshine` POSTs | —                               |
| `ZENDESK_SUPPORT_WEBHOOK_SECRET` | Shared secret (`X-Proton-Webhook-Secret`) for `/webhooks/zendesk-support`, `/webhooks/zendesk-handback`, and `/webhooks/zendesk-email` | —                |
| `EMAIL_DRAFT_ASSIST`            | `true` → AI posts replies as private notes for an agent to send (draft-assist); `false` → auto-emails the customer. Reuses `ZENDESK_SUPPORT_WEBHOOK_SECRET` for webhook auth. | `false`          |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Twilio credentials — enable the WhatsApp channel when both are set | —                |
| `TWILIO_WHATSAPP_NUMBER`    | WhatsApp sender, e.g. `whatsapp:+14155238886`          | —                               |
| `TWILIO_WEBHOOK_BASE_URL`   | Public https base for signature verification behind a tunnel/proxy | —                    |
| `GEMINI_LIVE_MODEL`         | Gemini Live model id for the phone channel                             | `gemini-live-2.5-flash-preview` |
| `GEMINI_LIVE_VOICE`         | Voice name for Gemini Live TTS output on the phone channel             | `Kore`                          |
| `TWILIO_API_KEY_SID`        | Twilio API Key SID — used to mint Voice access tokens for the browser softphone | —             |
| `TWILIO_API_KEY_SECRET`     | Twilio API Key Secret (paired with `TWILIO_API_KEY_SID`)               | —                               |
| `TWILIO_TWIML_APP_SID`      | TwiML App SID — the browser-to-Twilio call target (`outgoing_application_sid`) | —             |
| `PUBLIC_WSS_BASE_URL`       | Public `wss://` base for the `<Stream>` URL (falls back to `TWILIO_WEBHOOK_BASE_URL` with `https→wss`) | —  |
| `HANDOFF_STORE`             | `memory` (dev) or `firestore` (prod-grade persistence) | `memory`                        |
| `FIRESTORE_PROJECT_ID`      | GCP project hosting the Firestore database             | `lv-playground-genai`           |
| `FIRESTORE_DATABASE_ID`     | Firestore database id (e.g. `proton-db`)               | `proton-db`                     |
| `GEMINI_MODEL`              | Gemini model id                                        | `gemini-2.5-flash`              |
| `GEMINI_TTS_MODEL`          | TTS model (via Cloud TTS API)                          | `gemini-2.5-flash-tts`          |
| `GEMINI_TTS_VOICE`          | Voice name (e.g. `Kore`, `Charon`, `Callirrhoe`)       | `Kore`                          |
| `VOICE_DEFAULT_LANG`        | BCP-47 code for TTS output                             | `en-US`                         |
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` to route Gemini via Vertex AI                   | `false`                         |
| `VERTEX_PROJECT_ID`         | Required if `GOOGLE_GENAI_USE_VERTEXAI=true`           | —                               |
| `ZENDESK_*`                 | Sunshine Conversations + Support API + email           | —                               |
| `CHATWOOT_*` / `ZAMMAD_*`   | self-hosted CRM credentials                            | `http://localhost:3000`         |
| `BIGQUERY_PROJECT_ID`       | GCP project hosting the BigQuery dataset               | `lv-playground-genai`           |
| `BIGQUERY_DATASET`          | BigQuery dataset name for bot metrics                  | `demo_proton`                   |
| `BIGQUERY_CONVERSATIONS_TABLE` | BigQuery table for the Zendesk → BQ conversation sync | `conversations`              |

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

## Metrics / Dashboard

Two-phase instrumentation populates Looker Studio dashboards:

### Phase 1 — Zendesk conversation metrics

A Zendesk → BigQuery sync populates a dashboard with volume, resolution, and CSAT metrics.

**Run the sync** (from the backend directory):

```bash
cd apps/backend && .venv/bin/python scripts/sync_zendesk_metrics.py
```

**BigQuery destination** — dataset `lv-playground-genai.demo_proton`:

| Object | Type | Description |
|--------|------|-------------|
| `conversations` | Table | One row per Zendesk ticket — channel, `resolved_by` (bot/agent), CSAT score |
| `v_volume_by_month_channel` | View | Monthly ticket volume split by channel |
| `v_resolution_split` | View | Bot-resolved vs agent-transferred count by channel |
| `v_csat` | View | Average CSAT score by channel |

See [docs/dashboards/looker-bot-metrics-phase1.md](docs/dashboards/looker-bot-metrics-phase1.md) for the full Looker Studio setup guide.

### Phase 2 — Per-turn metrics (latency, fallback, bounce)

Live per-turn events stream into a `turn_events` table when the backend runs with 
`METRICS_PROVIDER=bigquery`. Three views aggregate the data by channel:
`v_speed_of_response` (response latency), `v_fallback_rate`, and `v_bounce_rate`.

| Env var | Default | Description |
|---------|---------|-------------|
| `METRICS_PROVIDER` | `noop` | `bigquery` to stream per-turn events; `noop` to disable |
| `BIGQUERY_TURN_EVENTS_TABLE` | `turn_events` | Table name in `lv-playground-genai.demo_proton` |
| `METRICS_SYNC_ENABLED` | `false` | `true` to auto-refresh Phase-1 tables on a schedule |
| `METRICS_SYNC_INTERVAL_HOURS` | `6` | Refresh interval (only used if `METRICS_SYNC_ENABLED=true`) |

See [docs/dashboards/looker-bot-metrics-phase2.md](docs/dashboards/looker-bot-metrics-phase2.md) for the Looker Studio setup guide.

---

## Roadmap

- Safari `MediaRecorder` support (transcoder or polyfill)
- JWT-based `loginUser` so the Sunshine Messenger pre-chat form is bypassed and customer identity is verified end-to-end
- Vertex AI Search Enterprise tier for snippets / extractive answers (cleaner content than our pre-extracted `body_excerpt`)
- Persistent conversation history (currently in-memory; only the handoff `session ↔ conversation` mapping is in Firestore)
- Frontend unit tests via Vitest
- Observability dashboards + alert wiring

See [CHANGELOG.md](CHANGELOG.md) for the version history.

---

## License

Not yet specified. Until a license is added, all rights are reserved by the repository owner.
