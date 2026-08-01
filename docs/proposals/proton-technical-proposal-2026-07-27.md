# PROTON — Technical Proposal: Self-Managed CRM on Google Cloud

**Replacing Zendesk with a self-hosted, AI-native Customer Complaint Management platform**

Prepared for: **PROTON (e.MAS Customer Operations)**
Prepared by: *[Delivery Team]*
Date: **2026-07-27** · Version: **1.0 (draft)**

> **Format note.** This document is the *content source* for the pitch deck. It
> is organized slide-by-slide (mapped to `docs/templates/2025 Presentation
> template`). Each `### Slide N` block = one slide: a headline, the on-slide
> bullets/tables, and *(speaker notes)* for the presenter. Copy each block onto
> the matching template layout.

---

# SECTION 1 — Executive Framing

### Slide 1 — Title

**PROTON Customer Complaint Management System**
*A self-managed, AI-native CRM on Google Cloud — engineered to your SOP, without per-seat SaaS fees.*

*(speaker notes: One line to set the frame — "You asked us to build your 5-channel complaint process. We built it on open, self-hosted foundations plus Google Gemini AI, so PROTON owns the platform and the data, and stops paying Zendesk per-agent licensing.")*

---

### Slide 2 — Executive Summary

**The situation.** PROTON's complaint operation runs today on **Zendesk**, a
per-seat SaaS CRM. Cost scales with every agent and every premium feature, and
the data and AI behaviour live inside a vendor you don't control.

**The proposal.** Replace Zendesk with a **self-managed CRM** — **Chatwoot
Community** (open-source, no per-seat licence) plus a **Google Gemini / Vertex
AI** automation layer — deployed on **Google Cloud Platform** and built directly
to PROTON's own 5-channel process-flow SOP.

**Why now / why us.**
- **Cost:** eliminate recurring per-agent SaaS licensing; pay for infrastructure
  you consume, not seats.
- **Ownership:** your conversations, your knowledge base, your AI prompts, your
  data — all inside PROTON's GCP tenancy.
- **Time-to-value:** the large majority of the platform is **already built and
  demonstrated live** against your SOP — this is a migration and hardening
  engagement, not a greenfield build.
- **AI-native:** Gemini answers in the customer's language, grounded on PROTON's
  own FAQ/KB — not a black-box chatbot.

*(speaker notes: Land the three words — Cost, Ownership, Speed. Then note the
live demo tenant already exists.)*

---

### Slide 3 — Our Understanding of Your Requirements

We read your two source documents end-to-end — the **Customer Complaint
Management System** business requirement and the **CRM Process Flow** workbook
(WhatsApp / Social / Email / IVR / SSI) — and mapped every requirement to the
platform.

**Your six requirement clusters:**

| # | Requirement cluster | What you need |
|---|---------------------|---------------|
| 1 | **Omnichannel inbound** | Call / Email / WhatsApp / Social in one agent view, single customer record, new-message alerts, call voice-to-text |
| 2 | **Agent management** | On-duty check before escalation, per-agent channel priorities, status-aware auto-assignment, reminders & timeout warnings |
| 3 | **FAQ + AI support** | Live-editable knowledge base, AI auto-suggested replies from FAQ, one-click reference, FAQ quality scoring |
| 4 | **Escalation & SLA** | Rule engine → PIC by category/SOP, email + WhatsApp alerts, case states (WIP/Resolved/Temp-Closed), 8h/48h auto-escalation |
| 5 | **Customer 360** | Two-way DMS + TSP integration, auto-identify by number, a 360 view card (personal / vehicle / service / call-centre history) |
| 6 | **Reporting / BI** | Channel & division analytics, dept/PIC KPIs, Call-Centre KPIs, NPS, lifecycle tracking, scheduled PDF/Excel export, anomaly alerts, Power BI |

**Cross-cutting drivers:** reduce SaaS cost · own the data & AI · honour the
5-channel SOP timers and surveys exactly · multi-tenant so the same platform can
serve PROTON and future business units.

*(speaker notes: This slide proves we understood *their* documents. Point at the
workbook on the second screen.)*

