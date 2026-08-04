# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A unified CRM platform built on **Chatwoot** (CRM/live chat) behind a
**Caddy** reverse proxy, with a small **FastAPI `agent` service** that layers
Gemini AI on top (auto-drafted replies, auto-escalation via Chatwoot human
handoff). It is multi-tenant: each customer gets its own isolated Chatwoot +
agent stack, with a shared Caddy/Postgres/Mailpit, all as Docker Compose on a
single GCE VM. See `README.md` for the full deploy/wiring runbook.

First-party code you edit lives in two services: **`agent/`** (Chatwoot sync
helpers and Gemini AI orchestration) and **`backend/`** (the vendored
AI-assist conversational backend from `proton-conversational-ai`). These
communicate over HTTP via `PROTON_BACKEND_URL` (deliberately fail-open, no
shared process or DB). To run the backend locally:
`cd backend/apps/backend && uv run uvicorn chatbot.main:app --port 8080`.

`deploy/` is runtime config/ops scripts. Chatwoot is an upstream app pulled
as a Docker image by `deploy/docker-compose.infra.yml` /
`deploy/docker-compose.tenant.yml` — there is no `crm/` source in this
checkout to modify. (The Chatwoot **SPA is forked**: patches in
`deploy/chatwoot-fork/patches/NNNN-*.patch` are `git apply`-ed onto upstream at
image-build time — that's where the CRM's custom "Knowledge" UI lives, along
with newer operator admin pages: PIC/Dealer escalation-routing (patch
`0039`), FAQ bulk CSV upload (patch `0040`), and a Customer 360 lookup by
phone/vehicle number (patch `0041`) — see "Operator-configurable persona &
knowledge" below for the backend routers behind them.)

**Zammad has been fully removed (2026-08).** The former CRM + ticketing
platform ran Chatwoot and Zammad side by side with the `agent` service
mirroring data between them; that sync layer, the Zammad client/router, and
every `zammad_*` `Settings` field (including the old `ZAMMAD_TICKETING_ENABLED`
flag) are gone. Escalations/handoffs stay entirely in Chatwoot — via a human
handoff, an `escalate` label, and (for email-channel conversations) a
two-thread escalation email. Don't build on Zammad; there's nothing left to
build on.

## Commands

All commands run from `agent/`.

```bash
cd agent
pip install -e '.[dev]'        # install app + test deps (pytest, respx, aiosqlite)
pytest                         # run the full suite (asyncio_mode=auto, no flags needed)
pytest tests/test_sync_escalation.py               # one file
pytest tests/test_orchestrator.py::test_name       # one test
```

Tests never hit postgres, the real Chatwoot API, or Gemini:
`tests/conftest.py` sets all required env vars and points `AGENT_DATABASE_URL`
at a throwaway sqlite file (aiosqlite); HTTP is stubbed with `respx`; Gemini
clients are injected.

The platform is multi-tenant: shared infra (`docker compose -p platform-infra
-f deploy/docker-compose.infra.yml --env-file deploy/infra.env up -d`) plus one
app stack per customer, provisioned with `deploy/scripts/add-tenant.sh <name>`
(see `docs/superpowers/specs/2026-07-16-per-tenant-isolation-design.md`).

## Agent service architecture

### The webhook pattern (both receivers follow it)

`app/routers/chatwoot.py` (the only router besides `health.py`) exposes two
endpoints, `/webhooks/chatwoot` and `/webhooks/chatwoot/bot`, deliberately
thin and identical in shape:

1. **Verify HMAC signature** (`app/security.py::verify_chatwoot_signature`)
   — `sha256=` over `f"{timestamp}."+body` with a 300s skew window. Bad
   signature → 401. Note the two receivers use *different* secrets:
   `/webhooks/chatwoot` uses `chatwoot_webhook_secret`, `/webhooks/chatwoot/bot`
   uses `chatwoot_bot_secret`.
2. **Dedupe** via `app/services/dedupe.py::claim_delivery` — an insert-or-skip
   against `processed_deliveries` keyed on `X-Chatwoot-Delivery`. The atomic
   PK insert (not check-then-insert) is what makes duplicate deliveries safe
   under concurrency. No delivery id → can't dedupe → process.
3. **Return 200 immediately**, dispatching real work to a FastAPI
   `BackgroundTasks`. The slow Chatwoot/Gemini calls never run inline in
   the request path.

**Invariant for background tasks** (`app/services/`): they take an
already-parsed payload, own their own DB session (no request-scoped session),
and **never raise for expected "nothing to do" cases** (missing fields, unknown
ids, downstream HTTP failures) — those are logged and skipped. Raising out of a
background task just produces an unretrieved-exception log, so don't.

### Sync flows (`app/services/sync.py`)

Chatwoot-only notification helpers — there is no external ticketing backend
to mirror to anymore:

- **`maybe_escalate`**: on `conversation_updated`, when the `escalate` label
  is present and the conversation is on an Email-channel inbox, fires the
  EM-7 two-thread escalation email (customer ack + PIC/dealer forward) via
  the backend's `ProtonConfigClient.notify_email_escalation`, gated by
  `email_escalation_enabled`. Fail-open throughout.
- **`maybe_stamp_dealer_escalation`**: on `conversation_updated`, the first
  time a `dealer_<slug>` label appears, stamps a `dealer_escalated_at`
  custom attribute (idempotent, never overwrites) so BI turnaround-time
  reporting has a real timestamp to diff against `resolved_at`.
- **`upsert_contact`** / **`record_conversation_status`**: no-op stubs kept
  as the router's dispatch targets for contact/status events, so the router
  doesn't need to change if a future Chatwoot-side integration needs a hook.

### AI layer (Gemini)

- `app/services/orchestrator.py` — the Chatwoot **agent-bot** flow
  (`/webhooks/chatwoot/bot`). Only acts on an incoming customer message on a
  `pending` conversation. **Debounces per conversation** (`DEBOUNCE_SECONDS`):
  bursts coalesce into one Gemini call that re-fetches fresh history; a task
  past its sleep (`processing=True`) runs to completion and can't be cancelled,
  so side effects never end up partial. Every decision is logged to `ai_actions`
  before execution. `AGENT_MODE=suggest` (default) posts the reply as a private
  note + reopens for a human; `auto` sends it directly.
- `app/ai/gemini.py` — wraps `google-genai`. `decide()` forces one of the three
  `app/ai/tools.py` function calls (`function_calling_config` mode `ANY`);
  anything else (plain text, errors after retry) falls back to
  `handoff_to_human` so a conversation never silently stalls. The SDK call is
  sync, so both entry points run it via `asyncio.to_thread` to avoid blocking
  the event loop.

### Operator-configurable persona & knowledge (backend)

The `backend/` **assistant config** (`features/chat/adapters/assistants_store.py`
`AssistantConfig`: `instructions`/`temperature`/`guardrails`/`response_guidelines`/
`language` + welcome/handoff/resolution + 7 lifecycle messages) is edited
per-inbox in the CRM (fork `KnowledgeSettings.vue`, patch `0013`+`0022`) and
reaches the customer-facing bot **three ways**, all fail-open + default-preserving
(empty persona → today's behavior, byte-identical):
1. the `agent/` agent-bot decision prompt (`orchestrator._build_system_prompt`,
   fetched via `ProtonConfigClient.get_assistant_persona`);
2. lifecycle customer messages (`app/services/lifecycle.py`, via
   `ProtonConfigClient.get_assistant_messages`);
3. the backend **WhatsApp `/chat/turn`** agent (used when `CHAT_AGENT_ENABLED`):
   persona **augments** (never replaces) the static `AGENT_INSTRUCTION` via a
   google-adk `InstructionProvider` reading a per-session composed instruction
   (`features/chat/{service.py,agents.py,chat_persona.py}`).

Persona resolution reuses `inbox_resolver.effective_assignment` (same path the
copilot uses). Separately, `backend/` has a **pgvector operator-authored KB** at
`/kb/knowledge` (default-off `KNOWLEDGE_PG_ENABLED`, its own per-tenant Postgres),
distinct from the read-only Vertex corpus listing at `/kb/documents`. FAQ
entries can also be bulk-imported via `POST /kb/faq/bulk` (CSV upload), with
a matching button on the fork's FAQs admin page (patch `0040`).

Two more operator admin surfaces follow the same backend-router +
Chatwoot-fork-page shape, both RBAC-gated (`require_permission`):
- **Escalation routing** (`features/chat/pic_store.py`'s Firestore-backed
  `PicStore`/`DealerStore`, `pic_admin_router.py` at `/admin/escalation`,
  permission `escalation.manage`, fork patch `0039`) lets operators edit
  PIC (department → contact) and dealer (slug → email) routing without
  touching env vars. `PicRegistry.lookup()` is store-first with the old
  `PIC_MAP_JSON`/dealer-JSON env-var parsing kept as a fallback for tenants
  that never touch the admin UI.
- **Customer 360** (`customer360_router.py` at `GET
  /admin/customer360/search?q=...`, permission `customer360.view`, fork
  patch `0041`) looks up a customer by phone number or vehicle number and
  aggregates existing CRM data (Chatwoot contact + conversations, RSA
  incidents). Not a DMS integration.

### Clients, config, DB

- `app/clients/deps.py` — `get_chatwoot_client` / `get_proton_config_client`
  are `lru_cache` singletons (one long-lived `httpx.AsyncClient`/`ProtonConfigClient`
  each; the latter returns `None` when `proton_backend_url`/`proton_backend_key`
  are unset, so callers fail-open without branching on env vars).
  `app/main.py`'s lifespan owns closing them via `aclose_clients`. Call these
  accessors; don't construct clients ad hoc.
- `app/config.py` — `Settings` (pydantic-settings). Field names map
  case-insensitively to env vars documented in `deploy/tenants/example.env` —
  **names must match verbatim**. `create_app()` calls
  `get_settings()` to fail fast on missing vars. Use the `*_display_url`
  properties for human-facing links (public URL if set, else internal).
- `app/db/` — SQLAlchemy 2.0 async. `_to_async_url` upgrades bare
  `postgresql://`/`sqlite://` URLs to their async driver form, so prod
  (postgres via psycopg3) and tests (sqlite via aiosqlite) share the same
  models. Tables: `processed_deliveries`, `ai_actions`, `conversation_lifecycle`.
  Schema is created via `Base.metadata.create_all` in `init_db`
  (no Alembic/migrations).

## Conventions

- Match the existing style: module docstrings explain the *why* and the
  concurrency/idempotency reasoning — keep that when editing.
- When adding a webhook event, follow the verify → dedupe → 200-fast → dispatch
  shape and add the handler as a background task in `app/services/`.
- Env vars are the single source of config truth; anything new must be added to
  both `app/config.py` and `deploy/tenants/example.env` (and `tests/conftest.py`
  if required at import time).

## Deploy notes

- **`agent`/`backend` images** are light and built on the VM: sync source to
  `/opt/platform` (not a git repo — it's synced source), then
  `docker compose -p <tenant> -f docker-compose.tenant.yml --env-file
  tenants/<tenant>.env up -d --build backend agent`.
- **Chatwoot custom image (`proton-chatwoot:<ver>-custom`)** — the Dockerfile
  globs `patches/*.patch`, so a new fork patch is auto-included. **Build off-VM
  and for `amd64`**: use Cloud Build —
  `gcloud builds submit deploy/chatwoot-fork/ --config
  deploy/chatwoot-fork/cloudbuild.yaml --substitutions _REGISTRY=<AR repo>` —
  which pushes to Artifact Registry; the VM then `docker compose ... pull` +
  `up -d --force-recreate chatwoot-rails chatwoot-sidekiq`. **A local Mac
  (`arm64`) `docker build`+`push` will fail the VM's `amd64` pull** ("no matching
  manifest"). Never build this heavy vite image on the 16 GB prod VM.
