# Proton Conversational AI — User Guide

A Gemini-powered, multi-channel customer-support agent for **Proton** (Malaysian
car brand). One AI brain answers across **web chat, browser voice, phone, email,
and WhatsApp**, grounded in a Proton knowledge base, with seamless **handoff to
human agents** and **CSAT/NPS** capture into Zendesk — plus a BigQuery/Looker
**metrics dashboard**.

This guide has two halves:

- **Part 1 — Technical guide** (developers / operators): architecture, setup,
  configuration, API reference, deployment.
- **Part 2 — End-user guide** (per channel): how a customer uses each channel.
- **Part 3 — Testing scenarios**: how to validate each channel end to end.
- **Part 4 — Known limitations & troubleshooting.**

---

## Part 1 — Technical guide

### 1.1 Architecture at a glance

| Layer | Technology |
|---|---|
| Backend | Python 3.12+ · FastAPI · Google ADK over **Gemini 2.5 Flash** |
| Frontend | Vue 3 · Vite · TypeScript · Pinia |
| Knowledge base | Vertex AI Search (Discovery Engine) |
| CRM / ticketing | Zendesk (Sunshine Conversations + Support API + native email); pluggable to Chatwoot/Zammad |
| Real-time | Server-Sent Events (web handoff), WebSockets + Twilio Media Streams (phone), Gemini Live (phone audio) |
| Persistence | Firestore (handoff state, optional session store) |
| Analytics | BigQuery + Looker Studio |

The backend is organized as a vertical feature slice under
`apps/backend/src/chatbot/features/chat/` with **ports** (interfaces) and
**adapters** (mock / Zendesk / Twilio / Gemini), so providers are swappable via
configuration.

### 1.2 Prerequisites

