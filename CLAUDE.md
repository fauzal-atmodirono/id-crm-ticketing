# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current State of the Repository

Two-app monorepo: a Python FastAPI backend (Google ADK over Gemini) and a Vue 3 frontend (Vite + Pinia + TypeScript). Voice goes end-to-end through Gemini — browser `MediaRecorder` captures Opus, the frontend decodes + re-encodes to 16 kHz mono WAV with a built-in VAD (tap-to-talk, auto-stop on silence), the audio `Part` reaches ADK, then Gemini TTS MP3 returns for browser playback. No Twilio. ADRs: [0001](docs/decisions/0001-python-fastapi-gemini-adk-stack.md) (backend stack), [0002](docs/decisions/0002-monorepo-vue-frontend-gemini-audio.md) (monorepo + Vue + Gemini audio). Layout follows `.agents/rules/project-structure.md` and `.agents/rules/project-structure-vue-frontend.md`.

### Layout

```
apps/
├── backend/
│   ├── pyproject.toml, uv.lock, .env.example, .venv/
│   └── src/chatbot/
│       ├── main.py                          FastAPI bootstrap, DI, CORS
│       ├── platform/                        config.py, logger.py, server.py
│       └── features/chat/
│           ├── ports.py                     ChatPort, TicketingPort, KnowledgePort, TextToSpeechPort
│           ├── service.py                   handle_turn + handle_voice_turn
│           ├── agents.py                    ADK support + summarizer agents
│           ├── router.py                    /webhooks/*, /chat/turn, /voice/turn
│           ├── adapters/                    mock, chatwoot_zammad, zendesk, gcp_voice (Gemini TTS)
│           └── test_*.py                    co-located pytest suites
└── frontend/
    ├── package.json, vite.config.ts, tsconfig.json
    └── src/
        ├── main.ts, App.vue
        ├── plugins/api.ts                   base URL + fetch wrappers
        ├── components/ui/                   ChannelTabs
        ├── components/layout/               AppHeader
        ├── layouts/MainLayout.vue
        ├── views/HomeView.vue
        └── features/
            ├── chat/                        api/, components/, store/, types/, index.ts
            └── voice/                       api/, components/ (VoiceLog, VoiceRecorder, WaveformDisplay), composables/useVoiceCapture.ts, store/, types/
```

### Commands

Backend (Python — run from `apps/backend/`):

```bash
cd apps/backend
uv sync                              # install deps into .venv/
.venv/bin/ruff format .
.venv/bin/ruff check . --fix
.venv/bin/mypy src/ --strict
.venv/bin/pytest src/
.venv/bin/uvicorn chatbot.main:app --reload   # → :8000
```

Frontend (Node — run from `apps/frontend/`):

```bash
cd apps/frontend
npm install
npm run type-check                   # vue-tsc strict
npm run build                        # production build
npm run dev                          # Vite dev server → :5173
```

Health check: backend `GET /` returns `{status, crm_provider, voice_provider, model}`. Frontend reads `VITE_API_BASE_URL` (default `http://localhost:8000`).

## The `.agents/` Framework (the actual "architecture" right now)

`.agents/` is a structured prompt library that governs how AI agents work in this repo. Treat it as authoritative — its files describe non-negotiable constraints. Four sibling directories, each with a distinct role:

| Directory | Contents | When it applies |
|---|---|---|
| `.agents/rules/` | ~40 always-on or model-decision rules | Hard constraints. Some are tagged `trigger: always_on` in their frontmatter and apply to every task; others are loaded conditionally based on the task. |
| `.agents/agents/` | 15 specialized agent personas (architect, backend-engineer, frontend-engineer, mobile-engineer, database-expert, devops-engineer, security-engineer, qa-analyst, ux-reviewer, performance-engineer, refactoring-specialist, incident-responder, technical-writer, test-automation-engineer, scout) | Used by `workflow-team.md` for `@agent[scope]` dispatch. Each persona has an EXCLUSIVE domain and explicit "DO NOT CROSS" boundaries. |
| `.agents/skills/` | ~50 reusable skill packs (debugging-protocol, code-review, refactoring-patterns, perf-optimization, frontend-design, guardrails, parallel-dispatch-{decomposition,ownership,dag,merge}, sequential-thinking, adr, plus per-language idiom packs) | Loaded on demand by workflows and agents. Many skills have language-specific subfiles under `languages/`. |
| `.agents/workflows/` | Phase definitions + two top-level orchestrators | Entry points for structured work. See below. |

