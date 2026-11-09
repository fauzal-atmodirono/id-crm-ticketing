# Runbook — mirroring the `aeon360` tenant to AEON360's prod project

**Date:** 2026-08-28 · **Owner:** Yuda
**Source:** Devoteam VM `crm-ticketing` (`asia-southeast2-a`), tenant `aeon360`
**Destination:** `gce-prod-innovation-svc-ai-backend-crm` (`prj-prod-innovation-svc-2p`, `asia-southeast1-a`), internal IP `10.94.0.4`

Decision (2026-08-28): **mirror everything**, conversations included — the
source content is our own test data, so there is nothing to filter.

---

## STATUS — COMPLETED 2026-08-28

The migration described below was executed end to end. Result:

| Step | Outcome |
|---|---|
| Prerequisites (§1) | Registry read grant, `PUBLIC_IP`, Firestore `aeon360-db` in `asia-southeast1` — all done |
| Tenant provisioned (§2) | 6 containers, image `crm-chatwoot:v4.15.1-custom` (renamed from `proton-chatwoot`) |
| Dump + restore (§3–§5) | 6 users, 2 inboxes, 12 conversations, 162 messages, 3 labels, 1 agent bot, 7 attachment files |
| `SECRET_KEY_BASE` (§6) | Carried across before first Rails boot |
| Firestore (§7) | **Method changed — see below.** 14 config docs copied |
| Vertex AI Search KB (§8) | **Nothing to migrate — the source datastore was never populated** |
| Post-restore URLs (§9) | Agent bot already pointed at AEON360's own WABA; no repointing needed |
| Domain | `innovation-hub.aeon360.com.my` mapped and verified |

### Corrections to the plan below, learned by doing

1. **§7's GCS export/import was not used and is not the right method.** The
   source database holds **64,921 `presence_events`** — telemetry from our own
   testing — against just **14 documents** of actual configuration
   (`platform_config`, `assistants`, `custom_statuses`, `inbox_timing`). A
   Firestore export/import copies everything, and needs a cross-project bucket
   IAM grant we could not make. Copying the four config collections directly
   with a small script avoided the junk and the IAM problem entirely. The
   scripts are `fs-export.py` / `fs-import.py`; the exporter skips
   `presence_events` by name.
2. **§8 is moot.** The source's `aeon360-kb` datastore and its pgvector KB were
   both empty (`kb_documents` = 0, `kb_chunks` = 0). There was never any
   knowledge content on this tenant. Creating a datastore in AEON360's project
   is a task for when they supply content, not a migration step.
3. **§9's premise was wrong in the useful direction.** The agent bot's
   `outgoing_url` was already `https://innovation.dev.aeon360.net/...` —
   AEON360's own WABA endpoint, not a Devoteam hostname. It needs no change,
   though it points at their *dev* WABA and a prod endpoint may be wanted.
4. **Two repo bugs surfaced and were fixed at source**, both of which would have
   hit the next tenant too:
   - `deploy/tenants/example.env` shipped `RBAC_BOOTSTRAP_ADMIN_USER_ID=` blank.
     Pydantic cannot parse an empty string into `int | None`, so the backend
     crash-loops on any correctly provisioned tenant. Already recorded as a known
     bug in the bahana runbook; now actually commented out.
   - `deploy/docker-compose.tenant.yml` hardcoded `CHATWOOT_PUBLIC_URL` /
     `AGENT_PUBLIC_URL` to the nip.io pattern with no override, which is
     unusable behind a load balancer where the VM has no public IP. Both now
     take `*_OVERRIDE` variables, defaults unchanged.
5. **`AGENT_MODE` deliberately diverges.** Source runs `auto`; prod was set to
   `suggest` so AI replies land as private notes for human approval. This tenant
   carries a live WhatsApp number and a sent message cannot be recalled. Flip it
   deliberately, not by inheritance.

### Post-cutover, 2026-08-29 — the CRM is live

AEON360's infra team attached the NEG; `https://innovation-hub.aeon360.com.my`
now serves the CRM end to end. Four things were fixed after the tenant was
already provisioned, three of which `add-tenant.sh` will hand to anyone who
provisions again.

**1. `PROTON_BACKEND_KEY` / `METRICS_API_KEY` are never generated.** This is
the nastiest of the three, because it presents as a broken product rather than
missing config. `add-tenant.sh` leaves both blank, so the Rails layout injects
`backendKey: ""` into the SPA, and every gated backend call — Knowledge, AI
assist, RBAC, reporting — returns 401 on a stack that is otherwise perfect.
Fixed by generating fresh 64-char values on the tenant:

