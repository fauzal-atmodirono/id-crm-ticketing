# Per-tenant isolation design

**Date:** 2026-07-16
**Status:** Approved (design) — pending implementation plan
**Owner:** platform

## 1. Problem & goal

Today a single Chatwoot + Zammad + agent stack serves multiple customers with
their data comingled in shared `chatwoot` / `zammad` databases. We want each
customer ("tenant") to get their own subdomains
(`proton.crm.<ip>.nip.io`, `wahchan.crm.<ip>.nip.io`, …) with **fully isolated
data** — one customer can never see another's contacts, conversations, or
tickets — while everything continues to run as Docker Compose on the single
existing GCE VM.

### Constraints driving the design

- **Zammad has no multi-tenancy.** Self-hosted Zammad is single-tenant per
  instance; there is no account/organization-level tenant isolation. Isolating
  two customers' ticketing data *requires two Zammad instances*.
- **Chatwoot multi-account is not real isolation for our purpose.** OSS
  self-hosted Chatwoot serves all accounts from one `FRONTEND_URL` with global
  login; subdomain-per-account against one instance is cosmetic, not isolating.

Together these force **a separate application stack per tenant**. There is no
lightweight logical-tenancy option that yields true data isolation here.

### Decisions locked during brainstorming

- **Scale:** a few tenants (2–4). Not a large SaaS.
- **VM:** stay on the current `e2-standard-4` (4 vCPU / 16 GB); tune memory
  limits rather than upsize now. The VM comfortably holds ~2 full tenant
  stacks; onboarding tenant #3 will require resizing (documented, not solved
  here).
- **Apps per tenant:** every tenant gets **both** Chatwoot and Zammad (uniform
  stack).
- **Existing data:** **fresh start** per tenant — stand up clean isolated
  stacks and re-onboard both customers. No data migration.
- **Subdomain shape:** tenant is the first label — `proton.crm.<ip>.nip.io`.
- **Mailpit:** shared (operator-facing, temporary until real SMTP). The one
  deliberately non-isolated surface.

## 2. Chosen approach

**Option A — shared infra + per-tenant application stacks.** A single shared
infrastructure layer (Caddy, Postgres, Redis, Mailpit) plus one set of
*application* containers per tenant (Chatwoot, Zammad, memcached, agent), each
with its own databases and Redis logical DBs. Only stateless/backing plumbing
is shared; all tenant data lives in per-tenant databases and per-tenant app
processes.

Alternatives considered and rejected:

- **Option B — fully separate stacks (own Postgres/Redis/Mailpit per tenant).**
  Marginally cleaner isolation story but costs ~1.5–2 GB/tenant for duplicate
  backing stores that per-database separation already isolates. On a 16 GB VM
  this is the difference between fitting 2 tenants and not.
- **Option C — one VM per tenant.** Strongest isolation, simplest per stack,
  but declined on budget. Treated as the **growth path** for when tenant count
  outgrows one VM.

## 3. Architecture

### 3.1 Two Compose projects sharing one network

- **`platform-infra`** (one project, runs once): `caddy`, `postgres`
  (pgvector), `redis`, `mailpit`. Declares and **creates** an external Docker
  bridge network named `platform`.
- **`platform-<tenant>`** (one project per customer): `chatwoot-rails`,
  `chatwoot-sidekiq`, `zammad-init`, `zammad-railsserver`, `zammad-scheduler`,
  `zammad-websocket`, `zammad-nginx`, `memcached`, `agent`. Each **attaches** to
  the external `platform` network (`external: true`).

All containers share the one bridge network, so Caddy and the tenant apps reach
the shared Postgres/Redis by name exactly as today.

### 3.2 Subdomain routing

nip.io resolves arbitrary sub-labels as long as the dashed IP is present, so
`proton.crm.<ip>.nip.io` resolves to the VM IP.

| URL | Reverse-proxied to |
|---|---|
| `<tenant>.crm.<ip>.nip.io` | `<tenant>-chatwoot-rails:3000` |
| `<tenant>.tickets.<ip>.nip.io` | `<tenant>-zammad-nginx:8080` |
| `<tenant>.agent.<ip>.nip.io` | `<tenant>-agent:8000` |
| `<tenant>.mail.<ip>.nip.io` | shared `mailpit:8025` (basic-auth) |

