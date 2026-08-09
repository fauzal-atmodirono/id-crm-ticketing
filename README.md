# Unified CRM Platform

Chatwoot (CRM/live chat) running behind a single Caddy reverse proxy, with a
small FastAPI **agent** service that adds a Gemini-powered AI layer on top
(auto-drafted replies, auto-escalation via Chatwoot human handoff). Zammad
(ticketing) has been fully removed (2026-08) — escalations stay in Chatwoot.

Each customer gets its own isolated Chatwoot + agent stack on its own subdomains; a shared Caddy, Postgres, and Mailpit back them all, running as Docker Compose on a single GCE VM.

## 1. Architecture

```
                                   Internet
                                      │
                                 80/443 (HTTP)
                                      │
                              ┌───────▼────────┐
                              │      Caddy      │  reverse proxy, nip.io vhosts
                              └───────┬────────┘
              ┌───────────────┬───────┴───────┐
              │               │               │
      crm.<ip>.nip.io  agent.<ip>.nip.io mail.<ip>.nip.io
              │               │               │
      ┌───────▼──────┐ ┌──────▼───────┐  ┌──────▼──────┐
      │   Chatwoot   │ │   agent      │  │   Mailpit   │
      │ rails+sidekiq│ │ (FastAPI)    │  │  (SMTP+UI)  │
      └───────┬──────┘ └──────┬───────┘  └─────────────┘
              │               │
              │   webhooks    │
              └──────────────►│
                              │
                     ┌────────▼────────┐
                     │ postgres (+pgvector) │  databases: chatwoot_<t>, agent_<t>
                     │ redis, memcached     │
                     └──────────────────────┘
```

Integration flows (implemented by the `agent` service):

- **Chatwoot → agent**: a webhook on contact/conversation events lets the
  agent send the EM-7 two-thread escalation email for Email-channel
  conversations tagged `escalate`, and stamp a `dealer_escalated_at`
  timestamp when a `dealer_<slug>` label is applied (for BI reporting). No
  data is mirrored to an external ticketing system — escalation stays
  entirely inside Chatwoot.
- **Chatwoot agent bot → agent → Gemini**: an AI bot subscribed to new
  incoming messages asks Gemini to draft a reply, escalate, or hand off to
  a human (`AGENT_MODE=suggest` by default — nothing is sent without human
  approval).

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
# One tenant may use the bare, un-prefixed hostnames (crm.<ip>.nip.io instead
# of <tenant>.crm.<ip>.nip.io) via --bare; the tenant named "default" gets this
# automatically:
./scripts/add-tenant.sh default        # served at crm/agent/mail.<ip>.nip.io
```

`add-tenant.sh` generates the tenant's secrets/DBs/Caddy route and brings its
stack up, then prints its three `nip.io` URLs. First visit to
`http://proton.crm.<PUBLIC_IP>.nip.io` lands on Chatwoot's onboarding wizard;
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

## 5. Phase-2 wiring: Chatwoot webhook

> **Per tenant.** Do this once per customer, using that tenant's subdomains
> (`<tenant>.crm.<ip>.nip.io`, `<tenant>.agent.<ip>.nip.io`) and editing that
> tenant's `deploy/tenants/<tenant>.env`. After editing the env, re-apply just
> the agent: `docker compose -p <tenant> -f docker-compose.tenant.yml
> --env-file tenants/<tenant>.env up -d agent`.

Do this once Chatwoot has completed its setup wizard.

### API tokens

- **Chatwoot**: log in → click your avatar (bottom-left) → **Profile
  Settings** → **Access Token** tab → copy the token into `CHATWOOT_API_TOKEN`
  in `tenants/<tenant>.env`. For the platform-level token (needed to register
  the AI bot in Phase-3): **Super Admin console** (`/super_admin`) →
  **Platform Apps** → create/copy the **Platform Token** into
  `CHATWOOT_PLATFORM_TOKEN`.

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

`AGENT_MODE` controls the Chatwoot-side bot's behavior: `suggest` (default)
drafts a private-note reply for a human to approve; `auto` sends replies
directly.

### Escalation routing (PIC / dealer contacts)

