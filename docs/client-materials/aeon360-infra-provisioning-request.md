# AEON360 CRM — infrastructure provisioning request

**For:** AEON360 infrastructure / cloud team
**From:** Devoteam (CRM platform)
**Date:** 2026-08-29 · **Revision 9** — **Production is live.** The load balancer is wired and `https://innovation-hub.aeon360.com.my` serves the CRM; §13.7's firewall request is withdrawn as moot

---

## How to read this document

**What is described here is our current playground setup.** Every machine type,
role, region, image tag and script in this document is what we run today in
Devoteam's own GCP playground project, serving live CRM instances. Nothing is
theoretical — but equally, nothing here was designed under AEON360's governance.
It was designed under ours, where we owned the project and set our own rules.

So please read this as **a working reference architecture, not a specification
we are asking AEON360 to accept**. It exists to answer "what does this thing
actually need to run", with real numbers rather than estimates, so that
AEON360's team has something concrete to review against its own standards.

**Where AEON360 policy differs, AEON360 policy wins.** We will adopt it. That
explicitly includes:

- **Naming and labelling** — resource names, project conventions, cost-centre
  and environment labels, required tags. The names in this document (`crm-backend`,
  `aeon360-db`, `aeon360-kb`) are ours and are placeholders.
- **Network design** — which VPC and subnet, whether a shared VPC applies,
  firewall rule ownership, Private Google Access, Cloud NAT.
- **Identity** — how service accounts are created and who owns them, whether
  keys are permitted at all, group-based access instead of individual grants.
- **Base image** — a hardened or golden image instead of stock Debian 12.
- **Backup, retention and residency** — AEON360's obligations supersede the
  lifecycle rules we suggest in §5.4.
- **Change control** — approvals, windows, and who is permitted to run the
  provisioning steps.

Some of those changes are configuration on our side and cost nothing. A few
change the design materially — §11 lists the specific organisation policies that
do, so they can be checked before work starts rather than discovered when a
command is refused.

**We would rather be redlined than guess.** Please mark this document up and
send it back; we will rework it to match, and revise before anything is
provisioned.

---

## 0. Summary of what was decided

| Question | Answer |
|---|---|
| Whose GCP project | **AEON360's own** — the existing **Dev** and **Prod** projects |
| Region / data residency | **Singapore — `asia-southeast1`** |
| Environments | **Two** — Dev and Prod, one VM each, fully separate |
| Infrastructure shape | **Single VM per environment.** Postgres, Redis, Memcached and the mail catcher all run on that VM as containers. No Cloud SQL, no Memorystore. **A load balancer is required after all** — organisation policy blocks external IPs on VMs, so inbound arrives GLB → ILB → VM; port contract in §13 |
| Reporting | **Chatwoot's native reporting.** No BigQuery, no warehouse |
| Domain | **`innovation-hub.aeon360.com.my`** — live; DNS and TLS in place |
| Credentials | **Option A** — VM-attached service account, no keys. Forced by org policy, proven in production (§2.3) |
| Voice / IVR | Out of scope for now — see §7.4 |

### Status: live in production as of 2026-08-29

This document began as a request for infrastructure. That infrastructure now
exists and the CRM is running on it. What follows is kept as the record of what
was provisioned and why, and remains the reference for the Dev environment if
that is stood up later.

| | |
|---|---|
| URL | **`https://innovation-hub.aeon360.com.my`** (GLB `34.54.12.27`, TLS at the GLB) |
| Hostname | Settled — one hostname, path-split across the three services (§13) |
| Credentials | Settled — Option A, VM-attached service account, proven (§2.3) |
| Path | GLB → ILB `ilb-prod-innovation-svc-ase1` (`10.94.0.2:80`) → zonal NEG → VM `10.94.0.4:80` |
| Verified | Chatwoot, AI backend, agent, RBAC, Knowledge and reporting all reachable and authenticating; Gemini answering via Vertex in `asia-southeast1` |

The tenant's data was mirrored from the Devoteam environment — see
`docs/runbooks/aeon360-prod-migration.md` for that, and for four defects found
after cutover that any future provisioning run will hit.

---

## 1. What is being deployed

The CRM is **Chatwoot (forked) plus two first-party Python services**, all
running as Docker containers on **one Linux VM per environment**, behind
**Caddy** as the reverse proxy. Postgres, Redis, Memcached and a mail catcher
run as containers on that same VM.

Outside the VM there are only four things: Google's AI services (Vertex AI /
Gemini and Vertex AI Search), one Firestore database, one container image
registry, and one Cloud Storage bucket for backups.

It is not Kubernetes and not Cloud Run. Per AEON360's decision, everything that
can live on the VM does.

Containers on each VM:

| Container | Image | Memory limit |
|---|---|---|
| `caddy` | `caddy:2.10-alpine` — pinned, see §4.4 | — |
| `postgres` | `pgvector/pgvector:pg16` | 1.5 GB |
| `mailpit` | `axllent/mailpit` | 128 MB |
| `chatwoot-rails` | custom fork image from Artifact Registry | 1.5 GB |
| `chatwoot-sidekiq` | same image | 1 GB |
| `agent` | built from source (`agent/`) | 384 MB |
| `backend` | built from source (`backend/`) | 768 MB |
| `redis` | `redis:7-alpine` | 256 MB |
| `memcached` | `memcached:1.6-alpine` | 64 MB |

