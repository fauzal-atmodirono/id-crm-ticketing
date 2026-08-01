# Unified Platform — Implementation Tasks

Derived from the approved plan (`~/.claude/plans/i-need-to-combine-zany-pony.md`).
Repo root: `/Users/fauzalatmodirono/Github/dvt-agentic-crm-ticketing`.

## Global Constraints

- Only create/modify files under `deploy/`, `agent/`, `docs/`, and root `README.md`. NEVER modify anything under `crm/` or `ticketing/` — those are upstream clones, reference-only (reading them is encouraged).
- Deployment uses official prebuilt images: `chatwoot/chatwoot:v4.15.1` and `ghcr.io/zammad/zammad:7.2.1` (fall back to the closest existing 7.2.x tag if that exact tag doesn't exist; note it in your report). Never build the two apps from source.
- Public hostnames: `crm.{PUBLIC_IP}.nip.io`, `tickets.{PUBLIC_IP}.nip.io`, `agent.{PUBLIC_IP}.nip.io`, `mail.{PUBLIC_IP}.nip.io`, plain HTTP (no TLS yet). `PUBLIC_IP` uses dashes in nip.io form (e.g. `crm.34-12-34-56.nip.io`) — nip.io accepts both dot and dash notation; use whatever `.env` provides verbatim via a `PUBLIC_HOST_SUFFIX` style variable if simpler.
- Chatwoot webhook/bot HMAC (verified in `crm/chatwoot/lib/webhooks/trigger.rb:54-63`): headers `X-Chatwoot-Timestamp: <unix ts>`, `X-Chatwoot-Signature: sha256=<hex HMAC_SHA256(secret, "{ts}.{body}")>`, `X-Chatwoot-Delivery: <delivery id>`.
- Zammad webhook HMAC (verified in `ticketing/zammad/lib/user_agent.rb:118-125`): header `X-Hub-Signature: sha1=<hex HMAC_SHA1(signature_token, body)>`, plus `X-Zammad-Trigger`, `X-Zammad-Delivery`.
- Agent service: Python 3.12, FastAPI, httpx, SQLAlchemy 2.x, pydantic-settings, `google-genai` SDK, pytest. `AGENT_MODE` defaults to `suggest` (draft-first).
- Webhook handlers must return 200 fast and do real work in background tasks — never call Gemini or slow APIs inline in the webhook response path.
- Loop prevention is mandatory: integration writes to Chatwoot are private notes; integration writes to Zammad use a dedicated integration user whose events are filtered out; delivery-ID dedupe via `processed_deliveries` table.
- Follow TDD for all Python code in `agent/` (tests first). Shell/YAML config files: validate with `docker compose config` / `bash -n` instead.

## Task 1 — Deploy stack: docker-compose, Caddyfile, env template, postgres init

Create the full runtime topology under `deploy/`.

### Files to create

**`deploy/docker-compose.yml`** — one compose project, one bridge network `platform`, named volumes: `postgres_data`, `redis_data`, `chatwoot_storage`, `zammad_storage`, `caddy_data`, `caddy_config`. Services:

| Service | Image | Notes |
|---|---|---|
| `caddy` | `caddy:2-alpine` | ONLY service publishing ports: `80:80`, `443:443`. Mount `./caddy/Caddyfile:/etc/caddy/Caddyfile:ro`, volumes `caddy_data:/data`, `caddy_config:/config`. Env `PUBLIC_IP` passed through. |
| `postgres` | `pgvector/pgvector:pg16` | Env `POSTGRES_USER=postgres`, `POSTGRES_PASSWORD=${POSTGRES_PASSWORD}`, `POSTGRES_DB=chatwoot`. Mount `./postgres/init:/docker-entrypoint-initdb.d:ro` and `postgres_data:/var/lib/postgresql/data`. Healthcheck `pg_isready -U postgres`. `mem_limit: 1500m`, `shared_buffers` via command arg `postgres -c shared_buffers=512MB`. |
| `redis` | `redis:7-alpine` | `command: redis-server --requirepass ${REDIS_PASSWORD}`. Healthcheck `redis-cli -a $$REDIS_PASSWORD ping`. `mem_limit: 300m`. |
| `memcached` | `memcached:1.6-alpine` | `command: memcached -m 64`. Zammad only. `mem_limit: 128m`. |
| `chatwoot-rails` | `chatwoot/chatwoot:v4.15.1` | `entrypoint: docker/entrypoints/rails.sh`, `command: ['bundle', 'exec', 'rails', 's', '-p', '3000', '-b', '0.0.0.0']` (mirror `crm/chatwoot/docker-compose.production.yaml` — READ IT as reference). Env via `env_file` + environment. Volume `chatwoot_storage:/app/storage`. `depends_on` postgres+redis `condition: service_healthy`. Healthcheck: `wget -qO- http://localhost:3000/api` (or the production compose's equivalent). `mem_limit: 2g`. |
| `chatwoot-sidekiq` | same image | `command: ['bundle', 'exec', 'sidekiq', '-C', 'config/sidekiq.yml']`, same env + storage volume, same depends_on. `mem_limit: 1500m`. |
| `zammad-init` | `ghcr.io/zammad/zammad:7.2.1` | `command: ['zammad-init']`, `restart: on-failure`, Zammad env (below), volume `zammad_storage:/opt/zammad/storage`, depends_on postgres healthy. |
| `zammad-railsserver` | same | `command: ['zammad-railsserver']`, depends_on `zammad-init: condition: service_completed_successfully`. `mem_limit: 2g`. |
| `zammad-scheduler` | same | `command: ['zammad-scheduler']`. `mem_limit: 1500m`. |
| `zammad-websocket` | same | `command: ['zammad-websocket']`. `mem_limit: 512m`. |
| `zammad-nginx` | same | `command: ['zammad-nginx']`, env `NGINX_SERVER_SCHEME=http`, `ZAMMAD_RAILSSERVER_HOST=zammad-railsserver`, `ZAMMAD_WEBSOCKET_HOST=zammad-websocket`. Caddy proxies to this on 8080. `mem_limit: 256m`. |
| `agent` | `build: ../agent` | Internal port 8000. Env from `.env` (agent section). depends_on postgres healthy. Healthcheck `GET /healthz` via python/wget. `mem_limit: 512m`. NOTE: `agent/` doesn't exist yet (Task 3) — still reference it; `docker compose config` tolerates a missing build dir only if it exists, so create a placeholder `agent/Dockerfile` (minimal `FROM python:3.12-slim` + `CMD ["sleep", "infinity"]`) with a comment that Task 3 replaces it. |
| `mailpit` | `axllent/mailpit` | SMTP :1025, UI :8025, both internal only. `mem_limit: 128m`. |

Zammad service env (shared block, use YAML anchor): `POSTGRESQL_HOST=postgres`, `POSTGRESQL_PORT=5432`, `POSTGRESQL_DB=zammad`, `POSTGRESQL_USER=zammad`, `POSTGRESQL_PASS=${ZAMMAD_DB_PASSWORD}`, `REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/1`, `MEMCACHE_SERVERS=memcached:11211`, `ELASTICSEARCH_ENABLED=false`, `TZ=Asia/Jakarta`. Mirror the official zammad-docker-compose repo's structure (service names/commands/env) — if unsure about an env var name, the Zammad image's entrypoint script is authoritative; check `ticketing/zammad/contrib/` and the image docs, and note assumptions in your report.

Chatwoot env (shared block via anchor): `RAILS_ENV=production`, `NODE_ENV=production`, `INSTALLATION_ENV=docker`, `RAILS_LOG_TO_STDOUT=true`, `SECRET_KEY_BASE=${SECRET_KEY_BASE}`, `FRONTEND_URL=http://crm.${PUBLIC_IP}.nip.io`, `POSTGRES_HOST=postgres`, `POSTGRES_USERNAME=postgres`, `POSTGRES_PASSWORD=${POSTGRES_PASSWORD}`, `POSTGRES_DATABASE=chatwoot`, `REDIS_URL=redis://redis:6379`, `REDIS_PASSWORD=${REDIS_PASSWORD}`, `ACTIVE_STORAGE_SERVICE=local`, `SMTP_ADDRESS=mailpit`, `SMTP_PORT=1025`, `SMTP_AUTHENTICATION=`, `SMTP_ENABLE_STARTTLS_AUTO=false`, `MAILER_SENDER_EMAIL=Chatwoot <noreply@localhost>`, `ENABLE_ACCOUNT_SIGNUP=false`, `WEB_CONCURRENCY=1`, `RAILS_MAX_THREADS=5`. Cross-check names against `crm/chatwoot/.env.example` and `crm/chatwoot/docker-compose.production.yaml`.

**`deploy/caddy/Caddyfile`** — global block `{ auto_https off }` with a comment "remove when a real domain lands"; four `http://` site blocks per Global Constraints hostnames: crm → `chatwoot-rails:3000`, tickets → `zammad-nginx:8080`, agent → `agent:8000`, mail → `mailpit:8025`. Use `{$PUBLIC_IP}` env interpolation.

**`deploy/postgres/init/01-databases.sh`** — runs as postgres superuser on first init: create role `zammad` (password `$ZAMMAD_DB_PASSWORD` — pass it into the postgres service env), create role `agent` (password `$AGENT_DB_PASSWORD`), create databases `zammad` (owner zammad) and `agent` (owner agent), and `CREATE EXTENSION IF NOT EXISTS vector;` in the `chatwoot` database. Use `psql -v ON_ERROR_STOP=1`. Must be idempotent enough not to crash a re-init (`IF NOT EXISTS` where possible; roles via DO block).

**`deploy/.env.example`** — every variable used by the compose file, grouped and commented: `PUBLIC_IP` (VM external IP, dash form for nip.io e.g. `34-101-1-2`), `POSTGRES_PASSWORD`, `ZAMMAD_DB_PASSWORD`, `AGENT_DB_PASSWORD`, `REDIS_PASSWORD`, `SECRET_KEY_BASE` (generate: `openssl rand -hex 64`), and the agent-service section: `CHATWOOT_URL=http://chatwoot-rails:3000`, `ZAMMAD_URL=http://zammad-nginx:8080`, `CHATWOOT_API_TOKEN=`, `CHATWOOT_PLATFORM_TOKEN=`, `CHATWOOT_ACCOUNT_ID=1`, `ZAMMAD_API_TOKEN=`, `GEMINI_API_KEY=`, `GEMINI_MODEL=gemini-2.5-flash`, `CHATWOOT_WEBHOOK_SECRET=`, `CHATWOOT_BOT_SECRET=`, `CHATWOOT_BOT_TOKEN=`, `ZAMMAD_WEBHOOK_SECRET=`, `ZAMMAD_INTEGRATION_LOGIN=integration@local`, `AGENT_MODE=suggest`, `AUTO_RESOLVE=false`, `AGENT_DATABASE_URL=postgresql+psycopg://agent:${AGENT_DB_PASSWORD}@postgres:5432/agent`. Comment each token with WHERE to obtain it (e.g. "Chatwoot → Profile Settings → Access Token").

### Verification (required before commit)

- `docker compose -f deploy/docker-compose.yml --env-file deploy/.env.example config` exits 0 (populate placeholder secrets in `.env.example` with dummy values so config passes, e.g. `changeme`).
- `bash -n deploy/postgres/init/01-databases.sh`.
- If docker is unavailable in the environment, say so in the report and validate YAML with python (`python3 -c "import yaml,sys; yaml.safe_load(open(...))"`).

## Task 2 — Ops scripts and runbook

### Files to create

**`deploy/scripts/provision-gce.sh`** — bash, `set -euo pipefail`, config vars at top (`PROJECT_ID`, `ZONE=asia-southeast2-a`, `VM_NAME=crm-ticketing`, `MACHINE_TYPE=e2-standard-4`). Steps: reserve static external IP (`gcloud compute addresses create`), create VM (`gcloud compute instances create` — Debian 12 image family `debian-12`, boot disk 60GB `pd-balanced`, network tag `crm-ticketing`, the reserved address), firewall rules allowing tcp:80,443 to that tag (`gcloud compute firewall-rules create`). Print the external IP and next steps at the end. Idempotent: guard each create with a "describe || create" pattern.

**`deploy/scripts/bootstrap-vm.sh`** — run ON the VM as a sudo-capable user. Steps: install Docker CE + compose plugin from Docker's official apt repo (not distro docker.io); `usermod -aG docker $USER`; create 4GB swapfile at `/swapfile` + fstab entry + `vm.swappiness=10` via sysctl.d; create `/opt/platform`; expect `deploy/` and `agent/` copied there (document `gcloud compute scp --recurse deploy agent VM:/opt/platform/` in the runbook); `cp .env.example .env` if `.env` missing, then auto-fill `POSTGRES_PASSWORD`, `ZAMMAD_DB_PASSWORD`, `AGENT_DB_PASSWORD`, `REDIS_PASSWORD` (`openssl rand -hex 16`) and `SECRET_KEY_BASE` (`openssl rand -hex 64`) in place only where value is empty/`changeme`; auto-detect `PUBLIC_IP` (`curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip` → dash form); then: `docker compose up -d postgres redis`, wait for health (`docker compose ps --format json` loop or `docker inspect` healthy loop with timeout), `docker compose run --rm chatwoot-rails bundle exec rails db:chatwoot_prepare`, `docker compose up -d`, print URLs.

**`deploy/scripts/backup.sh`** — cron-safe: `pg_dump -Fc` each of `chatwoot`, `zammad`, `agent` via `docker compose exec -T postgres pg_dump -U postgres -Fc <db>` into `/backups/YYYY-MM-DD/`, tar the `chatwoot_storage` and `zammad_storage` volumes (via `docker run --rm -v <vol>:/src -v /backups/...:/dest alpine tar czf`), delete backup dirs older than 7 days. Comment showing the crontab line `0 3 * * * /opt/platform/deploy/scripts/backup.sh >> /var/log/platform-backup.log 2>&1`.

**`README.md`** (repo root) — the runbook: (1) what this repo is (architecture diagram in ASCII: Caddy → Chatwoot/Zammad/agent, integration flows); (2) repo layout; (3) local quickstart (`cd deploy && cp .env.example .env && docker compose up -d`, first-run notes: Chatwoot onboarding at crm URL, Zammad setup wizard at tickets URL, Mailpit at mail URL); (4) GCE deploy (provision → scp → bootstrap); (5) Phase-2 wiring: exact click-paths to create the Chatwoot account webhook (Settings → Integrations → Webhooks, URL `http://agent.<IP>.nip.io/webhooks/chatwoot`, select events contact_created, contact_updated, conversation_updated, conversation_status_changed, conversation_resolved) and Zammad webhook + trigger (Admin → Manage → Webhook with signature token; Admin → Manage → Trigger scoped to tag `from-chatwoot`, action webhook); how to create API tokens in both apps; (6) Phase-3: `register_bot.py` usage, Zammad AI provider = Custom OpenAI with endpoint `https://generativelanguage.googleapis.com/v1beta/openai` + Gemini key; (7) switching to a real domain later (Caddyfile edit, FRONTEND_URL change); (8) backups & restore.

### Verification

- `bash -n` all three scripts; run shellcheck if available (note result either way).
- README renders sensibly (no broken fences).

## Task 3 — Agent service skeleton (FastAPI)

Create `agent/` — application factory, config, HMAC security, DB models, API clients, health endpoint. TDD required: tests written first for security verifiers and health endpoint.

### Files

- **`agent/pyproject.toml`** — project `agent-service`, Python `>=3.12`, deps: `fastapi`, `uvicorn[standard]`, `httpx`, `sqlalchemy>=2`, `psycopg[binary]`, `pydantic-settings`, `google-genai`; dev group: `pytest`, `pytest-asyncio`, `respx`, `aiosqlite` (tests run on sqlite). Configure pytest (`asyncio_mode = "auto"`).
- **`agent/Dockerfile`** — `python:3.12-slim`, install project, `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`. (Replaces Task 1's placeholder.)
- **`agent/app/config.py`** — `Settings(BaseSettings)` with every env var from `.env.example`'s agent section (Task 1 lists them; also `AGENT_DATABASE_URL`). Singleton accessor `get_settings()` with `lru_cache`.
- **`agent/app/security.py`** —
  - `verify_chatwoot_signature(secret, timestamp, body: bytes, signature, max_skew_seconds=300, now=None) -> bool`: reject missing/malformed header, timestamp skew > max_skew, signature format `sha256=<hex>` over `f"{timestamp}.".encode() + body` with HMAC-SHA256, `hmac.compare_digest`.
  - `verify_zammad_signature(token, body: bytes, signature) -> bool`: format `sha1=<hex>`, HMAC-SHA1 over raw body, `hmac.compare_digest`.
  - Exact schemes are in Global Constraints — match them byte-for-byte.
- **`agent/app/db/models.py`** — SQLAlchemy 2.0 declarative: `ContactLink(chatwoot_contact_id BigInteger unique not null, zammad_user_id BigInteger unique not null, email Text, created_at/updated_at)`; `ConversationLink(chatwoot_conversation_id unique not null, zammad_ticket_id unique not null, last_synced_state Text, timestamps)`; `ProcessedDelivery(delivery_id Text primary key, source Text, received_at)`; `AiAction(id, conversation_ref Text, decision Text, model Text, prompt_tokens Integer, output Text, created_at)`.
- **`agent/app/db/session.py`** — async engine from `AGENT_DATABASE_URL` (translate to async driver: postgresql+psycopg supports async; sqlite+aiosqlite in tests), `async_sessionmaker`, `init_db()` running `Base.metadata.create_all`.
- **`agent/app/clients/chatwoot.py`** — `ChatwootClient` (httpx.AsyncClient, base_url, header `api_access_token`): `get_messages(conversation_id)`, `create_message(conversation_id, content, private=True, token_override=None)` (token_override for bot token), `toggle_status(conversation_id, status)`, `get_contact(contact_id)`. Plus `ChatwootPlatformClient` for `create_agent_bot(name, outgoing_url)`. API paths per Chatwoot REST: `/api/v1/accounts/{account_id}/...`, platform `/platform/api/v1/agent_bots`.
- **`agent/app/clients/zammad.py`** — `ZammadClient` (header `Authorization: Token token=<...>`): `search_users(query)` (`GET /api/v1/users/search?query=...`), `create_user(...)`, `update_user(id, ...)`, `create_ticket(title, group, customer_id, article, tags)` (`POST /api/v1/tickets` with nested `article`), `add_article(ticket_id, body, internal=True)`, `find_or_create_organization(name)`.
- **`agent/app/routers/health.py`** — `GET /healthz`: DB `SELECT 1`; returns `{"status": "ok"}` else 503.
- **`agent/app/main.py`** — app factory: settings, lifespan running `init_db()`, include health router (webhook routers arrive in Task 4; structure for it).
- **`agent/tests/`** — `test_security.py` (both verifiers: happy path with signatures computed in-test, wrong secret, malformed header, stale timestamp, missing pieces), `test_health.py` (httpx ASGI client + sqlite; healthz 200). `conftest.py` sets test env vars before app import.

### Verification

- `cd agent && pip install -e ".[dev]" && pytest` (or `uv` if available) — all green, output pristine. Include RED evidence for security tests per TDD.
- `docker` build NOT required (image build happens on VM); ensure Dockerfile is coherent.

## Task 4 — Webhook routers and sync flows

Implement the integration flows. TDD required. Builds on Task 3's skeleton.

### Files

- **`agent/app/routers/chatwoot.py`** — `POST /webhooks/chatwoot`: read raw body; verify Chatwoot HMAC with `CHATWOOT_WEBHOOK_SECRET` (401 on failure); dedupe on `X-Chatwoot-Delivery` via `processed_deliveries` (insert-or-skip; missing header → process anyway); dispatch to background task by `event` field; return `{"ok": true}` immediately. Events: `contact_created`/`contact_updated` → `sync.upsert_contact`; `conversation_updated` → `sync.maybe_escalate` (label check); `conversation_status_changed`/`conversation_resolved` → record only (update `conversation_links.last_synced_state`).
- **`agent/app/routers/zammad.py`** — `POST /webhooks/zammad`: verify `X-Hub-Signature` with `ZAMMAD_WEBHOOK_SECRET`; dedupe on `X-Zammad-Delivery`; drop payloads where the latest article/change author login == `ZAMMAD_INTEGRATION_LOGIN` (loop prevention); background-dispatch to `sync.on_ticket_event`.
- **`agent/app/services/sync.py`** —
  - `upsert_contact(payload)`: extract email/name/company; skip if no email; search Zammad user by email, create or update; company → `find_or_create_organization`; upsert `contact_links`.
  - `maybe_escalate(payload)`: if label `escalate` present AND no existing `conversation_links` row for the conversation → `escalate_conversation(conversation_id)`.
  - `escalate_conversation(conversation_id, reason=None, priority=None, summary=None)`: fetch messages via ChatwootClient; render transcript (`[timestamp] Sender: text`, skip private notes); resolve/ensure Zammad customer (via contact link or upsert on the fly); create ticket (title = summary or first incoming message truncated 100 chars; group `Users`; tag `from-chatwoot`; first article = transcript, `internal=false`, note with Chatwoot conversation URL); insert `conversation_links`; post private note to Chatwoot: `"Escalated to Zammad ticket #<number>: <tickets url>/#ticket/zoom/<id>"`. Idempotent: unique constraint + pre-check.
  - `on_ticket_event(payload)`: look up `conversation_links` by ticket id (ignore unknown); if state changed → private note in Chatwoot ("Ticket #N moved to <state>"), update `last_synced_state`; if state closed and `AUTO_RESOLVE` → `toggle_status(conversation_id, "resolved")`.
- Wire routers into `app/main.py`.

### Tests (respx-mocked httpx; sqlite DB)

- Signature rejection (401) both endpoints; valid signature accepted.
- Dedupe: same delivery ID twice → second is a no-op (assert single downstream call).
- `upsert_contact`: creates when absent, updates when found, skips when no email.
- Escalation: label event creates ticket + link row + private note; second identical event does NOT create a second ticket.
- `on_ticket_event`: state change → note; author == integration login → dropped; unknown ticket → ignored; closed + AUTO_RESOLVE=true → toggle_status called.

### Verification

`pytest` full suite green, output pristine. TDD evidence in report.

## Task 5 — AI layer: Gemini orchestrator, responder, bot registration

TDD required (mock the Gemini client — never call the real API in tests).

### Files

- **`agent/app/ai/tools.py`** — three function declarations (google-genai `types.FunctionDeclaration` or plain dict schema): `send_reply(text: string)`, `escalate_to_ticket(reason: string, priority: string enum [low, normal, high], summary: string)`, `handoff_to_human(reason: string)`.
- **`agent/app/ai/gemini.py`** — thin wrapper: `decide(system_prompt, conversation_context) -> Decision` where `Decision = (action: str, args: dict, raw_text: str | None, prompt_tokens: int | None)`. Uses `google.genai.Client(api_key=...)`, model from settings, `tools=` with the declarations, `tool_config` forcing function calling (mode ANY). If the model returns plain text instead of a function call, map to `handoff_to_human(reason="model returned no action")`. Retries: one retry on transient error, then handoff.
- **`agent/app/services/orchestrator.py`** — `handle_bot_event(payload)`: only `event == "message_created"` and `message_type == "incoming"` and conversation status `pending`; skip messages authored by the bot itself; per-conversation in-process debounce (if a task for this conversation is already queued, coalesce — a simple `dict[conversation_id, asyncio.Task]` with 3s delay gathering the latest state); build context (last 20 messages + contact name/email); system prompt: support agent for the company, decide reply vs escalate vs handoff, keep replies short and grounded — no invented facts; call `gemini.decide`; log every decision to `ai_actions`; execute:
  - `send_reply`: `AGENT_MODE == "auto"` → public message via bot token, stay pending. `suggest` → private note `"🤖 Suggested reply:\n\n<text>"` + `toggle_status(open)` (handoff so a human sees it).
  - `escalate_to_ticket`: call `sync.escalate_conversation(conversation_id, reason, priority, summary)` then `toggle_status(open)`.
  - `handoff_to_human`: `toggle_status(open)`.
- **`agent/app/routers/chatwoot.py`** — add `POST /webhooks/chatwoot/bot`: verify HMAC with `CHATWOOT_BOT_SECRET`, dedupe, background-dispatch to orchestrator.
- **`agent/app/services/responder.py`** — Zammad draft flow: `draft_ticket_reply(payload)` called from `sync.on_ticket_event` when the event is a new customer article (not integration-authored, feature-flag `ZAMMAD_AI_DRAFTS` env default true): last articles → Gemini (same wrapper, but simple text generation, no tools — add `generate(system_prompt, context) -> str` to gemini.py) → `add_article(ticket_id, "🤖 AI suggested reply:\n\n<text>", internal=True)`.
- **`agent/scripts/register_bot.py`** — CLI (`python -m scripts.register_bot` or direct): reads env; creates agent bot via Platform API `POST /platform/api/v1/agent_bots` `{name: "Gemini Agent", outgoing_url: <AGENT_PUBLIC_URL>/webhooks/chatwoot/bot, bot_type: "webhook"}`; prints the bot's `access_token` + `secret` with instructions to put them in `.env` (`CHATWOOT_BOT_TOKEN`, `CHATWOOT_BOT_SECRET`); then assigns bot to inbox via `POST /api/v1/accounts/{account}/inboxes/{inbox_id}/set_agent_bot` with `{agent_bot: <id>}` — VERIFY this route in `crm/chatwoot/config/routes.rb` (search `set_agent_bot`) and adjust if different; takes `--inbox-id` arg.

### Tests

- Orchestrator: each of the three decisions executes the right side effects in `suggest` mode (private note + open; escalate + open; open) — assert via respx-mocked Chatwoot/Zammad and a stubbed `gemini.decide`; `auto` mode sends public message; non-incoming/non-pending events ignored; `ai_actions` row written per decision.
- Gemini wrapper: function-call response parsed to Decision; plain-text response → handoff fallback (mock the genai client object).
- Responder: customer article → internal draft article posted; integration-authored article → no draft.

### Verification

`pytest` full suite green (Tasks 3+4 tests still passing), output pristine. TDD evidence in report.
