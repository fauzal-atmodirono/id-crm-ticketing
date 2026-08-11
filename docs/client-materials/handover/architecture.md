# Architecture document

**Audience:** PROTON's technical, security and operations reviewers.
**Closes:** part of §2.3.4 (documentation handover)
**Companion:** `api-schema.md` · `configuration.md` · `../governance/risk-register.md`

`CLAUDE.md` in the repository root describes this system well for an engineer
joining the codebase. **This document has a different reader — one who wants to
know where the customer's phone number goes, what leaves the VM, and who can see
what.** Section 3 is the part that reader is looking for.

---

## 1. The system

```
                            INTERNET
                                │
        ┌───────────────────────┼────────────────────────┐
        │                       │                        │
   customers                operators                third parties
 (WhatsApp, voice,        (browsers, on              (callbacks in:
  email, web chat)         the CRM UI)                Twilio webhooks)
        │                       │                        │
        └───────────────────────┼────────────────────────┘
                                │  :443 TLS
╔═══════════════════════════════▼══════════════════════════════════════════╗
║  ONE GOOGLE COMPUTE ENGINE VM  ─ the whole platform, every tenant        ║
║                                                                          ║
║   ┌──────────────────────────────────────────────────────────────────┐   ║
║   │  CADDY  (shared)   TLS termination · per-tenant hostname routing │   ║
║   └───────┬──────────────────────────────────┬───────────────────────┘   ║
║           │                                  │                          ║
║  ╭────────▼──────────────────╮      ╭────────▼──────────────────╮       ║
║  │ TENANT "proton"           │      │ TENANT "wahchan"          │  …    ║
║  │                           │      │                           │       ║
║  │  chatwoot-rails ◄─────────┼──┐   │  (identical shape,        │       ║
║  │    (FORKED SPA, 58        │  │   │   separate containers,    │       ║
║  │     patches applied at    │  │   │   separate databases)     │       ║
║  │     image-build time)     │  │   │                           │       ║
║  │  chatwoot-sidekiq         │  │   ╰───────────────────────────╯       ║
║  │  redis      memcached     │  │                                      ║
║  │                           │  │                                      ║
║  │  agent  (FastAPI)  ───────┼──┤  webhooks in, Chatwoot API out       ║
║  │    └── HTTP ──► backend   │  │                                      ║
║  │  backend (FastAPI) ───────┼──┘                                      ║
║  ╰───────────┬───────────────╯                                         ║
║              │                                                          ║
║   ┌──────────▼────────────┐   ┌──────────────────────────────────┐      ║
║   │ POSTGRES (shared      │   │ MAILPIT (shared)                 │      ║
║   │ instance, one DB per  │   │ dev/test mail capture            │      ║
║   │ tenant per service)   │   └──────────────────────────────────┘      ║
║   └───────────────────────┘                                             ║
╚══════════════════════════════════════════════════════════════════════════╝
                                │
        ═══════════════ TRUST BOUNDARY: data leaves here ═══════════════
                                │
   ┌──────────┬──────────┬──────┴─────┬──────────┬───────────┬──────────┐
   ▼          ▼          ▼            ▼          ▼           ▼          ▼
 GEMINI    VERTEX AI  FIRESTORE   BIGQUERY    TWILIO      SMTP/     DMS/TSP
 /Vertex   Search &   (config &   (reporting  (WhatsApp   IMAP      (NOT
 (models)  embeddings  presence)  warehouse)  & voice)  (Gmail)   CONNECTED)
```

### Reading the diagram

- **Everything inside the double border is one virtual machine.** There is no
  second zone and no failover. This is risk register **R1** and it is an
  architectural fact, not a configuration gap.
- **Boxes marked *(shared)*** are one instance serving every tenant: Caddy,
  Postgres and Mailpit. Everything else is per tenant.
- **The seven services below the trust boundary are outside our control.** Section 3
  says exactly what each one receives.

---

## 2. Components

### 2.1 Shared infrastructure

| Component | Role | Shared or per-tenant | Notes |
|---|---|---|---|
| **Caddy** | TLS termination, per-tenant hostname routing | **Shared** | Single point of failure for every tenant simultaneously |
| **Postgres** | Relational storage | **Shared instance**, separate database per tenant per service | Isolation is by database, not by instance. A Postgres-level failure is a platform-wide outage |
| **Mailpit** | Mail capture for development and test | **Shared** | Not a production mail path — production mail goes to real SMTP |

### 2.2 Per-tenant application stack

Provisioned by `deploy/scripts/add-tenant.sh <name>`.