### 1.1 What single-VM means, stated plainly

This is a deliberate choice and it is the right one for this stage, but three
consequences follow from it and should be acknowledged rather than discovered:

- **The VM is a single point of failure.** If it is lost, the CRM is down until
  it is rebuilt. There is no automatic failover.
- **The database backup is therefore the entire recovery plan.** Postgres runs
  on the same disk as everything else, so the offsite bucket in §5.4 is not
  optional — without it, losing the VM loses the data and the backups together.
- **Scaling is vertical.** More load means a bigger machine type and a reboot,
  not more instances.

Managed Postgres or a load balancer can be introduced later without redesigning
the application; both are configuration and deployment changes on our side, not
a rewrite.

---

## 2. GCP projects, APIs and identity

Everything in §2–§6 is provisioned **twice** — once in the Dev project, once in
the Prod project — unless a row says otherwise.

### 2.1 APIs to enable, in both projects

```bash
gcloud services enable \
  compute.googleapis.com \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  discoveryengine.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  --project=<PROJECT_ID>
```

BigQuery is **not** required — reporting uses Chatwoot's own data (§7.3).

### 2.2 Service account for the CRM's AI services

One service account per project. The `agent` and `backend` containers both
authenticate as it.

```bash
gcloud iam service-accounts create crm-backend \
  --display-name="CRM AI backend" --project=<PROJECT_ID>
```

Project-level roles:

| Role | Why it is needed |
|---|---|
| `roles/datastore.user` | Firestore — feature switchboard, escalation routing, terminology dictionary, session and handoff state |
| `roles/aiplatform.user` | Vertex AI — Gemini calls for reply drafting, copilot, summaries, classification |
| `roles/discoveryengine.viewer` | Vertex AI Search — knowledge-base retrieval at answer time |
| `roles/discoveryengine.editor` | Only if operators will upload knowledge-base documents from the CRM admin UI. Recommended — it is how the knowledge base gets maintained without engineering involvement |

### 2.3 How the containers authenticate — AEON360 to decide

Both options are supported by the same code. The difference is entirely about
credential handling policy.

#### Option A — VM-attached service account (no key files)

The `crm-backend` service account is attached to the VM at creation. Containers
obtain short-lived tokens from the instance metadata server automatically. No
credential file exists anywhere on disk.

**Choose this if** AEON360 has any policy restricting downloadable service
account keys, or wants credentials that cannot be copied off the machine. It is
the option Google recommends and the stronger security posture: nothing to leak,
nothing to rotate, and access dies with the VM.

**Status: proven on 2026-08-28.** This was previously flagged as untested. It is
now the configuration running on AEON360's Prod VM: with `GCP_ADC_PATH` left
blank and `GOOGLE_GENAI_USE_VERTEXAI=true`, the backend constructs its Vertex
client from the metadata server as `sa-crm-backend@` and reports healthy. No
credential file exists on the VM. The caveat that used to sit here is withdrawn.

#### Option B — JSON key file mounted into the containers

A key is generated for `crm-backend`, placed on the VM at a fixed path, and
mounted read-only into both containers.

**Choose this if** AEON360 wants the configuration we have already run in
production, or if the VM must run under a service account that cannot be given
these roles for unrelated reasons.

**The trade-off:** it is a long-lived credential sitting on the VM's disk. It is
included in disk snapshots, it survives the VM, and rotating it is a manual
task. If it is ever exposed, it grants Vertex AI and Firestore access until
someone revokes it.

#### Our recommendation

**Option A, and the decision is effectively already made.** Organisation policy
`iam.disableServiceAccountKeyCreation` is enforced on both projects (§11), so
Option B cannot be used even if it were preferred. Option A is running on the
Prod VM as of 2026-08-28 and the backend is healthy against it, so there is no
longer an unverified path to weigh — this section is kept because the trade-off
is worth understanding, not because a choice remains open.

Under Option A, the VM must be created **with the `crm-backend` service account
attached**; this cannot be added later without stopping the instance.

### 2.4 VM service account — additional roles

Whichever identity the VM runs as also needs:

- `roles/logging.logWriter` and `roles/monitoring.metricWriter` — for the Google
  Cloud Ops Agent (host and container metrics, log-based alerting).
- `roles/artifactregistry.reader` — to pull the CRM images.
- `roles/storage.objectAdmin` **scoped to the backup bucket only**, not
  project-wide.

---

## 3. Compute

### 3.1 Production VM

