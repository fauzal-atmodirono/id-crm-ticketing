# Proton CRM Enhancement — phased development program spec

**Date:** 2026-07-18 · **Status:** program roadmap (design-level). Each phase becomes its
own implementation plan when executed. **Companion docs:**
`proton-requirements-gap-analysis.md` (Community feasibility + B1–B6),
`proton-vs-crm-requirements-comparison.md` (41-item Proton-vs-CRM matrix),
`2026-07-18-chatwoot-fork-foundation-design.md` (Phase 0 detail).

## Principles

- **Chatwoot Community**; **replace** enterprise-gated features with our own backend/fork —
  never unlock the proprietary `/enterprise` folder.
- **Reuse the `proton-conversational-ai` backend heavily** (the deep audit found §3/§4/§6
  cores already built).
- **Develop in `default` (reference tenant) → replicate** via one shared custom Chatwoot
  image + per-tenant runtime config (`PROTON_BACKEND_URL/KEY/FEATURES`).
- **Two repos:** `id-crm-ticketing` (deploy, fork, apps) + `proton-conversational-ai`
  (backend). Every phase notes which it touches.
- **Additive & reversible:** UI patches are additive; image rollback = revert
  `CHATWOOT_IMAGE` to stock.

## Requirement → phase map (all 41 items)

| Phase | CRM requirement items | Current status (from matrix) |
|---|---|---|
| **0 Foundation** | 3 (native insert), replaces Captain AI ✨; enables all UI | reuse + fork |
| **1 Filtering** | 41; self-service labels/views (Proton feedback) | mostly native |
| **2 Escalation/PIC** | 12, 13, 14, 15, 16 (+17,18 done) | 🟢 finish |
| **3 Reporting/BI** | 25, 28✓, 31, 33, 34, 35, 37, 38, 41 (+26,27,29,30,32,36,39,40 done) | 🟢 finish + views |
| **4 Customer 360** | 19, 20, 21, 22, 23, 24 | 🔴 greenfield (DMS/TSP) |
| **5 Agent routing** | 4, 5, 6, 7 | 🔴 greenfield |
| **6 Task timers** | 8 | 🟡 UI over live timers |
| **7 Telephony** | 1 (call+social), 3 (PSTN side) | 🟡 + provider |
| Native/config | 2 (notifications) | ✅ config only |
| **Already done — no phase** | 9, 10 (FAQ), 17, 18, 26, 27, 28, 29, 30, 32, 36, 39, 40 | ✅ shipped in `proton-conversational-ai` |

All 41 items are accounted for above — each appears in exactly one phase or is already shipped.

---

## Phase 0 — Fork foundation & Captain-AI replacement

**Detailed design:** `2026-07-18-chatwoot-fork-foundation-design.md`.
**Goal:** the patched-image pipeline + first two patches (rewire the ✨ reply-box menu to
our backend with native insert; native Proton left-nav menu) + runtime per-tenant config +
light right-panel via custom attributes + `default`→replicate rollout.
**Why first:** every UI phase rides on this image and the runtime-config mechanism.
**Repos:** both (`chatwoot-fork/` + compose here; `/assist/summarize|ask` in backend).
**DoD:** custom image builds off-VM → Artifact Registry; `default` shows menu + working AI
button (suggest/summarize/ask + insert); replicated to `proton`; stock rollback verified.

## Phase 1 — Self-service categorization & filtering