### Entry-Point Workflows

- **`workflows/workflow-solo.md`** — Single agent executes all phases sequentially. The phases are a strict state machine: `Research → Implement → Integrate → (E2E if UI/API changed) → Verify → Ship`. Each phase has its own file (`phase-research.md`, `phase-implement.md`, etc.). Phases must not be skipped; `task.md` tracks state with `[ ]`/`[/]`/`[x]` markers and an item is only `[x]` once Verify passes.
- **`workflows/workflow-team.md`** — Multi-agent pipeline manager. Dispatches sub-agents via `@agent-name` (single) or `@agent-name[scope]` (parallel) across the primitives `SCOUT → DESIGN → PRE-MORTEM → BUILD → TEST → REVIEW → REMEDIATE → VERIFY → DOCUMENT`. Intra-domain parallelism requires running the four-step protocol (decompose → validate ownership → build DAG → execute levels) using the `parallel-dispatch-*` skills, with each builder agent in its own `git worktree`. Includes 12 template workflows (Full Feature, Bug Fix, Audit, Mobile, Perf, Security, Infra, Docs, Incident, Tech Debt, Combined Audit, Pre-Mortem).
- Single-purpose orchestrators also exist (`bugfix.md`, `refactor.md`, `audit.md`, `perf-optimize.md`) that compose the same phases.

### Rule Hierarchy (read `rules/rule-priority.md` first)

When rules conflict, this priority order applies — top wins:

1. Security Mandate (`rules/security-mandate.md`)
2. Rugged Software Constitution (`rules/rugged-software-constitution.md`) — "code will be attacked; generate defensibility"
3. Code Completion Mandate + Logging and Observability Mandate
4. Testability-First architecture (`rules/architectural-pattern.md`) — I/O behind interfaces, pure business logic, dependencies point inward
5. Feature-specific principles, including language idiom files (`{go,typescript,vue,flutter,rust,python}-idioms-and-patterns.md`)
6. **PRD-gated** principles (`feature-flags-principles.md`, `ci-cd-gitops-kubernetes.md`) — only apply if the PRD explicitly requires them; do not introduce on speculation
7. YAGNI / KISS — last resort, only when no security/reliability/maintainability trade-off exists

### Single Source of Truth for Layout

`rules/project-structure.md` is the **single source of truth** for project organization. Philosophy: organize by **feature**, not by technical layer (no top-level `controllers/`, `models/`, `services/`). Each feature is a vertical slice. The default monorepo layout is `apps/backend/`, `apps/frontend/`, `apps/mobile/`; single-app projects flatten this. Language-specific layouts live in the `project-structure-{go-backend,vue-frontend,flutter-mobile,rust-cargo,python-backend}.md` files — read the relevant one before creating directories.

## How to Approach Work Here

1. **Before any non-trivial task**, scan `.agents/rules/` for files tagged `trigger: always_on` and any rules whose `description` matches the task — these are constraints, not suggestions.
2. **For a feature**, pick `workflow-solo.md` (single agent) or `workflow-team.md` (multi-agent dispatch). Do not skip phases for velocity; each phase has a completion gate. Track work in `task.md` at the repo root using `[ ]`/`[/]`/`[x]`.
3. **Research phase output goes in `docs/research_logs/{feature}.md`**; architecture decisions become ADRs in `docs/decisions/NNNN-short-title.md` (use the `adr` skill).
4. **When introducing the first piece of code**, the stack choice is a significant architectural decision — write an ADR and update this `CLAUDE.md` with the chosen build/test/lint commands.
5. **For parallel multi-agent work**, BUILD/TEST/REMEDIATE/OPTIMIZE/REFACTOR sub-agents run in `git worktree`s under `.wt/<agent>-<scope>` per `workflow-team.md`'s lifecycle section; merges are squash-merges in dependency order.

## Repo Conventions Worth Knowing

- Git commits follow conventional format (`<type>(<scope>): <description>`) per `rules/git-workflow-principles.md` and `workflows/phase-commit.md`.
- Remote: `origin` → `https://github.com/Yudaadi-devo/proton-conversational-ai.git` (branch `main`).
- `.claude/settings.local.json` only allows `rtk ls *` and `rtk find *` automatically; other tools will trigger permission prompts.
