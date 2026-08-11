# Unified CRM Platform

Chatwoot (CRM/live chat) running behind a single Caddy reverse proxy, with a
small FastAPI **agent** service that adds a Gemini-powered AI layer on top
(auto-drafted replies, auto-escalation via Chatwoot human handoff). Zammad
(ticketing) has been fully removed (2026-08) — escalations stay in Chatwoot.

Each customer gets its own isolated Chatwoot + agent stack on its own subdomains; a shared Caddy, Postgres, and Mailpit back them all, running as Docker Compose on a single GCE VM.

> ### Architecture scope — read before quoting an availability figure
>
> This platform runs on a **single GCE VM with Docker Compose**. There is no high
> availability, no failover and no second zone. **The 99.9% availability and P1
> `<2h` commitments in the RFP are not supportable on this architecture**; they
> require multi-zone HA (gap R17) and a 24/7 on-call rota, both of which are
> commercial decisions rather than engineering tasks.
>
> A restore script and an offsite backup copy now exist
> (`docs/runbooks/disaster-recovery.md`), but **no restore has been drilled and
> the RTO is unmeasured**, and the offsite copy is off until
> `BACKUP_GCS_BUCKET` is set — see §8. Recovery is not availability.
>
> **Operations runbooks:**
> [disaster recovery](docs/runbooks/disaster-recovery.md) ·
> [monitoring & alerts](docs/runbooks/monitoring-alerts.md) ·
> [data retention](docs/runbooks/data-retention.md) ·
> [environments & promotion](docs/runbooks/environments.md)

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
    backup.sh              per-tenant DB dumps + storage volume archives, + offsite copy
    restore.sh             verify-then-restore one tenant, dry-run by default
    archive-old-data.sh    archive+purge agent rows past the hot window
  gcs/                       GCS lifecycle policies (not applied to anything yet)
  monitoring/                Cloud Monitoring alert policies (not applied yet)
  chatwoot-fork/             patches + rebase.sh + PATCH-INVENTORY.md