**Requirements:** 41 (+ Proton feedback: "add more categorization filtering by
themselves"). **Approach:** **native** — document the label taxonomy our AI already emits
(`category_/subcat_/division_/dept_`), enable admin label management + **saved custom
views/filters** in Chatwoot Settings, and (optional) a small **Taxonomy Manager** nav-app
so ops curates the category list without code.
**Reuse:** AI classification labels already written to every conversation.
**Net-new:** documented label conventions; saved filter presets; optional taxonomy app
(a nav-menu item on the Phase-0 host route).
**Dependency:** Phase 0 (only if the taxonomy app is built; pure filtering needs nothing).
**Deploy:** config replicated per tenant via API script. **DoD:** ops can create a
category label + a saved filter view and filter conversations by it, unaided.

## Phase 2 — Escalation / PIC completion (B4)

**Requirements:** 12–16 (17,18 already done). **Approach:** extend the backend + Zammad
config. Finish: **dept→PIC mapping** (config/Firestore table → sets Zammad group/owner +
Chatwoot team at escalation), **email-CC to PIC** (wire the existing SMTP sender into the
escalation path, with attachments), **WA-to-PIC at escalation time** (not only on breach),
**tiered higher-level escalation** (level-1 → level-2 timers), and **surface case states**
(WIP/Temp-Closed) into the Chatwoot right panel via custom attributes.
**Reuse:** classification, `ZammadClient` ticketing, SLA engine (8h/48h), audit log,
`CaseState` enum. **Net-new:** dept→PIC table, escalation email templates, escalation-tier
config, Temp-Closed state. **Repos:** backend (primary) + Zammad admin config.
**DoD:** an escalated case auto-routes to the mapped PIC, emails+CCs them, WA-alerts at
escalation, and re-alerts a higher level on the tier timer; states visible to agents.

## Phase 3 — Reporting & BI (B5)

**Requirements:** 25, 31, 33, 34, 35, 37, 38, 41 (rest of §6 done). **Approach:** point
**Power BI** at the **existing BigQuery views**; add the missing views — **peak-hours** and
**complaint-type ranking** (#35), **tasks-per-agent + avg response** (#33),
**first-response-by-channel** (#34), a **dealer dimension** + wire **`reopen_count`** on the
Chatwoot path for CRR (#31), and case-lifecycle/state-trend views (#37/#38). Add the
**Reports** nav item (Phase-0 host) embedding the Power BI report; scheduled PDF/Excel to
management already exists.
**Reuse:** BQ schema/views/sync/export/anomaly/NPS/CSAT pipeline. **Net-new:** Power BI
dataset connection, ~5 BQ views, dealer dimension, reopen wiring, Reports embed.
**Repos:** backend (views) + external Power BI. **DoD:** Power BI dashboards render all §6
analytics off live data; the Reports menu opens them; scheduled report still delivers.

## Phase 4 — Customer 360 & DMS/TSP (B1)

**Requirements:** 19–24. **Approach:** a backend **DMS + TSP integration adapter**
(ports-and-adapters) that, on inbound (caller/WA number), fetches customer/vehicle/service/
call-center data; upgrade the right panel from light attributes to a **rich forked
Customer-360 widget** (native, ≤3s async). **Reuse:** Dashboard-app template,
`ChatwootAdapter`, contact-identity matching, Phase-0 right-panel slot. **Net-new:** DMS +
TSP clients + auth, `/crm/customer360` endpoint, caching, the 360 widget patch.
**Dependency (blocker):** **DMS/TSP API access** (endpoints, auth, rate limits). **Repos:**
backend + `chatwoot-fork` (right-panel patch). **DoD:** on inbound, the agent sees the
4-panel 360 (personal/vehicle/service/call-center) in the conversation right panel within
3 s, no Contacts detour.

## Phase 5 — Agent Routing & Presence (B2)

**Requirements:** 4–7. **Approach:** greenfield **routing service** that reads Chatwoot
agent **availability** (online/busy/offline) via API, honors **per-agent channel priority**
(stored via a small config nav-app), assigns by **status-aware policy**, and **intelligently
switches** to idle agents when the priority pool is saturated — replacing static
single-team assignment. **Reuse:** `ChatwootAdapter` (assignment + availability read),
webhook ingest. **Net-new:** routing service, channel-priority store + config UI, presence
poll, fallback tiers. **Repos:** backend + a nav-app (Phase-0 host). **DoD:** inbound is
assigned to an available, priority-matched agent; when all priority agents are busy it
cascades to idle agents; ops configures priorities in-app.

## Phase 6 — Task Timers & Agent Reminders (B3)

**Requirements:** 8. **Approach:** a **"My Tasks"** agent app (Phase-0 nav or right-panel)
showing per-conversation **countdowns** over the **existing SLA timers**, plus **in-Chatwoot
+ desktop/sound notifications** on reminder/timeout. **Reuse:** SLA engine (8h/48h) + audit
log. **Net-new:** My-Tasks UI, per-agent timer surfacing, notification push (Chatwoot
notification API / websocket, or a fork if native badges are wanted). **Repos:** backend +
app (+ optional fork). **DoD:** an agent sees live countdowns for their open cases and gets
a reminder + timeout warning (in-app + desktop/sound).

## Phase 7 — Telephony / PSTN & channel unification (B6)

**Requirements:** 1 (call + social), 3 (PSTN side; STT + transcript→Chatwoot already done).
**Approach:** procure a **PSTN number / CTI** and feed it into the **existing Gemini-Live
STT + transcript→Chatwoot** bridge; add **live in-call Chatwoot presence** (conversation
opens during the call, not only at hang-up); enable **social-media inboxes**
(Facebook/IG/Telegram) as **native Chatwoot inboxes** (config). **Reuse:** phone bridge,
Gemini-Live STT, transcript-landing (built). **Net-new:** PSTN/CTI wiring, in-call presence,
social inbox config. **Dependency (blocker):** **telephony provider**. **Repos:** backend +
Chatwoot config. **DoD:** an inbound PSTN call opens a live Chatwoot conversation with
running transcript; social inboxes appear in the unified view.

---

## Sequencing & dependencies

```
Phase 0 (foundation) ── required by ──▶ 1, 3, 4, 5, 6  (all UI/nav/right-panel work)
Phase 2 (escalation) ── independent, reuses backend ── can run right after 0
Phase 3 (reporting)  ── independent, reuses BQ ── can run in parallel with 2
Phase 4 (360)  ── BLOCKED on DMS/TSP API access
Phase 7 (PSTN) ── BLOCKED on telephony provider
```

**Recommended order (value × readiness × dependency):**
**0 → 1 → 2 → 3 → 4 → 5 → 6 → 7.** Phases 1–3 are quick, high-reuse wins; 4 slots in as
soon as DMS/TSP access lands; 5–6 are the greenfield operational core; 7 waits on a phone
provider. 2 and 3 can run in parallel (different subsystems).

## Cross-cutting concerns (apply to every phase)

- **Multi-tenant replication:** the custom image is shared; per-tenant behaviour via
  `PROTON_FEATURES`/`PROTON_BACKEND_URL`. A tenant-config script applies Chatwoot API
  settings (labels, dashboard apps, webhooks, custom attributes) per tenant. Build/verify
  in `default`, then flip `proton`/`wahchan`.
- **Secrets/config:** new service creds (DMS, TSP, Power BI, telephony) go in backend
  Pydantic config + the tenant env; never committed.
- **Capacity:** UI/backend phases are light on the VM; heavy builds stay off-VM. New
  always-on backends run on Cloud Run, not the 16 GB VM.
- **Testing:** each phase ships with tests (backend unit + a local Chatwoot smoke for
  fork patches) and a `default`-first live smoke before replication.
- **Upgrade:** the Chatwoot-fork upgrade runbook (bump pin → re-apply patches → rebuild)
  covers every fork-touching phase (0, 4, 6-optional).

## Open external dependencies to unblock

1. **DMS + TSP API access** → unblocks Phase 4 (highest-value greenfield).
2. **Telephony/CTI provider** → unblocks Phase 7.
3. **Power BI licensing / workspace** → Phase 3 (BQ data is ready today).
4. **Per-tenant AI backend** for `default`/`wahchan` → their AI button/features stay
   `PROTON_FEATURES`-disabled until a backend URL exists (only `proton` has one now).