- Python 3.12+ and [`uv`](https://github.com/astral-sh/uv)
- Node.js 18+ and npm
- (Live mode only) A GCP project with Vertex AI + Gemini access, a Zendesk
  account, and a Twilio account for WhatsApp/phone.

### 1.3 Quick start (mock mode — no external accounts)

The app runs fully in **mock mode** with no cloud credentials.

**Backend** (from `apps/backend/`):

```bash
uv sync                                        # install deps into .venv/
.venv/bin/uvicorn chatbot.main:app --reload    # → http://localhost:8000
```

**Frontend** (from `apps/frontend/`):

```bash
npm install
npm run dev                                    # → http://localhost:5173
```

Open http://localhost:5173 and use the **Chat** / **Voice** / **Phone** tabs.
Health check: `GET http://localhost:8000/` →
`{status, crm_provider, voice_provider, model}`.

> The frontend reads `VITE_API_BASE_URL` (default `http://localhost:8000`). To
> point a local UI at a local backend, create `apps/frontend/.env.local` with
> `VITE_API_BASE_URL=http://localhost:8000`.

### 1.4 Developer commands

Backend (from `apps/backend/`):

```bash
.venv/bin/ruff format .            # format
.venv/bin/ruff check . --fix       # lint
.venv/bin/mypy src/ --strict       # type-check (strict)
.venv/bin/pytest src/              # tests
```

Frontend (from `apps/frontend/`):

```bash
npm run type-check                 # vue-tsc strict
npm run build                      # production build
```

### 1.5 Configuration (environment variables)

Copy `apps/backend/.env.example` to `apps/backend/.env` and fill in what you need.
Everything has a mock-friendly default; only set a group when enabling that
provider.

**Core / application**

| Variable | Purpose | Default |
|---|---|---|
| `CRM_PROVIDER` | `chatwoot` or `zendesk` | `chatwoot` |
| `VOICE_PROVIDER` | `mock` or `gcp` (Gemini TTS) | `mock` |
| `KNOWLEDGE_PROVIDER` | `mock`, `zendesk`, or `vertex_search` | `mock` |
| `HOST` / `PORT` | bind address / port | `127.0.0.1` / `8000` |
| `FRONTEND_ORIGINS` | allowed CORS origins (JSON list) | localhost:5173–5180 |

**Gemini / Vertex**

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_MODEL` | chat/voice model | `gemini-2.5-flash` |
| `GOOGLE_GENAI_USE_VERTEXAI` | route via Vertex | `false` |
| `VERTEX_PROJECT_ID` / `VERTEX_LOCATION` | GCP project / region | — / `us-central1` |
| `GEMINI_TTS_MODEL` / `GEMINI_TTS_VOICE` | browser-voice TTS | `gemini-2.5-flash-tts` / `Kore` |

**Phone (Gemini Live + Twilio)**

| Variable | Purpose | Default |
|---|---|---|
| `GEMINI_LIVE_MODEL` | Live model (Vertex publisher id) | `gemini-live-2.5-flash-native-audio` |
| `GEMINI_LIVE_VOICE` | Live voice | `Kore` |
| `GEMINI_LIVE_LANGUAGE` | optional output language hint, e.g. `ms-MY` (empty = auto-detect) | `""` |
| `TWILIO_API_KEY_SID` / `TWILIO_API_KEY_SECRET` | Twilio API Key (for access tokens) | — |
| `TWILIO_TWIML_APP_SID` | TwiML App SID | — |
| `TWILIO_WEBHOOK_BASE_URL` | public HTTPS base (tunnel) | — |
| `PUBLIC_WSS_BASE_URL` | public WSS base for the media stream (falls back to `https→wss`) | — |
| `PHONE_TOKEN_TTL_SECONDS` | minted token lifetime | `300` |
| `PHONE_TOKEN_RATE_LIMIT` / `PHONE_TOKEN_RATE_WINDOW_SECONDS` | per-IP token rate limit | `10` / `60` |

**Zendesk CRM**

| Variable | Purpose |
|---|---|
| `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN` | Support API auth |
| `ZENDESK_APP_ID`, `ZENDESK_KEY_ID`, `ZENDESK_SECRET_KEY` | Sunshine Conversations |
| `SUNSHINE_WEBHOOK_SECRET` | HMAC secret for `/webhooks/sunshine` |
| `ZENDESK_SUPPORT_WEBHOOK_SECRET` | shared secret (`X-Proton-Webhook-Secret`) for support/handback/**email** webhooks |
| `EMAIL_DRAFT_ASSIST` | `true` = AI posts replies as private draft notes; `false` = auto-email (default) |

**Twilio WhatsApp**: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_WHATSAPP_NUMBER`, `TWILIO_WEBHOOK_BASE_URL`.

**Vertex AI Search (KB)**: `VERTEX_SEARCH_PROJECT_ID`, `VERTEX_SEARCH_LOCATION`
(`global`), `VERTEX_SEARCH_DATA_STORE_ID`, `VERTEX_SEARCH_ENGINE_ID`.

**Persistence**: `HANDOFF_STORE` (`memory`|`firestore`), `SESSION_STORE`
(`memory`|`firestore`), `FIRESTORE_PROJECT_ID`, `FIRESTORE_DATABASE_ID`,
`FIRESTORE_HANDOFF_COLLECTION`.

**Metrics / BigQuery**: `METRICS_PROVIDER` (`noop`|`bigquery`),
`METRICS_SYNC_ENABLED`, `METRICS_SYNC_INTERVAL_HOURS`, `BIGQUERY_PROJECT_ID`,
`BIGQUERY_DATASET`, `BIGQUERY_CONVERSATIONS_TABLE`, `BIGQUERY_TURN_EVENTS_TABLE`.

**Frontend** (`apps/frontend/.env` / `.env.local`): `VITE_API_BASE_URL`.

### 1.6 API reference

All routes live in `apps/backend/src/chatbot/features/chat/router.py`.

**Frontend-facing**

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/` | health check | none |
| POST | `/chat/turn` | text chat turn → `{reply, language, sentiment, handoff, products}` | none |
| POST | `/chat/csat` | web CSAT (1–5) | none |
| POST | `/chat/nps` | web NPS (0–10) | none |
| GET | `/chat/stream/{session_id}` | SSE stream of agent replies after handoff | none |
| POST | `/voice/turn` | voice turn (audio in → MP3 + `X-*` headers) | none |
| POST | `/voice/tts` | text → speech (MP3) | none |

**Phone (real-time)**

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/voice/phone/incoming` | Twilio Voice webhook → TwiML `<Connect><Stream>` | none (Twilio) |
| POST | `/voice/phone/token` | mint browser-softphone Twilio token | **Origin allowlist + rate limit + short TTL** |
| WS | `/voice/phone/stream` | bidirectional audio ↔ Gemini Live | none (Twilio media stream) |

**CRM webhooks**

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/webhooks/twilio-whatsapp` | inbound WhatsApp | Twilio `X-Twilio-Signature` (HMAC-SHA1) |
| POST | `/webhooks/sunshine` | Sunshine agent replies | `X-Api-Key` HMAC vs `SUNSHINE_WEBHOOK_SECRET` |
| POST | `/webhooks/zendesk-support` | agent public reply → SSE/WhatsApp relay | `X-Proton-Webhook-Secret` |
| POST | `/webhooks/zendesk-handback` | ticket solved → unpause AI + start CSAT | `X-Proton-Webhook-Secret` |
| POST | `/webhooks/zendesk-email` | inbound customer email → AI reply | `X-Proton-Webhook-Secret` |
| POST | `/webhooks/chatwoot`, `/webhooks/zendesk` | inbound chat (Chatwoot / Sunshine) | payload-validated |

### 1.7 Deployment notes

- Use a **persistent HTTPS URL** (e.g. Cloud Run) for production — phone and
  email webhooks must point at a stable backend. Ephemeral tunnels (cloudflared)
  are for local testing only and change on restart.
- Set `HANDOFF_STORE=firestore` and `SESSION_STORE=firestore` for durability.
- **Gate `/voice/phone/token`** (done: Origin allowlist + rate limit + short
  TTL). Add `FRONTEND_ORIGINS` for your production frontend origin.
- See `docs/USAGE.md` for the KB-scraper pipeline and Vertex AI Search setup, and
  `docs/decisions/` for architecture ADRs.

---

## Part 2 — End-user guide (per channel)

The web UI (http://localhost:5173) has three tabs: **Chat**, **Voice**, **Phone**.
Email and WhatsApp are inbound channels customers use from their own apps.

### 2.1 Chat (web)
Type a question in the Chat tab and press send. The AI answers from the Proton
knowledge base. If it can't help — or you ask for a person — it connects you to a
human agent; their replies appear inline (purple cards) in real time. When the
conversation is resolved you may be asked to rate it (1–5).

### 2.2 Voice (web, tap-to-talk)
Open the Voice tab, tap the microphone, and speak. Recording auto-stops after a
short silence. The AI replies out loud and shows a transcript. Use **Chrome or
Edge** (Safari isn't supported). Grant microphone permission when prompted.

### 2.3 Phone (browser softphone)
Open the Phone tab and click **Call support**. The card shows **Connecting…**,
then **On call** with a live timer, the **Proton AI** label, and an animated
equalizer that moves with the AI's voice. Speak naturally — you can **interrupt
the AI mid-sentence** (it stops and listens). It answers in your language
(English, Bahasa Melayu, or Chinese). Ask for a human and it escalates (a
specialist follows up via the ticket). When you're done, it may ask you to rate
the call 1–5. Click **Hang up** to end. Use **Chrome or Edge** + microphone
permission.

### 2.4 Email
Email **support@&lt;your-subdomain&gt;.zendesk.com** from any normal address. The AI
replies by email (usually within a minute). Reply in the same thread to continue
— it remembers the conversation. Ask for a human and a person takes over; once
the ticket is solved you'll get a short "rate 1–5" email.

### 2.5 WhatsApp
Message the configured Proton WhatsApp number. The AI replies with answers and
(where relevant) a numbered product list. Ask for a human to be connected to an
agent; after resolution you'll get a "rate 1–5" message.

---

## Part 3 — Testing scenarios

> Mock mode needs no accounts. Live phone/email/WhatsApp testing needs the
> relevant provider configured and a public tunnel. Full step-by-step live plans:
> `docs/testing/phone-channel-smoke-test.md` and
> `docs/testing/email-channel-smoke-test.md`.

### 3.1 Automated tests
```bash
cd apps/backend && .venv/bin/pytest src/        # backend unit/integration suite
cd apps/frontend && npm run type-check           # frontend types
```

### 3.2 Chat (web)
1. Ask a KB question (e.g. *"What's the warranty on the Proton X50?"*) → grounded answer.
2. Ask for a human → handoff; agent replies (Zendesk) appear inline via SSE.
3. After solve → CSAT widget → submit 1–5 → recorded.

### 3.3 Voice (web)
1. Tap mic, ask a question → spoken answer + transcript.
2. Confirm auto-stop on silence and MP3 playback.

### 3.4 Phone (live — see smoke-test doc)
| # | Scenario | Expected |
|---|---|---|
| A | Greeting | AI answers the call and greets |
| B | KB question | grounded spoken answer |
| C | **Barge-in** | talk over the AI → it stops and responds |
| D | Hang up | Zendesk ticket created with transcript (`external_id = phone-<CallSid>`) |
| E | Handoff | ask for a human → ticket **open** + `[Handoff to human agent]` note (async follow-up; not a live transfer) |
| F | CSAT | say you're done → AI asks 1–5 → say a number → `csat_N` tag + solved |
| — | Language | speak Bahasa Melayu → AI replies in BM and never claims it can't |

### 3.5 Email (live — see smoke-test doc)
Provision the webhook+trigger with `scripts/provision_zendesk_email_webhook.py`
(`PUBLIC_BASE_URL=<tunnel>`), then: simple Q&A → AI reply (Pending); multi-turn
thread; handoff → ticket Open + paused; Solve → CSAT email → reply a number →
`csat_N` + thank-you; loop guards (`no-reply@` ignored, AI never answers itself);
draft-assist mode (`EMAIL_DRAFT_ASSIST=true` → private `[AI draft]` note).

### 3.6 WhatsApp (live)
Inbound message → reply; ask for human → agent relay both ways; solve → CSAT over
WhatsApp.

---

## Part 4 — CRM, handoff, CSAT/NPS & metrics

### 4.1 Handoff & CSAT (unified)
All channels share one CSAT path (`record_csat_on_ticket`): a private
`⭐ Customer satisfaction: N/5 (via {channel})` comment + a `csat_N` tag. The
survey fires only on a genuine `paused → solved` transition (idempotent under
Zendesk's repeated webhook deliveries). Handoff opens/annotates a Zendesk ticket
and pauses the AI; for web/WhatsApp the human's replies relay back to the
customer, for email/phone it's an async ticket follow-up.

### 4.2 NPS
`POST /chat/nps` (`{session_id, score: 0–10}`) records a
`📣 Net Promoter Score: N/10` comment + `nps_N` tag (web only today).

### 4.3 Metrics dashboard
- **Phase 1** — `scripts/sync_zendesk_metrics.py` lands tickets into BigQuery
  (`conversations`) with views for volume, bot-vs-agent resolution, and CSAT.
- **Phase 2** — set `METRICS_PROVIDER=bigquery` to stream per-turn `turn_events`
  (latency, fallback, bounce); optional `METRICS_SYNC_ENABLED=true` auto-refresh.
- **Phase 3** — NPS column + `v_nps` view (promoter/passive/detractor buckets).
- Looker setup: `docs/dashboards/looker-bot-metrics-phase{1,2}.md`.

---

## Part 5 — Known limitations & troubleshooting

| Area | Limitation |
|---|---|
| Phone CSAT | The native-audio Live model tends to bundle the rating ask with a "thank you" rather than pausing for the answer. The **score is still recorded correctly**. A half-cascade model would pause reliably but isn't available in the demo GCP project. |
| Phone audio | µ-law↔PCM resampling is per-frame (no carry-over) → slightly choppy; fine for demo, not production-grade. `audioop` is deprecated in 3.12 / removed in 3.13 (use `audioop-lts` on a 3.13 move). |
| Phone handoff | Creates an async Zendesk ticket for human follow-up — there is **no live human call transfer**. |
| Voice (web) | Safari unsupported (no Opus in `MediaRecorder`); use Chrome/Edge/Firefox. |
| Tunnels | Ephemeral cloudflared/ngrok URLs change on restart → re-point TwiML/webhooks. Use a persistent URL in production. |
| Sessions | Chat history is in-memory by default; only handoff state persists (Firestore). New sessions start fresh. |

**Common issues**

- *Phone call ends immediately / no audio:* confirm `GEMINI_LIVE_MODEL` is a model
  your project can access (a `1008` close = no access / wrong id), the frontend
  points at the right backend (`VITE_API_BASE_URL`), and the TwiML App Voice URL =
  `<tunnel>/voice/phone/incoming`.
- *Email gets no AI reply:* the webhook+trigger must be provisioned and pointing
  at a running backend; an inbound email alone won't trigger the AI.
- *`403` on `/voice/phone/token`:* the request Origin isn't in `FRONTEND_ORIGINS`.
- *CSAT survey never fires:* it only fires on `paused → solved`.