Which department contact or dealer email an escalated conversation notifies
is configured per tenant in the backend's **Escalation Routing** admin page
(Chatwoot fork patch `0039`, `GET/POST /admin/escalation/*` on the backend,
permission `escalation.manage`) — no redeploy needed to add or change a PIC
or dealer entry. The old path, setting `PIC_MAP_JSON` and
`DEALER_EMAIL_MAP_JSON` in `tenants/<tenant>.env`, still works as a fallback
for tenants that never touch the admin page, but the admin UI is the
preferred way to manage routing going forward.

### Turning on the working-hours SLA clock

**Read this before setting `SLA_WORKING_HOURS_ENABLED=true` on a live tenant.**

SLA *reporting* has always measured working hours; SLA *enforcement* measured
the wall clock. The two halves disagreed. This flag puts enforcement on the
same clock — which changes what your existing targets mean:

> With `SLA_WORKING_HOURS_ENABLED` on, an SLA target of "2 hours" means
> **2 working hours**, measured against that inbox's configured business hours.
> **Your existing configured targets do not need changing** — they were always
> intended as working-hours targets. A case that arrives at 18:00 on Friday
> will breach a 2-hour target on Monday morning, not on Friday evening.

Practical consequences:

- **Fewer breaches, later.** Nothing breaches *sooner* than it does today. If
  breach volume does not drop after enabling, the inbox has no working-hours
  config and is falling back to calendar minutes — check that first.
- **The inbox's working hours must be real.** An inbox with
  `working_hours_enabled` off behaves exactly as before, flag on or off.
  Appendix B's hours for PROTON are Mon–Fri 08:30–17:30 and Sat/Sun/public
  holidays 09:00–17:00; `deploy/scripts/appendix-b-after-hours-text.json`
  carries them alongside the customer-facing wording, and they must match, or
  the clock contradicts what the customer was told.
- **Per-inbox override.** The SLA Policies admin page's `working_hours_enabled`
  beats the global switch in both directions; unset inherits it.
- **Roll back by setting it to `false`.** The off path is byte-identical to the
  pre-P1 engine, asserted in `test_sla_clock.py`.

Two related flags, both independent and both default-off:
`SLA_ACKNOWLEDGEMENT_ENABLED` (backend) makes an explicit acknowledgement
satisfy the first-response SLA, and `ESCALATION_REPLY_ACKNOWLEDGEMENT_ENABLED`
(agent) is what records one when a PIC or dealer answers by email. Neither does
anything on its own — the first reads a signal the second writes.

### Escalation on every channel (P2)

Before this, applying the `escalate` label to a **WhatsApp, Web or Phone** case
notified nobody: the code returned early unless the inbox was `Channel::Email`.
The label stuck and the operator assumed it had worked. Turn it on with:

```bash
ESCALATION_ALL_CHANNELS_ENABLED=true                   # agent
ESCALATION_ACK_CHAT_TEMPLATE=<customer-facing text>    # backend; blank = post nothing
```

The customer acknowledgement picks its transport from the channel — mail on an
Email inbox, an **outgoing message in the thread** everywhere else, and nothing
at all on voice (the caller has already been spoken to). The PIC and dealer
legs never depended on the channel and are unaffected.

The chat acknowledgement is a normal public reply, never a private note. If you
see it land as a note, that is a bug — say so, because the customer then
receives nothing while the conversation looks handled.

Four independent additions, all default-off:

| Flag | What it does |
|---|---|
| `ESCALATION_CC_DEALER` | CC the dealer's `cc_emails` on the forward. Default *false* (unlike `ESCALATION_CC_PIC`) — this mail carries the full transcript outside the company. |
| `ESCALATION_ATTACHMENT_BUDGET_BYTES` | Attach the customer's photos/PDFs to the PIC and dealer mail. `0` = off, and off costs not even an API call. The customer ack never receives attachments. |
| `ESCALATION_FAILURE_NOTE_ENABLED` | Post a **private** note when a leg fails to send, so a failed escalation is not just a log line. |
| `ESCALATION_PRESENCE_CHECK_ENABLED` | Add an offline PIC's online colleagues to the recipients. Only ever widens. |

**Tier-2** now emails the department's `escalation_manager_email` instead of
re-pinging the same PIC. Set it per department via
`PUT /admin/escalation/pics/<dept>` — the Escalation Routing admin *page* has no
field for it yet (the fork patch is not written).

**Scope honesty for §4.39:** the failure note covers SMTP *send* failures only.
Bounces and invalid recipients need a bounce mailbox (client question Q6) and
are not handled — do not report §4.39 as closed.

