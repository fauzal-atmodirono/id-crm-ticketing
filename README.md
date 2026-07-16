# Unified CRM + Ticketing Platform

Chatwoot (CRM/live chat) and Zammad (ticketing) running side by side behind a
single Caddy reverse proxy, with a small FastAPI **agent** service that keeps
contacts and conversations in sync between the two and adds a Gemini-powered
AI layer (auto-drafted replies, auto-escalation to tickets).

Everything runs as one Docker Compose project on a single GCE VM.

## 1. Architecture

```
                                   Internet
                                      │
                                 80/443 (HTTP)
                                      │
                              ┌───────▼────────┐
                              │      Caddy      │  reverse proxy, nip.io vhosts
                              └───────┬────────┘
              ┌───────────────┬───────┴───────┬────────────────┐
              │               │               │                │
      crm.<ip>.nip.io tickets.<ip>.nip.io agent.<ip>.nip.io mail.<ip>.nip.io
              │               │               │                │
      ┌───────▼──────┐ ┌──────▼───────┐ ┌─────▼──────┐  ┌──────▼──────┐
      │   Chatwoot   │ │    Zammad    │ │   agent    │  │   Mailpit   │
      │ rails+sidekiq│ │ rails+ws+... │ │ (FastAPI)  │  │  (SMTP+UI)  │
      └───────┬──────┘ └──────┬───────┘ └─────┬──────┘  └─────────────┘
              │               │               │
              │   webhooks    │   webhooks    │
              └──────────────►│◄──────────────┘
                              │
                     ┌────────▼────────┐
                     │ postgres (+pgvector) │  databases: chatwoot, zammad, agent
                     │ redis, memcached     │
                     └──────────────────────┘
```

Integration flows (implemented by the `agent` service):

- **Chatwoot → agent → Zammad**: a webhook on contact/conversation events
  lets the agent mirror contacts into Zammad and escalate conversations
  (tagged `escalate`) into tickets.
- **Zammad → agent → Chatwoot**: a webhook + trigger notify the agent of
  ticket state changes, which get mirrored back as private notes/status
  changes on the linked Chatwoot conversation.
- **Chatwoot agent bot → agent → Gemini**: an AI bot subscribed to new
  incoming messages asks Gemini to draft a reply, escalate, or hand off to
  a human (`AGENT_MODE=suggest` by default — nothing is sent without human
  approval).
- **Zammad AI provider**: Zammad's own "Custom OpenAI" AI integration talks
  directly to Gemini's OpenAI-compatible endpoint for ticket reply
  suggestions.

## 2. Repo layout

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
agent/                  FastAPI integration + AI service (built by compose)
crm/                    Upstream Chatwoot clone — reference only, do not edit
ticketing/              Upstream Zammad clone — reference only, do not edit
docs/                   Design/planning docs
```

## 3. Local quickstart

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

## 4. GCE deploy

```bash
# 1. Provision the VM, static IP, and firewall rule (run from your workstation)
PROJECT_ID=<your-gcp-project> ./deploy/scripts/provision-gce.sh

# 2. Copy the app onto the VM (paths printed at the end of step 1)
gcloud compute scp --recurse deploy agent crm-ticketing:/tmp/platform \
  --zone=asia-southeast2-a --project=<your-gcp-project>
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=<your-gcp-project> \
  --command="sudo mkdir -p /opt/platform && sudo mv /tmp/platform/* /opt/platform/"

# 3. SSH in and bootstrap: installs Docker, adds swap, generates infra.env,
#    detects PUBLIC_IP, and brings shared infra up.
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=<your-gcp-project>
sudo /opt/platform/deploy/scripts/bootstrap-vm.sh
```

`bootstrap-vm.sh` installs Docker, adds swap, generates `infra.env` (detecting
PUBLIC_IP), and brings shared infra up. It prints the Mailpit password (save
it — not stored). To provision customers at bootstrap time, pass
`TENANTS="proton wahchan"` in the environment; otherwise add them later on the
VM with `cd /opt/platform/deploy && ./scripts/add-tenant.sh <name>`.

## 5. Phase-2 wiring: Chatwoot ⇄ Zammad sync

> **Per tenant.** Do this once per customer, using that tenant's subdomains
> (`<tenant>.crm.<ip>.nip.io`, `<tenant>.tickets.<ip>.nip.io`,
> `<tenant>.agent.<ip>.nip.io`) and editing that tenant's
> `deploy/tenants/<tenant>.env`. After editing the env, re-apply just the agent:
> `docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d agent`.

Do this once both apps have completed their setup wizards.

### API tokens

- **Chatwoot**: log in → click your avatar (bottom-left) → **Profile
  Settings** → **Access Token** tab → copy the token into `CHATWOOT_API_TOKEN`
  in `tenants/<tenant>.env`. For the platform-level token (needed to register
  the AI bot in Phase-3): **Super Admin console** (`/super_admin`) →
  **Platform Apps** → create/copy the **Platform Token** into
  `CHATWOOT_PLATFORM_TOKEN`.
- **Zammad**: log in as admin → avatar (top-right) → **Profile** → **Token
  Access** → generate a token with the permissions you need → copy into
  `ZAMMAD_API_TOKEN` in `tenants/<tenant>.env`.

Restart the agent service after editing the tenant env:
`docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d agent`.

### Chatwoot → agent webhook

**Settings → Integrations → Webhooks** → **Add new webhook**:

- URL: `http://<tenant>.agent.<PUBLIC_IP>.nip.io/webhooks/chatwoot`
- Subscribe to events: `contact_created`, `contact_updated`,
  `conversation_updated`, `conversation_status_changed`