Caddy uses a static base `Caddyfile` that `import`s `caddy/tenants/*.caddy`;
each tenant has a generated `caddy/tenants/<tenant>.caddy` snippet with its four
site blocks. Adding a tenant drops in a snippet and reloads Caddy (`caddy
reload`, no restart).

### 3.3 Avoiding the shared-network DNS collision

Because every tenant project attaches to the same `platform` network, two
tenants cannot both expose a service literally named `chatwoot-rails` — the DNS
alias would collide. Each tenant's containers therefore get **tenant-prefixed
names/aliases** parameterized by `${TENANT}` (e.g. `proton-chatwoot-rails`,
`proton-zammad-nginx`, `proton-agent`) via `container_name` and/or network
aliases in `docker-compose.tenant.yml`. Caddy targets the concrete
tenant-prefixed alias. This is the key mechanic that makes shared-network
multi-tenancy work.

## 4. Data isolation

- **Postgres:** per-tenant databases and roles — `chatwoot_<tenant>`,
  `zammad_<tenant>`, `agent_<tenant>`, each owned by a `*_<tenant>` role with
  its own password. One shared Postgres server, N isolated databases. Because
  `postgres/init/01-databases.sh` runs only on first cluster init, tenant DBs
  and roles are created by `add-tenant.sh` against the **running** server, not
  the init hook. The init hook is trimmed to only what the shared server needs
  at bootstrap.
- **Redis:** shared instance, per-tenant **logical DB indexes**. Allocation is
  recorded in each tenant env. Example: `proton` → Chatwoot `db 0`, Zammad
  `db 1`; `wahchan` → Chatwoot `db 2`, Zammad `db 3`. 16 logical DBs support
  ~8 tenants — ample. Set via the `REDIS_URL` path segment
  (`redis://:<pass>@redis:6379/<n>`).
- **memcached:** **per-tenant** container. Zammad offers no cache-key
  namespacing, so a shared memcached risks cross-tenant cache bleed. memcached
  is tiny (~64 MB), so a dedicated one per tenant is cheap insurance.
- **Mailpit:** **shared**, deliberately. It is operator-facing and temporary
  (no real SMTP configured yet); all tenants' caught mail lands in one
  basic-auth'd inbox. Documented as the single non-isolated surface; trivially
  made per-tenant later if required.

## 5. Per-tenant configuration

Each tenant is fully described by `tenants/<tenant>.env` (gitignored; a
`tenants/example.env` template is committed). It carries:

- `TENANT`, `PUBLIC_IP`
- `FRONTEND_URL=http://<tenant>.crm.<ip>.nip.io`
- Postgres: `CHATWOOT_DB=chatwoot_<tenant>`, `ZAMMAD_DB=zammad_<tenant>`,
  `AGENT_DB=agent_<tenant>` and their per-tenant role passwords
- Redis DB indexes for Chatwoot and Zammad
- `SECRET_KEY_BASE` and other app secrets
- Agent tokens/secrets: `CHATWOOT_API_TOKEN`, `CHATWOOT_PLATFORM_TOKEN`,
  `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_WEBHOOK_SECRET`, `CHATWOOT_BOT_TOKEN`,
  `CHATWOOT_BOT_SECRET`, `ZAMMAD_API_TOKEN`, `ZAMMAD_WEBHOOK_SECRET`,
  `GEMINI_API_KEY`, and the `AGENT_MODE` / `AUTO_RESOLVE` / `ZAMMAD_AI_DRAFTS`
  behavior flags.

A single reusable `docker-compose.tenant.yml` is parameterized entirely by this
env file. **No per-tenant code or per-tenant compose file.**

## 6. Agent service — no code changes

The agent service (`agent/`) is already fully config-driven via
`app/config.Settings` (env vars). A per-tenant agent instance just receives its
tenant's `.env` (that tenant's Chatwoot/Zammad internal URLs, tokens, secrets,
and `agent_<tenant>` database URL). The Chatwoot/Zammad → agent webhook wiring
described in `README.md` is performed **per tenant** against that tenant's
`<tenant>.agent.<ip>.nip.io`. This design changes only deploy/infra files;
`agent/` source is untouched.

## 7. Provisioning & operations

- **`scripts/add-tenant.sh <name>`** (idempotent where practical):
  1. Generate secrets and write `tenants/<name>.env`.
  2. Create `chatwoot_<name>`, `zammad_<name>`, `agent_<name>` databases and
     their roles on the running Postgres.
  3. Render `caddy/tenants/<name>.caddy` and `caddy reload`.
  4. `docker compose -p <name> -f docker-compose.tenant.yml --env-file
     tenants/<name>.env up -d`.
  5. Print the four `nip.io` URLs and the Mailpit password note.
