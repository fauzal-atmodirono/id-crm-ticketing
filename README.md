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
deploy/                 Runtime: docker-compose.yml, Caddyfile, postgres init,
                         ops scripts (this is what you copy to the VM)
  docker-compose.yml
  .env.example
  caddy/Caddyfile
  postgres/init/01-databases.sh
  scripts/
    provision-gce.sh     create the GCE VM, static IP, firewall rule
    bootstrap-vm.sh       install Docker, swap, fill secrets, bring stack up
    backup.sh              cron job: DB dumps + storage volume archives
agent/                  FastAPI integration + AI service (built by compose)
crm/                    Upstream Chatwoot clone — reference only, do not edit
ticketing/              Upstream Zammad clone — reference only, do not edit
docs/                   Design/planning docs
```

## 3. Local quickstart

Requires Docker + the Compose plugin.

```bash
cd deploy
cp .env.example .env
# For a local run, PUBLIC_IP can be your machine's LAN IP in dash form
# (e.g. 192-168-1-50) or 127-0-0-1 if you only need it to resolve locally,
# and the *_PASSWORD / SECRET_KEY_BASE values can be any non-empty string
# (see the "generate" comments in .env.example for realistic ones):
#   openssl rand -hex 16   # for the *_PASSWORD vars
#   openssl rand -hex 64   # for SECRET_KEY_BASE
docker compose up -d
```

First-run notes:

- **Chatwoot**: visit `http://crm.<PUBLIC_IP>.nip.io` — you'll land on the
  account/admin onboarding wizard (create the first admin user and account).
- **Zammad**: visit `http://tickets.<PUBLIC_IP>.nip.io` — the setup wizard
  walks you through creating the first admin and organization.
- **Mailpit**: visit `http://mail.<PUBLIC_IP>.nip.io` to see all outbound
  mail from both apps (no real SMTP is configured; everything is caught
  locally).
- **Agent**: `http://agent.<PUBLIC_IP>.nip.io/healthz` should return
  `{"status": "ok"}` once the agent service and its dependencies are up.

## 4. GCE deploy

```bash
# 1. Provision the VM, static IP, and firewall rule (run from your workstation)
PROJECT_ID=<your-gcp-project> ./deploy/scripts/provision-gce.sh

# 2. Copy the app onto the VM (paths printed at the end of step 1)
gcloud compute scp --recurse deploy agent crm-ticketing:/tmp/platform \
  --zone=asia-southeast2-a --project=<your-gcp-project>
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=<your-gcp-project> \
  --command="sudo mkdir -p /opt/platform && sudo mv /tmp/platform/* /opt/platform/"

# 3. SSH in and bootstrap: installs Docker, adds swap, fills .env secrets,
#    detects PUBLIC_IP, and brings the whole stack up.
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=<your-gcp-project>
sudo /opt/platform/deploy/scripts/bootstrap-vm.sh
```

`bootstrap-vm.sh` prints the four `nip.io` URLs (crm/tickets/agent/mail) once
it's done. Give the containers a minute to finish starting before hitting
them.

## 5. Phase-2 wiring: Chatwoot ⇄ Zammad sync

Do this once both apps have completed their setup wizards.

### API tokens

- **Chatwoot**: log in → click your avatar (bottom-left) → **Profile
  Settings** → **Access Token** tab → copy the token into `CHATWOOT_API_TOKEN`
  in `.env`. For the platform-level token (needed to register the AI bot in
  Phase-3): **Super Admin console** (`/super_admin`) → **Platform Apps** →
  create/copy the **Platform Token** into `CHATWOOT_PLATFORM_TOKEN`.
- **Zammad**: log in as admin → avatar (top-right) → **Profile** → **Token
  Access** → generate a token with the permissions you need → copy into
  `ZAMMAD_API_TOKEN` in `.env`.

Restart the agent service after editing `.env`: `docker compose up -d agent`.

### Chatwoot → agent webhook

**Settings → Integrations → Webhooks** → **Add new webhook**:

- URL: `http://agent.<PUBLIC_IP>.nip.io/webhooks/chatwoot`
- Subscribe to events: `contact_created`, `contact_updated`,
  `conversation_updated`, `conversation_status_changed`,
  `conversation_resolved`

Set `CHATWOOT_WEBHOOK_SECRET` in `.env` to a random value and configure the
same value wherever Chatwoot lets you set a webhook signing secret (used to
verify the `X-Chatwoot-Signature` header).

### Zammad → agent webhook + trigger

**Admin → Manage → Webhook** → **Add Webhook**:

- Endpoint: `http://agent.<PUBLIC_IP>.nip.io/webhooks/zammad`
- Set a signature token — put the same value in `ZAMMAD_WEBHOOK_SECRET` in
  `.env` (verified via the `X-Hub-Signature` header).

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
platform API and `.env` populated with `CHATWOOT_PLATFORM_TOKEN`,
`CHATWOOT_API_TOKEN`, `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_URL`):

```bash
cd /opt/platform/agent
docker compose -f ../deploy/docker-compose.yml exec agent \
  python -m scripts.register_bot --inbox-id <your-chatwoot-inbox-id>
```

It creates a Chatwoot agent bot pointed at
`http://agent.<PUBLIC_IP>.nip.io/webhooks/chatwoot/bot`, assigns it to the
given inbox, and prints the bot's `access_token`/`secret` pair — copy those
into `.env` as `CHATWOOT_BOT_TOKEN` / `CHATWOOT_BOT_SECRET` and restart the
agent (`docker compose up -d agent`).

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
3. Update `FRONTEND_URL` in the Chatwoot environment block of
   `deploy/docker-compose.yml` (or via `.env` if you promote it to a
   variable) to `https://crm.example.com`.
4. `docker compose up -d` to apply — Caddy will request certificates on
   first request to each new hostname.

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
DATE=2026-07-01   # the backup date to restore

# Databases
docker compose exec -T postgres pg_restore -U postgres -d chatwoot --clean --if-exists < /backups/$DATE/chatwoot.dump
docker compose exec -T postgres pg_restore -U postgres -d zammad   --clean --if-exists < /backups/$DATE/zammad.dump
docker compose exec -T postgres pg_restore -U postgres -d agent    --clean --if-exists < /backups/$DATE/agent.dump

# Storage volumes (stop the consuming services first)
docker compose stop chatwoot-rails chatwoot-sidekiq
docker run --rm -v <project>_chatwoot_storage:/dest -v /backups/$DATE:/src alpine \
  sh -c "rm -rf /dest/* && tar xzf /src/chatwoot_storage.tar.gz -C /dest"
docker compose start chatwoot-rails chatwoot-sidekiq

docker compose stop zammad-railsserver zammad-scheduler zammad-websocket zammad-nginx
docker run --rm -v <project>_zammad_storage:/dest -v /backups/$DATE:/src alpine \
  sh -c "rm -rf /dest/* && tar xzf /src/zammad_storage.tar.gz -C /dest"
docker compose start zammad-railsserver zammad-scheduler zammad-websocket zammad-nginx
```

(`<project>` is the Compose project name, normally `deploy` — run
`docker volume ls` to confirm the exact volume names on your host.)
