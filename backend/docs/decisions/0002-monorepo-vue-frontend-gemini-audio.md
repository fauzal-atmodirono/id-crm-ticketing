# 0002. Monorepo restructure, Vue frontend, and Gemini end-to-end audio pipeline

**Date:** 2026-06-18
**Status:** Accepted
**Supersedes parts of:** [0001](0001-python-fastapi-gemini-adk-stack.md) — adds the frontend half of the system; the backend stack stays.

## Context

Three pressures converged at the same time and warrant a single coupled decision:

1. **Twilio is the wrong primitive for our voice channel.** Twilio requires real phone numbers (per-jurisdiction provisioning, monthly billing, SIP setup), exposes a TwiML round-trip per turn, and locks us into PSTN reach. Our target audience is web-based; we don't need PSTN.
2. **The current `/sim` UI is a single embedded HTML string** under `src/chatbot/features/sim/router.py`. It works for first-iteration debugging but it can't grow — no component model, no type safety, no test framework, no design system. Once we add a microphone, audio playback, and session/state, hand-rolling it is a net negative.
3. **Gemini 2.5 now offers native audio understanding** (audio `Part` inputs to `generateContent`) **and native TTS** (`gemini-2.5-flash-tts` exposed via the Cloud Text-to-Speech API). Together they collapse the previous STT → LLM → TTS three-call chain into one round trip with the same model handling tone, intent, and tool use.

These three pressures cannot be resolved independently — adding a real frontend forces a structural decision (monorepo per `project-structure-vue-frontend.md`), and the audio pipeline change determines what the frontend needs to send and play. Bundling them into one ADR keeps the rationale coherent.

## Decisions

### D1. Restructure to a two-app monorepo

Move from the current flat single-app layout to:

```
proton-conversational-ai/
├── apps/
│   ├── backend/                    # Python FastAPI service
│   │   ├── src/chatbot/            # (unchanged internals)
│   │   ├── pyproject.toml
│   │   ├── uv.lock
│   │   ├── .env.example
│   │   └── .venv/                  # recreated via uv sync
│   └── frontend/                   # Vue 3 + Vite + TypeScript
│       ├── src/
│       │   ├── features/
│       │   │   ├── chat/
│       │   │   └── voice/
│       │   ├── components/ui/
│       │   ├── layouts/
│       │   ├── views/
│       │   └── main.ts
│       ├── package.json
│       ├── vite.config.ts
│       └── tsconfig.json
├── docs/
├── .agents/
├── CLAUDE.md
├── README.md
└── ...
```

Per [project-structure.md](../../.agents/rules/project-structure.md) §"Adapting for Different Project Types", a monorepo with a backend and a frontend is the default layout. Single-app flatten no longer applies once a second app exists.

### D2. Drop Twilio entirely (no deprecation window)

- Delete `twilio_*` settings from `Settings`, `TWILIO_*` from `.env.example`, the `twilio` dep and the `twilio.*` mypy override from `pyproject.toml`.
- Delete the `/webhooks/voice/twilio` and `/webhooks/voice/twilio/process` routes and the TwiML generation logic.
- Delete the `static/audio` static mount (it existed only to serve TwiML `<Play>` URLs).
- Remove the three Twilio-related tests from `test_router.py`.

No feature flag, no `.deprecated.py`. The project hasn't shipped voice; preserving the dead code adds drag with no callers.

### D3. Gemini end-to-end audio (Option A — native understanding + Gemini TTS)

| Step | Before | After |
|---|---|---|
| Capture | Twilio call → `/webhooks/voice/twilio/process` with `SpeechResult` (already-transcribed text) | Browser `MediaRecorder` → `audio/ogg;codecs=opus` blob → multipart POST to `/voice/turn` |
| Transcription | Cloud Speech-to-Text (`GcpSpeechToTextAdapter`) | **None** — audio `Part` goes directly to ADK runner |
| LLM | ADK over Gemini (text input) | ADK over Gemini (audio + optional text instruction) |
| Synthesis | Cloud TTS standard voices (`Polly.Aditi` via Twilio `<Say>` or `GcpTextToSpeechAdapter` MP3 served via `<Play>`) | Gemini TTS (`gemini-2.5-flash-tts` model via Cloud TTS API, returns MP3 inline in response) |
| Playback | Twilio reads MP3 from `static/audio/*.mp3` | Browser `<audio>` plays MP3 returned in the HTTP response |

