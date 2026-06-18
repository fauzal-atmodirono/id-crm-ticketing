# Usage Guide

A practical, step-by-step walkthrough from a fresh clone to a running agent answering real customer messages. The [README](../README.md) gives the project overview; this document focuses on **how to drive it**.

---

## 1. First Run (Mock Mode)

No credentials required — in-memory adapters and a mock voice provider let you exercise the full request path locally.

```bash
cd proton-conversational-ai
uv sync                              # installs deps into .venv/
cp .env.example .env                 # if not already done
.venv/bin/uvicorn chatbot.main:app --reload
```

Verify it's alive:

```bash
curl http://localhost:8000/
```

Expected response:

```json
{
  "status": "healthy",
  "crm_provider": "chatwoot",
  "voice_provider": "mock",
  "model": "gemini-2.5-flash"
}
```

The defaults are `crm_provider=chatwoot` and `voice_provider=mock` — change them in `.env` once you have live credentials.

---

## 2. Send a Chat Message

The Chatwoot webhook accepts JSON. Open a second terminal:

```bash
curl -X POST http://localhost:8000/webhooks/chatwoot \
  -H 'Content-Type: application/json' \
  -d '{
    "event": "message_created",
    "message_type": "incoming",
    "content": "Saya butuh bantuan reset password",
    "conversation": {"id": 123},
    "sender": {"id": 456, "name": "Test User"}
  }'
```

Expected: `{"status":"ok"}`. The agent processed the turn — in mock mode the reply is logged but not posted to a real Chatwoot.

Non-message events are filtered:

```bash
curl -X POST http://localhost:8000/webhooks/chatwoot \
  -H 'Content-Type: application/json' \
  -d '{"event": "conversation_status_changed", "conversation": {"id": 123}, "sender": {"id": 456, "name": "T"}}'
# → {"status":"ignored"}
```

The Zendesk webhook (`POST /webhooks/zendesk`) accepts a similar shape — see `src/chatbot/features/chat/router.py` for the exact handler.

---

## 3. Simulate a Twilio Voice Call

Twilio sends form-encoded data (`application/x-www-form-urlencoded`). The call has two steps:

```bash
# Step 1 — call start: returns TwiML <Gather> asking Twilio to capture speech
curl -X POST http://localhost:8000/webhooks/voice/twilio \
  -d 'CallSid=CA12345' \
  -d 'From=+62812345678'

# Step 2 — speech result: Twilio POSTs back transcribed text; you return a reply
curl -X POST http://localhost:8000/webhooks/voice/twilio/process \
  -d 'CallSid=CA12345' \
  -d 'SpeechResult=Saya butuh bantuan reset password'
```

Both return TwiML XML (`<Response>...<Say>...</Say>...</Response>`). Twilio executes that on the live call.

> **Language note**: The voice prompts default to **id-ID (Bahasa Indonesia)** — see `router.py:110`. To serve English customers, change `language="id-ID"` and update the canned Indonesian phrases in `router.py`.

---

## 4. Switch to Live Providers

Edit `.env` to point at real services:

| To enable…             | Set                                                                       |
|------------------------|---------------------------------------------------------------------------|
| Live Zendesk           | `CRM_PROVIDER=zendesk` + all `ZENDESK_*` keys (incl. `ZENDESK_EMAIL`)     |
| Live Chatwoot + Zammad | `CRM_PROVIDER=chatwoot` + real `CHATWOOT_API_URL` / `_TOKEN` / `_ACCOUNT_ID` |
| Live GCP voice         | `VOICE_PROVIDER=gcp` + `gcloud auth application-default login`            |
| Vertex AI Gemini       | `GOOGLE_GENAI_USE_VERTEXAI=true` + `VERTEX_PROJECT_ID`                    |

### Wire incoming webhooks

Each platform needs to know where to POST events:

