# Phone Channel — Live Smoke Test Plan

> **Goal:** verify the phone/VOIP real-time bridge end-to-end with a real Twilio
> call and a live Gemini Live session.
> The automated suite covers the backend logic (token minting, TwiML generation,
> bridge state, codec path, unknown-tool handling) but **cannot simulate a real
> phone call** — so audio latency, barge-in behaviour, and the Zendesk ticket
> lifecycle can only be proven here.
>
> **Channel summary:** the browser loads a Twilio Access Token from
> `POST /voice/phone/token`, then the Twilio JS SDK places a Voice call.
> Twilio calls the TwiML App's Voice URL (`POST /voice/phone/incoming`); the
> server responds with a `<Connect><Stream>` pointing at the WebSocket endpoint
> `wss://<host>/voice/phone/stream`. From that point on mulaw-8kHz audio flows
> Twilio ⇄ backend ⇄ Gemini Live; the bridge resamples to/from the 16 kHz PCM
> that Gemini Live requires. On hang-up the bridge closes the session and creates
> a Zendesk ticket (`external_id = phone-<CallSid>`) with the full
> USER/ASSISTANT transcript.

---

## 0. Prerequisites

> ⚠️ **Security note:** `POST /voice/phone/token` is currently **unauthenticated**.
> A leaked token lets a third party place calls billed to your Twilio account.
> Expose the tunnel **only for the duration of this test** and gate the endpoint
> before any real deployment.

- [ ] Backend running and reachable on a **public HTTPS URL** served by a tunnel
      that passes **WebSocket upgrades** (cloudflared or ngrok both work), e.g.
      `https://<something>.trycloudflare.com`. Note: an ephemeral tunnel URL
      **changes on restart** — if it does, update the TwiML App Voice URL in the
      Twilio console and re-export `TWILIO_WEBHOOK_BASE_URL`.
- [ ] `apps/backend/.env` has all of the following set to non-empty values:

  **Twilio:**
  ```
  TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_API_KEY_SID=SKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   # Twilio API Key (Standard)
  TWILIO_API_KEY_SECRET=<secret>
  TWILIO_TWIML_APP_SID=APxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_WEBHOOK_BASE_URL=https://<your-tunnel>           # public https base
  ```
  `PUBLIC_WSS_BASE_URL` is optional — if unset the server derives the WebSocket
  URL by replacing `https://` → `wss://` in `TWILIO_WEBHOOK_BASE_URL`.

  **Gemini Live:**
  ```
  GEMINI_LIVE_MODEL=gemini-live-2.5-flash-preview         # confirm your project has access
  GEMINI_LIVE_VOICE=Kore                                  # or any Gemini Live voice name
  ```

  **Gemini / Vertex auth (one of):**
  ```
  GOOGLE_GENAI_USE_VERTEXAI=true
  VERTEX_PROJECT_ID=<your-gcp-project>
  VERTEX_LOCATION=us-central1
  ```
  or set a `GOOGLE_API_KEY` and leave `GOOGLE_GENAI_USE_VERTEXAI=false`.

  **Zendesk (for ticket creation on hang-up):**
  ```
  CRM_PROVIDER=zendesk
  ZENDESK_SUBDOMAIN=<your-subdomain>
  ZENDESK_EMAIL=<agent-email>
  ZENDESK_API_TOKEN=<token>
  ```

- [ ] **TwiML App** created in the Twilio console (Voice → TwiML Apps) with:
  - **Voice Request URL** = `https://<your-tunnel>/voice/phone/incoming` (HTTP POST)
  - Its SID recorded as `TWILIO_TWIML_APP_SID` in `.env`.
- [ ] **API Key** (not the Auth Token) created in the Twilio console (Account →
      API Keys). Note the SID and secret — these are `TWILIO_API_KEY_SID` /
      `TWILIO_API_KEY_SECRET`. The Auth Token is _not_ needed for the phone
      channel (no Twilio signature is checked on the stream endpoint).
- [ ] Health check passes: `GET <public-url>/` returns
      `{status, crm_provider, voice_provider, model}` with `crm_provider=zendesk`.
- [ ] Browser (Chrome/Edge recommended) has **microphone permission** granted for
      the frontend origin.

