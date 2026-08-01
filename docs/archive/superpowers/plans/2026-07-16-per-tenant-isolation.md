# Per-tenant Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each customer its own isolated Chatwoot + Zammad + agent stack on per-tenant subdomains (`<tenant>.crm.<ip>.nip.io`), sharing one Caddy/Postgres/Mailpit on the single existing VM.

**Architecture:** Split the current monolithic `deploy/docker-compose.yml` into a shared **infra** project (Caddy, Postgres, Mailpit + an external `platform` Docker network) and a reusable, env-parameterized **tenant** project (Chatwoot, Zammad, per-tenant Redis + memcached, agent). Every tenant container is name/alias-prefixed with `${TENANT}` so multiple tenant projects coexist on the one shared network. A per-tenant Postgres database + role gives data isolation; an `add-tenant.sh` script provisions a tenant end to end (secrets → DBs → Caddy route → stack up).

**Tech Stack:** Docker Compose, Caddy 2 (Caddyfile), PostgreSQL 16 (pgvector), Redis 7, Bash, existing FastAPI `agent` service (unchanged).

## Global Constraints

- **No changes to `agent/` source.** This is purely a deploy/infra change; the agent is already env-driven via `app.config.Settings`.
- **Subdomain shape:** tenant is the FIRST label — `<tenant>.crm.<ip>.nip.io`, `<tenant>.tickets...`, `<tenant>.agent...`, `<tenant>.mail...`.
- **Container naming:** every tenant container name/alias is `${TENANT}-<service>` (e.g. `proton-chatwoot-rails`). This is required to avoid DNS collisions on the shared network.
- **Data isolation:** per-tenant Postgres databases `chatwoot_<tenant>` / `zammad_<tenant>` / `agent_<tenant>`, each owned by a `*_<tenant>` role with its own password. Per-tenant Redis (`<tenant>-redis`) and memcached (`<tenant>-memcached`) containers.
- **Shared, non-isolated by design:** one Mailpit (operator-facing), one Postgres server, one Caddy. Documented, accepted.
- **Tenant name format:** must match `^[a-z][a-z0-9]*$` (valid DNS label, Docker name, and SQL identifier).
- **Memory limits (tuned for ~2 tenants in 16 GB):** chatwoot-rails 1500m, chatwoot-sidekiq 1024m, zammad-railsserver 1500m, zammad-scheduler 1024m, zammad-websocket 384m, zammad-nginx 256m, redis 256m, memcached 64m, agent 384m. Postgres 1500m (infra).
- **Pinned images (copy verbatim):** `chatwoot/chatwoot:v4.15.1`, `ghcr.io/zammad/zammad:7.1.1-0027`, `pgvector/pgvector:pg16`, `redis:7-alpine`, `memcached:1.6-alpine`, `caddy:2-alpine`, `axllent/mailpit`.
- **Zammad Elasticsearch stays disabled** (`ELASTICSEARCH_ENABLED: "false"`); Chatwoot `WEB_CONCURRENCY: 1`.
- **Compose project names:** infra = `platform-infra`; each tenant = its bare `<tenant>` name (so volumes are `<tenant>_chatwoot_storage`, etc.).

Verification note: this is infra code with no unit-test framework. Each task's "test" is a **validation gate** — `docker compose config` / `caddy validate` / `bash -n` — that must pass before commit. Live end-to-end bring-up steps are marked **[requires Docker host]** and are run during cutover (Task 9), not necessarily on the authoring machine.

---

### Task 1: Shared infra project (network, Postgres, Caddy, Mailpit)

**Files:**
- Create: `deploy/docker-compose.infra.yml`
- Create: `deploy/infra.env.example`
- Delete: `deploy/postgres/init/01-databases.sh` (tenant DBs now created by `add-tenant.sh`, not the one-time init hook)

**Interfaces:**
- Produces: external Docker network named `platform`; a running `postgres` (user `postgres`, maintenance DB `postgres`) reachable by name; a `caddy` container mounting `./caddy/Caddyfile` + `./caddy/tenants/`; a shared `mailpit`. Compose project name `platform-infra`.

- [ ] **Step 1: Write the infra compose file**

Create `deploy/docker-compose.infra.yml`:

```yaml
# Shared infrastructure for the multi-tenant platform: one Caddy, Postgres,
# and Mailpit serving every tenant. Creates the external `platform` network
# that per-tenant app stacks (docker-compose.tenant.yml) attach to.
#
# Usage:
#   docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env up -d

networks:
  platform:
    name: platform
    driver: bridge

volumes:
  postgres_data:
  caddy_data:
  caddy_config:

services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      PUBLIC_IP: ${PUBLIC_IP}
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./caddy/tenants:/etc/caddy/tenants:ro
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - platform

  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    command: postgres -c shared_buffers=512MB
    mem_limit: 1500m
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - platform
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 20s

  mailpit:
    image: axllent/mailpit
    restart: unless-stopped
    mem_limit: 128m
    networks:
      - platform
```

- [ ] **Step 2: Write the infra env template**

Create `deploy/infra.env.example`:

```bash
# Copy to deploy/infra.env and fill in before bringing up shared infra:
#   docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env up -d

# GCE VM external IP in dash form, used to build nip.io hostnames, e.g. 34-101-1-2
PUBLIC_IP=changeme

# Password for the postgres superuser. Do NOT use single quotes in this value.
POSTGRES_PASSWORD=changeme

# Mailpit UI is shared across tenants and gated behind Caddy basic_auth.
# Generate the hash with:
#   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'yourpassword'
MAILPIT_AUTH_USER=changeme
MAILPIT_AUTH_HASH=changeme
```

- [ ] **Step 3: Remove the obsolete Postgres init hook**

Run:

```bash
git rm deploy/postgres/init/01-databases.sh
```

Expected: file staged for deletion. (Per-tenant roles/DBs and the `vector` extension are created against the running server by `add-tenant.sh` in Task 5. The `pgvector/pgvector:pg16` image still ships the extension.)

- [ ] **Step 4: Validate the infra compose config**

Run:

```bash
cd deploy && cp infra.env.example infra.env \
  && sed -i.bak 's/^PUBLIC_IP=changeme/PUBLIC_IP=127-0-0-1/; s/^POSTGRES_PASSWORD=changeme/POSTGRES_PASSWORD=devpw/; s/^MAILPIT_AUTH_USER=changeme/MAILPIT_AUTH_USER=admin/; s/^MAILPIT_AUTH_HASH=changeme/MAILPIT_AUTH_HASH=x/' infra.env \
  && docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env config -q \
  && echo CONFIG_OK
```

Expected: prints `CONFIG_OK` with no error. (The base `caddy/Caddyfile` is created in Task 2; `config -q` only validates compose YAML + interpolation, not the mounted Caddyfile, so this passes now.)

- [ ] **Step 5: Clean up the throwaway infra.env**

Run:

```bash
rm -f deploy/infra.env deploy/infra.env.bak
```

(Real `infra.env` is generated on the VM by `bootstrap-vm.sh` in Task 8; it must never be committed.)