- **Chatwoot**: *Settings → Integrations → Webhooks* → `https://<your-host>/webhooks/chatwoot`
- **Zendesk**: *Admin Center → Apps and integrations → Webhooks* → `https://<your-host>/webhooks/zendesk`
- **Twilio**: *Phone Numbers → Active Number → Voice Configuration* → "A call comes in" Webhook → `https://<your-host>/webhooks/voice/twilio`

### Expose `localhost` to the public internet (for testing live webhooks)

```bash
# Option A
ngrok http 8000

# Option B
cloudflared tunnel --url http://localhost:8000
```

Copy the public HTTPS URL each tool prints and paste it into the dashboards above.

---

## 5. Verify Before Shipping

Run all three checks; they must pass green:

```bash
.venv/bin/ruff check .          # lint
.venv/bin/mypy src/ --strict    # strict type check
.venv/bin/pytest src/           # 9 unit + route tests, ~0.9s
```

These use in-memory mocks — safe to run repeatedly, no API costs.

---

## 6. Observe What the Agent Is Doing

The app emits structured JSON logs via `structlog`. With `DEBUG=true` in `.env`, you'll see one log line per:

- Webhook received (event type, conversation id)
- Agent tool call (`search_kb_tool`, `classify_ticket_tool`, `emit_handoff_tool`)
- Escalation trigger (keyword/sentiment/retry-limit)
- Outbound reply posted (channel, conversation id)
- Voice STT/TTS calls (duration, audio bytes)

Pipe to `jq` for readability during development:

```bash
.venv/bin/uvicorn chatbot.main:app --reload 2>&1 | jq -c .
```

---

## 7. End-to-End Cheat Sheet

Going live for the first time:

1. Fill remaining `.env` values (subdomain, email, tokens, GCP project, Twilio number).
2. Start server: `.venv/bin/uvicorn chatbot.main:app --reload`.
3. Expose with `ngrok` or deploy to your host.
4. Paste the public URL into Chatwoot / Zendesk / Twilio webhook configs.
5. Send a real message or place a real call.
6. Watch the JSON logs — confirm the agent's tool calls, reply, and (if triggered) handoff payload.

---

## Troubleshooting

| Symptom                                                | Likely cause                                                                | Fix                                                                                          |
|--------------------------------------------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| `GET /` returns wrong provider                         | `.env` not loaded — wrong working directory                                 | Run `uvicorn` from the repo root, not from `src/`.                                          |
| 401 from Zendesk Support API                           | `ZENDESK_EMAIL` missing or doesn't match the API token's owner              | Set `ZENDESK_EMAIL` in `.env` to the user who created the token.                            |
| Twilio webhook returns 200 but call says nothing       | Voice provider is still `mock`                                              | Set `VOICE_PROVIDER=gcp` and run `gcloud auth application-default login`.                   |
| Chatwoot webhook returns 200 but no reply in Chatwoot  | `CHATWOOT_ENABLED=false`, wrong API URL, or token lacks permission          | Verify `CHATWOOT_API_URL`/`_TOKEN`/`_ACCOUNT_ID` and that the agent user has bot privileges. |
| Gemini calls fail with auth error                      | Not authenticated to GCP for Vertex AI                                      | Run `gcloud auth application-default login` and set `VERTEX_PROJECT_ID`.                    |
| Tests pass but live calls hang                         | Live adapter timeout / network egress blocked                               | Check firewall; `httpx` clients default to 5s — extend in the adapter if needed.            |

---

## Related Documentation

- [README](../README.md) — project overview and quick-start
- [CHANGELOG](../CHANGELOG.md) — version history
- [docs/decisions/0001-python-fastapi-gemini-adk-stack.md](decisions/0001-python-fastapi-gemini-adk-stack.md) — stack choice rationale
- [CLAUDE.md](../CLAUDE.md) — directory map and AI-assisted development conventions
- [.agents/rules/](../.agents/rules/) — coding standards and architectural rules