| Component | Role | Notes |
|---|---|---|
| **chatwoot-rails** | The CRM and agent workspace | **The SPA is forked.** 58 patch files are `git apply`-ed onto upstream at image-build time — this is where the custom Knowledge, Cases, Workforce, Escalation Routing, Customer 360, Alert Preferences and Roles & Permissions pages live |
| **chatwoot-sidekiq** | Chatwoot's background jobs | Shares the Rails image and environment |
| **redis** | Chatwoot's queue and cache | Per tenant |
| **memcached** | Chatwoot's cache | Per tenant |
| **agent** | Chatwoot webhook receiver and agent-bot orchestrator | First-party. Two webhook endpoints plus a health check |
| **backend** | Conversational AI, knowledge base, metrics, admin APIs | First-party. 111 endpoints (see `api-schema.md`) |

### 2.3 How the two first-party services relate

`agent` and `backend` communicate **over HTTP only**, via
`PROTON_BACKEND_URL`/`PROTON_BACKEND_KEY`. No shared process, no shared database,
no shared memory — and the coupling is **deliberately fail-open**: if `backend` is
down, unreachable, or simply not configured, `agent` continues to work using its
local path and logs the failure.

This is worth stating to a reviewer because it inverts the usual expectation. The
AI enhancement layer is an *addition* to a working CRM, not a dependency of it.
A `backend` outage degrades AI features; it does not stop conversations.

The same principle runs through the webhook design: **verify → dedupe → return
200 → dispatch to a background task.** The slow Chatwoot and Gemini calls never
run in the request path, so a slow model cannot cause Chatwoot's webhook
deliveries to time out and retry.

### 2.4 The upstream boundary inside Chatwoot

Chatwoot is upstream software. This matters for two reasons a reviewer will care
about:

1. **Customisation is delivered as patches, not as configuration.** 58 of them,
   growing with every feature. Every upstream version bump — including one taken
   purely for a CVE — requires re-applying and re-verifying all 58. This is risk
   register **R2** and it must be priced as recurring effort.
2. **Eight of those patches (0052–0059) have never been applied to a real
   Chatwoot checkout and no built image contains any of them.** Every UI feature
   they deliver does not exist yet on any running tenant. Risk register **R7**.

---

## 3. Trust boundaries and the data that crosses them

**This is the section for a security or privacy reviewer.** Each row states what
a third party actually receives, not what it is nominally for.

### 3.1 Where customer personal data lives at rest

| Location | Holds | Inside the VM? |
|---|---|---|
| **Chatwoot's Postgres database** (per tenant) | The primary record: contact name, phone number, email address, full conversation transcripts, attachments, custom attributes including vehicle number and case fields | **Yes** |
| **`agent`'s Postgres database** (per tenant) | Webhook delivery ids, AI decision log (`ai_actions`), conversation lifecycle state. **AI decision rows can contain message text** | **Yes** |
| **`backend`'s Postgres database** (per tenant) | RBAC roles and assignments, SLA policies, RSA incident log (which includes customer location and vehicle details), the pgvector knowledge base and the resolved-case summary index | **Yes** |
| **Firestore** | Operator configuration — PIC and dealer routing, taxonomy, targets, SLA policy, assistant personas, status catalogue, alert rules — **and the agent presence event log** | **No — Google Cloud** |
| **BigQuery** | The reporting warehouse: per-conversation rows, token usage, QA labels | **No — Google Cloud** |
| **Twilio** | Message and call records held by Twilio under its own retention, including **call recordings** | **No — Twilio** |
| **Gmail / SMTP provider** | Every email sent and received, under the mail provider's retention | **No** |

**Two things a reviewer should notice.** Firestore holds the **agent presence
event log**, which is employee data rather than customer data, and it grows
without bound and has no retention owner (risk **R11**). And the **resolved-case
summary index** stores model-generated summaries of real conversations in a
searchable vector store — see §3.4.

### 3.2 What crosses the boundary, to whom

| Third party | Receives | Contains personal data? |
|---|---|---|
| **Google Gemini / Vertex AI** (models) | Conversation history for the turn, the system instruction, the operator persona, and **any attached customer media** — photos, voice notes, video | **Yes.** Message content is customer content. A photo of a vehicle can include a plate number, a location, or a person |
| **Vertex AI Search / embeddings** | Knowledge-base document text; the customer's query text for retrieval; resolved-case summaries when that index is enabled | **Yes**, in the query and the summaries |
| **Google Firestore** | Operator configuration; agent identifiers and presence transitions | **Employee** data, not customer data |
| **Google BigQuery** | Conversation metadata, timings, categories, token counts, QA labels | **Partially.** Per-conversation rows carry identifiers and category data |
| **Twilio** | Phone numbers, message bodies, media, and **call audio** | **Yes** — necessarily; it is the carrier |
| **SMTP / IMAP provider (Gmail)** | Full email bodies, addresses, attachments — including escalation forwards to PICs and dealers | **Yes** |
| **DMS / TSP** | **Nothing. Not connected.** No endpoint, no specification, no credentials | n/a — risk **R3** |