- [ ] **Step 6: Commit**

```bash
git add deploy/docker-compose.infra.yml deploy/infra.env.example
git commit -m "Add shared infra compose (caddy/postgres/mailpit) + external platform network"
```

---

### Task 2: Base Caddyfile + tenant snippet directory

**Files:**
- Create: `deploy/caddy/Caddyfile` (replace the existing single-tenant one)
- Create: `deploy/caddy/tenants/.gitkeep`

**Interfaces:**
- Produces: a base Caddyfile with global options + `import tenants/*.caddy`. Per-tenant `*.caddy` snippets are generated by `add-tenant.sh` (Task 5) into `deploy/caddy/tenants/`.

- [ ] **Step 1: Overwrite the Caddyfile with the multi-tenant base**

Replace the contents of `deploy/caddy/Caddyfile` with:

```
{
	# Plain HTTP + nip.io hostnames only — no TLS cert to manage yet.
	# remove when a real domain lands
	auto_https off
}

# Per-tenant site blocks are generated by scripts/add-tenant.sh into
# ./tenants/<name>.caddy and imported here. Each snippet defines four vhosts:
#   <tenant>.crm.<ip>.nip.io     -> <tenant>-chatwoot-rails:3000
#   <tenant>.tickets.<ip>.nip.io -> <tenant>-zammad-nginx:8080
#   <tenant>.agent.<ip>.nip.io   -> <tenant>-agent:8000
#   <tenant>.mail.<ip>.nip.io    -> shared mailpit:8025 (basic_auth)
import tenants/*.caddy
```

- [ ] **Step 2: Keep the tenants directory tracked while empty**

Run:

```bash
mkdir -p deploy/caddy/tenants && touch deploy/caddy/tenants/.gitkeep
```

- [ ] **Step 3: Validate the base Caddyfile (zero tenants)**

Run:

```bash
docker run --rm -v "$PWD/deploy/caddy:/etc/caddy" caddy:2-alpine \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && echo CADDY_OK
```

Expected: prints `CADDY_OK`. An `import` glob matching no files is valid in Caddy.

- [ ] **Step 4: Commit**

```bash
git add deploy/caddy/Caddyfile deploy/caddy/tenants/.gitkeep
git commit -m "Convert Caddyfile to multi-tenant base with per-tenant import glob"
```

---

### Task 3: Tenant env template + gitignore

**Files:**
- Create: `deploy/tenants/example.env`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `deploy/tenants/example.env` — the full variable surface a tenant needs; used by Task 4's compose validation and copied/rendered by `add-tenant.sh`. Real `deploy/tenants/<name>.env` files are gitignored.

- [ ] **Step 1: Write the tenant env template**

Create `deploy/tenants/example.env`:

```bash
# Template for a per-tenant env file. scripts/add-tenant.sh generates a real
# deploy/tenants/<tenant>.env from this shape (secrets auto-filled; agent
# tokens left blank for the operator to fill after onboarding).
#
# Bring a tenant up manually with:
#   docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d

# --- Identity --------------------------------------------------------------
TENANT=example
PUBLIC_IP=127-0-0-1

# --- Secrets (auto-generated per tenant; no single quotes in passwords) -----
CHATWOOT_DB_PASSWORD=changeme
ZAMMAD_DB_PASSWORD=changeme
AGENT_DB_PASSWORD=changeme
REDIS_PASSWORD=changeme
SECRET_KEY_BASE=changeme

# --- Agent service config --------------------------------------------------
# Filled by the operator AFTER running the Chatwoot/Zammad setup wizards and
# generating tokens (see README §5–6). Blank is fine until then.
CHATWOOT_API_TOKEN=
CHATWOOT_PLATFORM_TOKEN=
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_WEBHOOK_SECRET=
CHATWOOT_BOT_TOKEN=
CHATWOOT_BOT_SECRET=
ZAMMAD_API_TOKEN=
ZAMMAD_WEBHOOK_SECRET=
ZAMMAD_INTEGRATION_LOGIN=integration@local
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
AGENT_MODE=suggest
AUTO_RESOLVE=false
ZAMMAD_AI_DRAFTS=true
```

- [ ] **Step 2: Gitignore real tenant env files**

Modify `.gitignore` — replace the "Local env & scratch" block's `.env` line region so real tenant envs and the infra env are ignored but templates are kept. Change:

```
# Local env & scratch
.env
*.env.local
```

to:

```
# Local env & scratch
.env
deploy/infra.env
deploy/tenants/*.env
!deploy/tenants/example.env
*.env.local
```

- [ ] **Step 3: Verify ignore rules**

Run:

```bash
cp deploy/tenants/example.env deploy/tenants/proton.env
git check-ignore deploy/tenants/proton.env deploy/tenants/example.env; echo "exit=$?"
rm deploy/tenants/proton.env
```

Expected: `deploy/tenants/proton.env` is listed (ignored); `deploy/tenants/example.env` is NOT listed. `git check-ignore` exits 0 because at least one path matched.

- [ ] **Step 4: Commit**

```bash
git add deploy/tenants/example.env .gitignore
git commit -m "Add per-tenant env template and ignore real tenant env files"
```

---

### Task 4: Parameterized tenant compose stack

**Files:**
- Create: `deploy/docker-compose.tenant.yml`

**Interfaces:**
- Consumes: variables from `deploy/tenants/<tenant>.env` (Task 3) and the external `platform` network (Task 1).
- Produces: one tenant's full app stack. Container aliases `${TENANT}-chatwoot-rails:3000`, `${TENANT}-zammad-nginx:8080`, `${TENANT}-agent:8000` (consumed by Caddy snippets in Task 5). Named volumes `<tenant>_chatwoot_storage`, `<tenant>_zammad_storage`, `<tenant>_redis_data` (consumed by backup in Task 7).

- [ ] **Step 1: Write the tenant compose file**

Create `deploy/docker-compose.tenant.yml`:

```yaml
# One tenant's full application stack: Chatwoot + Zammad + agent, plus a
# dedicated Redis and memcached. Attaches to the shared external `platform`
# network created by docker-compose.infra.yml and reaches the shared Postgres
# by name. Every container name/alias is prefixed with ${TENANT} so multiple
# tenant projects coexist on the one shared network without DNS collisions.
#
# Usage (normally via scripts/add-tenant.sh):
#   docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d

x-chatwoot-env: &chatwoot-env
  RAILS_ENV: production
  NODE_ENV: production
  INSTALLATION_ENV: docker
  RAILS_LOG_TO_STDOUT: "true"
  SECRET_KEY_BASE: ${SECRET_KEY_BASE}
  FRONTEND_URL: http://${TENANT}.crm.${PUBLIC_IP}.nip.io
  POSTGRES_HOST: postgres
  POSTGRES_USERNAME: chatwoot_${TENANT}
  POSTGRES_PASSWORD: ${CHATWOOT_DB_PASSWORD}
  POSTGRES_DATABASE: chatwoot_${TENANT}
  REDIS_URL: redis://:${REDIS_PASSWORD}@${TENANT}-redis:6379
  REDIS_PASSWORD: ${REDIS_PASSWORD}
  ACTIVE_STORAGE_SERVICE: local
  SMTP_ADDRESS: mailpit
  SMTP_PORT: 1025
  SMTP_AUTHENTICATION: ""
  SMTP_ENABLE_STARTTLS_AUTO: "false"
  MAILER_SENDER_EMAIL: "Chatwoot <noreply@localhost>"
  ENABLE_ACCOUNT_SIGNUP: "false"
  WEB_CONCURRENCY: 1
  RAILS_MAX_THREADS: 5

x-zammad-env: &zammad-env
  POSTGRESQL_DB_CREATE: "false"
  POSTGRESQL_HOST: postgres
  POSTGRESQL_PORT: 5432
  POSTGRESQL_DB: zammad_${TENANT}
  POSTGRESQL_USER: zammad_${TENANT}
  POSTGRESQL_PASS: ${ZAMMAD_DB_PASSWORD}
  REDIS_URL: redis://:${REDIS_PASSWORD}@${TENANT}-redis:6379/1
  MEMCACHE_SERVERS: ${TENANT}-memcached:11211
  ELASTICSEARCH_ENABLED: "false"
  TZ: Asia/Jakarta

networks:
  platform:
    external: true
    name: platform

volumes:
  chatwoot_storage:
  zammad_storage:
  redis_data:

services:
  redis:
    image: redis:7-alpine
    container_name: ${TENANT}-redis
    restart: unless-stopped
    command: redis-server --requirepass ${REDIS_PASSWORD}
    mem_limit: 256m
    environment:
      REDIS_PASSWORD: "${REDIS_PASSWORD}"
    volumes:
      - redis_data:/data
    networks:
      platform:
        aliases:
          - ${TENANT}-redis
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a $$REDIS_PASSWORD ping | grep -q PONG"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

  memcached:
    image: memcached:1.6-alpine
    container_name: ${TENANT}-memcached
    restart: unless-stopped
    command: memcached -m 64
    mem_limit: 64m
    networks:
      platform:
        aliases:
          - ${TENANT}-memcached

  chatwoot-rails:
    image: chatwoot/chatwoot:v4.15.1
    container_name: ${TENANT}-chatwoot-rails
    restart: unless-stopped
    mem_limit: 1500m
    entrypoint: docker/entrypoints/rails.sh
    command: ["bundle", "exec", "rails", "s", "-p", "3000", "-b", "0.0.0.0"]
    environment:
      <<: *chatwoot-env
    volumes:
      - chatwoot_storage:/app/storage
    networks:
      platform:
        aliases:
          - ${TENANT}-chatwoot-rails
    depends_on:
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:3000/api > /dev/null 2>&1 || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s

  chatwoot-sidekiq:
    image: chatwoot/chatwoot:v4.15.1
    container_name: ${TENANT}-chatwoot-sidekiq
    restart: unless-stopped
    mem_limit: 1024m
    command: ["bundle", "exec", "sidekiq", "-C", "config/sidekiq.yml"]
    environment:
      <<: *chatwoot-env
    volumes:
      - chatwoot_storage:/app/storage
    networks:
      platform:
        aliases:
          - ${TENANT}-chatwoot-sidekiq
    depends_on:
      redis:
        condition: service_healthy

  zammad-init:
    image: ghcr.io/zammad/zammad:7.1.1-0027
    container_name: ${TENANT}-zammad-init
    command: ["zammad-init"]
    restart: on-failure
    environment:
      <<: *zammad-env
    volumes:
      - zammad_storage:/opt/zammad/storage
    networks:
      - platform

  zammad-railsserver:
    image: ghcr.io/zammad/zammad:7.1.1-0027
    container_name: ${TENANT}-zammad-railsserver
    command: ["zammad-railsserver"]
    restart: unless-stopped
    mem_limit: 1500m
    environment:
      <<: *zammad-env
    volumes:
      - zammad_storage:/opt/zammad/storage
    networks:
      platform:
        aliases:
          - ${TENANT}-zammad-railsserver
    depends_on:
      zammad-init:
        condition: service_completed_successfully

  zammad-scheduler:
    image: ghcr.io/zammad/zammad:7.1.1-0027
    container_name: ${TENANT}-zammad-scheduler
    command: ["zammad-scheduler"]
    restart: unless-stopped
    mem_limit: 1024m
    environment:
      <<: *zammad-env
    volumes:
      - zammad_storage:/opt/zammad/storage
    networks:
      - platform
    depends_on:
      zammad-init:
        condition: service_completed_successfully

  zammad-websocket:
    image: ghcr.io/zammad/zammad:7.1.1-0027
    container_name: ${TENANT}-zammad-websocket
    command: ["zammad-websocket"]
    restart: unless-stopped
    mem_limit: 384m
    environment:
      <<: *zammad-env
    volumes:
      - zammad_storage:/opt/zammad/storage
    networks:
      - platform
    depends_on:
      zammad-init:
        condition: service_completed_successfully

  zammad-nginx:
    image: ghcr.io/zammad/zammad:7.1.1-0027
    container_name: ${TENANT}-zammad-nginx
    command: ["zammad-nginx"]
    restart: unless-stopped
    mem_limit: 256m
    environment:
      <<: *zammad-env
      NGINX_SERVER_SCHEME: http
      ZAMMAD_RAILSSERVER_HOST: ${TENANT}-zammad-railsserver
      ZAMMAD_WEBSOCKET_HOST: ${TENANT}-zammad-websocket
    volumes:
      - zammad_storage:/opt/zammad/storage
    networks:
      platform:
        aliases:
          - ${TENANT}-zammad-nginx
    depends_on:
      - zammad-railsserver
      - zammad-websocket

  agent:
    build: ../agent
    image: platform-agent:latest
    container_name: ${TENANT}-agent
    restart: unless-stopped
    mem_limit: 384m
    environment:
      CHATWOOT_URL: http://${TENANT}-chatwoot-rails:3000
      ZAMMAD_URL: http://${TENANT}-zammad-nginx:8080
      CHATWOOT_PUBLIC_URL: http://${TENANT}.crm.${PUBLIC_IP}.nip.io
      ZAMMAD_PUBLIC_URL: http://${TENANT}.tickets.${PUBLIC_IP}.nip.io
      AGENT_PUBLIC_URL: http://${TENANT}.agent.${PUBLIC_IP}.nip.io
      CHATWOOT_API_TOKEN: ${CHATWOOT_API_TOKEN}
      CHATWOOT_PLATFORM_TOKEN: ${CHATWOOT_PLATFORM_TOKEN}
      CHATWOOT_ACCOUNT_ID: ${CHATWOOT_ACCOUNT_ID}
      CHATWOOT_WEBHOOK_SECRET: ${CHATWOOT_WEBHOOK_SECRET}
      CHATWOOT_BOT_TOKEN: ${CHATWOOT_BOT_TOKEN}
      CHATWOOT_BOT_SECRET: ${CHATWOOT_BOT_SECRET}
      ZAMMAD_API_TOKEN: ${ZAMMAD_API_TOKEN}
      ZAMMAD_WEBHOOK_SECRET: ${ZAMMAD_WEBHOOK_SECRET}
      ZAMMAD_INTEGRATION_LOGIN: ${ZAMMAD_INTEGRATION_LOGIN}
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      GEMINI_MODEL: ${GEMINI_MODEL}
      AGENT_MODE: ${AGENT_MODE}
      AUTO_RESOLVE: ${AUTO_RESOLVE}
      ZAMMAD_AI_DRAFTS: ${ZAMMAD_AI_DRAFTS}
      AGENT_DATABASE_URL: postgresql+psycopg://agent_${TENANT}:${AGENT_DB_PASSWORD}@postgres:5432/agent_${TENANT}
    networks:
      platform:
        aliases:
          - ${TENANT}-agent
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=3).status == 200 else 1)"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 20s
```