Consequences:
- `SpeechToTextPort` and its single implementation (`GcpSpeechToTextAdapter`) are deleted.
- `MockVoiceAdapter` drops its `transcribe()` method.
- `OrchestratorService.handle_voice_turn(session_id, audio_bytes)` no longer calls STT; it builds `types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")` and feeds that to the existing ADK runner. The signature stays for adapter compatibility but the body is rewritten.
- `GcpTextToSpeechAdapter.synthesize()` switches to the new Gemini TTS request shape — `input.text` + `voice.model_name="gemini-2.5-flash-tts"` + `voice.name="Kore"` (configurable).
- Two new endpoints serve the Vue frontend: `POST /chat/turn` (JSON in, JSON out) and `POST /voice/turn` (multipart in, `audio/mpeg` out with `X-Reply-Text` header).
- CORS middleware is added with origin = `settings.frontend_origin` (default `http://localhost:5173`, Vite's default port).
- `google-cloud-speech` dep is removed; `google-cloud-texttospeech` stays (carries the new Gemini TTS endpoint).

### D4. Vue 3 frontend stack

Per [vue-idioms-and-patterns.md](../../.agents/rules/vue-idioms-and-patterns.md) and [project-structure-vue-frontend.md](../../.agents/rules/project-structure-vue-frontend.md):

- **Vue 3** with `<script setup lang="ts">` exclusively. No Options API.
- **Vite** as the build tool.
- **TypeScript** strict mode.
- **Pinia** for state management (one store per feature).
- **No CSS framework** initially — vanilla CSS with custom properties for theming. Adding Tailwind/UnoCSS is deferred to a later ADR if/when component count justifies it.
- **No router** initially — the app has one view. Vue Router is deferred until a second route is needed.
- **No test framework wired yet** — `vitest` will be added in a follow-up commit; this ADR ships the structure but defers test coverage to keep the initial PR scoped.

## Options Considered (for D3)

### A. Gemini native audio (chosen)
- **Pros:** One Gemini call instead of three; preserves tone/emphasis; matches "use Gemini" intent; cheaper.
- **Cons:** No explicit transcript object to log; ADK audio-Part pathway is newer territory than text Parts.

### B. Cloud Speech-to-Text (Chirp 2) + Gemini TTS
- **Pros:** Keeps `SpeechToTextPort`; explicit transcript for logging; minimal ADK code change.
- **Cons:** Two round trips; loses non-lexical signal; not strictly "Gemini" end-to-end (user explicitly asked for Gemini).

### C. Gemini Live API (WebSocket bidirectional audio)
- **Pros:** Lowest latency; supports interruption; feels like a live call.
- **Cons:** WebSocket infra; different ADK code path; user explicitly excluded this.

## Consequences

### Positive
- One conversational model handles speech understanding and tool use — fewer moving parts, less prompt-engineering across stages, lower aggregate latency.
- No Twilio account/billing/number provisioning — local dev and prod use the same audio path (browser mic).
- Frontend has a real type system, component model, and test affordances. Iteration speed on the UI will be much higher than editing a string literal.
- Monorepo layout matches the documented architecture in `.agents/rules/` — no more "we'll move to monorepo later" tech debt.
- Audio observability improves: instead of separate STT/LLM/TTS logs, one structured log per turn with the audio blob hash and the synthesized reply.

### Negative
- PSTN reach is gone. Customers without a browser/mic (older users, phone-only support flows) cannot use voice. If that segment matters later, we'll add a separate Twilio gateway as a thin adapter on top of `/voice/turn` (without restoring the TwiML pipeline).
- Browser audio capture requires HTTPS in production (or `localhost` exemption). The frontend deploy needs a TLS cert.
- Safari `MediaRecorder` doesn't support `audio/ogg;codecs=opus`; only Chrome and Firefox do. Initial release will document Chrome/Firefox as supported and address Safari in a follow-up (transcoder service or polyfill).
- Adds a Node.js toolchain dependency for contributors. `apps/frontend` needs Node 20+ and a package manager (npm acceptable; pnpm preferred for speed).
- Monorepo restructure invalidates absolute paths in any docs, `.venv` activations, or local muscle memory. The user must `cd apps/backend` (or use the documented `--app-dir`/`PYTHONPATH` flags) when running backend commands.

### Risks
- **ADK audio Part compatibility:** Google ADK is young. The audio `Part` pathway through `Runner.run_async` works in raw `generateContent` calls but may surface bugs through ADK's tool loop. Mitigation: keep the mock `OrchestratorService.handle_voice_turn` test path so we catch regressions even without GCP creds.
- **Gemini TTS preview status:** Some voices/languages are still in preview. Mitigation: default to a GA voice (`Kore`) and English (`en-US`); document how to switch in `.env`.
- **CORS misconfig leaking prod origin:** If `FRONTEND_ORIGIN` defaults to `*`, an attacker could call `/chat/turn` from any page. Mitigation: default to `http://localhost:5173` (dev only); prod deployments must set a specific origin.

## Migration Plan

Executed in this order, one logical commit each:

1. **(this ADR)** Decision recorded.
2. `chore(structure): move backend to apps/backend/, scaffold apps/frontend/` — pure file moves + `uv sync` location change.
3. `feat(backend): drop Twilio integration` — remove dep, settings, routes, tests.
4. `feat(voice): switch to Gemini native audio + Gemini TTS` — rewrite gcp_voice adapter, mock, service, ports.
5. `feat(api): add /chat/turn and /voice/turn endpoints + CORS` — replace removed /sim endpoints with proper API.
6. `feat(frontend): bootstrap Vue 3 + Pinia + chat feature` — initial frontend with text chat working end-to-end.
7. `feat(frontend): add voice feature with MediaRecorder` — push-to-talk recording + MP3 playback.
8. `docs: update README, USAGE, CHANGELOG for monorepo + Gemini audio` — finalize.

## Related

- [0001](0001-python-fastapi-gemini-adk-stack.md) — backend stack (still authoritative)
- [project-structure.md](../../.agents/rules/project-structure.md) — monorepo layout
- [project-structure-vue-frontend.md](../../.agents/rules/project-structure-vue-frontend.md) — Vue layout
- [vue-idioms-and-patterns.md](../../.agents/rules/vue-idioms-and-patterns.md) — `<script setup>` + Pinia
- [architectural-pattern.md](../../.agents/rules/architectural-pattern.md) — ports & adapters preserved
- [Gemini TTS docs](https://docs.cloud.google.com/text-to-speech/docs/gemini-tts) — API reference for the new TTS endpoint