### 3.3 Where a customer's phone number goes, specifically

The question this document exists to answer, traced end to end:

1. **Arrives** at Twilio, which already has it — Twilio is the carrier.
2. **Stored** in Chatwoot's Postgres as the contact identifier, inside the VM.
3. **Normalised** for lookup. A Malaysian number arrives in several shapes
   (`+60…`, `60…`, `01…`, with and without spaces) and all forms are matched to
   one contact, because a lookup that matches only one shape presents a known
   customer as a stranger.
4. **Sent to Gemini** whenever it appears in message text within the conversation
   history for a turn. **Nothing strips it.**
5. **Sent to BigQuery** as part of the conversation row for reporting.
6. **Sent by email** to a PIC or dealer on escalation, in the forwarded thread.
7. **Asked of the model to omit** from `/assist/summarize` output — and **this is a
   prompt, not a control** (§3.4).

**There is no PII masking layer anywhere in this platform.** Requirement R16
(full PII masking) is not attempted, blocked on open question Q7 (masking scope).
A reviewer should treat every AI call as carrying whatever the customer typed.

### 3.4 The one privacy mitigation that is weaker than it looks

`/assist/summarize`'s prompt **asks** the model to leave out the customer's name,
phone number, email, home address and plate number. This is the mitigation the
design claims. Three things about it:

- **Nothing validates the output.** No code inspects, strips or checks the
  returned text, so a summary can carry an identifier and be stored as-is —
  including into the pgvector resolved-case index, where it becomes searchable.
- **An operator can argue with it.** The persona prefix (product name, guardrails,
  preferred language) is **prepended ahead of the task prompt**, so a tenant whose
  guardrails say *"always include the customer's full name"* places that
  instruction *earlier in the same request*, and the model may prefer it.
- **Therefore anyone with persona-edit access can weaken it without touching
  code.**

**It must not be presented as a control in any privacy or security discussion.**
It is a request to a language model. Risk register **R15**.

### 3.5 Authentication across the boundaries

| Boundary | Mechanism |
|---|---|
| Operator browser → Caddy | TLS |
| Operator browser → Chatwoot | Chatwoot's own session authentication |
| Chatwoot SPA → `backend` | Shared secret (`PROTON_BACKEND_KEY`), **or** the operator's Chatwoot access-token triplet when RBAC is enabled |
| Chatwoot → `agent` | HMAC-signed webhooks, `sha256=` over `f"{timestamp}."+body`, 300-second skew window. **The two receivers use different secrets** |
| `agent` → `backend` | Shared secret, fail-open |
| `agent`/`backend` → Chatwoot API | Chatwoot API tokens |
| Twilio → `backend` | Twilio signature verification |
| Platform → Google Cloud | Application Default Credentials — a mounted service-account key |

**The consequence a security review will land on:** with `RBAC_ENABLED=false`
(the default), all 39 permission-gated endpoints fall back to a **single shared
secret**, and every holder of that secret is effectively an administrator.
Per-role restriction begins only when RBAC is enabled with a database configured.
See `api-schema.md` §"Authentication and authorisation model".

---

## 4. Multi-tenancy

### 4.1 What isolation exists

| Layer | Isolated? | How |
|---|---|---|
| Application containers | **Yes** | A separate Chatwoot, Sidekiq, Redis, memcached, `agent` and `backend` per tenant |
| Relational data | **Yes, by database** | One database per tenant per service, in a shared Postgres instance |
| Firestore data | **Yes, by configuration** | Per-tenant project/database and collection settings |
| BigQuery data | **Yes, by dataset** | Per-tenant dataset |
| Secrets and configuration | **Yes** | One `deploy/tenants/<tenant>.env` per tenant |
| Hostname | **Yes** | Caddy routes `<tenant>.crm.<ip>.nip.io` to that tenant's containers |
| **Compute, memory, disk** | **No** | One VM, one Docker daemon, one kernel, one disk |
| **Availability** | **No** | Caddy and Postgres are shared; either failing takes every tenant down |