- [ ] **Step 2: Validate the tenant compose config renders**

Run:

```bash
cd deploy && docker compose -p example -f docker-compose.tenant.yml --env-file tenants/example.env config > /tmp/tenant-render.yml && echo CONFIG_OK
```

Expected: prints `CONFIG_OK`.

- [ ] **Step 3: Assert tenant-prefixed names appear in the render**

Run:

```bash
grep -q 'container_name: example-chatwoot-rails' /tmp/tenant-render.yml \
  && grep -q 'example-zammad-nginx' /tmp/tenant-render.yml \
  && grep -q 'agent_example:.*@postgres:5432/agent_example' /tmp/tenant-render.yml \
  && echo NAMES_OK
```

Expected: prints `NAMES_OK` (confirms `${TENANT}` interpolation reached container names, aliases, and the agent DB URL).

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.tenant.yml
git commit -m "Add parameterized per-tenant compose stack"
```

---

### Task 5: `add-tenant.sh` provisioning script

**Files:**
- Create: `deploy/scripts/add-tenant.sh`

**Interfaces:**
- Consumes: `docker-compose.infra.yml` (running), `docker-compose.tenant.yml`, `tenants/example.env`, `infra.env` (for `PUBLIC_IP` + Mailpit auth).
- Produces: `deploy/tenants/<name>.env`, Postgres roles/DBs `chatwoot_<name>`/`zammad_<name>`/`agent_<name>`, `deploy/caddy/tenants/<name>.caddy`, and a running `<name>` compose project. Idempotency: refuses if `tenants/<name>.env` already exists.

- [ ] **Step 1: Write the script**

Create `deploy/scripts/add-tenant.sh`:

```bash
#!/usr/bin/env bash
# Provision one tenant end to end: generate secrets, create its Postgres
# roles/databases on the shared server, render its Caddy route, and bring its
# app stack up. Run from the VM after shared infra (docker-compose.infra.yml)
# is up.
#
# Usage:
#   deploy/scripts/add-tenant.sh <tenant-name>
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
INFRA_PROJECT="platform-infra"
INFRA_FILE="docker-compose.infra.yml"
TENANT_FILE="docker-compose.tenant.yml"

TENANT="${1:-}"
if [[ ! "${TENANT}" =~ ^[a-z][a-z0-9]*$ ]]; then
  echo "ERROR: tenant name must match ^[a-z][a-z0-9]*$ (got '${TENANT}')" >&2
  exit 1
fi

cd "${DEPLOY_DIR}"

ENV_FILE="tenants/${TENANT}.env"
if [[ -e "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} already exists — tenant '${TENANT}' looks provisioned." >&2
  echo "       Use remove-tenant.sh first, or edit the env and re-run compose up." >&2
  exit 1
fi

# --- Preconditions: shared network + postgres must be up --------------------
if ! docker network inspect platform >/dev/null 2>&1; then
  echo "ERROR: shared 'platform' network not found. Bring infra up first:" >&2
  echo "       docker compose -p ${INFRA_PROJECT} -f ${INFRA_FILE} --env-file infra.env up -d" >&2
  exit 1
fi

if [[ ! -f infra.env ]]; then
  echo "ERROR: deploy/infra.env not found (needed for PUBLIC_IP + Mailpit auth)." >&2
  exit 1
fi
# shellcheck disable=SC1091
set -a; . ./infra.env; set +a
: "${PUBLIC_IP:?PUBLIC_IP must be set in infra.env}"
: "${MAILPIT_AUTH_USER:?MAILPIT_AUTH_USER must be set in infra.env}"
: "${MAILPIT_AUTH_HASH:?MAILPIT_AUTH_HASH must be set in infra.env}"

echo "==> Provisioning tenant '${TENANT}' (PUBLIC_IP=${PUBLIC_IP})"

# --- 1. Generate per-tenant secrets + env file ------------------------------
CHATWOOT_DB_PASSWORD="$(openssl rand -hex 16)"
ZAMMAD_DB_PASSWORD="$(openssl rand -hex 16)"
AGENT_DB_PASSWORD="$(openssl rand -hex 16)"
REDIS_PASSWORD="$(openssl rand -hex 16)"
SECRET_KEY_BASE="$(openssl rand -hex 64)"

sed \
  -e "s/^TENANT=.*/TENANT=${TENANT}/" \
  -e "s/^PUBLIC_IP=.*/PUBLIC_IP=${PUBLIC_IP}/" \
  -e "s/^CHATWOOT_DB_PASSWORD=.*/CHATWOOT_DB_PASSWORD=${CHATWOOT_DB_PASSWORD}/" \
  -e "s/^ZAMMAD_DB_PASSWORD=.*/ZAMMAD_DB_PASSWORD=${ZAMMAD_DB_PASSWORD}/" \
  -e "s/^AGENT_DB_PASSWORD=.*/AGENT_DB_PASSWORD=${AGENT_DB_PASSWORD}/" \
  -e "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=${REDIS_PASSWORD}/" \
  -e "s/^SECRET_KEY_BASE=.*/SECRET_KEY_BASE=${SECRET_KEY_BASE}/" \
  tenants/example.env > "${ENV_FILE}"
echo "==> Wrote ${ENV_FILE}"

# --- 2. Create Postgres roles + databases on the running server -------------
echo "==> Creating databases chatwoot_${TENANT} / zammad_${TENANT} / agent_${TENANT}"
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<SQL
CREATE ROLE chatwoot_${TENANT} LOGIN PASSWORD '${CHATWOOT_DB_PASSWORD}';
CREATE ROLE zammad_${TENANT}   LOGIN PASSWORD '${ZAMMAD_DB_PASSWORD}';
CREATE ROLE agent_${TENANT}    LOGIN PASSWORD '${AGENT_DB_PASSWORD}';
CREATE DATABASE chatwoot_${TENANT} OWNER chatwoot_${TENANT};
CREATE DATABASE zammad_${TENANT}   OWNER zammad_${TENANT};
CREATE DATABASE agent_${TENANT}    OWNER agent_${TENANT};
SQL

# Chatwoot uses pgvector; create the extension as superuser in its DB.
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d "chatwoot_${TENANT}" \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'

# --- 3. Render + install the Caddy route, then reload -----------------------
cat > "caddy/tenants/${TENANT}.caddy" <<CADDY
http://${TENANT}.crm.${PUBLIC_IP}.nip.io {
	reverse_proxy ${TENANT}-chatwoot-rails:3000
}

http://${TENANT}.tickets.${PUBLIC_IP}.nip.io {
	reverse_proxy ${TENANT}-zammad-nginx:8080
}