---

### Slide 4 — What to Expect

| **Business Expectation** | **Technical Expectation** |
|--------------------------|---------------------------|
| No per-agent / per-seat licence fees | Chatwoot **Community** (open-source), self-hosted on GCP — unlimited agents |
| Your data stays yours | All conversations, KB, and AI prompts inside **PROTON's GCP project** |
| AI answers customers instantly, in their language | **Gemini on Vertex AI**, same-language replies, grounded on your FAQ/KB |
| Your SOP, not a generic product | Process-flow implemented as code: disclaimers, idle-close, YES/NO resolution, rating surveys |
| Faster complaint resolution | Auto-classification → PIC routing, SLA timers (2-min WA ack … 48h alarm), auto-escalation |
| Management visibility | BigQuery analytics + Looker/**Power BI**, scheduled PDF/Excel reports, anomaly alerts |
| Predictable, scalable cost | Managed GCP services; pay for compute/storage consumed, scale per tenant |
| Low delivery risk | Majority of capabilities **already built & demoed live**; engagement is migration + hardening |
| Continuity during switch-over | Phased cutover from Zendesk; parallel-run before decommission |

*(speaker notes: Read across a couple of rows — business promise on the left,
the concrete technical mechanism on the right. This is where "we can actually do
this" lands.)*

---

# SECTION 2 — Target Architecture: Zendesk → Google Cloud

### Slide 5 — Current State vs. Target State

**Today — Zendesk (SaaS):**
- Per-agent subscription; premium features (SLA, roles, AI/Copilot) behind paid tiers
- Data and AI behaviour hosted by the vendor
- FAQ / KB inside Zendesk; limited control over AI grounding
- Integrations (DMS/TSP, telephony) bolted onto a closed platform

**Target — Self-managed on GCP:**
- Open-source Chatwoot core — no seat licences; SLA/roles/AI **built by us**, not rented
- Runs inside **PROTON's GCP project**; full data residency & control
- Knowledge base + AI prompts owned and edited by PROTON operators (no-code)
- Native, first-party integration surfaces for DMS/TSP, telephony, and BI

```
   BEFORE (Zendesk SaaS)                 AFTER (GCP self-managed)
   ┌─────────────────────┐              ┌──────────────────────────────┐
   │  Zendesk cloud      │              │  Google Cloud (PROTON project)│
   │  · tickets & chat   │   migrate    │  · Chatwoot (omnichannel)     │
   │  · FAQ / Guide      │  ─────────►  │  · Gemini/Vertex AI layer     │
   │  · per-seat billing │              │  · BigQuery + Power BI         │
   │  · vendor-owned data│              │  · PROTON-owned data & prompts │
   └─────────────────────┘              └──────────────────────────────┘
```

*(speaker notes: The arrow is the whole pitch — same capabilities, moved onto
foundations PROTON owns and controls, at infrastructure cost.)*

---

### Slide 6 — GCP Production Architecture

**Presentation & channels**
- **Cloud Load Balancing + Caddy** — TLS, routing, one entry point per tenant
- Chatwoot omnichannel inboxes: **WhatsApp, Email, Social (FB/IG), Web, Voice**

**Application (containerized, GCP-managed)**
- **Chatwoot** (Rails + Sidekiq) — CRM / live-chat core (custom "Knowledge" UI)
- **`agent` service** (FastAPI) — webhook sync, AI orchestration, SLA/lifecycle
- **`backend` service** (FastAPI) — Gemini agent, KB, routing, metrics
- Run on **GKE** (or **Cloud Run** for the stateless services) with autoscaling

**AI & knowledge**
- **Vertex AI — Gemini** (drafted replies, classification, same-language answers)
- **Vertex AI Search** + **pgvector** (Cloud SQL) — KB grounding, no black box

**Data & state**
- **Cloud SQL for PostgreSQL** (Chatwoot DB + per-tenant `pgvector` KB)
- **Memorystore (Redis)** — queues/cache · **Cloud Storage** — attachments/exports
- **Secret Manager** — API keys, tokens, webhook secrets

**Analytics**
- **BigQuery** (metrics warehouse + views) → **Looker Studio / Power BI**

**Ops**
- **Cloud Monitoring + Logging**, automated **backups**, **multi-tenant isolation**
  (one app stack + isolated DBs per business unit)

```
 Internet ──► Cloud LB / Caddy ──► ┌── Chatwoot (GKE) ──► Cloud SQL (Postgres)
                                   ├── agent   (Cloud Run) ──► Vertex AI (Gemini)
                                   ├── backend (Cloud Run) ──► Vertex AI Search / pgvector
                                   └── Sidekiq/Redis (Memorystore)
        Secret Manager · Cloud Storage · BigQuery ──► Looker Studio / Power BI
        Cloud Monitoring & Logging · Automated Backups
```

*(speaker notes: Emphasize these are managed GCP services — the single-VM stack
we demoed is the dev/pilot tier; this is its productionized, autoscaling,
HA evolution. Vertex AI keeps Gemini close to the data.)*

---

### Slide 7 — Data Migration: Zendesk → Platform

**What we migrate**
- **Tickets / conversations** — historical complaint records + status/labels → Chatwoot conversations
- **Contacts / customers** — identities, phone/email, custom fields → Chatwoot contacts
- **Knowledge / Guide (FAQ)** — articles → PROTON's owned KB (Vertex Search + pgvector)
- **Attachments** → Cloud Storage · **Reporting history** → BigQuery (for trend continuity)

**How**
- Zendesk **API export** → transform → bulk import via Chatwoot API + KB ingest
- **Parallel run**: platform live alongside Zendesk during validation
- **Cutover**: switch channel endpoints (WhatsApp/Email/Social) once validated
- **Decommission** Zendesk after sign-off

*(speaker notes: Migration is de-risked by the parallel run — no big-bang. We
validate data fidelity before pointing the channels over.)*

---

# SECTION 3 — Delivery

### Slide 8 — Project Timeline (≈ 12–16 weeks, phased)

| Phase | Weeks | Focus | Key outcomes |
|-------|-------|-------|--------------|
| **P0 — Discovery & GCP foundation** | 1–2 | Access, data contracts, GCP project/landing zone | GCP org/project, network, Secret Manager, CI; Zendesk export sample validated |
| **P1 — Core platform cutover** | 3–7 | Stand up production GCP stack; migrate data; wire channels | Chatwoot + agent + backend live on GKE/Cloud Run + Cloud SQL; WhatsApp/Email/Social connected; KB migrated; AI + SOP flows live |
| **P2 — Gap closure** | 6–12 | The genuine net-new work (parallelized with P1 tail) | **Customer 360 + DMS/TSP** integration; **RBAC** roles/permissions; **telephony/IVR** provider hookup; **Reports Tier-2** tabs |
| **P3 — Hardening, parallel-run & handover** | 12–16 | HA, monitoring, UAT, cutover, decommission Zendesk | Autoscaling/HA, backups, monitoring dashboards; UAT sign-off; Zendesk decommissioned; ops runbook + training |

*(speaker notes: P0/P1 are largely assembly of already-built components — that's
why the front half is fast. The real engineering effort concentrates in P2's
external-dependency items, DMS/TSP and telephony, which need PROTON access.)*

---

### Slide 9 — Scope of Work

**Included**
- GCP landing zone: project, networking, IAM, Secret Manager, CI/CD
- Deploy production Chatwoot + `agent` + `backend` on GKE / Cloud Run + Cloud SQL
- **Omnichannel**: WhatsApp, Email, Social (FB/IG), Web widget, Voice-to-text
- **AI layer**: Gemini/Vertex reply drafting, same-language answers, KB grounding, auto-classification
- **Knowledge base**: migration + operator no-code authoring (FAQ + pgvector uploads)
- **Agent management**: presence, channel-priority routing, status-aware assignment, My-Tasks timers
- **Escalation & SLA**: category→PIC routing, email + WhatsApp alerts, case states, 8h/48h auto-escalation, per-channel ACK timers
- **Lifecycle/SOP**: disclaimers, idle-warn/auto-close, YES/NO resolution gate, AI & agent rating surveys, auto-categorization, email auto-ack
- **Customer 360**: DMS + TSP two-way integration + 360 view card *(subject to PROTON API access)*
- **RBAC**: per-tenant roles & permissions model
- **Reporting/BI**: BigQuery warehouse + views, Looker/Power BI, scheduled PDF/Excel, anomaly alerts, NPS/CSAT
- **Data migration** from Zendesk (tickets, contacts, KB, attachments)
- **Ops**: monitoring, logging, backups, multi-tenant isolation, runbook + admin training

**Assumptions**
- PROTON provides GCP billing/org access, DMS/TSP API access + data contracts, Zendesk export access, and a telephony/CTI provider decision
- One production tenant (PROTON); additional tenants provisioned on the same pattern

---

### Slide 10 — Out of Scope

- **Telephony/PSTN carrier procurement** — we integrate the CTI/provider; the phone-line contract & number provisioning is PROTON's (voice-to-text engine itself is built)
- **DMS / TSP source-system changes** — we consume their APIs; internal changes to those systems are their owners' responsibility
- **SSI dealer-satisfaction survey workflow** — lives in the e.MAS mobile app + dealer process; the platform **ingests & reports** SSI results but does not host that survey flow
- **Power BI licences** — we connect to BigQuery; Microsoft licensing is PROTON's
- **Non-CRM business applications**, custom hardware, and end-user device management
- **Ongoing content authoring** (FAQ/KB articles) after handover — enabled via no-code tools, owned by PROTON operators
- **Third-party SaaS subscriptions** beyond the GCP + open-source stack

---

### Slide 11 — Deliverables

| Deliverable | Description |
|-------------|-------------|
| **Production GCP environment** | Fully provisioned, IaC-described GCP project running the platform |
| **Migrated CRM** | Chatwoot with imported Zendesk tickets, contacts, KB, attachments |
| **AI automation layer** | Gemini/Vertex reply, classification, KB-grounded answers, SOP flows — live |
| **Customer 360 integration** | DMS/TSP two-way sync + 360 view card *(pending PROTON API access)* |
| **RBAC** | Roles & permissions model with admin UI |
| **Reporting suite** | BigQuery + Looker/Power BI dashboards, scheduled exports, anomaly alerts |
| **Data-migration report** | Record counts, fidelity validation, parallel-run results |
| **Operations runbook** | Deploy, backup/restore, monitoring, tenant provisioning, incident response |
| **Admin & agent training** | Sessions + materials for operators and administrators |
| **UAT sign-off & cutover** | Validated go-live and Zendesk decommission |

---

### Slide 12 — Why This Works

- **Proven:** the platform already runs live against PROTON's SOP — this is
  migration + hardening, not invention.
- **Open + owned:** no per-seat lock-in; PROTON controls the data, the AI, and
  the roadmap.
- **AI-native on Google:** Gemini + Vertex AI, grounded on PROTON's own
  knowledge — measurable deflection and faster resolution.
- **Cost-down, capability-up:** replace recurring SaaS licensing with managed
  infrastructure you scale on your terms.

*(speaker notes: Close on the same three words you opened with — Cost,
Ownership, Speed — now backed by everything in between.)*

---

## Appendix A — Requirement coverage snapshot (internal reference)

Detailed, evidence-backed status per requirement lives in
`docs/proton-crm-gap-analysis-2026-07-27.md`. Headline: of the six clusters,
four are substantially delivered, Reporting is delivered at the engine level
(Tier-2 tabs remaining), and **Customer 360 / DMS-TSP is the primary net-new
build** (blocked on PROTON API access). RBAC is fully designed, pending build.

> **Open items to confirm with PROTON** (carried from the gap analysis):
> (1) DMS + TSP API access, auth & data contracts; (2) telephony/CTI provider
> decision; (3) FAQ-source confirmation (SOP names "Zendesk" — superseded by the
> owned KB in this proposal); (4) the referenced **Escalation Policy** document;
> (5) Zendesk export access for migration.