```bash
openssl rand -hex 32   # PROTON_BACKEND_KEY
openssl rand -hex 32   # METRICS_API_KEY
```

Deliberately **generated, not copied from the source tenant**: they are shared
only between the SPA, agent and backend of one tenant, all reading the same env
file, so each environment should hold its own.

**2. The Redis onboarding flag survives a Postgres restore.**
`add-tenant.sh` runs `db:chatwoot_prepare` against an empty database, which sets
`CHATWOOT_INSTALLATION_ONBOARDING=true` in **Redis**. Restoring users into
**Postgres** does not clear it, so Chatwoot redirects every visitor to the
install wizard and the CRM looks empty — as though the restore silently failed
when it in fact succeeded. Clear it after any restore-into-a-fresh-tenant:

```bash
docker exec <tenant>-chatwoot-rails bundle exec rails runner \
  'Redis::Alfred.delete(Redis::Alfred::CHATWOOT_INSTALLATION_ONBOARDING)'
```

**3. `RBAC_DATABASE_URL` / `RSA_DATABASE_URL` are never rewritten.** Only
`KNOWLEDGE_DATABASE_URL` is. Left blank, RBAC and RSA bind to nothing and their
restored tables are unreachable. Both should point at the tenant's backend DB.

**4. A pre-existing Firestore index bug, not a migration artefact.**
`presence_store.py`'s `latest()` issues
`where(agent_id ==).order_by(at DESC).limit(n)`, and its docstring claimed that
shape was exempt from composite indexing. It is not. Every call raised
`400 The query requires an index`, the fail-open `except` swallowed it into
`None`, and `RoutingService._is_routable` read that as "presence unknown" — so
presence-aware routing silently degraded to no-presence routing on **both**
tenants, with nothing visible in the UI. Fixed by provisioning the index on each
database and correcting the docstring (commit `b5050f0`):

```bash
gcloud firestore indexes composite create \
  --collection-group=presence_events \
  --field-config=field-path=agent_id,order=ascending \
  --field-config=field-path=at,order=descending \
  --database=aeon360-db --project=<project>
```

Verified on both: `presence_store_latest_failed` dropped to zero and `latest()`
returns rows.

### Domain mapping, as built

One hostname serves all three services, because AEON360 provided one. Caddy
splits by path in `caddy/tenants/aeon360-domain.caddy`:

| Path | Goes to |
|---|---|
| `/healthz` | Caddy itself (200 `ok`) — also on the catch-all vhost, deliberately duplicated so a health check that sends the real `Host` still works |
| `/webhooks/chatwoot`, `/webhooks/chatwoot/*`, `/apps/*` | agent `:8000` |
| `/metrics/* /kb/* /assist/* /routing/* /authz/* /admin/* /rsa/* /voice/* /alerts/* /calls/*` | backend `:8080` |
| everything else | chatwoot-rails `:3000` |

**The agent matcher is deliberately narrow.** Chatwoot serves
`/webhooks/twilio`, `/webhooks/whatsapp/<number>` and friends for inbound
channel delivery; a blanket `/webhooks/*` would swallow them and lose every
inbound message with no error on either side. Verified: `/webhooks/twilio` and
`/twilio/callback` both reach Chatwoot.

TLS terminates at the GLB (`34.54.12.27`), so the vhost is plain `http://` and
Caddy never attempts ACME — it could not anyway, the VM has no public IP.

### Still outstanding

All of these need a decision or an input rather than engineering work. Nothing
is half-applied.

- **SMTP.** Deliberately not ported — the destination has no credentials, so
  mail falls back to Mailpit. `SMTP_PORT=587` from the source would have pointed
  Chatwoot at Mailpit on the wrong port and failed silently. Needs a relay host,
  port, username, password and sender address.
- **A dedicated escalation mailbox**, if email escalation threading is wanted.
  It must not be shared with another tenant — the threading keys off
  conversation ids and two tenants sharing one mailbox collide.
- **Vertex AI Search datastore** in AEON360's project, once they supply
  knowledge content. `VERTEX_SEARCH_*` is intentionally blank until then;
  `KNOWLEDGE_PG_ENABLED=true` already serves the pgvector half.
- **`AGENT_MODE`** is `suggest`. Flip to `auto` deliberately when the AI replies
  have been reviewed in practice.
