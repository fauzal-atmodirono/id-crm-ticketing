# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A unified CRM + ticketing platform: **Chatwoot** (CRM/live chat) and **Zammad**
(ticketing) run side by side behind a **Caddy** reverse proxy, with a small
**FastAPI `agent` service** that keeps them in sync and layers Gemini AI on top
(auto-drafted replies, auto-escalation). It is multi-tenant: each customer gets its own isolated Chatwoot + Zammad + agent stack, with a shared Caddy/Postgres/Mailpit, all as Docker Compose on a single GCE VM. See `README.md` for the full deploy/wiring runbook.

First-party code you edit lives in two services: **`agent/`** (sync and Gemini AI
orchestration) and **`backend/`** (the vendored AI-assist conversational backend
from `proton-conversational-ai`). These communicate over HTTP via
`PROTON_BACKEND_URL` (deliberately fail-open, no shared process or DB). To run
the backend locally: `cd backend/apps/backend && uv run uvicorn chatbot.main:app --port 8080`.

`deploy/` is runtime config/ops scripts. Chatwoot and Zammad are upstream apps
pulled as Docker images by `deploy/docker-compose.infra.yml` /
`deploy/docker-compose.tenant.yml` — there is no `crm/` or `ticketing/` source
in this checkout to modify.

## Commands

All commands run from `agent/`.

```bash
cd agent
pip install -e '.[dev]'        # install app + test deps (pytest, respx, aiosqlite)
pytest                         # run the full suite (asyncio_mode=auto, no flags needed)
pytest tests/test_sync_escalation.py               # one file
pytest tests/test_orchestrator.py::test_name       # one test
```

Tests never hit postgres, the real Chatwoot/Zammad APIs, or Gemini:
`tests/conftest.py` sets all required env vars and points `AGENT_DATABASE_URL`
at a throwaway sqlite file (aiosqlite); HTTP is stubbed with `respx`; Gemini
clients are injected.

The platform is multi-tenant: shared infra (`docker compose -p platform-infra
-f deploy/docker-compose.infra.yml --env-file deploy/infra.env up -d`) plus one
app stack per customer, provisioned with `deploy/scripts/add-tenant.sh <name>`
(see `docs/superpowers/specs/2026-07-16-per-tenant-isolation-design.md`).

## Agent service architecture

### The webhook pattern (all three receivers follow it)

Routers in `app/routers/` (`chatwoot.py`, `zammad.py`) are deliberately thin
and identical in shape:

1. **Verify HMAC signature** (`app/security.py`) — Chatwoot uses
   `sha256=` over `f"{timestamp}."+body` with a 300s skew window; Zammad uses
   `sha1=` over the body. Bad signature → 401. Note the two Chatwoot receivers
   use *different* secrets: `/webhooks/chatwoot` uses `chatwoot_webhook_secret`,
   `/webhooks/chatwoot/bot` uses `chatwoot_bot_secret`.
2. **Dedupe** via `app/services/dedupe.py::claim_delivery` — an insert-or-skip
   against `processed_deliveries` keyed on the provider's delivery-id header.
   The atomic PK insert (not check-then-insert) is what makes duplicate
   deliveries safe under concurrency. No delivery id → can't dedupe → process.
3. **Return 200 immediately**, dispatching real work to a FastAPI
   `BackgroundTasks`. The slow Chatwoot/Zammad/Gemini calls never run inline in
   the request path.

**Invariant for background tasks** (`app/services/`): they take an
already-parsed payload, own their own DB session (no request-scoped session),
and **never raise for expected "nothing to do" cases** (missing fields, unknown
ids, downstream HTTP failures) — those are logged and skipped. Raising out of a
background task just produces an unretrieved-exception log, so don't.

### Sync flows (`app/services/sync.py`)

Bidirectional Chatwoot ⇄ Zammad mirroring, all idempotent via the mapping
tables + their unique constraints (with a pre-check as belt-and-suspenders):

- **Chatwoot contact → Zammad user**: `upsert_contact` → `_ensure_zammad_customer`.
- **Chatwoot conversation → Zammad ticket**: `maybe_escalate` fires when the
  `escalate` label is present; `escalate_conversation` builds the ticket from
  the transcript, tags it `from-chatwoot`, and records a `ConversationLink`.
- **Zammad ticket → Chatwoot conversation**: `on_ticket_event` mirrors state
  changes back as a private note (and auto-resolves the conversation when the
  ticket closes, if `AUTO_RESOLVE`). Guarded against `last_synced_state`
  no-ops. The Zammad router drops payloads authored by `zammad_integration_login`
  to prevent the note-posting loop.

### AI layer (Gemini)

- `app/services/orchestrator.py` — the Chatwoot **agent-bot** flow
  (`/webhooks/chatwoot/bot`). Only acts on an incoming customer message on a
  `pending` conversation. **Debounces per conversation** (`DEBOUNCE_SECONDS`):
  bursts coalesce into one Gemini call that re-fetches fresh history; a task
  past its sleep (`processing=True`) runs to completion and can't be cancelled,
  so side effects never end up partial. Every decision is logged to `ai_actions`
  before execution. `AGENT_MODE=suggest` (default) posts the reply as a private
  note + reopens for a human; `auto` sends it directly.
- `app/services/responder.py` — the Zammad draft-reply flow, called from
  `on_ticket_event`. Drafts an internal note for new *customer* articles
  (sender == `"Customer"`), gated by `ZAMMAD_AI_DRAFTS`.
- `app/ai/gemini.py` — wraps `google-genai`. `decide()` forces one of the three
  `app/ai/tools.py` function calls (`function_calling_config` mode `ANY`);
  anything else (plain text, errors after retry) falls back to
  `handoff_to_human` so a conversation never silently stalls. The SDK call is
  sync, so both entry points run it via `asyncio.to_thread` to avoid blocking
  the event loop.

### Clients, config, DB

- `app/clients/deps.py` — `get_chatwoot_client` / `get_zammad_client` are
  `lru_cache` singletons (one long-lived `httpx.AsyncClient` + pool each).
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
  models. Tables: `contact_links`, `conversation_links`, `processed_deliveries`,
  `ai_actions`. Schema is created via `Base.metadata.create_all` in `init_db`
  (no Alembic/migrations).

## Conventions

- Match the existing style: module docstrings explain the *why* and the
  concurrency/idempotency reasoning — keep that when editing.
- When adding a webhook event, follow the verify → dedupe → 200-fast → dispatch
  shape and add the handler as a background task in `app/services/`.
- Env vars are the single source of config truth; anything new must be added to
  both `app/config.py` and `deploy/tenants/example.env` (and `tests/conftest.py`
  if required at import time).
