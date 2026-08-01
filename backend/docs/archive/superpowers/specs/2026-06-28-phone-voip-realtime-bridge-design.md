# Phone / VOIP Channel — Real-Time Bridge (Core) — Design

**Date:** 2026-06-28
**Status:** Approved (brainstorm complete)
**Scope:** Spec #1 of 2 — the **core real-time voice bridge**: a caller (browser
softphone over VOIP, or a real phone later) holds a low-latency spoken conversation
with the KB-grounded AI over the phone, and the call transcript is captured to a
Zendesk ticket. **Handoff and CSAT are a separate follow-up spec** and are explicitly
out of scope here.

## Context & North Star

The POC serves web text, voice (browser, Gemini), WhatsApp, and email — one agent core,
many channels, Zendesk as the hub. Phone is the next channel. The driving requirement is
**latency**: the existing browser-voice channel does three sequential model round-trips per
turn (transcribe → agent → TTS) and feels slow; on a phone call that lag is unacceptable.
So the phone channel uses a **real-time streaming architecture** — **Twilio Media Streams ⇄
Gemini Live API** — native audio-to-audio with built-in voice-activity detection and
barge-in, collapsing the three calls into one streaming session.

VOIP note: test calls are placed from a **browser softphone** (Twilio Voice JS SDK / WebRTC)
→ Twilio → our webhook, so no real phone number and **no international/PSTN charges** for the
POC. The backend webhook + bridge code is identical whether the call originates from a
browser softphone or a real PSTN number later.

The eventual cross-channel **Bot Metrics dashboard** (separate future project) wants phone
volume + CSAT; this core spec captures the call to Zendesk so that data exists, but the
phone CSAT itself lands in the follow-up spec.

### Decisions locked during brainstorming

- **Call model:** real-time streaming (Gemini Live + Twilio Media Streams), NOT turn-based
  `<Gather>` — chosen specifically to fix the latency complaint; gives barge-in.
- **Handoff (callback + ticket) and CSAT (spoken rating):** deferred to the **follow-up spec**.
- **VOIP test path:** browser softphone via Twilio Voice JS SDK; no real number for the POC.
- **Grounding:** the Live model uses the SAME `KnowledgePort` (Vertex AI Search) as the other
  channels, exposed as a function-calling tool.

## Architecture & Data Flow

```
Caller — browser softphone (Twilio Voice JS SDK / WebRTC)     [real PSTN later, same code]
  │ places call (to a Twilio TwiML App)
  ▼
Twilio Voice ──POST──▶ /voice/phone/incoming
                         returns TwiML: <Connect><Stream url="wss://<host>/voice/phone/stream"/></Connect>
  │ opens a WebSocket; streams base64 μ-law 8kHz audio frames with start/media/stop events
  ▼
PhoneBridge (one instance per call)
  • decode Twilio μ-law 8k  ⇄  PCM for Gemini Live (and back)
  • Gemini Live session: native audio in → audio out, built-in VAD + barge-in
  • relay Gemini audio frames back to Twilio (re-encoded μ-law 8k)
  • on Twilio/Gemini interrupt → send Twilio "clear" to flush buffered playback (barge-in)
  • accumulate input + output transcripts
  • tool: kb_search(query) → KnowledgePort (Vertex AI Search) — grounding
  ▼ on call end (stop event / WS close / hangup)
Zendesk ticket  (session_id = phone-<CallSid>, external_id = session_id)  ← transcript mirrored
```

The call audio stays on a single streaming path end-to-end — that is what removes the
multi-second latency of the turn-based pipeline.

## Components & Isolation Boundaries

New code under `apps/backend/src/chatbot/features/chat/phone/` (a subpackage — distinct
streaming infra, reusing shared ports):

| File | Responsibility | Tested how |
|---|---|---|
| `phone/audio_codec.py` | pure conversions: μ-law 8k ⇄ PCM (16k in / 24k out as Gemini Live requires); base64 frame decode/encode | unit tests (pure functions, golden vectors) |
| `phone/gemini_live.py` (adapter) | thin wrapper over `google-genai` Live API: `connect()` a session with system instruction + tool schemas; `send_audio(pcm)`; async-iterate yielded events (audio chunk / input-transcript / output-transcript / tool-call / turn-complete / interrupted) | logic unit-tested with a fake session; real session = live smoke test |
| `phone/bridge.py` (`PhoneBridge`) | orchestrate ONE call: pump Twilio WS ⇄ Gemini Live via the codec; dispatch `kb_search` tool calls to `KnowledgePort`; accumulate transcript; on end, write the transcript to Zendesk via `ConversationLogPort` | tool-dispatch + transcript-accumulation + end-of-call logging unit-tested with fakes; socket pump = thin glue |
| `router.py` additions | `POST /voice/phone/incoming` (returns `<Connect><Stream>` TwiML); `WS /voice/phone/stream` (Twilio Media Streams); `POST /voice/phone/token` (mint a Twilio access token for the browser softphone) | TwiML + token endpoints unit-tested; WS endpoint thin |
| frontend `features/phone/` | minimal "Call support" softphone widget using `@twilio/voice-sdk` (fetches a token, `Device.connect()`), call/hang-up + status | vue-tsc type-check; manual |
| **Reused** | `KnowledgePort` (kb_search), `ConversationLogPort` (transcript ticket), the agent persona/system prompt, `Settings` | already tested |

## Gemini Live Session

- Configured with the **same support persona / system instruction** the text+voice agent uses
  (consistency across channels), adapted for spoken phone style (concise, no markdown).