---

## 1. Scenario A — Happy path: AI answers the call

1. [ ] Open the web app (`http://localhost:5173` or the deployed URL).
2. [ ] Navigate to the **Phone** tab.
3. [ ] Click **Call support** and grant microphone access when the browser prompts.

**Expected:**
- [ ] The call connects within a few seconds; you hear the AI's greeting spoken
      aloud with low latency (< 2 s from "connected" to first word).
- [ ] The **Phone** tab shows a "Connected" or "In call" indicator.
- [ ] Backend logs show the WebSocket handshake on `/voice/phone/stream`, a
      `media_stream_started` event, and a Gemini Live session open.

> If you hear **no greeting**: check the tunnel is passing WebSocket upgrades
> (see §5), confirm `GEMINI_LIVE_MODEL` is a model your project has access to,
> and check the Gemini Live auth vars.

---

## 2. Scenario B — KB grounding: spoken answer from the knowledge base

1. [ ] During the connected call, ask a question the knowledge base can answer, e.g.:
       *"What is the battery warranty on the Proton e.MAS 7?"*

**Expected:**
- [ ] The AI responds with a correct, specific answer grounded in the KB (not a
      generic "I don't know").
- [ ] Backend logs show `tool_call: kb_search` followed by a Gemini Live
      response containing the answer.
- [ ] The answer is fluent and delivered without noticeable truncation.

> If the AI says it cannot find information: confirm `KNOWLEDGE_PROVIDER` is set
> to `zendesk` or `vertex_search` (not `mock`) and the KB has the relevant article.

---

## 3. Scenario C — Barge-in: interrupting the AI mid-sentence

1. [ ] Let the AI start a longer response (ask an open-ended question such as
       *"Tell me everything about Proton's EV lineup"*).
2. [ ] While the AI is still speaking, say something to interrupt it.

**Expected:**
- [ ] The AI **stops speaking promptly** (within ~1 s of your interruption).
- [ ] Backend logs show a Twilio `clear` command sent to the Media Stream, which
      flushes the queued audio.
- [ ] The AI responds to your interruption rather than finishing its original
      answer.

> If the AI does not stop: confirm the bridge's barge-in handler is active and
> that the tunnel is not introducing buffering that delays the inbound audio
> event.

---

## 4. Scenario D — Hang-up: Zendesk ticket created with transcript

1. [ ] During (or at the end of) the call, hang up by clicking **End call** (or
       closing the browser tab / navigating away).

**Expected:**
- [ ] The WebSocket session closes cleanly; backend logs show
      `phone_session_closed` and `zendesk_ticket_created`.
- [ ] In Zendesk, a **new ticket** appears with:
  - [ ] **External ID** = `phone-<CallSid>` (visible in ticket Admin detail or
        via API: `GET /api/v2/tickets/<id>.json`).
  - [ ] **Status** = `solved`.
  - [ ] A **comment** (internal note) containing the full USER / ASSISTANT
        transcript of the call.
- [ ] The call duration was enough to produce at least two transcript entries
      (one USER, one ASSISTANT).

> If no ticket appears: confirm `CRM_PROVIDER=zendesk` and the Zendesk API
> credentials are correct. Check backend logs for `set_ticket_external_id`
> errors. If the ticket exists but has no transcript, the `conversation_log`
> adapter may not be wired — see §5.

---

## 5. Scenario E — Handoff: AI escalates to a human agent

1. [ ] Start a call (follow Scenario A steps 1–3 to connect).
2. [ ] Once connected, say something that triggers a handoff, for example:
       *"I need to speak to a human"* or *"This is a complaint — I want a manager."*

**Expected:**
- [ ] The AI acknowledges the request and says a specialist will follow up, e.g.
      *"I've flagged this for a human agent — someone will follow up with you soon."*
- [ ] After hanging up, a Zendesk ticket exists with:
  - [ ] **External ID** = `phone-<CallSid>`.
  - [ ] **Status** = `open` (not solved — a human must close it).
  - [ ] An **internal note** beginning with `[Handoff to human agent]` that includes
        the reason the caller gave plus a brief summary of the conversation.
  - [ ] The full USER / ASSISTANT **transcript** of the call appended to the ticket.
  - [ ] **No** `csat_*` tag — handed-off calls are not surveyed.

