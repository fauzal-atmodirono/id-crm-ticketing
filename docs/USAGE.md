# Usage Guide

A practical walkthrough — from a fresh clone to a running agent that you can talk to in a browser. The [README](../README.md) gives the project overview; this document focuses on **how to drive it**.

> **TL;DR**: Start the backend (`apps/backend/`) on `:8000`, start the frontend (`apps/frontend/`) on `:5173`, open the browser, switch between the Chat and Voice tabs. The Voice tab uses your microphone and Gemini TTS — no Twilio, no phone numbers, no public webhooks.

---

## 1. First Run (Mock Mode)

No GCP credentials required — in-memory mock adapters let you exercise the full pipeline locally. (Gemini calls themselves still need credentials; without them the agent returns its fallback message.)

### Backend

```bash
cd apps/backend
uv sync                                # creates .venv/ and installs deps
cp .env.example .env                   # then edit .env (see §3)
.venv/bin/uvicorn chatbot.main:app --reload
```

Verify:

```bash
curl http://localhost:8000/
```

```json
{
  "status": "healthy",
  "crm_provider": "chatwoot",
  "voice_provider": "mock",
  "model": "gemini-2.5-flash"
}
```

### Frontend

In a second terminal:

```bash
cd apps/frontend
npm install
npm run dev                            # → http://localhost:5173
```