- Declares one function tool in this core spec: `kb_search(query: string)` → returns top KB
  snippets from `KnowledgePort.search_kb`. The model calls it to ground answers; the bridge
  executes it and returns the result into the live session.
- Native audio in/out; the SDK's built-in VAD/turn detection drives endpointing and barge-in.
- Input and output transcripts are requested from the session so the bridge can build the
  Zendesk transcript without a separate transcription call.

## Audio Codec

- Twilio Media Streams: 8 kHz μ-law (G.711), 20 ms frames, base64 in the `media` event payload.
- Gemini Live: PCM (exact input/output sample rates confirmed in planning against the SDK; the
  codec resamples between Twilio 8 kHz and the Live rates).
- `audio_codec.py` exposes pure functions: `twilio_to_pcm(b64_mulaw) -> pcm_bytes`,
  `pcm_to_twilio(pcm_bytes) -> b64_mulaw`, plus the resample step. Pure + deterministic →
  unit-tested with small golden vectors. No I/O.

## Barge-In

Gemini Live emits an "interrupted"/turn-change signal when the caller starts talking over the
AI. On that signal the bridge sends Twilio a `clear` message to flush any buffered AI audio so
the caller isn't talking over stale playback. This is the one piece of real-time UX glue.

## Transcript → Zendesk (core logging)

At call end (Twilio `stop`, WS close, or hangup), the bridge writes the accumulated
conversation (USER/ASSISTANT turns) to a Zendesk ticket via `ConversationLogPort`, with
`session_id = phone-<CallSid>` and the ticket's `external_id = session_id` (consistent with
WhatsApp/email so the future handback/CSAT trigger can route by it). In this core spec every
call is logged as a **solved** conversation record (no detection-gated handoff yet — that is
the follow-up spec). Best-effort: a logging failure never drops the call.

## Browser Softphone Test Path (VOIP, no real number)

- Backend `POST /voice/phone/token` mints a short-lived **Twilio Access Token** (Voice grant)
  scoped to a **Twilio TwiML App** whose Voice URL points at `/voice/phone/incoming`.
- Frontend `features/phone/` widget: a "Call support" button that fetches a token, creates a
  `Device`, and `connect()`s — placing a WebRTC call into Twilio → our TwiML → the stream.
- This is the demo/test entry point; it needs a Twilio account SID, an API key/secret, and a
  TwiML App SID (config below). No phone number is purchased.

## Error Handling

- Wrap Gemini Live session errors + Twilio WS errors so one failing call never crashes others;
  log structured events; close the call cleanly.
- If the Gemini Live session drops mid-call, play/say a short fallback and end gracefully.
- `ConversationLogPort` writes are best-effort try/except (matches the other channels).
- The WS handler tolerates Twilio's `connected`/`start`/`media`/`stop` events and ignores
  unknown event types.

## Testing

Unit (pytest-asyncio, co-located):
- `audio_codec`: round-trip and known-vector conversions; base64 framing.
- `PhoneBridge`: `kb_search` tool dispatch calls `KnowledgePort.search_kb` and feeds the result
  back; transcript accumulation builds USER/ASSISTANT turns from session events; end-of-call
  writes the transcript to `ConversationLogPort` with the right `session_id`/`external_id`;
  barge-in signal triggers a Twilio `clear`.
- `/voice/phone/incoming` returns `<Connect><Stream>` TwiML with the correct wss URL;
  `/voice/phone/token` returns a token (Twilio SDK mocked).
- Gemini Live adapter logic with a fake session (no live calls).

Live smoke test (separate scenario doc, like email/WhatsApp): place a browser-softphone call,
confirm low-latency spoken conversation, KB-grounded answers, barge-in works, and the call
transcript appears on a Zendesk ticket.

Quality gates: `ruff format`, `ruff check`, `mypy --strict`, `pytest`; frontend `vue-tsc`.

## Configuration

| Setting | Purpose |
|---|---|
| `gemini_live_model` | Gemini Live model id (confirmed in planning) |
| `gemini_live_voice` | Live voice name |
| `twilio_account_sid` / `twilio_auth_token` | reused; account for Voice |
| `twilio_api_key_sid` / `twilio_api_key_secret` | mint browser-softphone access tokens |
| `twilio_twiml_app_sid` | TwiML App whose Voice URL = `/voice/phone/incoming` |
| `public_wss_base_url` (or reuse the tunnel base) | the `wss://…/voice/phone/stream` URL handed to Twilio in the TwiML |

## Out of Scope (this spec)

- **Handoff (callback + Zendesk ticket) and CSAT (spoken 1–5)** — the immediate **follow-up
  spec**, layered on the working bridge.
- Warm transfer / `<Dial>`, DTMF input.
- Real PSTN number provisioning.
- The unified metrics/dashboard.
- Retrofitting the existing browser-voice channel to the streaming architecture (possible
  later; not required here).

## Risks / Unknowns to Resolve in Planning

1. **Gemini Live API specifics** — exact `google-genai` live-session API surface (connect
   options, audio in/out sample rates, transcript + tool-call event shapes, function-calling in
   Live, the correct model id). Verify against current SDK docs before writing tasks.
2. **Twilio Media Streams protocol** — `connected`/`start`/`media`/`stop` event shapes, μ-law
   framing, and the `clear`/`mark` messages used for barge-in.
3. **Sample-rate bridging** — Twilio 8 kHz μ-law ⇄ Gemini Live PCM rates; correctness verified
   in the codec unit tests once the exact rates are confirmed.
4. **FastAPI WebSocket under the existing server/CORS setup** — confirm the WS route coexists
   with the current HTTP app and the tunnel passes WS upgrades.