agent/                  FastAPI integration + AI service (built by compose)
backend/                Vendored AI-assist conversational backend
docs/                   Design/planning docs; docs/runbooks/ = ops runbooks
```

Chatwoot itself is **not vendored here** — it is pulled as a Docker image and
patched at build time from `deploy/chatwoot-fork/patches/`. There is no `crm/`
directory in this checkout, so Chatwoot's own source cannot be read from it.

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

### AI conversational quality (P7)

Nine settings on the **backend** service, eight boolean and one weight, all
default-off or default-zero. With none of them set the platform behaves exactly
as it did before P7: sentiment stays unclassified, FAQ ranking is pure semantic
search, an attached photo gets today's generic instruction, and nothing is
auto-summarised or indexed when a case is resolved.

```bash
SENTIMENT_CLASSIFIER_ENABLED=true       # classify sentiment on the turn's existing call
SENTIMENT_TONE_ADJUSTMENT_ENABLED=true  # pick the reply's tone from that sentiment
TRANSLATION_ENABLED=true                # POST /assist/translate (inbound, private note)
TRANSLATION_OUTBOUND_TAMIL_ENABLED=false  # leave this alone — see below
FAQ_KEYWORD_WEIGHT=0.0                  # hybrid FAQ rank; 0.0 reproduces today exactly
FAQ_SUGGESTION_POPUP_ENABLED=true       # needs a fork image built with patch 0058 — see below
MEDIA_DIAGNOSIS_PROMPT_ENABLED=true     # diagnostic instruction when a photo/video arrives
RESOLVED_CASE_INDEX_ENABLED=true        # index resolved-case SUMMARIES into pgvector
AUTO_SUMMARY_ON_RESOLVE_ENABLED=true    # post that summary as a private note
```

**Before any of the sentiment/tone/media settings above do anything at all, the
tenant needs `CHAT_AGENT_ENABLED=true`** (default false), and the media
instruction additionally needs `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED`. Sentiment,
tone and media diagnosis all reach production through the backend `/chat/turn`
agent; a tenant with all eight P7 settings on and `CHAT_AGENT_ENABLED` off gets
none of them and no warning. This dependency is easy to miss because
`CHAT_AGENT_ENABLED` predates P7 and is not listed in the P7 block above.

`FAQ_KEYWORD_WEIGHT` is the one that is not a switch. **`0.0` is not merely
"off": it is the value that reproduces today's FAQ ordering *and today's scores*,
entry for entry**, which is the whole safety argument for shipping hybrid
ranking onto a live tenant. Raise it only on the strength of a calibration run —
and see the caveat below about what has not been measured.

A correction worth recording, because it is the shape of bug this programme kept
finding: **for its first several commits this tunable did nothing at any value.**
`FirestoreLiveFaqStore.search` passed the weight down but never the query string,
so the keyword signal had nothing to match and every weight produced today's
ranking. The unit tests missed it because they called the ranking function
directly and supplied the query themselves — a layer below the bug. It is fixed,
and there are now two tests that drive `search` itself rather than the ranker, so
the tunable is asserted where it is actually consumed.

Two dependencies rather than nine independent switches:

- `SENTIMENT_TONE_ADJUSTMENT_ENABLED` does nothing without
  `SENTIMENT_CLASSIFIER_ENABLED`: with no classifier there is no sentiment to
  select a tone from, and the bot keeps its static tone paragraph. Tone is
  re-composed per turn (not once per session), so the customer's *first* angry
  message is already answered in the measured register whenever the model
  classified that turn; a sentiment older than fifteen minutes is treated as
  stale, so an hour-old complaint does not make "thanks, all sorted" come back
  apologetic.
- `RESOLVED_CASE_INDEX_ENABLED` needs the pgvector KB (`KNOWLEDGE_PG_ENABLED` +
  `KNOWLEDGE_DATABASE_URL`), because the index is a table in that database. With
  the index on and the KB unconfigured, the backend logs
  `resolved_case_index_enabled_but_kb_not_configured` at boot and every resolve
  logs `resolved_case_index_no_repository` — it never fails the resolve.
  Resolving a case is the agent's action; the summary is an add-on.
  `AUTO_SUMMARY_ON_RESOLVE_ENABLED` is *independent* of it: the private note
  works without any database, and either flag can be on with the other off.

**Tamil.** Inbound Tamil translation — so an agent can read a Tamil message — is
enabled with `TRANSLATION_ENABLED`. **Outbound Tamil replies to customers remain
disabled** pending an evaluation of 30 real Tamil enquiries scored by a Tamil
speaker. Enabling `TRANSLATION_OUTBOUND_TAMIL_ENABLED` before that evaluation
sends unverified machine translation to customers. It is deliberately excluded
even from `deploy/scripts/check-suites-both-flag-states.sh`'s all-flags-on run,
and a test asserts it stays excluded — do not "complete" that list.

**Resolved-case suggestions** are generated from summaries of previously
resolved cases and are not approved guidance: a resolved-case summary is what a
colleague did last month. The index stores summaries rather than transcripts —
structurally, the stored record has no transcript field — and the summariser
prompt asks the model to omit customer names, phone numbers, email and home
addresses and plate numbers. **That mitigation is weaker than it sounds, in two
specific ways, and both matter before anyone calls it a PII control:**

- Nothing inspects, redacts or validates the summary before it is stored or
  posted. An instruction to a model is a request, not a mechanism, and a summary
  can still carry a name or a plate number if the model includes one.
- An operator's own persona **guardrails** are prepended *ahead* of that
  instruction in the same prompt, so a guardrail saying the opposite ("always
  include the customer's full name") is text the model may well prefer. Anyone
  with persona-edit access can therefore weaken the mitigation without touching
  code — not by deleting the sentence (nothing in the wiring removes it; the
  persona prefix is prepended and the summariser prompt survives verbatim) but by
  arguing with it.

Full PII masking is gap R16 and is blocked on Q7. `RESOLVED_CASE_INDEX_ENABLED`
defaults off for exactly this reason.

Five things this work does **not** deliver, each of which reads as delivered if
nobody says otherwise:

- **`FAQ_SUGGESTION_POPUP_ENABLED` is a fork-side switch and needs a rebuilt
  fork image before it does anything.** Nothing on the back end reads the
  field — the strip is entirely client-side and its only gate is
  `hasFeature('faq_suggestion_popup')`, read from `PROTON_FEATURES`. For most of
  P7 the two were genuinely independent switches (an earlier version of this
  section claiming otherwise was wrong, and was corrected). Fork patch
  `0058-feature-flag-unification.patch` plus the `docker-compose.tenant.yml`
  passthrough now make this variable populate that list, so **one setting is
  sufficient — on a stack whose Chatwoot image was built with 0058.** Fork patch
  `0056-faq-composer-apply.patch` is what adds the strip itself: dismissible, in
  the reply composer's top panel, gated on a confidence threshold (0.75, only
  `live_faq` hits from `GET /kb/suggest` carry a score) and re-using 0002's
  `protonAssistResult` bridge for its Apply button — the same mechanism
  `ReplyBox.vue` already writes the composer from, so the iframe-sandbox
  limitation the agent-app README describes is superseded for this one feature,
  not solved in general. Two things are still owed: (1) neither `0056` nor
  `0058` has been through a Cloud Build or applied to a real Chatwoot checkout —
  see the caveats in both patch headers and the register; and (2) until that
  image is built and pulled, a live tenant still has two switches and
  `faq_suggestion_popup` must be added to `PROTON_FEATURES` by hand. Adding it by
  hand keeps working afterwards — the union is additive, never subtractive.
  Agent-assist FAQ suggestions in the side panel are unaffected — they predate
  this and work as before.

- **The index is written and nothing reads it yet.** Summaries are stored and
  labelled `resolved_case` (distinct from the curated KB's own label), and a
  purge of the namespace provably cannot touch authored FAQs — but no suggestion
  panel queries it, so an agent sees no resolved-case suggestions today. The
  labelling machinery exists so that whichever surface adds them cannot present
  them with the curated KB's authority.
- **No calibration or corpus baseline was measured.** There are no real
  Gemini/Vertex credentials in the environment this was built in, so the four
  calibration sets, the Malay SMS corpus and their runners all ship with their
  numbers recorded as `TBD — unmeasured` rather than invented. The stub runs
  score 97–100%, and that figure means nothing: the same author wrote both the
  ground-truth labels and the naive keyword stub being scored. Never quote it.
- **The Malay query normaliser ships switched off**
  (`NORMALISE_RETRIEVAL_QUERY_ENABLED`, a module constant in
  `nlu_normalise.py`, not an env var). Its acceptance gate is "only ship it if
  it measurably improves the corpus pass rate", and that rate has never been
  measured for real. Note also that `kb_suggest_router.py` has a second,
  pre-existing live-FAQ retrieval path that bypasses the normaliser entirely, so
  even switched on it would not cover every query.
- **The media-diagnosis prompt has never been in front of the real model with a
  real photo.** No WhatsApp number and no real credentials here.
  `docs/testing/2026-08-09-media-diagnosis-prompt-live-check.md` is a template
  awaiting that run, not a result.

Fork patch `0055-translate-action.patch` adds the agent-facing **Translate**
button and, like `0053`/`0054`, was **hand-built and never verified against
upstream Chatwoot** (no network to github from the build environment). It is the
lower-risk of the three: every context line comes from the already-merged `0002`
patch rather than from unverified upstream lines, and it stacks on nothing.
Validate it on the first Cloud Build before demonstrating the button. The
backend endpoint is mounted and independently testable regardless.

### AI & agent measurement (P8)

Six settings on the **backend** service, four boolean and two tunable, all
default-off or default-zero. With none of them set the platform behaves exactly
as it did before P8: no Gemini call is metered, `GET /metrics/ai-cost` answers
404, no NPS question is ever asked, CSAT stays channel-level only (`v_csat` is
byte-for-byte unchanged, pinned as a literal string by a test), and QA stays
today's channel-agnostic manual rubric.

```bash
TOKEN_METERING_ENABLED=true      # record a token_usage row per Gemini call
AI_COST_REPORTING_ENABLED=true   # GET /metrics/ai-cost + the v_ai_cost views
NPS_SAMPLE_RATE=1.0              # share of surveys asking NPS INSTEAD of CSAT; 0.0 = never
CSAT_BY_AGENT_ENABLED=true       # mounts v_csat_by_agent beside the unchanged v_csat
CSAT_RANKING_MIN_SAMPLES=10      # ratings needed before an agent is *ranked* (not listed)
CALL_QA_ENABLED=true             # channel dimension + the five-criterion call rubric
```

All six are in `deploy/scripts/check-suites-both-flag-states.sh`'s all-on run
and `src/chatbot/test_p8_flags.py` asserts they are, so the list is not
maintained from memory. The two tunables are in that array at **non-default**
values (`1.0`, `25`) on purpose — a tunable listed at its own default makes the
on-run walk the identical path to the off-run. Adding this block turned that
script red on five tests, four of them CSAT-path tests that had been reading
`NPS_SAMPLE_RATE` from the ambient environment and one bare-`Settings()`
defaults test; a sixth, `test_faq_hybrid_rank`'s default-weight test, had left
the gate red since P7 for the same reason. All six are fixed.

**Two migrations are owed before two of the flags can be switched on** — this
repo has no Alembic and `create_table(exists_ok=True)` does not add columns to
an existing table:

```sql
-- against each tenant's AGENT_DATABASE_URL, before TOKEN_METERING_ENABLED
ALTER TABLE ai_actions ADD COLUMN IF NOT EXISTS output_tokens INTEGER;
ALTER TABLE ai_actions ADD COLUMN IF NOT EXISTS cached_tokens INTEGER;
-- against each tenant's BigQuery dataset, before CALL_QA_ENABLED
-- (channel STRING, five rubric_* BOOL, call_qa_percentage FLOAT64 — the exact
--  statement is in features/metrics/qa_schema.py's module docstring)
```

Then the eleven new warehouse views have to be created: `ensure_views(settings)`
per tenant covers seven of them (`v_csat_by_agent` only when its flag is on),
`ai_cost_view_ddls`' three have **no runtime caller** and must be created by hand
*after* the `token_usage` table exists, and `v_kb_staleness` cannot be created at
all (below). Task 7's `v_call_qa` is the exception — `qa_view_ddls` *is* called,
by `BigQueryQaLabels` on init, so it appears on its own once the QA adapter runs. Until then
`GET /metrics/ai-cost` returns `read_status: "unavailable"` rather than a
confident `0.00`, which is deliberate: with no warehouse there is no evidence of
zero spend. Full sequence in
`docs/analysis/2026-08-09-blocked-work-register.md` §§3c-1, 3c-2, 3c-3.

**What this package does not measure.** These figures exist to be defended in a
monthly review, so the gaps are stated here rather than found later:

- **`chat.turn` — the busiest AI surface in the product — is not metered at
  all, and cannot be from where we stand.** google-adk takes a model *string*
  and constructs its own `google.genai.Client` inside the installed package, so
  no wrapper at our client boundary can see it. The `service.py` client that
  *is* wrapped only transcribes, and its rows are labelled `chat.transcribe`
  precisely so a transcription's token count is never read as the bot's. This is
  an architectural limit of ADK, not an oversight, and it is the single reason
  the cost report must not be read as a bill.
- **`phone.live` is not metered**: the Live API reports usage in server
  messages, not on a response. `connect_live` is routed through the wrapper for
  the structural guarantee only.
- **`embed` is visible but unpriceable.** Embeddings bill per character and
  `EmbedContentResponse` carries no `usage_metadata`, so all three counts are
  `None` by construction. The price table has a per-character class, but
  `token_usage` has no character-count column, so an end-to-end embedding cost
  is not computable even with a rate on file.
- **Thinking-model tokens are billed and not captured.**
  `thoughts_token_count` and `tool_use_prompt_token_count` fall outside the
  three recorded classes, so those three sum to **less than**
  `total_token_count` — the five priced surfaces are understated too, and
  `completeness.excluded_token_classes` says so on the payload.
- **There is deliberately no total.** The only money figure is
  `priced_subtotal_usd`, and a test asserts the *absence* of `total`, `total_*`
  and `*_total`, so a future tidy-up into a headline figure breaks the build.
  Unmetered surfaces are rows with `cost_usd: null` **and** `calls: null` — a
  `0` would claim the surface is free, and an absent row would claim the
  inventory is complete.
- **`resolved_by` cannot distinguish AI from human.** `mapping.py` derives it
  from Chatwoot `status` alone, so `resolved_by='bot'` means "resolved". All
  five AI-performance views are pinned off it by
  `test_no_ai_report_is_built_on_resolved_by`; human involvement is inferred
  instead from assignment, the `escalate` label and `escalated_to`, and each
  view carries that basis as a column. What it still cannot see is a human who
  replied without ever being assigned (`first_reply_created_at` is set by
  agent-bot replies too). Consequently `v_resolution_split`'s live
  `closed_by_bot` / `transfer_to_agent` column names are **misleading** — those
  numbers are resolved and not-yet-resolved — and are left alone because
  renaming them breaks existing dashboards. The client guide's Reports chapter
  now says so as well; it previously described that tile as a bot-vs-agent
  split, which it is not.
- **Deflection means resolved with no agent message at all.** A conversation
  the bot answered before a human took over is *not* deflected. The definition
  travels as a column on `v_ai_deflection` because two reasonable definitions
  differ by roughly a factor of two.
- **No report cuts by sentiment.** P7's sentiment is a Chatwoot custom
  attribute that `mapping.py` never reads into `ConversationRow`, and this
  repo's own `test_every_schema_column_is_either_a_row_field_or_explicitly_sync_
  only` correctly forbids a warehouse column nothing populates. Prerequisites in
  order are in the register; when it lands, unclassified must be its own bucket
  and **never** `neutral`.
- **`v_kb_staleness` returns nothing.** It reads a `faq_entries` table that does
  not exist and that nothing populates (`faq_feedback` records feedback, not
  serves, and has no edit timestamp). It is kept deliberately — declared
  unreachable in its module docstring, asserted by
  `test_the_missing_faq_entries_loader_is_stated_not_implied`, and carried in
  the register — because it becomes correct the moment a snapshot loader exists.
  **The thing that must not happen is a dashboard built on it in the belief that
  an empty result means a healthy KB.** Found alongside it: `faq_view_ddls` has
  no runtime caller either, so `v_faq_quality` is not created by any deploy path
  today (pre-existing).
- **Call QA is manual by design, not by omission.** Nothing reads a transcript
  or calls a model to score a call; every one of the five criteria traces to a
  human-submitted field on `POST /qa/label`. The phone transcript path has never
  run against a real Twilio call, so an automated scorer built on it would be
  confident noise. A partly-scored rubric reports `incomplete`, never a low
  percentage.
- **⑦ AI Root Cause Analysis and ⑧ KB Improvement recommendations are not
  built**, in code, docs or comments — a test greps every view for "root cause"
  and "recommend". Both need a model to summarise failure patterns across many
  cases: their own package. AI Accuracy is answered by P7's calibration baseline
  as a measured figure, which is itself still `TBD — unmeasured`.
- **The web live-chat widget's survey is left unsampled.** It calls
  `/chat/csat` / `/chat/nps` from its own UI, so NPS sampling covers WhatsApp,
  email and phone only. `should_survey_nps` is exported and ready for a frontend
  wiring task.
- **Nothing here was validated against real BigQuery, Gemini, Twilio or
  Postgres** — no such credentials exist in the environment it was built in.
  Every figure is produced by code unit-tested against recorded and synthetic
  usage-metadata shapes and in-memory fakes.

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

**Full procedure, including the disaster case:
[`docs/runbooks/disaster-recovery.md`](docs/runbooks/disaster-recovery.md).**
This section is the summary.

`deploy/scripts/backup.sh` iterates over every tenant in `deploy/tenants/*.env`
and, for each, dumps its `chatwoot_<tenant>`, `agent_<tenant>` and
`backend_<tenant>` Postgres databases (`pg_dump -Fc`), archives its
`<tenant>_chatwoot_storage` volume into `/backups/YYYY-MM-DD/`, writes a
`SHA256SUMS` manifest plus a per-tenant row-count file, prunes backup directories
older than 7 days, and — **only if `BACKUP_GCS_BUCKET` is set** — syncs the
night's directory to GCS and verifies the copy.

> **Two things this section used to claim and should not have.** It listed
> `zammad_<tenant>` and `<tenant>_zammad_storage`; Zammad was fully removed in
> 2026-08 and `backup.sh` has not touched either for some time, so the restore
> loop below would have failed on a database that does not exist. And
> `backend_<tenant>` — the operator-authored knowledge base and the RBAC tables —
> was created by `add-tenant.sh` but **never dumped** until 2026-08-11, so
> **archives older than that cannot restore it.**

Install as a nightly cron job (as root, or a user with docker access). Set the
bucket **in the cron line**; cron does not inherit a variable you exported in a
shell:

```
0 3 * * * BACKUP_GCS_BUCKET=<bucket> /opt/platform/deploy/scripts/backup.sh >> /var/log/platform-backup.log 2>&1
```

**With `BACKUP_GCS_BUCKET` unset, backups exist only on the VM they protect** —
losing the VM loses the data and its backups together. Creating the bucket is §2
of the DR runbook, and **it has not been created**.

### Restore

Use `deploy/scripts/restore.sh` rather than a hand-typed `pg_restore` loop. It
verifies the archive before it drops anything, can restore **into a different
tenant** (which is what makes a drill possible without touching production), and
is a **dry run unless you pass `--apply`**:

```bash
cd /opt/platform/deploy

# Dry run: verifies checksums and that every dump parses. Changes nothing.
./scripts/restore.sh --tenant proton --date 2026-08-10

# Do it (you will be asked to type the destination tenant name back):
./scripts/restore.sh --tenant proton --date 2026-08-10 --apply

# Restore production's backup into a scratch tenant, from the offsite copy —
# this is the drill, and the only safe way to practise:
./scripts/restore.sh --tenant proton --date 2026-08-10 --into drill --from-gcs --apply
```

`./scripts/restore.sh --help` lists every flag. The script's header states
exactly what `--apply` overwrites.

> **No restore has ever been executed and the RTO is unmeasured.** The script was
> exercised only against stub `docker`/`gsutil` commands. Treat the first real run
> as a first run — see the DR runbook §7, and
> `docs/analysis/2026-08-09-blocked-work-register.md` §3c-4.

### Retention and archival

`deploy/scripts/archive-old-data.sh` moves `ai_actions` and
`processed_deliveries` rows past `ARCHIVE_HOT_WINDOW_DAYS` (default 730) into GCS
as NDJSON plus a manifest, then purges them — dry run unless `--apply`. What is
and is not retained, and the recordings-versus-7-years conflict that is still an
open question with the client, is in
[`docs/runbooks/data-retention.md`](docs/runbooks/data-retention.md).

### Monitoring

**There is no monitoring stack and no alert reaches a human today**; the first
indication of a problem is a customer complaint. What can be observed now, and
what an operator has to add, is in
[`docs/runbooks/monitoring-alerts.md`](docs/runbooks/monitoring-alerts.md).