http://${TENANT}.agent.${PUBLIC_IP}.nip.io {
	reverse_proxy ${TENANT}-agent:8000
}

http://${TENANT}.mail.${PUBLIC_IP}.nip.io {
	basic_auth {
		${MAILPIT_AUTH_USER} ${MAILPIT_AUTH_HASH}
	}
	reverse_proxy mailpit:8025
}
CADDY
echo "==> Wrote caddy/tenants/${TENANT}.caddy; reloading Caddy"
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec caddy \
  caddy reload --config /etc/caddy/Caddyfile

# --- 4. Bring up the tenant stack -------------------------------------------
compose_tenant() {
  docker compose -p "${TENANT}" -f "${TENANT_FILE}" --env-file "${ENV_FILE}" "$@"
}

echo "==> Starting ${TENANT} redis + memcached"
compose_tenant up -d redis memcached

echo "==> Preparing Chatwoot database for ${TENANT}"
compose_tenant run --rm chatwoot-rails bundle exec rails db:chatwoot_prepare

echo "==> Starting the full ${TENANT} stack"
compose_tenant up -d

cat <<EOF

==> Tenant '${TENANT}' is up. Give containers a minute, then visit:

  http://${TENANT}.crm.${PUBLIC_IP}.nip.io      (Chatwoot — onboarding wizard)
  http://${TENANT}.tickets.${PUBLIC_IP}.nip.io  (Zammad — setup wizard)
  http://${TENANT}.agent.${PUBLIC_IP}.nip.io    (agent /healthz)
  http://${TENANT}.mail.${PUBLIC_IP}.nip.io     (shared Mailpit, basic_auth)

Next: run each app's setup wizard, then fill the CHATWOOT_*/ZAMMAD_*/GEMINI_*
tokens in ${ENV_FILE} (README §5–6) and re-apply the agent:
  docker compose -p ${TENANT} -f ${TENANT_FILE} --env-file ${ENV_FILE} up -d agent
EOF
```

- [ ] **Step 2: Make it executable and syntax-check**

Run:

```bash
chmod +x deploy/scripts/add-tenant.sh && bash -n deploy/scripts/add-tenant.sh && echo SYNTAX_OK
```

Expected: prints `SYNTAX_OK`.

- [ ] **Step 3: Verify tenant-name validation rejects bad input**

Run:

```bash
deploy/scripts/add-tenant.sh 'Bad_Name'; echo "exit=$?"
```

Expected: prints the `must match ^[a-z][a-z0-9]*$` error and `exit=1` (the regex guard runs before any Docker call).

- [ ] **Step 4: [requires Docker host] End-to-end smoke test**

Run (on a host where infra is up):

```bash
cd deploy
docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env up -d
./scripts/add-tenant.sh smoke
curl -s -H "Host: smoke.agent.${PUBLIC_IP}.nip.io" http://localhost/healthz
```

Expected: `add-tenant.sh` completes; the curl returns `{"status":"ok"}` once the agent is healthy. Tear down with `remove-tenant.sh` (Task 6) after.

- [ ] **Step 5: Commit**

```bash
git add deploy/scripts/add-tenant.sh
git commit -m "Add add-tenant.sh: provision a tenant end to end"
```

---

### Task 6: `remove-tenant.sh` decommission script

**Files:**
- Create: `deploy/scripts/remove-tenant.sh`

**Interfaces:**
- Consumes: `tenants/<name>.env`, the running `<name>` project, and infra Postgres/Caddy.
- Produces: tears down the tenant project (optionally its volumes), drops its DBs/roles, removes its Caddy snippet and reloads.

- [ ] **Step 1: Write the script**

Create `deploy/scripts/remove-tenant.sh`:

```bash
#!/usr/bin/env bash
# Decommission a tenant: stop its stack, drop its databases/roles, and remove
# its Caddy route. Prompts before destroying data.
#
# Usage:
#   deploy/scripts/remove-tenant.sh <tenant-name> [--purge-volumes]
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
INFRA_PROJECT="platform-infra"
INFRA_FILE="docker-compose.infra.yml"
TENANT_FILE="docker-compose.tenant.yml"

TENANT="${1:-}"
PURGE="${2:-}"
if [[ ! "${TENANT}" =~ ^[a-z][a-z0-9]*$ ]]; then
  echo "ERROR: tenant name must match ^[a-z][a-z0-9]*$ (got '${TENANT}')" >&2
  exit 1
fi

cd "${DEPLOY_DIR}"
ENV_FILE="tenants/${TENANT}.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found — nothing to remove for '${TENANT}'." >&2
  exit 1
fi

echo "This will STOP tenant '${TENANT}' and DROP its databases (chatwoot_${TENANT}, zammad_${TENANT}, agent_${TENANT})."
if [[ "${PURGE}" == "--purge-volumes" ]]; then
  echo "It will ALSO delete its storage volumes (attachments etc.) — irreversible."
fi
read -r -p "Type the tenant name to confirm: " confirm
if [[ "${confirm}" != "${TENANT}" ]]; then
  echo "Aborted." >&2
  exit 1
fi

# --- 1. Stop the tenant stack -----------------------------------------------
down_args=(down)
[[ "${PURGE}" == "--purge-volumes" ]] && down_args+=(--volumes)
docker compose -p "${TENANT}" -f "${TENANT_FILE}" --env-file "${ENV_FILE}" "${down_args[@]}" || true

# --- 2. Drop databases + roles ----------------------------------------------
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<SQL
DROP DATABASE IF EXISTS chatwoot_${TENANT};
DROP DATABASE IF EXISTS zammad_${TENANT};
DROP DATABASE IF EXISTS agent_${TENANT};
DROP ROLE IF EXISTS chatwoot_${TENANT};
DROP ROLE IF EXISTS zammad_${TENANT};
DROP ROLE IF EXISTS agent_${TENANT};
SQL

# --- 3. Remove the Caddy route + reload -------------------------------------
rm -f "caddy/tenants/${TENANT}.caddy"
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec caddy \
  caddy reload --config /etc/caddy/Caddyfile || true

# --- 4. Remove the env file -------------------------------------------------
rm -f "${ENV_FILE}"

echo "==> Tenant '${TENANT}' removed."
[[ "${PURGE}" != "--purge-volumes" ]] && \
  echo "    Storage volumes kept. Delete manually with: docker volume rm ${TENANT}_chatwoot_storage ${TENANT}_zammad_storage ${TENANT}_redis_data"
