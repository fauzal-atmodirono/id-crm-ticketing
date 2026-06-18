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

Open `http://localhost:5173` — the header should show three green badges (CRM, Voice, Model). If the CORS badge says "Backend unreachable", check the backend is up and `FRONTEND_ORIGIN` in `apps/backend/.env` matches the Vite dev port (`http://localhost:5173`).

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

Click the **Voice** tab. Hold the microphone button to record, release to send. The frontend:

1. Captures audio with `MediaRecorder` (Chrome/Firefox: `audio/ogg;codecs=opus`).
2. POSTs the blob to `/voice/turn` as multipart form-data.
3. Backend forwards the bytes to Gemini as a multimodal `Part` — no separate transcription.
4. Gemini's reply text is synthesized by Gemini TTS (`gemini-2.5-flash-tts`) into an MP3.
5. Browser auto-plays the MP3 and displays the reply text (from the `X-Reply-Text` response header).

Equivalent `curl` (works with any `audio/ogg` file):

```bash
curl -X POST http://localhost:8000/voice/turn \
  -F 'session_id=cli-test' \
  -F 'audio=@/path/to/clip.ogg;type=audio/ogg' \
  -D headers.txt \
  -o reply.mp3

# Reply text — URL-encoded UTF-8 in the X-Reply-Text header
grep -i '^x-reply-text:' headers.txt
```

### Browser support

| Browser | Status                                              |
|---------|-----------------------------------------------------|
| Chrome  | ✅ `audio/ogg;codecs=opus`                          |
| Firefox | ✅ `audio/ogg;codecs=opus` or `audio/webm`          |
| Safari  | ❌ no Opus in `MediaRecorder` yet (see ADR 0002)    |

---

## 4. Switching to Live Providers

Edit `apps/backend/.env`:

| To enable…             | Set                                                                       |
|------------------------|---------------------------------------------------------------------------|
| Live Zendesk           | `CRM_PROVIDER=zendesk` + all `ZENDESK_*` keys (incl. `ZENDESK_EMAIL`)     |
| Live Chatwoot + Zammad | `CRM_PROVIDER=chatwoot` + real `CHATWOOT_API_URL` / `_TOKEN` / `_ACCOUNT_ID` |
| Live Gemini TTS        | `VOICE_PROVIDER=gcp` + `gcloud auth application-default login`            |
| Vertex AI Gemini       | `GOOGLE_GENAI_USE_VERTEXAI=true` + `VERTEX_PROJECT_ID`                    |

CRM webhooks remain available for production traffic (where customers chat directly through Chatwoot/Zendesk widgets rather than your Vue app):

- **Chatwoot**: *Settings → Integrations → Webhooks* → `https://<your-host>/webhooks/chatwoot`
- **Zendesk**: *Admin Center → Apps and integrations → Webhooks* → `https://<your-host>/webhooks/zendesk`

For external testing, expose your local backend with `ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000`.

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
4. Set `FRONTEND_ORIGIN` in the backend to the deployed frontend origin.
5. (Optional) Wire Chatwoot/Zendesk webhooks for customers who chat through those platforms instead of your Vue app.

---

## Troubleshooting

| Symptom                                                | Likely cause                                                                | Fix                                                                                          |
|--------------------------------------------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| Frontend header shows "Backend unreachable"            | Backend not running, wrong port, or CORS origin mismatch                    | Verify backend is up; ensure `FRONTEND_ORIGIN` in `apps/backend/.env` matches the frontend's exact origin (scheme + host + port). |
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