- **Prod WABA endpoint.** The agent bot points at
  `innovation.dev.aeon360.net` — AEON360's *dev* WABA. Confirm whether a prod
  endpoint should replace it.
- **The Dev environment.** `prj-dev-innovation-svc-8e` has a VM (`10.90.0.3`)
  but no CRM. If it is provisioned, expect the same four post-cutover items
  above — they are properties of `add-tenant.sh`, not of this migration.

### Resolved — no longer outstanding

- ~~NEG + health-check firewall~~ — done by AEON360's infra team 2026-08-29.
  `https://innovation-hub.aeon360.com.my` serves the CRM; the earlier
  `503 no healthy upstream` is gone.
- ~~IAP access to port 80~~ — was requested so we could verify over a tunnel.
  Moot now the site is publicly reachable. Port 22 IAP remains available for
  operations.

---

## 0. Why this does not use `backup.sh` / `restore.sh`

Both scripts exist and are well-guarded, but:

- `backup.sh` has **never been run against a real VM** (its own header says so).
- It backs up **every** tenant, including `proton` — the live customer — and
  writes one `SHA256SUMS` manifest covering all of them. Copying only the
  `aeon360` files to another host leaves `restore.sh` verifying a manifest whose
  other entries are absent.
- `restore.sh`'s safety properties are mostly about **not destroying a
  populated destination**. Our destination is empty, so they buy little here.

Debugging our backup tooling for the first time *during* a customer migration is
the wrong place to find out it needs work. This runbook does a targeted dump and
restore instead. **Exercising `backup.sh` properly is still worth doing — just
separately, on its own.**

---

## 1. Prerequisites on the destination (must be done first)

These gate `add-tenant.sh`. All three need a human — the assistant's tooling
blocks IAM changes, prod env edits and token minting.

```bash
# 1a. Let the VM's service account pull the CRM image
gcloud artifacts repositories add-iam-policy-binding aeon360 \
  --location=asia-southeast1 --project=prj-prod-innovation-svc-2p \
  --member=serviceAccount:sa-crm-backend@prj-prod-innovation-svc-2p.iam.gserviceaccount.com \
  --role=roles/artifactregistry.reader \
  --impersonate-service-account=sa-ci-deploy@prj-prod-innovation-svc-2p.iam.gserviceaccount.com

# 1b. Placeholder hostname base (real domain comes after the LB is provisioned)
gcloud compute ssh gce-prod-innovation-svc-ai-backend-crm \
  --project=prj-prod-innovation-svc-2p --zone=asia-southeast1-a \
  --tunnel-through-iap --strict-host-key-checking=no \
  --command='cd /opt/platform/deploy && sudo sed -i "s|^PUBLIC_IP=.*|PUBLIC_IP=10-94-0-4|" infra.env && grep ^PUBLIC_IP infra.env'

# 1c. Firestore database — SINGAPORE. THE LOCATION IS PERMANENT.
gcloud firestore databases create --database=aeon360-db \
  --location=asia-southeast1 --type=firestore-native \
  --project=prj-prod-innovation-svc-2p
```

> **1c is the one that cannot be undone.** `add-tenant.sh` defaults
> `FIRESTORE_LOCATION` to `asia-southeast2` (Jakarta), which is wrong for
> AEON360. Creating the database explicitly in `asia-southeast1` first means the
> script finds it, checks the location matches, and skips creation. It also
> avoids the VM's `gcloud` running as `sa-crm-backend`, which lacks
> `datastore.databases.create`.

---

## 2. Provision the empty tenant on the destination

```bash
gcloud compute ssh gce-prod-innovation-svc-ai-backend-crm \
  --project=prj-prod-innovation-svc-2p --zone=asia-southeast1-a \
  --tunnel-through-iap --strict-host-key-checking=no \
  --command='cd /opt/platform/deploy && sudo env \
    CHATWOOT_IMAGE_PIN=asia-southeast1-docker.pkg.dev/prj-prod-innovation-svc-2p/aeon360/crm-chatwoot:v4.15.1-custom \
    FIRESTORE_PROJECT_ID_VALUE=prj-prod-innovation-svc-2p \
    FIRESTORE_LOCATION=asia-southeast1 \
    ./scripts/add-tenant.sh aeon360'
```

**`CHATWOOT_IMAGE_PIN` is not optional.** The script's built-in default points at
Devoteam's registry, which this VM cannot reach. Omit it and compose falls back
to upstream `chatwoot/chatwoot`, producing a CRM that looks fine but has no
Knowledge, RBAC, SLA or reporting pages.