### 4.2 What that means in practice

**Isolation here is logical, not physical.** A tenant cannot read another
tenant's data through the application, and that is the isolation that matters for
confidentiality. But:

- A **noisy tenant is a shared-resource problem.** One tenant's Sidekiq backlog,
  runaway query or large media upload competes with every other tenant for the
  same CPU, memory and disk.
- A **shared-component failure is a platform-wide outage**, not a single-tenant
  one. Caddy and Postgres have no per-tenant blast-radius containment.
- **The disk is shared.** One tenant filling it stops every tenant.

If a client requires physical or availability isolation between tenants, this
architecture does not provide it, and saying so now is cheaper than saying so
during an incident.

---

## 5. The single-VM reality, stated plainly

This is repeated here, in its own section, because it is the single most
consequential fact about the deployment and it is easy to lose in a diagram.

**Everything runs on one Google Compute Engine VM.** Caddy, every tenant's
Chatwoot, Rails, Sidekiq, Redis, memcached, `agent`, `backend`, Postgres and
Mailpit. There is no second zone, no failover, no load balancer in front of a
second instance, and no replicated database.

The consequences, all of which follow directly:

| Event | Effect |
|---|---|
| Zone outage | **Total outage, every tenant** |
| Host maintenance | **Total outage, every tenant** |
| Disk full | **Total outage, every tenant** |
| Bad deploy | **Total outage, every tenant**, until rolled back |
| Postgres failure | **Total outage, every tenant** |
| Caddy misconfiguration | **Total outage, every tenant** |

**99.9% availability permits roughly 43 minutes of downtime per month.** A single
VM with a single Docker daemon cannot be *engineered* to that figure. Monitoring
shortens an outage; it does not prevent one. A 2-hour P1 response additionally
assumes a staffed on-call rotation that has not been costed.

**Compounding it, and this is the part that should be settled first:** the backup
script exists and runs, and **no restore has ever been performed from its
output.** A backup that has never been restored is a hypothesis. One VM, no
failover, and an unrehearsed restore are risks that multiply rather than add.

Closing this requires R17 (multi-zone HA) — load-balanced Chatwoot, replicated
Postgres, per-tenant isolation across zones. That is a programme of work, not a
configuration change, and **it is a commercial conversation about either the
price or the SLA.** See risk register **R1** and **R12**.

Two additional build constraints that belong with the deployment reality:

- **The Chatwoot custom image must be built off-VM and for `amd64`.** A local
  arm64 Mac build produces an image the VM cannot pull ("no matching manifest"),
  and this heavy Vite build must never run on the 16 GB production VM. Use Cloud
  Build.
- **`/opt/platform` on the VM is synced source, not a git repository.** There is no
  `git pull` deployment path; source is synced and the light `agent`/`backend`
  images are built there.

---

## 6. What this document does not cover, and what is missing

Stated so a reader does not assume the omissions are deliberate simplifications.

| Topic | State |
|---|---|
| Disaster recovery procedure | **Missing.** `restore.sh` and the disaster-recovery runbook do not exist |
| Data retention policy | **Missing.** No retention policy or purge for `presence_events`; `archive-old-data.sh` does not exist |
| Monitoring and alerting runbook | **Missing.** Deep health checks exist in code; the runbook does not |
| Environments (dev/test/prod) definition | **Missing** |
| Fork rebase procedure | **Missing.** `rebase.sh` does not exist |
| Capacity and sizing model | Not produced. No load test has been run |
| Network topology below the VM | Out of scope — standard GCE networking |

The first five are package P13's operational deliverables. P13 delivered its code
half (deep health enrichment, audit-log purging) and none of its operational half.

---

## 7. Review status

| Check | State |
|---|---|
| Diagram renders and is legible at A4 | **Yes** — plain-text box diagram, no external rendering dependency, no image asset to lose |
| Trust boundaries and cross-boundary data documented | **Yes** — §3 |
| Multi-tenancy shared-versus-per-tenant stated | **Yes** — §4 |
| Single-VM reality stated plainly | **Yes** — §5 |
| **Reviewed by someone who has not worked on this repository** | **NOT DONE** |

**The last row is a genuine gap and is recorded rather than glossed.** The
programme's definition of done for this document requires review by someone
outside the project, with their questions becoming revisions — precisely because
the author of an architecture document cannot tell which parts are only
comprehensible to the author. That review has not happened, so this is version 1
of a document that is expected to change once it has a reader.

**The most useful reviewer would be PROTON's own security or infrastructure
reviewer**, and §3 and §5 are the sections to put in front of them first.