### Changing the reporting timezone

`REPORTING_TIMEZONE` defaults to `UTC`, and that default emits **byte-identical**
view DDL to before it existed — so no dashboard number moves until you change
it deliberately.

Changing it **re-buckets every historical figure on every chart** the next time
`ensure_views()` runs. Totals stay the same; cases slide between adjacent days,
weeks and months (a case created 23:00 MYT is the *next* UTC day). That is why
it looks like "close but not quite" rather than an obvious error.

**Before switching a live tenant**, run the comparison and keep the output — it
is your evidence that Monday's movement was expected:

```bash
python3 scripts/compare-reporting-timezone.py \
  --project <bq-project> --dataset <bq-dataset> \
  --from 2026-07-01 --to 2026-07-31 \
  --to-timezone Asia/Kuala_Lumpur
```

It is read-only by construction — it refuses to run anything that is not a bare
SELECT, and its date window is parameterised, never interpolated.

Then set `REPORTING_TIMEZONE`, redeploy the backend, and re-run `ensure_views()`.
An unsupported zone is rejected at view-creation time, not at query time on a
dashboard.

**Change-record step, not optional:** attach the comparison output to the change
record, **schedule the switch at a month boundary** so the seam in any published
series falls at a natural break, and **tell the reporting team before, not
after**. They are the people who will otherwise spend a morning reconciling a
discrepancy that we created on purpose.

### Presenting the control-item slide

Nine of the fourteen control items render from real data. Five report
**"not measured"** with a stated reason: four need call-queue instrumentation
that does not exist (gap R9, 4-6 weeks), and the HQ-escalation row needs the
client to define what an HQ escalation is (question Q5).

**Those rows are deliberately blank rather than zero.** A zero would be a claim
about performance; a blank is a statement about instrumentation. If anyone
"tidies" them to 0 before a client meeting, the slide starts asserting a 0%
abandon rate on a platform with no call queue.

The endpoint returns all fourteen rows either way -- the client counts them
against the printed page -- and carries a note saying how many are measurable.

```bash
CONTROL_ITEMS_ENABLED=true
TARGETS_SEED_ENABLED=true    # creates only; never overwrites an operator edit
```

### Agent presence, custom statuses & the workforce dashboard (P6)

Seven flags, all default-off — but **not seven independent ones**; see the
dependency notes below before enabling any of them individually. With all seven
off the backend registers no scheduler job at all — not a poller that ticks and
finds nothing, which would still call Chatwoot once a minute on every tenant
that never asked for any of this.

```bash
PRESENCE_TRACKING_ENABLED=true          # the poller + the presence-event log
PRESENCE_CUSTOM_STATUSES_ENABLED=true   # selecting/editing the ten-status catalogue
PRESENCE_THRESHOLD_ALERTS_ENABLED=true  # the 10-minute / 1-hour alerts
ACW_ENABLED=true                        # After-Call-Work as a presence state
ROUTING_FAIR_SHARE_ENABLED=true         # least-loaded within a tier
ROUTING_SWEEP_ENABLED=true              # also needs ROUTING_ENABLED=true
FOLLOW_UP_DATE_ENABLED=true             # per-ticket follow-up date (agent svc)
```

`PRESENCE_TRACKING_ENABLED` is the primitive: it is what fills the
presence-event log every other flag reads. `GET /admin/workforce` (permission
`workforce.view`, Chatwoot fork patch `0053`) is only mounted when it is on —
with presence tracking off the dashboard would render a row per agent with
every presence field blank, which is honest but indistinguishable from a broken
page.

**The status catalogue is a dependency of three of the others, in two different
ways** — this is the one flag interaction worth reading twice, because the
failure modes are silent:

- **`PRESENCE_CUSTOM_STATUSES_ENABLED` is what makes the absence alerts able to
  fire at all.** It gates *selecting* a status: the four `/routing/presence`
  endpoints (and the "My status" page in fork patch `0054`) are only mounted
  when it is on, and off means a plain 404 there. The alerts only ever fire for
  the away-from-desk statuses an **agent picks** — Lunch, Break, Toilet, Prayer.
  Chatwoot's own native Busy and Offline deliberately never count as an absence
  (Busy is an agent working; Offline is an agent off shift — alerting on it would
  page an administrator after every logoff, every evening, per agent). So
  `PRESENCE_THRESHOLD_ALERTS_ENABLED=true` with this flag off correctly produces
  **no alerts whatsoever**: there is no way for anyone to record an absence in
  the first place. That is not a bug to go looking for.