> If the ticket status is `solved` instead of `open`: confirm the bridge's finalize
> path checks `self.handoff is not None` and creates the ticket as `open` on handoff.
> If no handoff note appears: check backend logs for `tool_call: request_human_handoff`
> and confirm the `request_human_handoff` Live tool is registered in the Gemini Live
> session.

---

## 6. Scenario F — CSAT: in-call satisfaction rating on an AI-resolved call

1. [ ] Start a **fresh** call (follow Scenario A steps 1–3).
2. [ ] Ask a question the AI can fully answer (e.g. a KB question from Scenario B).
3. [ ] Let the AI complete its answer — it will then ask you to rate the experience
       from 1 to 5.
4. [ ] Say a digit, e.g. *"four"* or *"4"*.

**Expected:**
- [ ] The AI confirms the rating and thanks you before the call ends.
- [ ] After hanging up, the Zendesk ticket:
  - [ ] **Status** = `solved`.
  - [ ] Has a `csat_<N>` tag (e.g. `csat_4`).
  - [ ] Has an **internal comment** `⭐ Customer satisfaction: N/5 (via phone)`.
- [ ] Open the ticket from Scenario E (the handed-off call) and confirm it has
      **no** `csat_*` tag — the `submit_csat` tool is suppressed when a handoff
      was already recorded for the session.

> If no CSAT tag appears after an AI-resolved call: check backend logs for
> `tool_call: submit_csat` and confirm the `submit_csat` Live tool is registered
> in the Gemini Live session's tool set.
> If a handed-off ticket unexpectedly receives a CSAT tag: confirm the bridge's
> finalize path guards on `self.handoff is None` before invoking `submit_csat`.

---

## 7. Teardown (when done testing)

- [ ] Close any open test browser tabs to avoid stray WebSocket connections.
- [ ] Close or suspend the tunnel to prevent unauthenticated token minting while
      not testing.
- [ ] Close out any test tickets in Zendesk (or leave them for inspection).
- [ ] The TwiML App and API Key can remain in the Twilio console for future
      testing — no teardown required.

---

## Quick troubleshooting

| Symptom | Likely cause |
|---|---|
| No audio at all / call connects but silent | WebSocket not upgrading through the tunnel (check tunnel supports `Upgrade: websocket`); wrong `GEMINI_LIVE_MODEL` id (no project access); Gemini Live auth missing (`GOOGLE_GENAI_USE_VERTEXAI` / `VERTEX_PROJECT_ID` / API key) |
| Token error / call fails to connect | `TWILIO_API_KEY_SID` or `TWILIO_API_KEY_SECRET` wrong; `TWILIO_TWIML_APP_SID` not set or wrong SID; TwiML App Voice URL does not point at `<tunnel>/voice/phone/incoming` |
| Greeting heard, then silence after KB question | Gemini Live model stalled on unknown tool response — bridge now handles unknown tools with a no-op, so if this occurs check `KNOWLEDGE_PROVIDER` and the tool definition |
| Choppy / robotic audio | Known POC limitation: the resample path is stateless (no history buffer between mulaw↔PCM16 frames). Acceptable for smoke-testing; not production quality |
| No Zendesk ticket after hang-up | `CRM_PROVIDER` not set to `zendesk`; Zendesk API credentials wrong; `set_ticket_external_id` failed — check backend logs for the error; or the session closed before any transcript entry was saved |
| WebSocket 403 or connection immediately closed | Tunnel rejecting WebSocket upgrades; CORS mismatch on the WebSocket endpoint; `TWILIO_WEBHOOK_BASE_URL` / `PUBLIC_WSS_BASE_URL` pointing at the wrong host |
| Call placed but Twilio says "application error" | TwiML App Voice URL unreachable or the `POST /voice/phone/incoming` endpoint returned a non-200 — check tunnel and backend health |

---

*Backend code: `apps/backend/src/chatbot/features/chat/phone/` (`bridge.py`,
`gemini_live.py`, `token.py`, `twiml.py`),
`apps/backend/src/chatbot/features/chat/router.py` (phone routes).*