Chatwoot auto-generates the webhook's signing secret server-side
(`has_secure_token`) — you cannot set your own. After creating the webhook
above, fetch it back via the API and copy its `secret` field into
`tenants/<tenant>.env` as `CHATWOOT_WEBHOOK_SECRET`:

```bash
curl -H "api_access_token: <CHATWOOT_API_TOKEN>" \
  http://<tenant>.crm.<PUBLIC_IP>.nip.io/api/v1/accounts/1/webhooks
```

Then restart the agent service to pick it up:
`docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d agent`.

### Zammad → agent webhook + trigger

**Admin → Manage → Webhook** → **Add Webhook**:

- Endpoint: `http://<tenant>.agent.<PUBLIC_IP>.nip.io/webhooks/zammad`
- Set a signature token — put the same value in `ZAMMAD_WEBHOOK_SECRET` in
  `tenants/<tenant>.env` (verified via the `X-Hub-Signature` header).

**Admin → Manage → Trigger** → **Add Trigger**:

- Condition: scope to tag `from-chatwoot` (tickets created by the agent
  service from escalated Chatwoot conversations)
- Action: **Webhook** → select the webhook created above

This lets Zammad notify the agent whenever a ticket that originated from
Chatwoot changes state, so the change can be mirrored back as a note on the
linked conversation.

## 6. Phase-3: AI layer (Gemini)

### Register the Chatwoot AI agent bot

Once `agent/scripts/register_bot.py` exists (ships with the agent service),
run it from the VM (or anywhere with network access to the Chatwoot
platform API and `tenants/<tenant>.env` populated with `CHATWOOT_PLATFORM_TOKEN`,
`CHATWOOT_API_TOKEN`, `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_URL`, and `AGENT_PUBLIC_URL`):

```bash
docker compose -p <tenant> -f ../deploy/docker-compose.tenant.yml \
  --env-file ../deploy/tenants/<tenant>.env exec agent \
  python -m scripts.register_bot --inbox-id <your-chatwoot-inbox-id>
```

It creates a Chatwoot agent bot pointed at
`http://<tenant>.agent.<PUBLIC_IP>.nip.io/webhooks/chatwoot/bot`, assigns it
to the given inbox, and prints the bot's `access_token`/`secret` pair — copy
those into `tenants/<tenant>.env` as `CHATWOOT_BOT_TOKEN` / `CHATWOOT_BOT_SECRET`
and restart the agent:
`docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d agent`.

### Zammad AI provider (Custom OpenAI → Gemini)

Zammad has a built-in "AI provider" integration compatible with the OpenAI
API shape. Point it at Gemini's OpenAI-compatible endpoint instead of
running a second integration:

**Admin → Manage → AI Provider** (or **Integrations → AI**, depending on
Zammad version) → provider **Custom OpenAI**:

- Endpoint: `https://generativelanguage.googleapis.com/v1beta/openai`
- API key: your `GEMINI_API_KEY` (same one used by the agent service, from
  [Google AI Studio](https://aistudio.google.com/apikey))
- Model: match `GEMINI_MODEL` in `.env` (default `gemini-2.5-flash`)

With this enabled, Zammad's own "suggest a reply" / ticket AI features call
Gemini directly, independent of the agent service's own Chatwoot-side bot.

`AGENT_MODE` controls the Chatwoot-side bot's behavior: `suggest` (default)
drafts a private-note reply for a human to approve; `auto` sends replies
directly. `AUTO_RESOLVE` controls whether the agent may resolve/close
Chatwoot conversations on its own when the linked Zammad ticket closes.

## 7. Switching to a real domain later

The nip.io setup is HTTP-only and meant to get you running fast. To move to
a real domain with TLS:

1. Point your domain's DNS (A records) at the VM's static IP — e.g.
   `crm.example.com`, `tickets.example.com`, `agent.example.com`,
   `mail.example.com` (or drop Mailpit's public exposure entirely once you
   have real SMTP).
2. Edit `deploy/caddy/Caddyfile`: replace the `http://*.{$PUBLIC_IP}.nip.io`
   site blocks with your real hostnames, and remove the
   `{ auto_https off }` global block so Caddy provisions Let's Encrypt certs
   automatically.
3. Update `CHATWOOT_FRONTEND_URL` in `deploy/tenants/<tenant>.env` to
   `https://crm.example.com` for each tenant.
4. Re-apply each tenant stack:
   `docker compose -p <tenant> -f docker-compose.tenant.yml --env-file tenants/<tenant>.env up -d`
   — Caddy will request certificates on first request to each new hostname.

## 8. Backups & restore

`deploy/scripts/backup.sh` dumps the `chatwoot`, `zammad`, and `agent`
Postgres databases (`pg_dump -Fc`) and archives the `chatwoot_storage` and
`zammad_storage` volumes into `/backups/YYYY-MM-DD/`, then prunes backup
directories older than 7 days.

Install as a nightly cron job (as root, or a user with docker access):

```
0 3 * * * /opt/platform/deploy/scripts/backup.sh >> /var/log/platform-backup.log 2>&1
```

### Restore

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