- **`ACW_ENABLED` and the fair-share `routable` filter do *not* need this flag.**
  They read the catalogue rather than write to it, and a lookup falls back to the
  shipped definitions when a tenant's Firestore document was never seeded — so
  After-Call-Work works on an ACW-only tenant instead of logging
  `custom_status_set_unknown_key` on every completed call, which is what it used
  to do. A genuine store outage still resolves to "no extra information", and the
  native Chatwoot status stays the only thing that can ever *exclude* an agent
  from routing.

**The one thing to understand before showing this to an operator:**

> Custom statuses mirror into Chatwoot's native Online/Busy/Offline. Selecting
> "Lunch" shows as **Busy** inside Chatwoot's own UI and as **Lunch** on the
> workforce dashboard. This is deliberate: Chatwoot's presence field is a fixed
> enum, and mirroring means an agent is still correctly excluded from routing
> even if the custom-status service is unavailable.
>
> The "Availability history" column is derived from transitions to and from
> Offline. It is **not** a login/logout record — an agent who closes their
> laptop without going offline stays shown as available until their next
> transition.

Four more things the dashboard and the alerts do not claim, each of which a
reader would otherwise assume:

- **`cases_closed_today` is always blank, never `0`.** No helper can
  date-filter "resolved today" without an unbounded full-history scan on every
  ~30s poll. Same rule as the control-item slide above: a zero would be a claim
  about performance, a blank is a statement about instrumentation. The response
  carries `cases_closed_today_caveat` saying so.
- **The 1-hour alert's WIP list is scoped to `SLA_INBOX_IDS`** — the same inbox
  scope the SLA engine watches, *not* an account-wide audit. If a tenant routes
  agent chats through inboxes outside that scope, those open cases are invisible
  to the alert. Do not present it as exhaustive.
- **The follow-up date has no Chatwoot UI yet.** The backend and agent-service
  behaviour exist and are tested (a follow-up date provably never appears as an
  SLA breach), but the conversation-panel field needs P3's panel patch, which is
  not part of this work. `FOLLOW_UP_DATE_ENABLED` keeps it invisible until then.
- **Patches `0053-workforce-dashboard.patch` and `0054-agent-status-selector.patch`
  were hand-built and could not be verified against upstream Chatwoot** from the
  environment they were written in (no network to github). Their hunks are
  internally consistent and both apply to a synthetic tree; that is not proof
  either applies to the real fork, and `0054`'s hunks sit *on top of* `0053`'s
  own added lines, so if `0053` needs a line-number fix-up, `0054` needs the same
  one. **Validate both on the first Cloud Build**, before anyone plans a demo
  around the Workforce dashboard or the agent-facing "My status" page. Until that
  build is green, `PRESENCE_CUSTOM_STATUSES_ENABLED` on a tenant buys a working
  API with no UI in front of it.

Requirement 4.69 asks for After-Call-Work **and** average handling time. P6
delivers the ACW state only; AHT stays blocked on the missing call-queue
instrumentation (gap R9), the same gap that blanks four of the control items.

An agent can never be trapped out of routing by a status: ACW auto-exits after
`ACW_TIMEOUT_SECONDS`, and that timeout is derived from the stored event
timestamp rather than an in-process timer, so it survives a restart. The sweeper
that enforces it only bounds *how long detection takes*, not whether it happens.

## 7. Switching to a real domain later

The nip.io setup is HTTP-only and meant to get you running fast. To move to
a real domain with TLS:

1. Point your domain's DNS (A records) at the VM's static IP — e.g.
   `crm.example.com`, `agent.example.com`, `mail.example.com` (or drop
   Mailpit's public exposure entirely once you have real SMTP).
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

`deploy/scripts/backup.sh` iterates over every tenant in `deploy/tenants/*.env` and, for each, dumps its `chatwoot_<tenant>`, `zammad_<tenant>`, and `agent_<tenant>` Postgres databases (`pg_dump -Fc`) and archives its `<tenant>_chatwoot_storage` and `<tenant>_zammad_storage` volumes into `/backups/YYYY-MM-DD/`, then prunes backup
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
