# PROTON CRM Enhancement — Requirements → Chatwoot Community gap analysis

**Source requirement:** `docs/CRM System Enhancement 260414.pdf` (PROTON e.MAS "Customer
Complaint Management System"). **Target:** deliver on the **Chatwoot Community**
(free self-hosted) edition; close feature gaps with **custom Dashboard Apps
(sidebar menus) + a backend**, not paid Chatwoot tiers. Date: 2026-07-17.

## Legend

| Mark | Meaning |
|---|---|
| ✅ **Native** | Works in Chatwoot Community — just configure |
| ⚙️ **Assemble** | Achievable with Community automation rules / webhooks / Zammad / a backend |
| 🔵 **Already built** | Covered by the existing `proton-conversational-ai` backend / dashboard apps |
| 🔴 **Gap → build** | Not in Community — needs a custom sidebar app, backend, or external tool |

## Chatwoot Community hard limits (confirmed, 2026)

Community edition does **NOT** include: **SLA management**, **Captain AI** (reply
suggestions / summaries / AI assist), **Agent Capacity & advanced/capacity-based
assignment**, **custom roles & permissions**, **customizable/custom dashboards**,
**voice calls (telephony)**, **agent scheduling**, **SSO/SAML**. Community round-robin
auto-assignment is basic (online agents only, not status/capacity/channel-priority
aware). These are the lines several requirements below fall on — so the answer is
"build it as a Dashboard App + backend," which is what this document plans.

## Existing assets — do NOT rebuild these

- **`proton-conversational-ai` backend** (Cloud Run `…run.app`): AI conversation,
  live-agent bridge (agent replies stream back to customer), **direct Zammad ticket
  creation** on complaints, and **AI classification** writing labels
  (`ai-escalation`, `category_*`, `subcat_*`, `division_*`, `dept_*`, `sla_<int>`)
  + `sla_minutes` custom attribute.
- **Dashboard Apps already live:** `FAQ Admin`, `FAQ Assist` (agent-assist).
- **Zammad** (ticketing back-office) + **agent-service** (Chatwoot↔Zammad sync —
  intentionally **unused for proton**; do not wire it, per the CRM handoff, to avoid
  double-ticketing).
- **Chatwoot Community** (the `proton` tenant on the shared VM).

---

## 1. Integration of Inbound

| Requirement | Verdict | How |
|---|---|---|
| All channels (Call/Email/WA/Social) in one agent interface & single CRM view | ✅ Native (Email, WhatsApp, FB, IG, Telegram, Line, SMS, Website, API) — configure each inbox. **Call is the exception** → 🔴 | Chatwoot omnichannel is core. Telephony is a paid/voice feature; in Community, integrate a CTI/softphone via the **API channel** + webhooks. |
| New inbound → pop-up notification (system alert) | ✅ Native | Chatwoot desktop + audio notifications; enable per agent (Profile → Notifications) and browser push. |
| On Call → voice recognition → text | 🔴 Gap → build | No native telephony/STT in Community. Needs external: CTI capture → STT (Whisper / Google STT) → post transcript into the Chatwoot conversation via API. |

## 2. Agent Management

| Requirement | Verdict | How |
|---|---|---|
| Check who is on duty before escalating | ⚙️ Assemble | Community exposes agent **availability** (online/busy/offline) + **business hours**; "on-duty-aware routing" is custom logic (backend reads availability via API before assigning). |
| Set **channel priorities** per agent (WA/Email/Call priority) | 🔴 Gap → build | Not a Chatwoot concept in any tier. Build an **Agent Routing config** (Dashboard App) + a routing service that stores per-agent channel priority and assigns accordingly. |
| **Polling assignment by agent status** (idle/busy/offline) | 🔴 Gap → build | Community round-robin ignores status/capacity (that's Enterprise Agent Capacity). Build status-aware assignment in the routing service (assign via API, skip busy/offline). |
| Intelligent switch to idle agents when priority channel is busy | 🔴 Gap → build | Same routing service — fallback tier when primary pool is saturated. |
| **Reminder** & **Timeout Warning** (sound/desktop/in-system) | 🔴 Gap → build | No per-task timers in Community (SLA is Enterprise). Build a **task-timer service** emitting Chatwoot notifications (API) + a sidebar "My Tasks" app with countdowns. Zammad's scheduler can drive back-office timers. |

## 3. FAQ template with AI support

| Requirement | Verdict | How |
|---|---|---|
| FAQ knowledge base, real-time updates | 🔵 Already built | `FAQ Admin` app + backend Firestore. (Chatwoot **Help Center/Portal** also exists in Community if a public KB is wanted.) |
| Auto-match FAQ by chat keywords + real-time suggestion pop-ups | 🔵 Already built | `FAQ Assist` (agent-assist) app + backend — this is the custom substitute for Captain AI's reply suggestions (which are paid). |
| One-click insert + feedback/score FAQ | 🔵 Already built (minor extension) | agent-assist app; add a thumbs-up/down → backend scoring if not already present. |

## 4. Escalation Process

| Requirement | Verdict | How |
|---|---|---|
| Rule engine by classification/category (+SOP) → identify PIC | ⚙️ Assemble (classification 🔵 done) | Backend already classifies (labels). Add a **department→PIC mapping** and set the Zammad ticket owner/group + Chatwoot team from it. Community **automation rules** can route by label/team for simple cases. |
| Notify & **CC relevant personnel by email** (+attachments) | ⚙️ Assemble | Zammad ticket triggers/notifications send email to owner/CC (Community Zammad). Or the backend sends templated email. |
| **WhatsApp alert to PIC** (optional) | 🔴 Gap → build | Outbound WA to a staff number via the WA API from the backend/agent-service. |
| Case statuses (Escalate / WIP / Resolved) + higher-level reminder | ⚙️ Assemble | Chatwoot: open/pending/resolved/snoozed + labels/custom attributes; **Zammad** has richer states (WIP/Temporary-Closed/Closed) — use Zammad as the state system of record. |
| Status changes record agent/time/remarks | ✅ Native | Zammad ticket history + Chatwoot activity log / private notes. |
| Auto-escalation **8h → higher level**, **48h unresolved alarm** | 🔴 Gap → build (Zammad-side feasible) | SLA is Enterprise in Chatwoot. Use **Zammad triggers + scheduler** (Community) for time-based escalation, or a backend scheduler emitting Chatwoot/email/WA alerts. |

## 5. Customer Profile — "Customer 360 View"

| Requirement | Verdict | How |
|---|---|---|
| Two-way data connection with **DMS** + **TSP** | 🔴 Gap → build | External systems — a backend integration service calling DMS/TSP APIs (two-way sync). Not a Chatwoot feature at any tier. |
| Auto-identify customer by caller / WA number | ⚙️ Assemble | Chatwoot already matches contacts by phone/WA identifier; enrichment from DMS/TSP is the custom part. |
| Pull Customer / Vehicle / Service / Call-Center info | 🔴 Gap → build | Backend queries DMS/TSP on inbound and returns the profile. |
| **Customer 360 View Card auto-pops on agent page** | 🔴 Gap → **flagship sidebar app** | Build a Chatwoot **Dashboard App** ("Customer 360") that renders in the conversation sidebar, receives the contact/conversation via `postMessage`, calls the backend, and shows the 4 panels (personal / vehicle / service / call-center). |
| ≤3s sync, async loading | ⚙️ (build constraint) | Backend caching + skeleton/async render in the 360 app. |

## 6. Reporting

| Requirement | Verdict | How |
|---|---|---|
| Power BI (or equiv) dashboards: channel-source, case-division, daily/weekly/monthly trends | 🔴 Gap → build (external BI) | Community reports are basic (Overview/Conversation/Agent/Label/Inbox/CSAT). Feed **Power BI / Metabase** from the Chatwoot + Zammad **APIs/DB** (read replica or a reporting DB). |
| Dept/PIC analysis: distribution, first-response/resolution ranking, **CRR (Complaint Reopen Rate)** | 🔴 Gap → build | Same BI layer; CRR/reopen needs the Zammad reopen data. |
| Call-Centre KPI: SLA achievement, tasks/agent, avg first-response, closure time, peak hours, complaint-type ranking | 🔴 Gap → build | BI layer over Chatwoot agent/conversation events + the task-timer service's SLA data. |
| **NPS for Agent** (rate agent from Call & WA) | ⚙️ Assemble | Chatwoot **CSAT** exists in Community (post-resolution survey); relabel as NPS and scope to Call/WA inboxes, or a custom survey app. |
| Case life-cycle tracking (creation→closing, state trends) | 🔴 Gap → build | BI layer over Zammad ticket state transitions. |
| Export PDF/Excel + **auto-send to management on schedule** | 🔴 Gap → build | Power BI subscriptions / a scheduled export job (Community Chatwoot has manual CSV download only). |
| Configurable **Anomaly Warning Dashboard** (e.g., channel explosion) | 🔴 Gap → build | Alerting on the BI/reporting layer (threshold rules → notify). |
| Run report by request/tag key work | ⚙️ Assemble | Chatwoot **Label reports** natively; richer tag analytics in the BI layer. |

---

## What to build (custom "sidebar menus" = Chatwoot Dashboard Apps) + backing services

Chatwoot **Dashboard Apps** are a Community feature: you register an app
(Settings → Integrations → Dashboard Apps) that renders your URL in an **iframe inside
the conversation sidebar**, and Chatwoot hands it the live `conversation` + `contact`
payload via `postMessage`. This is the same mechanism as `FAQ Admin`/`FAQ Assist` —
the sanctioned way to add UI to Community without the paid plan. Non-contextual tools
(admin/reporting) are hosted as static/SPA pages behind Caddy (as the FAQ apps already
are) and linked.

| # | Build | Type | Closes | Proton-repo status (audit 2026-07-17) |
|---|---|---|---|---|
| **B1** | **Customer 360 View** | Sidebar Dashboard App + backend | §5 (all) | 🔴 **Not present** — greenfield (contact-identity matching only) |
| **B2** | **Agent Routing & Presence** | Config app + routing service | §2 (channel priority, status polling, intelligent switch) | 🔴 **Not present** — static team assignment only; greenfield |
| **B3** | **Task Timers & Reminders** | Sidebar "My Tasks" app + timer service | §2 (reminder/timeout), §4 (8h/48h) | 🟡 **Partial** — server-side 8h/48h SLA timers live; missing agent countdown UI + in-Chatwoot push |
| **B4** | **Escalation / PIC engine** | Backend (extend proton backend) + Zammad config | §4 (PIC, email CC, WA alert, states) | 🟢 **Mostly done** — classification, Zammad ticketing, audit log, SLA timer, single-PIC WA alert; missing dept→PIC map + email CC |
| **B5** | **Reporting & BI** | External BI + reporting DB + anomaly alerts | §6 (all) | 🟢 **Mostly done** — BigQuery schema+views, sync, XLSX/PDF export, scheduled email, anomaly (z-score), NPS/CSAT; uses **Looker Studio not Power BI** |
| **B6** | **Telephony + Voice-to-Text** | CTI integration + STT → API channel | §1 (Call, transcription) | 🟡 **Partial** — Twilio softphone + Gemini Live real-time STT + transcript→ticket; missing PSTN/CTI + Chatwoot call plumbing |
| — | **FAQ AI** | Dashboard Apps + backend | §3 | 🟢 **Done** — FAQ Admin + FAQ Assist apps + Vertex-embedding semantic search + feedback |

> **Audit source:** `apps/backend` (FastAPI/Google-ADK/Gemini on Cloud Run), two static
> Dashboard Apps, Firestore + BigQuery. Full evidence in the "Proton-repo audit" section below.

### Suggested sequencing (highest value / lowest dependency first)

1. **B1 Customer 360** — biggest agent-experience win, self-contained once DMS/TSP
   access is available, and a clean showcase of the Dashboard-App pattern.
2. **B3 Task Timers** + **B2 Agent Routing** — the operational core (assignment +
   reminders) that the requirement leans on hardest.
3. **B4 Escalation/PIC** — mostly extends the existing backend + Zammad.
4. **B5 Reporting/BI** — parallel track; needs a data-export decision.
5. **B6 Telephony/STT** — largest external dependency (a phone provider); defer until
   the call channel is procured.

### Cross-cutting decisions to confirm before building

- **DMS / TSP API access** (endpoints, auth, rate limits) — blocks B1.
- **Who owns the dashboard-app code** — vendor into `id-crm-ticketing` or keep in
  `proton-conversational-ai` (same open question as the FAQ apps in the CRM handoff).
- **Reporting data source** — Chatwoot/Zammad API polling vs a Postgres read path.
- **Telephony provider** for the Call channel.
- **Multi-tenant fit** — these builds are proton-specific; decide whether they become
  reusable per-tenant modules in the CRM platform or proton-only.

---

## Proton-repo audit (`proton-conversational-ai`, 2026-07-17)

Stack: **Python 3.12 / FastAPI / Google ADK (Gemini 2.5)** backend on Cloud Run,
Vue 3 frontend, two **static single-file HTML** Dashboard Apps, **Firestore** (sessions,
FAQ vectors, audit log) + **BigQuery** (metrics). Ports-and-adapters architecture.

### What is already built (reuse, don't rebuild)

- **FAQ AI (§3) — DONE.** `apps/chatwoot-agent-app` (Assist) + `apps/chatwoot-faq-admin`
  (CRUD) + `backend/.../adapters/live_faq.py` (Vertex `text-embedding-004` semantic search,
  score floor 0.55) + `/kb/suggest`, `/kb/feedback`, `/kb/faq`. (One-click insert blocked by
  iframe sandbox → copy-to-clipboard, documented.)
- **B4 Escalation (§4) — mostly done.** AI classification → labels
  (`category_/subcat_/division_/dept_/sla_*` + `sla_minutes`); `ZammadClient.create_ticket()`
  (`ZAMMAD_DIRECT_TICKETING`); Firestore `case_audit_log`; **SLA engine** (`features/chat/sla.py`,
  APScheduler, `SLA_BREACH_NO_RESPONSE` 8h / `SLA_BREACH_UNRESOLVED` 48h); WA alert to a single
  `sla_pic_whatsapp` on breach. **Missing:** dept→PIC lookup table, email-CC to PIC, per-dept
  multi-PIC routing.
- **B5 Reporting (§6) — mostly done.** BigQuery `conversations` schema + views
  (`v_volume_by_month_channel`, `v_resolution_split`, `v_csat`, `v_nps`, `v_nps_by_agent`,
  `v_dept_pic_performance`, `v_sla_achievement`, `v_reopen_rate` = CRR, `v_resolution_time`);
  Chatwoot→BQ `sync.py`; XLSX+PDF `export.py` + `/metrics/export`; scheduled email reports;
  **anomaly** (`anomaly.py`, z-score channel volume) + `/metrics/anomalies`; `/chat/nps`,
  `/metrics/dashboard`. **Missing vs spec:** it's **Looker Studio, not Power BI** (data is
  BI-tool-agnostic); no peak-hours or complaint-type-ranking views yet.
- **B6 Telephony (§1) — partial.** Twilio Voice JS softphone (`/voice/phone/*`, Media Streams
  WS) + **Gemini Live** real-time µ-law↔PCM STT + transcript→ticket on hang-up.
  **Missing:** real PSTN number / call-centre CTI, and Chatwoot API-channel plumbing so calls
  land as Chatwoot conversations.
- **B3 Timers — partial.** The 8h/48h server timers above are reusable; **missing** only the
  agent-facing "My Tasks" countdown sidebar app + in-Chatwoot notification push.

### Greenfield (no existing code)

- **B1 Customer 360 / DMS / TSP** — nothing beyond contact-identity matching.
- **B2 Agent Routing & Presence** — only static single-team assignment; no channel-priority,
  availability-aware, or intelligent-switch routing.

### Reusable building blocks (the replication kit)

- **Dashboard App template** (`apps/chatwoot-agent-app/index.html`): self-contained HTML, no
  build step; postMessage handshake (`chatwoot-dashboard-app:fetch-info` → `appContext`
  `{conversation, contact}`); config via URL params (`backendBaseUrl`, `apiKey`); registered in
  Chatwoot → Integrations → Dashboard Apps; served static behind Caddy. **Copy this for B1.**
- **Backend adapters:** `ChatwootAdapter` (full CRUD, dual-header auth), `ZammadClient`
  (`create_ticket`/`add_note`) — reuse directly.
- **Webhook ingest:** `POST /webhooks/chatwoot` with HMAC `compare_digest` gate.
- **Config:** Pydantic `BaseSettings` in `platform/config.py` (add new service creds as fields).
- **API-key auth:** constant-time `x-api-key` compare (for privileged endpoints).
- **Label/attribute contract** (already emitted; consume, don't re-invent):
  `ai-escalation, category_*, subcat_*, division_*, dept_*, sla_*` + `sla_minutes`.

### Fastest path per build

1. **B1 Customer 360** — copy `chatwoot-agent-app/index.html` → new `customer360` app; add one
   backend route `GET /crm/customer360?phone=…` + a DMS/TSP adapter (ports-and-adapters). The
   iframe handshake, CORS, and async-skeleton pattern are already solved.
2. **B4 finish** — add a dept→PIC map (JSON env or Firestore) and wire Zammad `group`/owner +
   email-CC to it. Everything else runs.
3. **B3 finish** — build the "My Tasks" countdown Dashboard App over the existing SLA timers.
4. **B5 finish** — point **Power BI** at the existing BigQuery views (or keep Looker Studio);
   add the two missing views.
5. **B2 / B6-PSTN** — greenfield; need Chatwoot-API routing logic and a telephony provider.