**Keep the tenant name `aeon360` on both sides.** The dumps reference roles
`chatwoot_aeon360` / `agent_aeon360` / `backend_aeon360` by name; identical names
mean ownership maps with no rewriting.

If this fails partway it leaves `tenants/aeon360.env` behind, which blocks
re-runs. Clean up with `sudo ./scripts/remove-tenant.sh aeon360 --purge-volumes`
— deleting the env file alone orphans the Postgres roles and databases.

---

## 3. Dump the source

On the Devoteam VM (`crm-ticketing`, zone `asia-southeast2-a`):

```bash
sudo mkdir -p /tmp/aeon360-mig
for db in chatwoot agent backend; do
  sudo docker exec platform-infra-postgres-1 \
    pg_dump -U postgres -Fc "${db}_aeon360" > "/tmp/aeon360-mig/aeon360-${db}.dump"
done

# Chatwoot attachment storage (compose project `aeon360`, volume `chatwoot_storage`)
sudo docker run --rm \
  -v aeon360_chatwoot_storage:/src -v /tmp/aeon360-mig:/dest \
  alpine tar czf /dest/aeon360-storage.tar.gz -C /src .

ls -lh /tmp/aeon360-mig/
```

Sanity-check each dump parses before moving it:

```bash
for f in /tmp/aeon360-mig/*.dump; do
  echo "== $f"; sudo docker exec -i platform-infra-postgres-1 pg_restore --list < "$f" | wc -l
done
```

A dump that fails `--list` is truncated. Re-dump rather than carrying it across.

---

## 4. Transfer

There is no direct route between the two VMs, so it goes via the workstation.

```bash
gcloud compute scp --recurse crm-ticketing:/tmp/aeon360-mig ./aeon360-mig \
  --zone=asia-southeast2-a

gcloud compute scp --recurse ./aeon360-mig \
  gce-prod-innovation-svc-ai-backend-crm:/tmp/aeon360-mig \
  --project=prj-prod-innovation-svc-2p --zone=asia-southeast1-a \
  --tunnel-through-iap
```

The second hop runs over the IAP tunnel and is slow. Install NumPy locally
(`pip install numpy`) if the archive is large — gcloud warns about this on every
tunnelled transfer and it is a real speed difference.

---

## 5. Restore into the destination

All on the destination VM. **Stop the app containers first** — restoring under a
live Rails process gives a half-migrated database.

```bash
cd /opt/platform/deploy
sudo docker compose -p aeon360 -f docker-compose.tenant.yml \
  --env-file tenants/aeon360.env stop chatwoot-rails chatwoot-sidekiq agent backend

for db in chatwoot agent backend; do
  sudo docker exec -i platform-infra-postgres-1 \
    pg_restore -U postgres -d "${db}_aeon360" --clean --if-exists --no-owner \
    --role="${db}_aeon360" < "/tmp/aeon360-mig/aeon360-${db}.dump"
done

# Attachments — replace the volume contents wholesale
sudo docker run --rm \
  -v aeon360_chatwoot_storage:/dst -v /tmp/aeon360-mig:/src \
  alpine sh -c 'rm -rf /dst/* /dst/..?* 2>/dev/null; tar xzf /src/aeon360-storage.tar.gz -C /dst'
```

`pg_restore` prints warnings about dropping objects that do not exist — expected
with `--clean --if-exists` against a freshly created schema. Errors mentioning
missing **roles** are not expected and mean the tenant name diverged.

---

## 6. Carry `SECRET_KEY_BASE` across — do not skip

`add-tenant.sh` generates a fresh `SECRET_KEY_BASE`. The restored database came
from a different one. At minimum that invalidates every session and cookie; if
any Chatwoot column encryption is in play it is the difference between data
being readable and silently failing to decrypt.

Copy it without printing it to the terminal:

```bash
SKB=$(gcloud compute ssh crm-ticketing --zone=asia-southeast2-a \
  --command='sudo grep ^SECRET_KEY_BASE= /opt/platform/deploy/tenants/aeon360.env' 2>/dev/null | tr -d "\r")

gcloud compute ssh gce-prod-innovation-svc-ai-backend-crm \
  --project=prj-prod-innovation-svc-2p --zone=asia-southeast1-a \
  --tunnel-through-iap --strict-host-key-checking=no \
  --command="cd /opt/platform/deploy && sudo sed -i 's|^SECRET_KEY_BASE=.*|${SKB}|' tenants/aeon360.env"

unset SKB
```