| Property | Value |
|---|---|
| Machine type | **`e2-standard-4`** — 4 vCPU / 16 GB |
| Boot disk | **60 GB `pd-balanced`** |
| Image | **Debian 12** (`debian-12` family, `debian-cloud`) |
| Zone | **`asia-southeast1-a`** (or `-b` / `-c` — AEON360's preference) |
| External IP | **Static, reserved.** Hostnames and every registered webhook are pinned to it |
| Network tag | e.g. `crm-prod` |
| Service account | `crm-backend`, if Option A (§2.3) |
| Swap | 4 GB swapfile — created by our bootstrap script, no action needed |

**Sizing rationale**, so it can be checked independently: the containers declare
~4 GB of limits for the application (Rails 1.5 + Sidekiq 1.0 + backend 0.77 +
agent 0.38 + Redis 0.25 + Memcached 0.06) plus ~1.6 GB for Postgres, Caddy and
Mailpit. That leaves roughly 10 GB headroom on a 16 GB machine, which is what
absorbs Sidekiq bursts, Postgres cache growth and in-place image builds. This is
the size our current production host runs, carrying three separate CRM instances.

The 60 GB disk holds the Postgres data directory, Chatwoot's uploaded
attachments, Docker images and local backup dumps. Attachment volume is the part
that grows unpredictably — worth a disk-usage alert (§8) rather than a larger
disk up front, since `pd-balanced` can be grown online.

### 3.2 Development VM

| Property | Value |
|---|---|
| Machine type | **`e2-standard-2`** — 2 vCPU / 8 GB |
| Boot disk | **40 GB `pd-balanced`** |
| Everything else | As production, with its own static IP, network tag (`crm-dev`) and firewall rule |

Roughly a third of production's cost. Its purpose is that changes are executed
somewhere before the machine serving customers — today we have no such
environment, and production deploys build in place on the production host.

### 3.3 Host software

Docker Engine is **not** pre-installed. Our bootstrap script installs `docker-ce`
from Docker's official apt repository, adds the swapfile and starts the shared
services.

**If AEON360 mandates a golden/hardened base image, or a specific Docker
distribution, tell us before provisioning** — the script must then be adapted
rather than run as-is.

---

## 4. Domain, DNS and TLS — specification for AEON360

> **Superseded in part.** This section was written assuming the VM holds a
> public IP and Caddy issues its own certificates. Organisation policy
> `compute.vmExternalIpAccess` is enforced (§11), so inbound arrives
> GLB → ILB → VM instead. **§4.1's hostnames still apply; §4.2's records now
> point at the load balancer, not the VM; §4.3's Let's Encrypt flow is replaced
> by a certificate on the load balancer.** See §13 for the port contract and
> §13.6 for what changes.

This section is the DNS specification requested. AEON360 owns the domain and
creates the records; we do not need registrar or zone access.

### 4.1 Hostnames required

**Three hostnames per environment, six in total.** The names below are a
suggested pattern — AEON360 should confirm the actual names, and any naming
convention works as long as all three exist per environment.

| Environment | Purpose | Suggested hostname |
|---|---|---|
| Prod | CRM web application (agents, supervisors, administrators) and the AI backend paths | `crm.aeon360.example` |
| Prod | Webhook receiver — inbound events from the CRM to the AI service | `crm-agent.aeon360.example` |
| Prod | Mail catcher UI — diagnostics, basic-auth protected | `crm-mail.aeon360.example` |
| Dev | CRM web application | `crm.dev.aeon360.example` |
| Dev | Webhook receiver | `crm-agent.dev.aeon360.example` |
| Dev | Mail catcher UI | `crm-mail.dev.aeon360.example` |

All three per environment are required. The webhook hostname is not optional —
it is how the CRM delivers events to the AI service, and it must be reachable
from the internet because the WhatsApp provider also calls in.

### 4.2 Record specification

| Setting | Value |
|---|---|
| Record type | **`A`** |
| Value | The environment's **static external IP** (we provide it after the VM is created) |
| TTL | **300 seconds** while cutting over; raise to 3600 once stable |
| Proxying / CDN | **Must be `DNS only`.** If the zone is on Cloudflare, the orange-cloud proxy must be **off** — see 4.3 |
| `AAAA` records | **Do not create.** The VM is IPv4-only; an `AAAA` record makes clients try a dead address first |
| `CNAME` | Not used for these names |

### 4.3 CAA records — the one that silently breaks certificates

Certificates are issued automatically by **Let's Encrypt**. If the domain
publishes a `CAA` record that does not include Let's Encrypt, **every
certificate request is refused** and the CRM is unreachable over HTTPS — with
an error that appears at the web server, not in DNS, so it is easy to
misdiagnose.

Please check the zone apex:

```bash
dig CAA aeon360.example +short
```

- **No output** — nothing to do. Any CA may issue.
- **Records exist** — one of them must authorise Let's Encrypt:

```
aeon360.example.  IN  CAA  0 issue "letsencrypt.org"
```

The same applies to any CDN or WAF terminating TLS in front of these names: the
CRM's certificate flow validates on port 443 at the VM itself, so a proxy that
terminates TLS breaks issuance. Per §0 there is no load balancer in this design,
so this only matters if the domain's DNS provider proxies by default.

### 4.4 TLS and proxy behaviour

Caddy obtains and renews Let's Encrypt certificates automatically, provided
**443 is reachable from the public internet and DNS resolves to the VM**. No
certificate needs to be purchased, installed or renewed by AEON360.

Port 80 must also stay open — it serves the plaintext fallback hostnames that
already-registered webhooks may use, and it backs up certificate renewal.

**If a proxy, WAF or load balancer is ever placed in front of this CRM**, it must
preserve request headers whose names contain underscores. Chatwoot's own API
contract uses `api_access_token`, and a proxy that strips it produces `401`
responses that read exactly like bad credentials. This cost us a full day on the
current AEON360 integration and is the reason Caddy is pinned to `2.10-alpine`
rather than a floating tag.

### 4.5 Firewall

| Direction | Rule |
|---|---|
| Ingress | `tcp:80, tcp:443` to the environment's network tag. Source `0.0.0.0/0` — the CRM is used by staff and called by Twilio; neither has a stable enumerable range |
| Ingress | SSH for named operators. Direct to the external IP, or via IAP. **If IAP is mandated, grant `roles/iap.tunnelResourceAccessor`** — without it `gcloud compute ssh --tunnel-through-iap` fails with `4033: not authorized`, which is what we hit on the current deployment |
| Egress | Open to the internet, or explicitly allowlisted per 4.6 |

Optionally restrict the Dev environment's ingress to AEON360's corporate ranges —
it has no external webhook callers until WhatsApp is pointed at it.

### 4.6 Egress the VM requires

- `googleapis.com` — Vertex AI, Firestore, Discovery Engine, Cloud Storage
- `*-docker.pkg.dev` (Artifact Registry) and, unless mirrored, **Docker Hub**
  (`registry-1.docker.io`) for `caddy`, `pgvector`, `redis`, `memcached`, `mailpit`
- `download.docker.com` and Debian apt mirrors — host bootstrap and patching
- `acme-v02.api.letsencrypt.org` — certificate issuance and renewal
- Twilio (WhatsApp) and the chosen SMTP relay

**If AEON360 blocks public registry pulls**, Artifact Registry remote
repositories mirroring Docker Hub must exist before day one, or the stack cannot
start.

---

## 5. Data services

### 5.1 Postgres, Redis, Memcached — on the VM

Per AEON360's decision, all three run as containers on the VM. Postgres 16 with
the **`pgvector`** extension; its data lives in a Docker volume on the boot disk.
Nothing to provision, and no Cloud SQL or Memorystore instance is needed.

This makes §5.4 (backups) load-bearing rather than best-practice.

### 5.2 Firestore — one database per environment, location is permanent

```bash
gcloud firestore databases create \
  --database=aeon360-db \
  --location=asia-southeast1 \
  --type=firestore-native \
  --project=<PROJECT_ID>
```

Run once in each project. **A Firestore database's location cannot be changed
after creation** — moving it later means creating a second database and
migrating. Singapore (`asia-southeast1`) is confirmed, so this is settled; it is
called out because it is the one irreversible step in this document.

This database holds the feature switchboard, escalation routing (PIC and dealer
contacts), the terminology dictionary and session/handoff state. It is not
optional: if it is missing or misconfigured the CRM opens with no features
enabled and no admin page from which to enable them.

### 5.3 Vertex AI Search — knowledge base

Per environment: a datastore plus a search engine.

- Datastore `aeon360-kb` — type `GENERIC`, content config `CONTENT_REQUIRED`
- Engine `aeon360-kb-engine` — `SOLUTION_TYPE_SEARCH`, attached to that datastore

**Location `global`.** This is a Discovery Engine constraint, not an oversight —
the datastore is created in the `global` collection. The documents AEON360
ingests are stored by that service; if this conflicts with a data-residency
requirement, raise it now and we will scope what the knowledge base can hold.

Both are created empty. AEON360 content must be ingested before the AI can cite
it.

**Gemini region:** we will configure Vertex AI calls against
`asia-southeast1` to keep inference in-region. If a required model is not served
there, the fallback is a different Vertex region — we will confirm on day one and
report which region each model resolves to, rather than assume.

### 5.4 Cloud Storage — offsite backups

Per environment (Prod is mandatory; Dev is recommended but can be skipped):

```bash
BUCKET=<PROJECT_ID>-platform-backups
gsutil mb -p <PROJECT_ID> -l asia-southeast1 -b on gs://$BUCKET
gsutil versioning set on gs://$BUCKET
gsutil lifecycle set deploy/gcs/backup-bucket-lifecycle.json gs://$BUCKET
gsutil iam ch serviceAccount:<vm-service-account>:roles/storage.objectAdmin gs://$BUCKET
```

The lifecycle policy ships in our repository: Standard → Nearline at 30 days →
Coldline at 365 days, non-current versions deleted after 90 days.

A nightly cron job on the VM dumps every database and syncs the night's
directory to this bucket. Given §1.1, **this bucket is the recovery plan** —
please treat it as mandatory for Prod.

If AEON360 has a retention or residency policy for backups, apply it to this
bucket now; the lifecycle rules above are a starting point, not a
recommendation about AEON360's obligations.

---

## 6. Container images

Per environment (or one shared registry in the Prod project that Dev is granted
read access to — AEON360's preference):

- **One Artifact Registry Docker repository**, location **`asia-southeast1`**.
- The **Chatwoot fork image must be built off-VM for `linux/amd64`** via Cloud
  Build. It is a heavy front-end build: building it on the production VM is not
  supported, and building it on an Apple-silicon laptop produces an image the VM
  cannot pull.
- The Cloud Build service account needs `roles/artifactregistry.writer` on the
  repository; each VM's identity needs `roles/artifactregistry.reader`.
- The `agent` and `backend` images are small and are currently built on the VM
  from source. If AEON360 requires that all running images come from the
  registry, they can be Cloud Build artifacts too — a small change to our deploy
  procedure, worth deciding once rather than retrofitting.

---

## 7. Non-GCP dependencies and scope

### 7.1 WhatsApp

The production number is already live on Twilio. Required:

- The **Twilio account or subaccount and WABA sender** for the CRM, with
  credentials shared securely with the deployment operator.
- The sender's **inbound webhook URL repointed** to the new CRM hostname at
  cutover.

Note this is a change on a live number with no sandbox available — schedule a
low-traffic window with the rollback prepared. If AEON360 can provision a second
test number for the Dev environment, that removes the "first test is in
production" problem entirely; it is the single highest-value optional item in
this document.

### 7.2 Email

- An **SMTP relay account** for outbound mail — notifications, escalation
  emails, agent invitations. We need host, port, username, password and the
  sender address the CRM should send from.
- If email escalation with reply-threading is wanted, a **dedicated mailbox owned
  by AEON360**. It must not be shared with another CRM instance or with the Dev
  environment — the threading keys off conversation ids, and two instances
  sharing one mailbox will cross wires.
- **Dev should not use a real relay.** The mail catcher is the default and keeps
  test traffic from reaching real people.

### 7.3 Reporting — Chatwoot native, confirmed

No BigQuery dataset, no warehouse, no additional IAM. What this means concretely:

- **Chatwoot's own reporting works fully** — conversation volume, first response
  time, resolution time, agent and team performance, labels, inboxes, CSAT. It
  reads from the CRM's own Postgres on the VM, so it is live from day one with
  nothing to configure.
- **The custom report sections we add render empty.** Our fork appends extra
  panels beneath the native reports; those read from a metrics warehouse, and
  with no warehouse configured they show nothing. The native reports above them
  are unaffected.
- **One configuration point matters here:** the metrics provider must be set to
  `noop`, never `mock`. The `mock` setting renders canned demonstration figures —
  another customer's fixture data — which on a live tenant appears to be
  AEON360's own numbers. We will set and verify this explicitly.

If AEON360 later wants the extended reporting, adding it is a BigQuery dataset
plus two IAM roles; nothing in this provisioning changes.

### 7.4 Voice / IVR — out of scope

Not provisioned. If wanted later it needs a Twilio voice number and a call flow;
it is a configuration addition, not an infrastructure change.

---

## 8. Operations and access

- **Named operators** with SSH access and enough IAM to run the provisioning
  steps. **The specific roles are now itemised in §12** — that section replaces
  what used to be a vague request here, and is written against what our account
  actually holds today.
- **Google Cloud Ops Agent** installed on each VM — one command, not part of our
  bootstrap script today — plus a **Cloud Monitoring notification channel**
  pointing at a mailbox a human actually watches. An alert policy with no channel
  is a log line with extra steps.
- **Minimum alerts worth having on Prod:** disk usage above 80%, memory
  saturation, VM unreachable, and the nightly backup job failing.
- **The nightly backup cron entry** on each VM, with the bucket name set in its
  environment. Verify it survives a reboot — a cron line that only exists in a
  shell session is the classic way to discover there are no backups.
- **Host patching** is AEON360's; container image rebuilds are ours. Worth
  agreeing a cadence for both.

---

## 9. Provisioning checklist for AEON360

Per environment — Dev first, then Prod.

- [x] Enable the nine APIs in §2.1
- [x] Create the `crm-backend` service account with the roles in §2.2
- [x] Decide Option A or Option B (§2.3) and tell us which
- [x] ~~Reserve a static external IP~~ — **not possible**: org policy `compute.vmExternalIpAccess` is DENY. Superseded by the GLB/ILB path (§11, §13)
- [x] Create the VM per §3.1 / §3.2 — **with the service account attached if Option A**
- [x] Create the firewall rule for `tcp:80,443` on the environment's network tag
- [x] **Grant the operator roles in §12** — at minimum
      `roles/compute.instanceAdmin.v1` on the service project and
      `roles/compute.networkViewer` on the host project (§12.4)
- [x] Confirm the workload service account holds the roles in §12.3
- [x] Create the `GCE_VM_IP_PORT` NEG on **port 80 only** (§13.1) and the
      health check (§13.4)
- [x] Open the health-check ranges `130.211.0.0/22` + `35.191.0.0/16` and the
      proxy-only subnet to `tcp:80` (§13.5) — the LB serves 502 without them
- [x] ~~Open `35.235.240.0/20 → tcp:80`~~ — **withdrawn**, moot now the site is publicly reachable (§13.7)
- [x] Confirm the load balancer preserves the `Host` header (§13.3) and
      underscore-containing header names (§13.6)
- [x] Hostnames and DNS — settled as the single host `innovation-hub.aeon360.com.my`, resolving to the GLB
- [x] Check the `CAA` record permits `letsencrypt.org` (§4.3)
- [x] Create the Firestore database `aeon360-db` in `asia-southeast1` (§5.2)
- [ ] Create the Vertex AI Search datastore and engine (§5.3) — **still open**, deferred until AEON360 supplies knowledge content; nothing to index today
- [ ] Create the backup bucket with versioning and lifecycle (§5.4) — **still open, and the most important remaining item**: Postgres lives on the VM's own disk, so today there is no offsite copy at all
- [x] Create the Artifact Registry repository in `asia-southeast1` (§6)
- [ ] Provide SMTP credentials and the sender address (§7.2) — **still open**; mail currently goes to Mailpit
- [ ] Provide Twilio WABA credentials, and a Dev test number if possible (§7.1) — **still open**; inbound WhatsApp is not yet pointed at this CRM
- [x] Confirm whether public registry pulls are permitted (§4.6)
- [x] Confirm whether a golden VM image is mandated (§3.3)
- [x] **Share the organisation policies in force on both projects (§11)** — do
      this first, before anything else on this list. Two of them would change
      the design, and it is cheaper to know now
- [ ] Confirm naming, labelling and tagging conventions to apply to every
      resource above — ours are placeholders

---

## 10. What happens once the above exists

Our side, for context — roughly half a day per environment plus verification:

1. Provision script — static IP, VM, firewall rule.
2. Copy the platform source to `/opt/platform` on the VM.
3. Bootstrap script — Docker, swap, shared services (Caddy, Postgres, Mailpit) up.
4. Instance provisioning — generates secrets, creates databases and roles,
   creates the Firestore database, renders the reverse-proxy route, starts the
   application stack.
5. Wiring — run the CRM setup wizard, mint API tokens, register the webhook and
   the AI agent bot, point the browser-facing backend URL at the HTTPS hostname,
   enable the agreed feature flags, set the metrics provider to `noop` (§7.3).
6. Content and configuration — ingest the knowledge base, configure escalation
   routing, create roles and users.
7. Cut the WhatsApp sender over, with rollback ready.

Dev is built first and proves the sequence; Prod follows.

---

## 11. Organisation policies to check before we start

Per the note at the top of this document, the design came from a playground
project with no organisation policies applied. AEON360's projects almost
certainly have some. Five of them would change this design rather than merely
inconvenience it — the others we can absorb quietly.

Please run this in both projects and share the output:

```bash
gcloud resource-manager org-policies list --project=<PROJECT_ID>
```

### The five that matter

| Constraint | If enforced | What we do about it |
|---|---|---|
| `compute.vmExternalIpAccess` | **Confirmed enforced (DENY) on the Dev project, 2026-08-27.** Blocks the static external IP, so §4's original DNS and certificate design cannot work — there is no inbound path for staff, for Twilio's webhooks, or for Let's Encrypt validation | **Resolved:** the CRM goes behind GLB → ILB, with certificates managed on the load balancer; outbound already works via Cloud NAT (verified). **The port contract for the NEG is specified in §13**, and §13.6 lists what changes on our side |
| `gcp.resourceLocations` | If restricted to `asia-southeast1`, it **blocks the Vertex AI Search datastore**, which must be created in `global` (§5.3) | Either an exception for Discovery Engine, or the knowledge-base feature is descoped. There is no third answer — the location is not ours to choose |
| `iam.disableServiceAccountKeyCreation` | **Removes Option B** from §2.3 | Option A becomes the only path. This is fine, and is what we recommend anyway — it just means the day-one verification step is mandatory rather than optional |
| `compute.trustedImageProjects` | **Blocks stock Debian 12** if `debian-cloud` is not on the allowlist | We adapt the bootstrap script to AEON360's approved image. We need to know which one, and whether it already carries Docker |
| `compute.requireOsLogin` | Changes how operators reach the VM | No design impact — we use OS Login. Worth confirming the operator group is granted the right role |

### Ones we can absorb without changes

`storage.uniformBucketLevelAccess` (our bucket is already created with uniform
access), `compute.requireShieldedVm` (Debian 12 images support Shielded VM;
we enable it), `compute.disableSerialPortAccess`, `iam.allowedPolicyMemberDomains`,
and label or tag enforcement. If any of these are set, tell us and we will
include them — they do not affect the architecture.

### Also worth confirming

- **Whether the projects sit under a shared VPC.** If so, we need the host
  project, the subnet in `asia-southeast1`, and who creates firewall rules —
  §4.5 assumes we can create them in the same project.
- **Whether VPC Service Controls apply.** A perimeter around these projects
  changes how the VM reaches Vertex AI, Firestore and Cloud Storage, and needs
  Private Google Access configured rather than the plain egress in §4.6.
- **Whether budget alerts or quota limits are set** on the projects that would
  interrupt Vertex AI usage.

---

## 12. Access required for the deployment team

This section replaces the general statement in §8 with a specific request.

It is written against what we actually hold today. On **2026-08-27** we
inspected the Dev VM `gce-dev-innovation-svc-ai-backend-crm` in
`prj-dev-innovation-svc-8e` and confirmed our operator account currently holds
exactly three relevant permissions:

- `compute.instances.get`
- `compute.instances.osLogin` — **non-admin, so no `sudo`**
- `iap.tunnelInstances.accessViaIAP`

That is enough to look at the VM and nothing more. We cannot install packages,
add swap, read the startup-script log, create any GCP resource, or see the host
project at all. The list below is what closes that gap.

Every role is requested **per environment** — Dev first, Prod only when Dev is
proven.

### 12.1 Dev/Prod service project — operator account

| Role | Why it is needed |
|---|---|
| **`roles/compute.instanceAdmin.v1`** | The most important one. Grants `osAdminLogin` (sudo on the VM), `setMetadata`, `start`/`stop`/`reset`, `setMachineType`, `disks.resize` and `disks.get`. Without it we cannot install Docker, add swap, run the bootstrap script, or change the machine size |
| **`roles/iap.tunnelResourceAccessor`** | **Already granted — please keep it.** With no external IP on the VM, IAP is our only route in |
| **`roles/datastore.owner`** | To create the Firestore database (§5.2). Creating a *database* needs `datastore.databases.create`, which `roles/datastore.user` does not include |
| **`roles/discoveryengine.admin`** | To create the Vertex AI Search datastore and engine (§5.3) |
| **`roles/artifactregistry.writer`** | Scoped to the `aeon360` repository — to push the CRM images (§6) |
| **`roles/cloudbuild.builds.editor`** | To build the Chatwoot fork image off-VM for `amd64` (§6) |
| **`roles/iam.serviceAccountUser`** | Scoped to `sa-crm-backend@…` — required to attach it when a VM is created or recreated |
| **`roles/storage.admin`** | To create the backup bucket and apply its lifecycle policy (§5.4). Can be narrowed to `roles/storage.objectAdmin` on the bucket if AEON360 creates it |
| **`roles/logging.viewer`** + **`roles/monitoring.viewer`** | To read startup-script logs and diagnose failures. Without these we cannot see *why* something failed on the VM, only that it did |

**Finer-grained alternative to the first row**, if AEON360 prefers not to grant
`instanceAdmin.v1`: **`roles/compute.osAdminLogin`** gives sudo, and
**`roles/compute.viewer`** adds `compute.instances.getGuestAttributes` — the
permission whose absence makes `gcloud compute ssh` fail outright (we currently
work around it with `--strict-host-key-checking=no`). This pair covers
day-to-day work but not resizing or metadata changes.

**Optional, useful:**

- **`roles/iam.securityReviewer`** — so we can verify the workload service
  account's roles ourselves rather than asking. `projects.getIamPolicy` is
  currently denied to us.
- **`roles/serviceusage.serviceUsageAdmin`** — only if APIs ever need changing.
  All nine required APIs are already enabled, so this is low priority.

### 12.2 Host project (shared VPC) — operator account

| Role | Why it is needed |
|---|---|
| **`roles/compute.networkViewer`** | Read-only, and the more urgent of the two. We currently have **no** visibility into the host project — we cannot see the subnet, the firewall rules or Cloud NAT, so we cannot confirm whether the inbound path exists or advise on what to change |
| **`roles/compute.networkUser`** | Scoped to the `asia-southeast1` subnet — only if we are expected to create the load balancer or attach backends (§11) |

We are deliberately **not** requesting `roles/compute.securityAdmin`. Firewall
rules should stay with AEON360's network team; we need to *see* them and tell
you which rules to create.

### 12.3 Workload service account — please confirm, not for us

These are the roles the running containers need on `sa-crm-backend@…`. We could
not read the project IAM policy, so this is unverified from our side:

- **`roles/datastore.user`** — Firestore reads and writes
- **`roles/aiplatform.user`** — Gemini calls
- **`roles/discoveryengine.viewer`** — knowledge-base retrieval at answer time,
  plus **`roles/discoveryengine.editor`** if operators will upload documents
  from the CRM admin UI
- **`roles/artifactregistry.reader`** — pulling the CRM images
- **`roles/logging.logWriter`** + **`roles/monitoring.metricWriter`** — for the
  Ops Agent
- **`roles/storage.objectAdmin`** — scoped to the backup bucket only, never
  project-wide

### 12.4 If only two things can be granted now

To unblock progress immediately, without waiting for the full list to be
approved:

1. **`roles/compute.instanceAdmin.v1`** on the Dev service project
2. **`roles/compute.networkViewer`** on the host project

Those two let us get Docker installed, add swap, and establish whether an
inbound path exists — which is enough to confirm or rule out the load-balancer
redesign in §11 before anyone builds anything on top of it.

---

## 13. Load balancer port specification (`GCE_VM_IP_PORT` NEG)

Because the VM cannot hold an external IP (§11), inbound traffic arrives
GLB → ILB → VM. This section is the port contract for that last hop.

### 13.1 The answer in one line

**One endpoint, one port: the VM's internal IP on `TCP/80`.**

```
network-endpoint-type = GCE_VM_IP_PORT
instance              = gce-dev-innovation-svc-ai-backend-crm
ip                    = 10.90.0.3          # the VM's primary internal IP
port                  = 80
zone                  = asia-southeast1-a
```

That is the whole backend. There is no second endpoint and no per-service port,
and 13.3 explains why adding them would break the product.

### 13.2 What actually listens, and where

Only Caddy publishes ports on the VM host. Every other service is reachable
solely inside the Docker network — they have no host port binding at all, so
they are not addressable by a NEG even if you wanted them to be.

| Service | Port | Bound where | NEG-addressable |
|---|---|---|---|
| **Caddy** | **80** | **VM host** | ✅ **this is the endpoint** |
| Caddy (TLS) | 443 | VM host | ⚠️ not used in this design — see 13.5 |
| Chatwoot Rails | 3000 | Docker network only | ❌ |
| AI backend | 8080 | Docker network only | ❌ |
| Agent (webhooks) | 8000 | Docker network only | ❌ |
| Mailpit UI | 8025 | Docker network only | ❌ |
| Postgres / Redis / Memcached | 5432 / 6379 / 11211 | Docker network only | ❌ — and must never be exposed |

### 13.3 Why one port, not one per service

Caddy is not just a TLS terminator here — **it is the application router**, and
the routing it performs cannot be replicated by an L7 load balancer without
re-implementing it.

Two things happen on port 80 that the product depends on:

**Host-based routing.** All three hostnames (§4.1) arrive on the same port and
Caddy separates them:

| Host header | Routed to |
|---|---|
| `crm.<domain>` | Chatwoot Rails `:3000` — plus the path rules below |
| `crm-agent.<domain>` | Agent service `:8000` |
| `crm-mail.<domain>` | Mailpit `:8025`, behind basic auth |

**Path-based routing within the CRM host.** These prefixes are peeled off and
sent to the AI backend on `:8080`, while everything else falls through to
Chatwoot:

```
/metrics/*  /kb/*  /assist/*  /routing/*  /authz/*
/admin/*    /rsa/*  /voice/*  /alerts/*   /calls/*
```

**This is why the NEG must point at Caddy and not at Chatwoot directly.** If the
load balancer routes to Chatwoot `:3000`, every one of those prefixes reaches
Chatwoot instead of the backend, and Chatwoot answers with its own HTML 404
page. The failure is silent and misleading: the backend is healthy, the CRM
loads, and every AI, Knowledge, RBAC and reporting panel reports
`404: <!DOCTYPE html>…`. We have hit exactly this before — it is documented in
our provisioning script as the single most common wiring mistake.

The same reasoning rules out publishing `3000`/`8000`/`8080` on the host to give
the ILB separate backends: it would expose services that currently have no host
binding, and still lose the path routing.

**Requirement on the load balancer: preserve the `Host` header.** Caddy selects
the vhost from it. A load balancer that rewrites `Host` to the backend address
collapses all three hostnames into one and nothing routes correctly.

### 13.4 Health check

The load balancer needs a target that is cheap and always answers. Two options:

**Preferred — a dedicated health vhost.** We add a catch-all block to the Caddy
config that answers any unmatched Host:

```
:80 {
	respond /healthz "ok" 200
}
```

Health check: `HTTP`, port `80`, request path `/healthz`. No host header needed,
which makes it robust to hostname changes.

**Alternative — use Chatwoot's own endpoint.** Health check: `HTTP`, port `80`,
request path `/api`, **host header set to the CRM hostname** (required — without
it the request does not match the CRM vhost). This is the same endpoint the
container health check already uses.

We recommend the first. It is one line of our config and does not couple the
load balancer's health to Chatwoot's boot time, which is 60+ seconds and would
otherwise cause flapping on every restart.

### 13.5 What AEON360 needs to open — firewall

All in the host project `prj-dev-host-fa`, targeting the VM's network tag:

| Source range | Ports | Why |
|---|---|---|
| `130.211.0.0/22` and `35.191.0.0/16` | `tcp:80` | **Google health check probes. Mandatory** — without this the backend is permanently unhealthy and the LB serves 502, with nothing wrong on the VM |
| The regional **proxy-only subnet** | `tcp:80` | The internal Application Load Balancer's proxies originate from here, not from the client IP. A regional internal ALB also requires this subnet to exist in `asia-southeast1` with purpose `REGIONAL_MANAGED_PROXY` |
| `35.235.240.0/20` | `tcp:80,443` | IAP forwarding — **for verification, not for serving.** See 13.7 |

### 13.6 What changes on our side once TLS moves to the load balancer

Stated so nothing is a surprise:

- **Caddy stops issuing certificates.** TLS terminates at the GLB; the VM serves
  plain HTTP on port 80 behind it. Our Caddy config already runs with
  `auto_https off` and plain `http://` vhosts, so this is the configuration it is
  already in — §4.3 and §4.4's Let's Encrypt flow simply no longer applies.
- **The certificate becomes AEON360's** to provision and renew on the load
  balancer — a Google-managed certificate is the simplest option.
- **The underscore-header requirement moves to the load balancer** (§4.4).
  Chatwoot's API contract uses `api_access_token`; if the GLB or ILB strips
  headers containing underscores, it produces `401`s that read exactly like bad
  credentials. This is the failure that cost us a day on the current
  integration. Please confirm the LB configuration preserves it.
- **`PROTON_BACKEND_PUBLIC_URL` must be the HTTPS hostname.** It is injected into
  the browser page as the backend origin; an `http://` value on an HTTPS page is
  blocked as mixed content and every AI and Knowledge panel dies.

### 13.7 IAP access to port 80 — request withdrawn, no action needed

**Status: no longer required.** This section previously asked AEON360 to open
`35.235.240.0/20 -> tcp:80` so we could verify the stack over an IAP tunnel
while there was no public route. The load balancer is now wired and
`https://innovation-hub.aeon360.com.my` is reachable, so verification happens
over the real URL. **Port 22 IAP remains in place and is all we need for
operations.**

Kept only because the diagnostic detail is worth not relearning: IAP's two error
codes look alike and mean opposite things. `4033: not authorized` is the
firewall blocking. `4003: failed to connect to backend` covers **both** "nothing
is listening" *and* "the firewall silently dropped the packet" -- a DROP rule
produces a timeout indistinguishable from a closed port unless you already know
a listener exists. We read a `4003` as proof the firewall was open, withdrew
this request once on that basis, then had to reinstate it after proving a live
listener on port 80 was still unreachable. If this ever needs re-diagnosing:
establish a known-good listener first, then interpret the code.

**Still required and unaffected:** the health-check ranges in §13.5,
`130.211.0.0/22` and `35.191.0.0/16` -> `tcp:80`. Those are Google's load
balancer probes, a different range from IAP, and without them the backend
service reports unhealthy regardless of the CRM being correct. They are in
place as of 2026-08-29 -- the backend service is healthy.