```

- [ ] **Step 2: Make executable and syntax-check**

Run:

```bash
chmod +x deploy/scripts/remove-tenant.sh && bash -n deploy/scripts/remove-tenant.sh && echo SYNTAX_OK
```

Expected: prints `SYNTAX_OK`.

- [ ] **Step 3: Verify it refuses an unknown tenant**

Run:

```bash
deploy/scripts/remove-tenant.sh nonexistent; echo "exit=$?"
```

Expected: prints `ERROR: tenants/nonexistent.env not found` and `exit=1`.

- [ ] **Step 4: Commit**

```bash
git add deploy/scripts/remove-tenant.sh
git commit -m "Add remove-tenant.sh: decommission a tenant"
```

---

### Task 7: Multi-tenant backup

**Files:**
- Modify: `deploy/scripts/backup.sh`

**Interfaces:**
- Consumes: `tenants/*.env` (tenant list), infra Postgres, per-tenant volumes `<tenant>_chatwoot_storage` / `<tenant>_zammad_storage`.
- Produces: `/backups/<date>/<tenant>-{chatwoot,zammad,agent}.dump` and `<tenant>-{chatwoot,zammad}_storage.tar.gz` per tenant.

- [ ] **Step 1: Rewrite backup.sh to iterate tenants**

Replace the contents of `deploy/scripts/backup.sh` with:

```bash
#!/usr/bin/env bash
# Nightly backup for the multi-tenant platform: for every tenant defined by
# deploy/tenants/*.env, pg_dump its three databases and tar its Chatwoot/Zammad
# storage volumes into /backups/YYYY-MM-DD/, then prune dirs older than 7 days.
# Cron-safe (no interactive prompts, absolute paths only).
#
# Install with:
#   0 3 * * * /opt/platform/deploy/scripts/backup.sh >> /var/log/platform-backup.log 2>&1
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/platform/deploy}"
INFRA_PROJECT="${INFRA_PROJECT:-platform-infra}"
INFRA_FILE="${INFRA_FILE:-docker-compose.infra.yml}"
BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
DATE="$(date +%F)"
DEST="${BACKUP_ROOT}/${DATE}"

echo "==> $(date -Is) Starting backup into ${DEST}"
mkdir -p "${DEST}"
cd "${DEPLOY_DIR}"

pg() {
  docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres "$@"
}

resolve_volume() {
  # Named volume "<tenant>_<logical>" — match exactly by tenant-prefixed name.
  local name="$1"
  docker volume ls --format '{{.Name}}' | grep -E "(^|_)${name}\$" | head -n1
}

shopt -s nullglob
tenant_envs=(tenants/*.env)
if [[ ${#tenant_envs[@]} -eq 0 ]]; then
  echo "WARNING: no tenants/*.env found; nothing to back up" >&2
fi

for env_file in "${tenant_envs[@]}"; do
  [[ "$(basename "${env_file}")" == "example.env" ]] && continue
  tenant="$(grep -E '^TENANT=' "${env_file}" | head -n1 | cut -d= -f2-)"
  [[ -z "${tenant}" ]] && { echo "WARNING: no TENANT in ${env_file}, skipping" >&2; continue; }

  echo "==> Backing up tenant: ${tenant}"
  for app in chatwoot zammad agent; do
    echo "    dumping ${app}_${tenant}"
    pg pg_dump -U postgres -Fc "${app}_${tenant}" > "${DEST}/${tenant}-${app}.dump"
  done

  for logical in chatwoot_storage zammad_storage; do
    volume="$(resolve_volume "${tenant}_${logical}")"
    if [[ -z "${volume}" ]]; then
      echo "    WARNING: no volume for ${tenant}_${logical}, skipping" >&2
      continue
    fi
    echo "    archiving ${volume}"
    docker run --rm -v "${volume}:/src:ro" -v "${DEST}:/dest" \
      alpine tar czf "/dest/${tenant}-${logical}.tar.gz" -C /src .
  done
done

echo "==> Pruning backups older than ${RETENTION_DAYS} days"
find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} +

echo "==> $(date -Is) Backup complete: ${DEST}"
```

- [ ] **Step 2: Syntax-check**

Run:

```bash
bash -n deploy/scripts/backup.sh && echo SYNTAX_OK
```

Expected: prints `SYNTAX_OK`.

- [ ] **Step 3: Commit**

```bash
git add deploy/scripts/backup.sh
git commit -m "Make backup.sh iterate per-tenant databases and volumes"
```

---

### Task 8: Bootstrap + provision scripts for the split topology

**Files:**
- Modify: `deploy/scripts/bootstrap-vm.sh` (§4 env handling + §6 bring-up)
- Modify: `deploy/scripts/provision-gce.sh` (closing instructions only)

**Interfaces:**
- Consumes: `docker-compose.infra.yml`, `infra.env.example`, `add-tenant.sh`.
- Produces: a VM with Docker + swap, a generated `infra.env`, shared infra running, and (optionally) initial tenants provisioned from a `TENANTS` env var.

- [ ] **Step 1: Replace the .env section (bootstrap §4) with infra.env generation**

In `deploy/scripts/bootstrap-vm.sh`, replace the block from the `# 4. .env:` header through the end of the Mailpit generation (the lines from `if [[ ! -f .env ]]; then` down to the `fi` closing the `MAILPIT_HASH` check — currently lines ~102–144) with:

```bash
# ---------------------------------------------------------------------------
# 4. infra.env: create from template, fill blank/"changeme" secrets.
# ---------------------------------------------------------------------------
if [[ ! -f infra.env ]]; then
  echo "==> Creating infra.env from infra.env.example"
  cp infra.env.example infra.env
fi

fill_if_blank() {
  local var="$1" value="$2" current
  current="$(grep -E "^${var}=" infra.env | head -n1 | cut -d= -f2-)"
  if [[ -z "${current}" || "${current}" == "changeme" ]]; then
    sed -i -e '/^'"${var}"'=/d' infra.env
    printf '%s=%s\n' "${var}" "${value}" >> infra.env
    echo "==> Filled ${var}"
  fi
}

fill_if_blank POSTGRES_PASSWORD "$(openssl rand -hex 16)"
fill_if_blank MAILPIT_AUTH_USER "admin"

MAILPIT_PW="$(openssl rand -hex 12)"
MAILPIT_HASH="$(echo "$MAILPIT_PW" | docker run --rm -i caddy:2-alpine caddy hash-password --plaintext - 2>/dev/null | tail -1)"
if [[ -n "${MAILPIT_HASH}" ]]; then
  # Caddy reads this hash from a rendered Caddyfile snippet (not via env), so
  # store it literally — no $$ escaping needed here.
  fill_if_blank MAILPIT_AUTH_HASH "${MAILPIT_HASH}"
  echo "==> Mailpit UI password (save this now, it is not stored): ${MAILPIT_PW}"
else
  echo "WARNING: Failed to generate Mailpit password hash; check Docker" >&2
fi
```

- [ ] **Step 2: Point PUBLIC_IP detection (bootstrap §5) at infra.env**

In the same file, in the "Auto-detect PUBLIC_IP" block, change the two references from `.env` to `infra.env`:

```bash
if [[ -n "${DETECTED_IP}" ]]; then
  PUBLIC_IP_DASH="${DETECTED_IP//./-}"
  sed -i "s|^PUBLIC_IP=.*|PUBLIC_IP=${PUBLIC_IP_DASH}|" infra.env
  echo "==> Detected external IP ${DETECTED_IP}, set PUBLIC_IP=${PUBLIC_IP_DASH}"
else
  echo "WARNING: could not auto-detect external IP from GCE metadata; check PUBLIC_IP in infra.env manually" >&2
  PUBLIC_IP_DASH="$(grep -E '^PUBLIC_IP=' infra.env | head -n1 | cut -d= -f2-)"
fi
```

- [ ] **Step 3: Replace the bring-up section (bootstrap §6) with infra + tenants**

Replace the entire "§6 Bring up ..." section (from the `wait_healthy() {` definition through the closing `EOF` of the final `cat` block — currently lines ~161–207) with:

```bash
# ---------------------------------------------------------------------------
# 6. Bring up shared infra, wait for postgres, then provision any tenants
#    named in the TENANTS env var (space-separated). Tenants can also be added
#    later with scripts/add-tenant.sh.
# ---------------------------------------------------------------------------
wait_healthy() {
  local service="$1" timeout="${2:-180}" waited=0 cid health
  echo "==> Waiting for ${service} to become healthy (timeout ${timeout}s)"
  cid="$(docker compose -p platform-infra -f docker-compose.infra.yml ps -q "${service}")"
  while true; do
    health="$(docker inspect --format='{{.State.Health.Status}}' "${cid}" 2>/dev/null || echo unknown)"
    if [[ "${health}" == "healthy" ]]; then
      echo "==> ${service} is healthy"
      return 0
    fi
    if (( waited >= timeout )); then
      echo "ERROR: ${service} did not become healthy within ${timeout}s (last status: ${health})" >&2
      docker compose -p platform-infra -f docker-compose.infra.yml logs --tail=50 "${service}" >&2 || true
      exit 1
    fi
    sleep 3
    waited=$((waited + 3))
  done
}

echo "==> Starting shared infra (caddy, postgres, mailpit)"
docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env up -d
wait_healthy postgres

for tenant in ${TENANTS:-}; do
  echo "==> Provisioning tenant '${tenant}'"
  ./scripts/add-tenant.sh "${tenant}"
done

cat <<EOF

==> Bootstrap complete. Shared infra is up.

Provision customers with:
  cd ${DEPLOY_DIR} && ./scripts/add-tenant.sh <tenant-name>

Each tenant is then reachable at (once its containers finish starting):
  http://<tenant>.crm.${PUBLIC_IP_DASH}.nip.io      (Chatwoot)
  http://<tenant>.tickets.${PUBLIC_IP_DASH}.nip.io  (Zammad)
  http://<tenant>.agent.${PUBLIC_IP_DASH}.nip.io    (agent)
  http://<tenant>.mail.${PUBLIC_IP_DASH}.nip.io     (shared Mailpit)

See the root README.md for per-tenant Phase-2/Phase-3 wiring steps.
EOF
```

- [ ] **Step 4: Update provision-gce.sh closing instructions**

In `deploy/scripts/provision-gce.sh`, in the final `cat <<EOF` block, replace step 3's URL list (the four `http://...nip.io` lines under "Once it prints the URLs, visit:") with:

```
  3. Provision each customer, then visit their subdomains:
       cd /opt/platform/deploy && ./scripts/add-tenant.sh <tenant-name>
       http://<tenant>.crm.${PUBLIC_IP_DASH}.nip.io
       http://<tenant>.tickets.${PUBLIC_IP_DASH}.nip.io
       http://<tenant>.agent.${PUBLIC_IP_DASH}.nip.io
       http://<tenant>.mail.${PUBLIC_IP_DASH}.nip.io
```

- [ ] **Step 5: Syntax-check both scripts**

Run:

```bash
bash -n deploy/scripts/bootstrap-vm.sh && bash -n deploy/scripts/provision-gce.sh && echo SYNTAX_OK
```

Expected: prints `SYNTAX_OK`.

- [ ] **Step 6: Commit**

```bash
git add deploy/scripts/bootstrap-vm.sh deploy/scripts/provision-gce.sh
git commit -m "Rework bootstrap/provision scripts for infra + per-tenant model"
```

---

### Task 9: Retire the old monolith, update README, cutover

**Files:**
- Delete: `deploy/docker-compose.yml`
- Delete: `deploy/.env.example`
- Modify: `README.md`
- Modify: `CLAUDE.md` (deploy topology note)

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces: docs that describe the multi-tenant workflow; the single-tenant compose + env template removed.

- [ ] **Step 1: Delete the superseded single-tenant files**

Run:

```bash
git rm deploy/docker-compose.yml deploy/.env.example
```

- [ ] **Step 2: Rewrite README §2 (Repo layout) deploy sub-tree**

In `README.md`, replace the `deploy/` sub-tree in the "Repo layout" code block with:

```
deploy/                 Runtime (this is what you copy to the VM)
  docker-compose.infra.yml   shared Caddy + Postgres + Mailpit + platform network
  docker-compose.tenant.yml  one parameterized tenant app stack
  infra.env.example
  tenants/example.env        per-tenant env template (real <tenant>.env gitignored)
  caddy/Caddyfile            base globals + import tenants/*.caddy
  caddy/tenants/             generated per-tenant route snippets
  postgres/                   (init hook removed; tenant DBs made by add-tenant.sh)
  scripts/
    provision-gce.sh     create the GCE VM, static IP, firewall rule
    bootstrap-vm.sh       install Docker, swap, generate infra.env, bring infra up
    add-tenant.sh         provision one customer end to end
    remove-tenant.sh      decommission one customer
    backup.sh              per-tenant DB dumps + storage volume archives
```

- [ ] **Step 3: Replace README §3 (Local quickstart)**

Replace the entire "## 3. Local quickstart" section body with:

````markdown
Requires Docker + the Compose plugin. Bring up shared infra, then add tenants.

```bash
cd deploy
cp infra.env.example infra.env
# Set PUBLIC_IP (dash form, e.g. 127-0-0-1), POSTGRES_PASSWORD, and Mailpit auth:
#   openssl rand -hex 16   # POSTGRES_PASSWORD
#   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'yourpassword'
docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env up -d

# Provision a customer (repeat per customer):
./scripts/add-tenant.sh proton
```

`add-tenant.sh` generates the tenant's secrets/DBs/Caddy route and brings its
stack up, then prints its four `nip.io` URLs. First visit to
`http://proton.crm.<PUBLIC_IP>.nip.io` lands on Chatwoot's onboarding wizard;
`http://proton.tickets.<PUBLIC_IP>.nip.io` on Zammad's setup wizard;
`http://proton.agent.<PUBLIC_IP>.nip.io/healthz` returns `{"status":"ok"}`.
Mailpit is shared across tenants at `http://<tenant>.mail.<PUBLIC_IP>.nip.io`
(basic-auth from `infra.env`).
````

- [ ] **Step 4: Update README §4 (GCE deploy) bootstrap step**

In "## 4. GCE deploy", replace the step-3 fenced description so bootstrap brings up infra and tenants are added afterward. Replace the sentence/paragraph after the step-3 `gcloud ... ssh` + `bootstrap-vm.sh` command with:

```
`bootstrap-vm.sh` installs Docker, adds swap, generates `infra.env` (detecting
PUBLIC_IP), and brings shared infra up. It prints the Mailpit password (save
it — not stored). To provision customers at bootstrap time, pass
`TENANTS="proton wahchan"` in the environment; otherwise add them later on the
VM with `cd /opt/platform/deploy && ./scripts/add-tenant.sh <name>`.
```

- [ ] **Step 5: Make README §5–6 (wiring) per-tenant**

At the top of "## 5. Phase-2 wiring", insert this note; then throughout §5–6 the URLs use the tenant prefix:

```markdown
> **Per tenant.** Do this once per customer, using that tenant's subdomains
> (`<tenant>.crm.<ip>.nip.io`, `<tenant>.tickets.<ip>.nip.io`,
> `<tenant>.agent.<ip>.nip.io`) and editing that tenant's
> `deploy/tenants/<tenant>.env`. After editing the env, re-apply just the agent:
> `docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d agent`.
```

Update the webhook URLs in §5 to `http://<tenant>.agent.<PUBLIC_IP>.nip.io/webhooks/chatwoot` and `.../webhooks/zammad`, the Chatwoot API host to `http://<tenant>.crm.<PUBLIC_IP>.nip.io`, and in §6 the bot `outgoing_url` to `http://<tenant>.agent.<PUBLIC_IP>.nip.io/webhooks/chatwoot/bot`. Change the register-bot invocation to:

```bash
docker compose -p <tenant> -f ../deploy/docker-compose.tenant.yml \
  --env-file ../deploy/tenants/<tenant>.env exec agent \
  python -m scripts.register_bot --inbox-id <your-chatwoot-inbox-id>
```

- [ ] **Step 6: Replace README §8 (Backups & restore) command paths**

Update the restore commands to the split topology: DB restores run against the
infra project, per-tenant DB names, and per-tenant dump filenames. Replace the
restore code block with:

```bash
cd /opt/platform/deploy
DATE=2026-07-01     # backup date to restore
T=proton            # tenant to restore

# Databases (dumps are named <tenant>-<app>.dump)
for app in chatwoot zammad agent; do
  docker compose -p platform-infra -f docker-compose.infra.yml exec -T postgres \
    pg_restore -U postgres -d "${app}_${T}" --clean --if-exists < /backups/$DATE/$T-$app.dump
done

# Storage volumes (stop the tenant's consuming services first)
docker compose -p $T -f docker-compose.tenant.yml --env-file tenants/$T.env stop chatwoot-rails chatwoot-sidekiq
docker run --rm -v ${T}_chatwoot_storage:/dest -v /backups/$DATE:/src alpine \
  sh -c "rm -rf /dest/* && tar xzf /src/$T-chatwoot_storage.tar.gz -C /dest"
docker compose -p $T -f docker-compose.tenant.yml --env-file tenants/$T.env start chatwoot-rails chatwoot-sidekiq
```

- [ ] **Step 7: Update the CLAUDE.md deploy note**

In `CLAUDE.md`, update the "What this repo is" / commands area note about the stack being brought up. Replace the line:

```
The whole stack is built/run via
`cd deploy && docker compose up -d`.
```

with:

```
The platform is multi-tenant: shared infra (`docker compose -p platform-infra
-f deploy/docker-compose.infra.yml --env-file deploy/infra.env up -d`) plus one
app stack per customer, provisioned with `deploy/scripts/add-tenant.sh <name>`
(see `docs/superpowers/specs/2026-07-16-per-tenant-isolation-design.md`).
```

- [ ] **Step 8: Verify no stale references remain**

Run:

```bash
grep -rn "docker-compose.yml\|deploy/.env.example\|crm.\${PUBLIC_IP}\|tickets.\${PUBLIC_IP}" README.md CLAUDE.md deploy || echo NO_STALE_REFS
```

Expected: prints `NO_STALE_REFS` (all references now use the split files and tenant-prefixed hostnames). If any line prints, fix it and re-run.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Retire single-tenant compose; document multi-tenant workflow"
```

- [ ] **Step 10: [requires Docker host] Full cutover rehearsal**

On the VM (or a Docker host), execute the fresh-start cutover from spec §10:

```bash
cd deploy
docker compose -p platform-infra -f docker-compose.infra.yml --env-file infra.env up -d
./scripts/add-tenant.sh proton
./scripts/add-tenant.sh wahchan
```

Expected: both tenants come up; each tenant's `/healthz` returns ok; Chatwoot/Zammad wizards load on their subdomains. Run each tenant's §5–6 wiring, verify an escalation round-trip, then decommission the old shared stack. Confirm 2 tenants stay under the 16 GB ceiling with `docker stats` + `free -m`.

---

## Self-Review

**Spec coverage:**
- §2 chosen approach (shared infra + per-tenant stacks) → Tasks 1, 4. ✓
- §3.1 two projects + external network → Tasks 1, 4. ✓
- §3.2 subdomain routing table → Task 5 (Caddy snippet) + Task 2 (base). ✓
- §3.3 DNS-collision-avoiding tenant-prefixed names → Task 4 (aliases/container_name), asserted in Task 4 Step 3. ✓
- §4 data isolation: per-tenant Postgres DBs/roles → Task 5; per-tenant Redis/memcached → Task 4; shared Mailpit → Tasks 1, 5. ✓
- §5 per-tenant env surface → Task 3 template, Task 5 generation. ✓
- §6 agent no code change → honored (only compose env); Global Constraints. ✓
- §7 add/remove/backup scripts → Tasks 5, 6, 7. ✓ zammad-init per tenant → Task 4 + Task 5 bring-up. ✓
- §8 memory tuning → Global Constraints + Task 4 limits. ✓
- §9 repo layout → realized across Tasks 1–9. ✓
- §10 cutover fresh start → Task 9 Step 10. ✓
- §11 out of scope → respected (no migration, agent stays per-tenant). ✓
- §12 risks (memory, shared Postgres/Mailpit) → surfaced in Task 9 Step 10 verification + docs. ✓

**Placeholder scan:** No TBD/TODO/"handle errors" placeholders; every code step contains full file or block content. Live-only steps are explicitly marked **[requires Docker host]** with concrete commands and expected output. ✓

**Type/name consistency:** Container aliases (`${TENANT}-chatwoot-rails`, `${TENANT}-zammad-nginx`, `${TENANT}-agent`) are identical across the tenant compose (Task 4), the Caddy snippet (Task 5), and backup volume names (`${tenant}_chatwoot_storage`, Task 7). DB names (`chatwoot_<t>`/`zammad_<t>`/`agent_<t>`) and roles match across Tasks 4/5/6/7. Compose project names (`platform-infra`, `<tenant>`) are consistent across all scripts. ✓
