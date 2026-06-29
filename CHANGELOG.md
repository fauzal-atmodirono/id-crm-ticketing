# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — User guide (2026-06-29)

- **Comprehensive user guide** at `docs/USER_GUIDE.md` covering technical setup,
  every channel (chat, voice, phone, email, WhatsApp) for both operators and end
  users, the full API/endpoint reference, CRM/handoff/CSAT/NPS behaviour, the
  metrics dashboard, and per-channel testing scenarios + known limitations.

### Changed — Phone call UI (2026-06-29)

- **Redesigned the browser softphone UI** (`features/phone/components/PhoneCall.vue`
  + new `PhoneVisualizer.vue`). Replaces the bare button with a themed call card
  with four states (idle / connecting / in-call / error), a live call timer, an
  agent label, and an **audio-reactive equalizer** that visualises the AI's voice
  (tapped from the Twilio remote stream via an `AnalyserNode`, with a "breathing"
  fallback). Uses the app's design tokens, mirrors the voice waveform, and respects
  `prefers-reduced-motion`.

### Fixed — Phone channel hardening + live-test fixes (2026-06-29)

- **Secured the token endpoint.** `POST /voice/phone/token` (called by a public
  SPA, so unauthenticated by nature) is hardened with defense-in-depth: an Origin
  allowlist (reuses `FRONTEND_ORIGINS`), a per-IP sliding-window rate limit
  (`PHONE_TOKEN_RATE_LIMIT` / `PHONE_TOKEN_RATE_WINDOW_SECONDS`), and a short token
  TTL (`PHONE_TOKEN_TTL_SECONDS`, default 300s vs Twilio's 3600s).
- **Multi-turn calls.** The Gemini Live event loop re-enters `session.receive()`
  per turn, so a call streams across turns instead of dropping after the first AI
  reply.
- **Correct Vertex model id.** Default `GEMINI_LIVE_MODEL` is now
  `gemini-live-2.5-flash-native-audio` (the AI-Studio name `…-preview` is rejected
  by Vertex with a 1008 policy violation).
- **Multilingual phone agent.** The phone system instruction now tells the agent
  it speaks English/Bahasa Melayu/Chinese, must match the caller's language, and
  must never claim it cannot speak a language or hand off over language. Optional
  `GEMINI_LIVE_LANGUAGE` (e.g. `ms-MY`) sets an output language hint (honoured by
  half-cascade models; native-audio auto-detects).
- **CSAT prompt.** The agent keeps helping until the caller signals they are done
  before asking for a 1–5 rating (no mid-conversation surveys). *Known limitation:
  the native-audio model tends to bundle the rating ask with a thank-you rather
  than pausing — the score is still recorded correctly.*

### Added — Bot-metrics dashboard Phase 2 (2026-06-29)

- **Bot-metrics dashboard (Phase 2).** A new `MetricsPort` abstraction enables 
  per-turn streaming of events (latency, fallback, session metadata) into 
  `lv-playground-genai.demo_proton.turn_events`, with `METRICS_PROVIDER=bigquery` 
  (or `noop` to disable). Three aggregation views back Looker tiles: 
  `v_speed_of_response` (initial vs. subsequent response latency by channel, p99), 
  `v_fallback_rate` (intent-fallback rate by channel), and `v_bounce_rate` 
  (single-turn session rate). **In-app sync scheduler:** `METRICS_SYNC_ENABLED=true` 
  + `METRICS_SYNC_INTERVAL_HOURS=<N>` (default off) automatically refresh the 
  Phase-1 `conversations` table on a schedule. See 
  `docs/dashboards/looker-bot-metrics-phase2.md`.

### Added — Bot-metrics dashboard Phase 1 (2026-06-29)

- **Bot-metrics dashboard (Phase 1).** A Zendesk → BigQuery sync
  (`scripts/sync_zendesk_metrics.py`) lands every conversation (ticket) into
  `lv-playground-genai.demo_proton.conversations` with channel, resolution
  (bot/agent), and CSAT, plus three views (`v_volume_by_month_channel`,
  `v_resolution_split`, `v_csat`) for a Looker Studio dashboard (Volume,
  Transfer-vs-Closed, CSAT). NPS/fallback/bounce/speed and accuracy/quality are
  later phases. See `docs/dashboards/looker-bot-metrics-phase1.md`.

### Added — Phone channel (real-time Gemini Live + Twilio Media Streams) (2026-06-29)

- **Real-time phone channel via Twilio Media Streams and Gemini Live.** Added `POST /voice/phone/incoming` (Twilio Voice webhook that returns TwiML `<Connect><Stream>` to open a Media Stream to the backend), `WS /voice/phone/stream` (WebSocket endpoint that bridges raw µ-law audio bidirectionally between Twilio and a Gemini Live session), and `POST /voice/phone/token` (mints a Twilio Voice access token so the browser softphone can place calls without a real PSTN number or SIM). The Gemini Live session (`GEMINI_LIVE_MODEL`, `GEMINI_LIVE_VOICE`) streams µ-law audio in both directions with sub-second barge-in — the caller can interrupt the AI mid-utterance and it responds immediately. Each call is grounded through the existing `kb_search` tool (same Vertex AI Search engine as the web and WhatsApp channels). On hang-up the full call transcript is written to a Zendesk Support ticket keyed by `session_id = phone-<CallSid>` via the existing `ConversationLogPort`. The Vue frontend gains a **Phone** tab housing a browser softphone ("Call support" button, Twilio Voice JS SDK). **Handoff and CSAT for the phone channel are a follow-up and are not included in this release.**

### Added — Phone channel — human handoff + CSAT (2026-06-29)

- **Phone channel — human handoff + CSAT.** The phone AI can now escalate via a
  `request_human_handoff` Live tool (creates an **open** Zendesk ticket with a handoff note
  + transcript for a human to follow up), and collects an in-call spoken 1–5 rating on
  AI-resolved calls via a `submit_csat` tool (recorded as the same `csat_N` tag + comment as
  every other channel, `channel="phone"`). Handed-off calls are not surveyed.

### Added — Email channel via Zendesk-native email (2026-06-28)

- **Email channel via Zendesk-native email** — inbound customer emails become Zendesk
  tickets; a trigger delivers each end-user comment to `/webhooks/zendesk-email`. The AI
  auto-replies as a public comment (Zendesk emails it), gated by the detection gate, with
  email loop guards. Handoff pauses the AI and a human takes the ticket; on Solve the
  unified CSAT survey runs over email (`record_csat(channel="email")`). Draft-assist mode
  (`EMAIL_DRAFT_ASSIST=true`) posts AI replies as private notes for an agent to send.

### Added — WhatsApp channel via Twilio + detection-gated Zendesk capture (2026-06-26)

- **WhatsApp as an inbound channel (Twilio).** Added `POST /webhooks/twilio-whatsapp`: signature-verified (`verify_twilio_signature`, HMAC-SHA1 over the form), it maps the sender to `session_id = whatsapp-{E164}`, runs the existing `OrchestratorService.handle_turn`, and replies via a new `TwilioChannelAdapter` (a `ChatPort`) over the Twilio REST API. Signature verification prefers `TWILIO_WEBHOOK_BASE_URL` so it works behind tunnels/proxies (cloudflared, ngrok) where `request.url` reports the wrong scheme/host.
- **Detection-gated per-conversation Zendesk capture.** New `ConversationLogPort` (Zendesk + NoOp impls) mirrors every WhatsApp conversation into a single Support ticket keyed by `external_id = session_id`. A pure `should_open_ticket` gate decides status — actionable (the agent's `flag_for_ticket_tool`, a handoff, or strongly negative sentiment) opens the ticket; everything else is logged as a solved record. `OrchestratorService.capture_conversation` appends only new turns and persists the ticket id + logged count in session state, so one conversation maps to one ticket even across restarts.
- **WhatsApp-friendly rendering.** The product carousel (a frontend widget) is flattened to a numbered text list with bold titles (WhatsApp `*...*`); model image cards are sent as Twilio media messages (`MediaUrl`); scraped copy is sanitized so stray markdown (`[label](url)` links, `**bold**`, headings/bullets, footnote `*`) renders cleanly. Duplicate/over-branded model cards from Vertex Search are deduped and title-cleaned at source in `show_models_tool` (fixes both WhatsApp and the web carousel).

### Added — single-ticket WhatsApp handoff + channel-agnostic CSAT survey (2026-06-26)

- **One ticket per WhatsApp conversation, with live two-way handoff.** The capture ticket is now the single hub for WhatsApp (no separate Sunshine ticket). On handoff the AI pauses, the ticket flips to `open` with a handoff summary, and the customer is told *"You're being connected to a human agent…"*. A per-session state machine (`active → paused → awaiting_survey → active`) is persisted in session state. While paused, customer messages are posted as `Customer (WhatsApp):` private notes; the agent's **public** reply fires `POST /webhooks/zendesk-support`, which relays it back to the customer's WhatsApp via Twilio. The web/Sunshine handoff path is unchanged (WhatsApp branches on `session_id.startswith("whatsapp-")`).
- **Channel-agnostic CSAT survey.** When the agent resolves the ticket, `POST /webhooks/zendesk-handback` starts a 1–5 satisfaction survey: WhatsApp delivers it as text and captures the reply (lenient parse, one nudge then graceful resume); the custom web UI renders a new `CsatSurvey.vue` widget driven by a `survey` SSE event and submits via a new `POST /chat/csat`. Both channels share one `record_csat` path — a private `⭐ Customer satisfaction: N/5` comment, a `csat_N` tag, and `csat_score` in session state — then the AI resumes.
- **Quality.** Built via TDD across 11 tasks with a final whole-branch review; the review caught and fixed a sticky-`handoff_triggered` re-handoff loop (now cleared on return to `active`).

### Fixed — duplicate CSAT survey on solved-ticket re-fire (2026-06-26)

- **The handback survey was re-sent on every update to a solved ticket.** The Zendesk handback trigger fires whenever a solved ticket is updated, so `record_csat`'s own comment + tag writes re-fired it — re-sending the survey and re-trapping the customer in `awaiting_survey`. The handback webhook now starts the WhatsApp survey **only** on a genuine `paused → solved` transition (idempotent); once the survey is sent or completed, re-fires are ignored.

### Fixed — intermittent empty agent replies (2026-06-24)

- **"(no reply — AI may be paused or response was empty)" on text chat and "(no audio reply…)" on voice, despite a healthy turn.** Gemini occasionally returns a final response with no text part at all (an intermittent generation miss, distinct from the non-first-part case already handled). Reproduced on session `sim-7157` ("saya ingin test drive bisa?") — a clean turn with no tool effect in state and no assistant reply; the same prompt returned a proper reply 6/6 times locally, confirming non-determinism. Added a safety net in `OrchestratorService`: an empty reply that is **not** a deliberate handoff is retried once, and if still empty the user gets a graceful fallback ("Maaf, saya tidak dapat memproses balasan tadi…") instead of a blank turn. Shared by both `handle_turn` and `handle_voice_turn` via a new `_run_support_agent` helper; chat now emits `chat_turn_empty_reply_text` / `chat_turn_empty_reply_after_retry` diagnostics mirroring voice.

### Fixed — handoff history, lead profile sync, and empty voice replies (2026-06-23)

- **Agent received only the last 6 messages on handoff.** Commit `3eb8ab2` made `_escalate_handoff` pass the full transcript, but `SunshineConversationsAdapter._post_business_summary` independently re-sliced it to `[-6:]`, so the agent's summary message still showed only the tail. Removed the slice — the entire conversation is now posted. (The full transcript was already persisted to Firestore; this only affected what the agent saw in-conversation.)
- **Customer phone and preferred model never reached the Zendesk user profile.** `_upsert_user` nested `phones` and `metadata` inside the Sunshine `profile` object, but Sunshine's profile schema only persists `givenName`/`surname`/`email`/`avatarUrl`/`locale` and silently drops everything else (confirmed against the live record — `givenName`/`email` survived, top-level `metadata` was empty). Phone and `preferred_model` now travel as **top-level user `metadata`** (`metadata.customer_phone`, `metadata.preferred_model`) on both the create (`POST`) and 409 conflict (`PATCH`) paths. Existing user records only pick this up on their next handoff.
- **Hands-free voice could return "(no audio reply — AI may be paused or the model returned nothing)" despite a successful turn.** The ADK run loop read only `event.content.parts[0].text`, so when Gemini placed the spoken reply after a function-call part the text was dropped and no audio was synthesized (observed on session `voice-579`: a clean transcription with no assistant reply). Both `handle_voice_turn` and `handle_turn` now scan **all** response parts and join their text.
- **Diagnostics for the empty-audio path.** Added structured warnings so any remaining occurrence is unambiguous: `voice_turn_empty_reply_text` (with `final_event_seen` + `final_part_kinds`) and `voice_tts_returned_empty_audio`, alongside reproduction tests in `test_voice_empty_audio_diagnostics.py` and `adapters/test_sunshine_conversations.py`.

### Added — Cloud Run Firestore persistence, continuous listening, and voice barge-in (2026-06-23)

- **Cloud Run Session & Pause State Persistence (Firestore):**
  - Added a `session_store` configuration setting to control the ADK runner session persistence backend.
  - Implemented `FirestoreSessionService` to store ADK runner session states (variables, flags, events) inside a GCP Firestore collection.
  - Updated Zendesk and Chatwoot/Zammad adapters to store and query the AI pause status in Firestore when `handoff_store == "firestore"`.
  - Synchronized chat transcript history inside `session.state["chat_history"]` to preserve conversation context across stateless container instances.
  - Refactored `OrchestratorService` to extract DRY helper methods, resolving type checking errors and reducing the statement count below Ruff's limit.
  - Added unit/mock integration tests in `test_service.py` verifying all Firestore session service database CRUD operations.
- **Hands-Free Continuous Listening & Barge-in Voice Interruption:**
  - Implemented a continuous hands-free voice loop in the frontend Vue application.
  - Updated `voice.store.ts` to track and control the active assistant audio playback (`activeAudio`, `stopAssistantAudio()`).
  - Added `isContinuousMode` in `VoiceRecorder.vue` to manage the continuous session toggle.
  - Implemented a watcher on `voice.phase` to automatically restart the recording capture when the assistant finishes speaking naturally, ensuring no button clicks are required between turns.
  - Implemented a watcher on `capture.level` to support barge-in: detecting user speech during assistant playback immediately stops the playback, discards the recorder buffer containing echo, and starts a fresh recording capture for the user's speech.
  - Enhanced UI button labels and status texts to display voice interruption instructions (e.g., *"Replying — speak to interrupt"*).

### Added — car sales qualification, handoff CRM user sync, and handback webhook (2026-06-22)

- **Car Sales Qualification & Test-Drive Tool:** Added a new `book_test_drive_tool` in `agents.py` to capture lead information (name, phone, email, preferred model, preferred dealer) when users show intent to buy or book test drives.
- **Ticket Classification Mandate:** Enabled `classify_ticket_tool` in the ADK agent, instructing it to classify the conversation (category, subcategory, priority) before triggering any human handoff.
- **CRM User Profile Synchronization:** Integrated lead capture details directly into Zendesk and Sunshine Conversations user records. When handoff triggers, the customer's name, email, phone number, and preferred model are synchronized with Zendesk and Sunshine profile APIs.
- **Zendesk Handback Webhook:** Implemented `POST /webhooks/zendesk-handback` in `router.py` to receive solved/closed ticket updates from Zendesk. The webhook automatically unpauses the AI and unregisters the handoff session to seamlessly resume AI support.
- **Automated Tests & Quality Checks:** Added service-level lead-capture and classification tests in `test_service.py` and handback webhook routing tests in `test_router.py`. All tests, Mypy type-checking, and Ruff linting pass with zero issues.
- **Mock Customer Seeding Script:** Added `import_mock_users.py` and `test_zendesk_auth.py` in `apps/backend/scripts/` to generate 100 randomized Malaysian mock customer profiles (containing ethnic names, phone numbers, and vehicle interest/buyer status tags) and import them concurrently via the Zendesk API.
- **Frontend Cloud Run Deployment Config:** Added multi-stage `Dockerfile`, `nginx.conf.template`, `.dockerignore`, and `.gcloudignore` in `apps/frontend/` to containerize and serve Vue/Vite static assets via Nginx on Cloud Run with dynamic port binding. Deployed frontend and redeployed backend with updated CORS configuration.

### Added — voice handoff STT & TTS (2026-06-20)

- **Voice Speech-to-Text (STT) transcription:** Integrated Gemini GenAI verbatim audio transcription in `OrchestratorService.handle_voice_turn` for both normal AI turns and human-agent handoffs. Transcribed text is saved to Firestore history/history lists.
- **Backend TTS replay endpoint:** Added `POST /voice/tts` to synthesize plain text replies into speech MP3 using `GeminiTextToSpeechAdapter`.
- **Frontend TTS autoplay and manual audio replay:** Updated `useVoiceStore` to attach the SSE agent stream during live handoffs, fetch agent reply audio via `postVoiceTts`, and play it back automatically.
- **Clean Voice bubble UI with play/replay icons:** Redesigned `VoiceLog.vue` to show clear speech bubble cards instead of raw HTML5 media players. Integrated a custom SVG speaker icon that indicates playback, triggers manual replays, and animates/pulses when audio is active.
- **Transcribed voice placeholder swap:** Replaces the frontend user's audio placeholder bubble ('Voice message') with the transcribed text verbatim for all voice turns once the backend returns the `X-User-Transcription` header.
- **Automated tests:** Added `test_voice_handoff.py` and updated `test_service.py` to assert transcription forwarding, message saving, and transcription returning.

### Added — frontend UX & Zendesk webhook (2026-06-19)

- **Frontend chat autoscroll UX:** Added automated scrolling to the bottom of the chat container in `ChatLog.vue` when new messages (from the user, AI, or agent) are appended.
- **Zendesk Support Webhook route:** Added `/webhooks/zendesk-support` to backend router to synchronize agent public comments on standard Support tickets into the SSE stream (for backward compatibility when fallback ticketing is active).
- **Handoff conversation transcript persistence in Firestore:** Automatically saves the initial AI-to-user conversation history when registering a handoff session in `handoff_sessions/<session_id>` document.
- **Dynamic message logging in Firestore during handoff:** Stores subsequent user turns (relayed to agent) and agent replies (received via Sunshine and Support webhook) in real time under the same document.

### Fixed — duplicate tickets & immediate handoff routing (2026-06-19)

- **Ticket creation on human agent handoff:** Switched Sunshine Conversations handoff summary message author from `business` to `user` (using the session ID as external ID). This ensures Zendesk Messaging registers user activity immediately at handoff and creates the ticket in the agent workspace without requiring the customer to send another message.
- **Prevention of duplicate Support tickets:** Bypassed manual standard Support ticket creation in `OrchestratorService` when `live_chat_available` is true, since Sunshine Conversations automatically manages Messaging tickets.

### Added — handoff bridge, KB grounding, persistence (2026-06-19)

- **Sunshine Conversations bridge** for inline human-agent handoff. `SunshineConversationsAdapter` (httpx, v2 REST) opens a conversation per session and posts the AI summary + recent transcript as a business message; `forward_customer_message` relays subsequent customer turns. Reuses the existing `ZENDESK_KEY_ID` / `ZENDESK_SECRET_KEY` for HTTP Basic auth.
- **`HandoffBridge`** coordinator with per-session `asyncio.Queue` subscribers and a pluggable `HandoffStorePort` backing.
- **`GET /chat/stream/{session_id}`** — Server-Sent Events stream that yields `agent_message` events with a 30 s heartbeat.
- **`POST /webhooks/sunshine`** — HMAC-SHA256-verified webhook handler. Customer-authored events are dropped (we already showed them in the UI when the user pressed Send); business-authored events are published to the matching session's queue.
- **Frontend `ChatMessage.role` extended with `'agent'`** + purple-tinted card styling. `chat.store` opens an `EventSource` on handoff when `live_chat_available` is true, appends incoming agent messages to the same log, and closes the stream on `resetSession`.
- **`HandoffStatePort`** — persistence boundary for the bridge. Two implementations: `InMemoryHandoffStore` (dev/tests) and `FirestoreHandoffStore` (one doc per active handoff in `proton-db/handoff_sessions/<session_id>` with indexed reverse-lookup). The bridge keeps a read-through in-process cache; a backend restart costs one Firestore round-trip per session before warm.
- **`KNOWLEDGE_PROVIDER=vertex_search` path** — `VertexAISearchAdapter` queries the `proton-kb-engine` Discovery Engine over the `proton-kb` data store and reads `struct_data` (our authoritative fields) ahead of `derived_struct_data`. Falls back to the pre-extracted `body_excerpt` for content because Standard tier doesn't return snippets.
- **`scripts/scrape_proton.py` rewritten as a structured KB pipeline** — produces JSONL Documents (one per line) with `{id, struct_data: {title, link, language, body_excerpt, sections}, content: {uri, mime_type}}` from the Proton website sitemap, uploads JSONL + HTML to GCS, creates the data store + engine if missing, and imports with `data_schema="document"`. Discrete CLI stages (`--scrape-only`, `--upload-only`, `--import-only`, `--purge`).
- **Agent prompt** rewritten as a three-step playbook (Search → Answer → optionally Escalate). Explicit `NO_MATCHES` branching, Markdown-link citations from `Source URL`, replies in the customer's language (en / ms / zh). Grounds the agent in Proton's product domain.
- **Settings** — `SUNSHINE_WEBHOOK_SECRET`, `HANDOFF_STORE`, `FIRESTORE_PROJECT_ID`, `FIRESTORE_DATABASE_ID`, `FIRESTORE_HANDOFF_COLLECTION`, `KNOWLEDGE_PROVIDER`, `VERTEX_SEARCH_*`.
- **ADR-0003** — coupled decision for the Sunshine Conversations bridge, Vertex AI Search KB, and Firestore handoff persistence.

### Changed — handoff & KB (2026-06-19)

- **BREAKING — handoff UX is no longer the Zendesk Messenger widget.** `<script id="ze-snippet">`, `zESettings`, `plugins/zendesk.ts`, and the `HandoffStatus.vue` full-panel swap are removed. Handoff now keeps the existing chat log + input visible and renders agent replies inline as a new `'agent'` message role. Deployments that depended on the embedded widget must switch to registering a Sunshine Conversations webhook integration pointing at `POST /webhooks/sunshine`.
- **BREAKING — `ChatTurnResponse` adds `forwarded_to_agent: bool`** and `HandoffPayload` adds `live_chat_available: bool`. The voice `X-Handoff-*` headers gain `X-Handoff-Live-Chat`.
- **`OrchestratorService` constructor** takes new optional `human_agent_bridge: HumanAgentBridgePort | None` and `handoff_bridge: HandoffBridge | None` parameters. When both are present and the session is already handed off, `/chat/turn` text is forwarded to Sunshine Conversations instead of Gemini.
- **Zendesk Support tickets** are now attributed to a session-scoped pseudo-user (`Proton AI Customer (<session-id>)` with email `<session-id>@proton.devoteam.example`) instead of defaulting to the API token owner. Stops escalation emails from landing in the admin's inbox.
- **`InMemoryKnowledgeAdapter`** is now a development fallback only — the production path is `KNOWLEDGE_PROVIDER=vertex_search`.
- **Agent tool set** — `classify_ticket_tool` is dropped from the registered tools because its result was stored only in `tool_context.state` (unread downstream) and Gemini was treating the classify call as the turn's terminal action, silently skipping the customer-facing text reply. The function stays defined so it can be re-enabled once its state is wired into the handoff payload.

### Fixed — handoff & KB (2026-06-19)

- **Empty replies on grounded questions.** Root cause: Gemini 2.5 Flash treating `classify_ticket_tool` as the terminal call. Fixed by dropping the tool from the registered set.
- **GCS object URI surfaced to the customer as the citation link.** Vertex's `derived_struct_data.link` overrides our canonical Proton URL with the bucket URI. Adapter now prefers our explicit `struct_data` before falling back to derived.
- **Two-ticket fragmentation on handoff.** The Zendesk Support ticket (with AI transcript) and the Messenger conversation (live chat) are still distinct records, but now share a `session-<id>` tag and external_id linkage so agents can correlate. The user-facing UI no longer shows two chat surfaces.
- **Handoff state lost on backend restart.** `HandoffBridge`'s `session_id ↔ conversation_id` map is now persisted in Firestore (`HANDOFF_STORE=firestore`). Subscribers (SSE queues) are still in-process; clients reconnect after a restart.
- **Sunshine webhook signature verification rejected all real v2 traffic.** `verify_webhook_signature` only computed an HMAC-SHA256 of the body and compared it to `X-Api-Key`, but Sunshine Conversations v2 sends the **raw shared secret** directly in `X-Api-Key` — so every genuine webhook would have returned `401`. The check now accepts the request when `X-Api-Key` matches the shared secret (constant-time) **or** an HMAC-SHA256 of the body (legacy Smooch v1 back-compat); both still require knowledge of the secret. Added `adapters/test_sunshine_conversations.py` covering both accept paths plus rejection.
- **KB citations rendered as raw markdown in the chat UI.** `ChatLog.vue` interpolated the reply as plain text, so the agent's grounded `[label](url)` citations showed literally instead of as clickable links. Added `features/chat/markdown.ts` (`marked` → `DOMPurify`) and switched assistant/agent messages to sanitized `v-html`; links open in a new tab with `rel="noopener noreferrer"`. User/system messages stay literal. The LLM-authored text is treated as untrusted and sanitized before rendering.

### Added
- **Monorepo structure** (`apps/backend/` + `apps/frontend/`) per `project-structure-vue-frontend.md`.
- **Vue 3 frontend** at `apps/frontend/` (Vite + TypeScript + Pinia, `<script setup lang="ts">` exclusively). Vertical feature slices for `chat/` and `voice/`, each with its own `api/`, `store/`, `components/`, and `types/`.
- **`useVoiceCapture` composable** — `getUserMedia` + `AnalyserNode` + `MediaRecorder` with built-in **voice activity detection** (sustained-silence auto-stop, configurable RMS floor / min-speech / trailing-silence). On stop, the captured Opus blob is decoded via `AudioContext.decodeAudioData`, mixed to mono, linearly resampled to 16 kHz, and packed as 16-bit PCM WAV — Gemini accepts WAV natively, so Chrome's webm/opus output is no longer rejected.
- **`WaveformDisplay.vue`** — 48-bar canvas waveform driven by the live analyser; switches palette when speech is detected.
- **`VoiceRecorder.vue`** rewritten as **tap-to-talk** with a four-phase status machine (`idle → listening → processing → speaking`), pulsing halo locked to mic level, mid-turn manual-stop, and a <kbd>Space</kbd>-key shortcut.
- **`phase` state in `useVoiceStore`** — `'idle' | 'listening' | 'processing' | 'speaking'`. Assistant audio autoplays and flips back to `idle` on `ended`.
- **`POST /chat/turn`** — JSON in, JSON out frontend chat endpoint (returns `{reply, language, sentiment, handoff}`).
- **`POST /voice/turn`** — multipart audio in (now `audio/wav`), `audio/mpeg` out with `X-Reply-Text` (URL-encoded) and `X-Handoff-Reason` response headers.
- **CORS middleware** on the backend; new `FRONTEND_ORIGINS` setting accepts a JSON list of allowed origins and defaults to Vite's port fallbacks (`5173`–`5180`).
- **Vertex AI env mirroring** — `bootstrap_application` now copies `vertex_project_id` / `vertex_location` into `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` so the `google-genai` SDK actually sees them when `GOOGLE_GENAI_USE_VERTEXAI=true`. A single `.env` now drives both layers.
- **`GEMINI_TTS_MODEL` / `GEMINI_TTS_VOICE`** settings selecting the Gemini TTS model and voice (defaults: `gemini-2.5-flash-tts`, `Kore`).
- **ADR 0002** documenting the monorepo, Vue frontend, and Gemini end-to-end audio decisions together.

### Changed
- **BREAKING — voice pipeline rewritten end-to-end through Gemini.**
  - `OrchestratorService.handle_voice_turn` now sends the audio bytes directly as a multimodal `Part` to the ADK runner. No separate transcription step.
  - `GcpTextToSpeechAdapter` replaced by `GeminiTextToSpeechAdapter` — uses Cloud TTS with `model_name="gemini-2.5-flash-tts"`.
  - `MockVoiceAdapter` now implements only `TextToSpeechPort`.
- **Frontend upload format**: voice turns now post `audio/wav` (16 kHz mono PCM-16) instead of `audio/ogg`/`audio/webm`. The wire MIME is consumed verbatim by the ADK `Part`.
- **Settings — `frontend_origin: str` → `frontend_origins: list[str]`** (BREAKING for anyone overriding `.env`). `.env.example` updated; old `FRONTEND_ORIGIN=…` keys must be migrated to `FRONTEND_ORIGINS=["…"]`.
- **CORS consolidated to one middleware** in `bootstrap_application` (the duplicate `allow_origins=["*"]` in `create_app` was removed). Stacking two `CORSMiddleware` layers caused the restrictive one to win for preflight while the permissive one shaped simple-request responses — symptoms were "Failed to fetch" on `/chat/turn` from Vite fallback ports.
- **Frontend replaces the old `/sim` simulator.** The inline-HTML `features/sim/` package was deleted; the Vue app at `apps/frontend/` is the new UI surface.
- **CLAUDE.md, README, USAGE** updated for the new layout and `cd apps/backend` / `cd apps/frontend` workflow.

### Fixed
- **Chat "Failed to fetch" from Vite fallback ports** (e.g., `:5174`, `:5176`) — the old single-origin allowlist returned `400 Disallowed CORS origin` on preflight. Replaced with the multi-origin `FRONTEND_ORIGINS` list.
- **Voice `Maaf, terjadi kendala teknis…` fallback in Chrome** — Gemini does not accept `audio/webm;codecs=opus`, so every Chrome capture hit the exception handler. Re-encoding to WAV in the browser fixes the round trip natively.
- **`google-genai` not honouring Settings on Vertex** — declared but never propagated, so even with `GOOGLE_GENAI_USE_VERTEXAI=true` the SDK fell back to AI Studio (and 401'd without `GOOGLE_API_KEY`). Bootstrap now mirrors the values into the env vars the SDK actually reads.

### Removed
- **`useMediaRecorder` composable** — superseded by `useVoiceCapture` (which adds VAD + WAV encoding). Hold-to-talk semantics are gone; tap-to-talk replaces them.
- **Duplicate `CORSMiddleware`** registration in `chatbot/platform/server.py` (the permissive `allow_origins=["*"]` one).
- **BREAKING — Twilio integration removed entirely.**
  - Routes `/webhooks/voice/twilio` and `/webhooks/voice/twilio/process` deleted.
  - All TwiML generation and the `static/audio/` mount removed.
  - Settings `TWILIO_*`, `PUBLIC_BASE_URL`, and `TWILIO_ESCALATION_PHONE` removed.
  - Deps `twilio`, `google-cloud-speech` and the `twilio.*` mypy override removed from `apps/backend/pyproject.toml`.
- `SpeechToTextPort` protocol and `VoiceTranscript` model deleted — Gemini consumes audio natively.
- `GcpSpeechToTextAdapter` deleted.

### Planned
- Safari support for the voice capture path (Safari's `MediaRecorder` is feature-limited; needs a polyfill or a WebAudio-only PCM capture path).
- Continuous "conversation mode" — auto-resume listening after the assistant finishes speaking.
- Frontend unit tests via Vitest (with the WAV encoder + VAD timing as first targets).
- Persistent session store (currently in-memory).
- Webhook signature verification on `/webhooks/zendesk` and `/webhooks/chatwoot`.
- Observability dashboards and alert wiring.

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