Open `http://localhost:5173` — the header should show three green badges (CRM, Voice, Model). If the CORS badge says "Backend unreachable", check the backend is up and that the Vite port is included in `FRONTEND_ORIGINS` in `apps/backend/.env` (the default list covers `5173`–`5180`, so Vite's port fallbacks work out of the box).

---

## 2. Sending a Chat Message

The Vue **Chat** tab is the canonical way to talk to the agent. Type and hit `Enter` (Shift+Enter for newline). Behind the scenes it POSTs to `/chat/turn`:

```bash
curl -X POST http://localhost:8000/chat/turn \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"sim-1","text":"How do I reset my password?"}'
```

```json
{
  "reply": "...",
  "language": "en",
  "sentiment": "neutral",
  "handoff": null
}
```

When the agent triggers escalation, `handoff` populates and the frontend renders an escalation banner instead of a normal reply.

---

## 3. Sending a Voice Message

Click the **Voice** tab. **Tap** the microphone, start talking, and stop — the agent auto-replies when you pause for ~1.2 s. <kbd>Space</kbd> toggles the mic without the mouse. The frontend:

1. Captures audio with `MediaRecorder` (Chrome: `audio/webm;codecs=opus`, Firefox: `audio/ogg;codecs=opus`) while an `AnalyserNode` powers the live waveform and the silence-based VAD.
2. On auto-stop (or manual tap-to-stop), decodes the Opus stream via `AudioContext.decodeAudioData`, mixes to mono, resamples to 16 kHz, and packs as 16-bit PCM **WAV**.
3. POSTs `audio/wav` to `/voice/turn` as multipart form-data.
4. Backend forwards the bytes to Gemini as a multimodal `Part` — no separate transcription.
5. Gemini's reply text is synthesized by Gemini TTS (`gemini-2.5-flash-tts`) into an MP3.
6. Browser auto-plays the MP3 and displays the reply text (from the `X-Reply-Text` response header).

Equivalent `curl` (Gemini accepts WAV, MP3, OGG, FLAC, AAC, AIFF natively):

```bash
curl -X POST http://localhost:8000/voice/turn \
  -F 'session_id=cli-test' \
  -F 'audio=@/path/to/clip.wav;type=audio/wav' \
  -D headers.txt \
  -o reply.mp3

# Reply text — URL-encoded UTF-8 in the X-Reply-Text header
grep -i '^x-reply-text:' headers.txt
```

### Browser support

| Browser | Status                                                                                |
|---------|---------------------------------------------------------------------------------------|
| Chrome  | ✅ `audio/webm;codecs=opus` — decoded + re-encoded to WAV in-browser                  |
| Firefox | ✅ `audio/ogg;codecs=opus` — decoded + re-encoded to WAV in-browser                   |
| Safari  | ❌ no Opus in `MediaRecorder` yet (see ADR 0002)                                      |

---

## 4. Switching to Live Providers

Edit `apps/backend/.env`:

| To enable…                       | Set                                                                                                          |
|----------------------------------|--------------------------------------------------------------------------------------------------------------|
| Live Zendesk                     | `CRM_PROVIDER=zendesk` + all `ZENDESK_*` keys (incl. `ZENDESK_EMAIL`)                                        |
| Live Chatwoot + Zammad           | `CRM_PROVIDER=chatwoot` + real `CHATWOOT_API_URL` / `_TOKEN` / `_ACCOUNT_ID`                                |
| Live Gemini TTS                  | `VOICE_PROVIDER=gcp` + `gcloud auth application-default login`                                              |
| Vertex AI Gemini                 | `GOOGLE_GENAI_USE_VERTEXAI=true` + `VERTEX_PROJECT_ID`                                                       |
| **Vertex AI Search KB grounding**| `KNOWLEDGE_PROVIDER=vertex_search` + `VERTEX_SEARCH_PROJECT_ID` / `_DATA_STORE_ID` / `_ENGINE_ID`; populate the data store via `scripts/scrape_proton.py` |
| **Inline human-agent chat**      | Register a Sunshine Conversations webhook at `POST /webhooks/sunshine`, copy the secret into `SUNSHINE_WEBHOOK_SECRET` |
| **Persistent handoff state**     | `HANDOFF_STORE=firestore` + `FIRESTORE_PROJECT_ID` + `FIRESTORE_DATABASE_ID` (the database must exist; ADC handles auth) |

CRM webhooks remain available for production traffic (where customers chat directly through Chatwoot/Zendesk widgets rather than your Vue app):

- **Chatwoot**: *Settings → Integrations → Webhooks* → `https://<your-host>/webhooks/chatwoot`
- **Zendesk**: *Admin Center → Apps and integrations → Webhooks* → `https://<your-host>/webhooks/zendesk`
- **Sunshine Conversations** (for live agent → customer messages): SC dashboard → Integrations → Webhooks → target `https://<your-host>/webhooks/sunshine`, triggers `conversation:message`, copy the generated secret into `.env` as `SUNSHINE_WEBHOOK_SECRET`.

For external testing, expose your local backend with `ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000`.

### Vertex AI Search KB pipeline

```bash
cd apps/backend
.venv/bin/python scripts/scrape_proton.py               # scrape → JSONL → GCS → import
.venv/bin/python scripts/scrape_proton.py --purge       # wipe + re-import (after schema changes)
.venv/bin/python scripts/scrape_proton.py --import-only # re-import existing GCS JSONL
```

The script writes structured Documents (one per line) to `apps/backend/scraped_data/proton-kb.jsonl`, uploads alongside the raw HTML, and imports with `data_schema="document"`. The adapter then queries `proton-kb-engine` and uses our explicit `struct_data.{title,link,body_excerpt}` for the citation + content.

### Inline human-agent flow

1. Customer types something like "I need to talk to a human" in the Vue chat.
2. Backend's `_escalate_handoff` runs: creates a Zendesk Support ticket (with AI transcript summary) AND opens a Sunshine Conversations conversation via the bridge. The session ↔ conversation mapping is written to `proton-db/handoff_sessions/<session_id>` if `HANDOFF_STORE=firestore`.
3. Backend returns `handoff: {..., live_chat_available: true}`. The frontend opens `EventSource(/chat/stream/<session_id>)` and shows a thin "Connected to a human agent" banner above the chat log.
4. Customer's next `/chat/turn` is short-circuited and forwarded to Sunshine Conversations instead of Gemini (`{forwarded_to_agent: true}` in the response). The user message renders normally; the agent's reply will arrive via SSE.
5. Agent replies in Zendesk Agent Workspace. The reply triggers a `conversation:message` webhook to `POST /webhooks/sunshine`, which the bridge fans out to every open EventSource for that session. The reply renders inline as a purple "agent" card.
6. "New session" closes the EventSource, clears the Firestore document, and resets the chat.

---

## 5. Verify Before Shipping

Backend:

```bash
cd apps/backend
.venv/bin/ruff check .
.venv/bin/mypy src/ --strict
.venv/bin/pytest src/
```

Frontend:

```bash
cd apps/frontend
npm run type-check
npm run build
```

All five checks must pass green.

---

## 6. Observe What the Agent Is Doing

The backend emits structured JSON logs via `structlog`. With `DEBUG=true` you see one log line per:

- Webhook received (event type, conversation id)
- `chat_turn_received` / `voice_turn_received` (session, payload size)
- Agent tool call (`search_kb_tool`, `classify_ticket_tool`, `emit_handoff_tool`)
- Escalation trigger (keyword/sentiment/retry-limit)
- Outbound reply posted (channel, conversation id)
- `gemini_tts_synthesis_completed` (size_bytes)

Pipe to `jq` during dev:

```bash
.venv/bin/uvicorn chatbot.main:app --reload 2>&1 | jq -c .
```

---

## 7. End-to-End Cheat Sheet

Going live for the first time:

1. Fill remaining `apps/backend/.env` values (CRM + GCP).
2. Start backend on a reachable host with HTTPS (browser mic capture needs it).
3. Build the frontend: `npm run build` → deploy `apps/frontend/dist/` to a static host. Set `VITE_API_BASE_URL` at build time to your backend's HTTPS URL.
4. Set `FRONTEND_ORIGINS` in the backend to a JSON list containing the deployed frontend origin (e.g. `FRONTEND_ORIGINS=["https://app.example.com"]`).
5. (Optional) Wire Chatwoot/Zendesk webhooks for customers who chat through those platforms instead of your Vue app.

---

## Troubleshooting

| Symptom                                                | Likely cause                                                                | Fix                                                                                          |
|--------------------------------------------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| Frontend header shows "Backend unreachable"            | Backend not running, wrong port, or CORS origin mismatch                    | Verify backend is up; ensure the frontend's origin (scheme + host + port) is in `FRONTEND_ORIGINS` in `apps/backend/.env`. Defaults cover Vite ports `5173`–`5180`. |
| `GET /` returns wrong provider                         | `.env` not loaded — wrong working directory                                 | Run `uvicorn` from `apps/backend/` so it finds `.env` in cwd.                                |
| 401 from Zendesk Support API                           | `ZENDESK_EMAIL` missing or doesn't match the API token's owner              | Set `ZENDESK_EMAIL` in `.env` to the user who created the token.                            |
| Voice tab record button does nothing                   | Safari (no Opus in MediaRecorder) or `getUserMedia` blocked by HTTP origin  | Use Chrome/Firefox; serve from `localhost` or HTTPS.                                        |
| `/voice/turn` returns 200 but no audio plays           | Voice provider is still `mock` (returns text-as-bytes, not real MP3)        | Set `VOICE_PROVIDER=gcp` and run `gcloud auth application-default login`.                   |
| Gemini calls fail with auth error                      | Not authenticated to GCP for Vertex AI or Cloud TTS                         | Run `gcloud auth application-default login` and set `VERTEX_PROJECT_ID` if using Vertex.    |
| Tests pass but live calls hang                         | Live adapter timeout / network egress blocked                               | Check firewall; `httpx` clients default to 5s — extend in the adapter if needed.            |

---

## Related Documentation

- [README](../README.md) — project overview and quick-start
- [CHANGELOG](../CHANGELOG.md) — version history
- [docs/decisions/0001-python-fastapi-gemini-adk-stack.md](decisions/0001-python-fastapi-gemini-adk-stack.md) — backend stack rationale
- [docs/decisions/0002-monorepo-vue-frontend-gemini-audio.md](decisions/0002-monorepo-vue-frontend-gemini-audio.md) — monorepo + Vue + Gemini audio
- [CLAUDE.md](../CLAUDE.md) — directory map and AI-assisted development conventions
- [.agents/rules/](../.agents/rules/) — coding standards and architectural rules