Then start the stack:

```bash
sudo docker compose -p aeon360 -f docker-compose.tenant.yml \
  --env-file tenants/aeon360.env up -d
```

---

## 7. Firestore — separate migration, not in the dumps

The custom-feature switchboard, PIC/dealer escalation routing and the term
dictionary live in Firestore, not Postgres. Without this step the CRM comes up
with its feature switchboard empty.

```bash
# Export from Devoteam's project
gcloud firestore export gs://<a-bucket-we-own>/aeon360-fs-$(date +%F) \
  --database=aeon360-db --project=lv-playground-genai

# Import into AEON360's — the destination service account needs read on the bucket
gcloud firestore import gs://<a-bucket-we-own>/aeon360-fs-<date> \
  --database=aeon360-db --project=prj-prod-innovation-svc-2p
```

Cross-project import requires AEON360's Firestore service agent to have
`roles/storage.objectViewer` on the source bucket. If that grant is refused, the
fallback is re-entering the switchboard and routing config by hand through the
CRM admin pages — tedious but not large.

---

## 8. Vertex AI Search knowledge base — re-ingest, not copy

Datastore contents are not exportable as a dump. The documents must be ingested
again into `aeon360-kb` in AEON360's project, from the original source files.

The pgvector KB in `backend_aeon360` **does** transfer with the Postgres dump in
§5 — only the Vertex AI Search corpus needs redoing.

---

## 9. Post-restore fixes — the restored data points at the old host

The dump faithfully carries URLs from the Devoteam VM. Each of these keeps
working *silently against the wrong host* until corrected.

| Item | Where | Action |
|---|---|---|
| Agent bot `outgoing_url` | `agent_bots` table | Repoint to AEON360's WABA endpoint, or the new CRM host |
| Chatwoot webhooks | `webhooks` table | Repoint at the new `agent` hostname |
| Inbox channel configs | `channel_*` tables | Review Twilio credentials and callback URLs |
| API access tokens | `access_tokens` | These carry over, so the existing `CHATWOOT_API_TOKEN` value stays valid — reuse it rather than minting a new one |

Check what is currently registered:

```bash
sudo docker exec aeon360-chatwoot-rails bundle exec rails runner \
  'AgentBot.all.each { |b| puts [b.id, b.name, b.outgoing_url].inspect }
   Webhook.all.each { |w| puts [w.id, w.url].inspect }'
```

---

## 10. Verify

No public route exists yet, so verification goes through the SSH forward
(port 80 is not open to IAP; port 22 is).

```bash
gcloud compute ssh gce-prod-innovation-svc-ai-backend-crm \
  --project=prj-prod-innovation-svc-2p --zone=asia-southeast1-a \
  --tunnel-through-iap --strict-host-key-checking=no -- -N -L 8080:localhost:80
```

Then from a second terminal:

```bash
curl -H "Host: aeon360.crm.10-94-0-4.nip.io" http://localhost:8080/api   # Chatwoot
curl http://localhost:8080/healthz                                        # Caddy health vhost
```

For the UI, add `127.0.0.1  aeon360.crm.10-94-0-4.nip.io` to `/etc/hosts`,
forward local port 80 instead, and browse the hostname directly so the origin
matches `FRONTEND_URL`.

Confirm the mirror landed: the user list, inboxes, labels and custom roles from
the Devoteam tenant should all be present, and previously configured
conversations should open with their attachments.

---

## 11. At cutover, when the load balancer exists

Not a redeploy — an env change plus a Caddy route regeneration:

| Setting | Now | At cutover |
|---|---|---|
| `PUBLIC_IP` (`infra.env`) | `10-94-0-4` | real IP / domain |
| `caddy/tenants/aeon360.caddy` | `*.10-94-0-4.nip.io` | real hostnames |
| `FRONTEND_URL`, `CHATWOOT_PUBLIC_URL`, `AGENT_PUBLIC_URL` | nip.io | real |
| `PROTON_BACKEND_PUBLIC_URL` | nip.io | **must be `https://`** |

Plus re-registering the webhook and agent-bot URLs (§9).

`PROTON_BACKEND_PUBLIC_URL` is the one that bites: it is injected into the SPA as
the browser-facing backend origin, so an `http://` value on an HTTPS page is
blocked as mixed content and every AI, Knowledge and RBAC panel dies with no
obvious cause.