- **`scripts/remove-tenant.sh <name>`**: `docker compose -p <name> ... down`;
  drop the tenant's DBs/roles (behind a confirmation prompt); remove the Caddy
  snippet and reload.
- **`scripts/backup.sh`** (extended): loop over `tenants/*.env`, dumping each
  tenant's three databases (`pg_dump -Fc`) and archiving per-tenant storage
  volumes, preserving the existing 7-day prune.

Zammad still requires its one-time `zammad-init` per tenant (DB migrate /
seed) — modeled as the tenant project's init container, gated so the long-lived
services wait for it (`service_completed_successfully`), mirroring the current
single-tenant compose.

## 8. Capacity & memory tuning

Tuned `mem_limit`s so ~2 tenants fit in 16 GB, with an explicit "upsize at #3"
trigger.

| Layer | Component | Tuned limit |
|---|---|---|
| Shared infra | postgres / redis / caddy / mailpit | ~2 GB total |
| Per tenant | chatwoot-rails | 1.5 GB |
| | chatwoot-sidekiq | 1 GB |
| | zammad-railsserver | 1.5 GB |
| | zammad-scheduler | 1 GB |
| | zammad-websocket | 0.4 GB |
| | zammad-nginx | 0.25 GB |
| | memcached | 0.06 GB |
| | agent | 0.4 GB |
| **Per tenant total** | | **~6 GB** |

- 2 tenants ≈ 2 + (2 × 6) = **~14 GB** — fits 16 GB with swap headroom (the
  bootstrap script already adds swap).
- 3 tenants ≈ ~20 GB ⇒ resize to `e2-standard-8` (8 vCPU / 32 GB), or move to
  the one-VM-per-tenant growth path.
- `WEB_CONCURRENCY=1` (Chatwoot) stays; Zammad Elasticsearch stays disabled.

## 9. Repository changes

```
deploy/
  docker-compose.infra.yml        # caddy, postgres, redis, mailpit + external `platform` net
  docker-compose.tenant.yml       # one parameterized per-tenant app stack
  tenants/
    example.env                   # committed template
    <name>.env                    # per-tenant config (gitignored)
  caddy/
    Caddyfile                     # base globals + `import tenants/*.caddy`
    tenants/<name>.caddy          # generated per-tenant site blocks
  postgres/init/01-databases.sh   # trimmed to shared-server bootstrap only
  scripts/
    add-tenant.sh                 # provision a tenant end to end
    remove-tenant.sh              # decommission a tenant
    backup.sh                     # extended to iterate tenants
    provision-gce.sh / bootstrap-vm.sh  # updated to bring up infra + iterate tenants
```

The current monolithic `deploy/docker-compose.yml` is split into
`docker-compose.infra.yml` + `docker-compose.tenant.yml`.

## 10. Cutover (fresh start)

1. Bring up `platform-infra` (`docker compose -f docker-compose.infra.yml -p
   platform-infra up -d`).
2. `add-tenant.sh proton` and `add-tenant.sh wahchan`.
3. For each tenant: run the Chatwoot and Zammad setup wizards and perform the
   per-tenant webhook wiring from `README.md` §5–6 against that tenant's
   subdomains.
4. Decommission the old shared stack once both tenants are verified.

## 11. Out of scope / future

- Data migration from the old shared stack (explicitly chosen against — fresh
  start).
- Per-tenant Mailpit / real SMTP.
- Tenant #3+ VM resize automation (documented trigger, manual for now).
- Real domains + TLS (existing README §7 path still applies; Caddy base globals
  keep `auto_https off` for the nip.io phase).
- Making the agent a single multi-tenant service (kept one-per-tenant for
  isolation and zero code change).

## 12. Risks

- **Memory pressure:** 2 tenants sit close to the 16 GB ceiling; a runaway
  Rails/Sidekiq process could OOM a neighbor. Mitigated by hard `mem_limit`s and
  swap; the real fix at #3 is a resize.
- **Shared Postgres blast radius:** one Postgres server for all tenants — a
  server-level outage affects everyone (acceptable at this scale; the isolation
  goal is *data*, not *availability*).
- **Shared Mailpit:** accepted, documented.
- **Redis DB-index bookkeeping:** manual allocation per tenant; `add-tenant.sh`
  must assign non-colliding indexes and record them.
