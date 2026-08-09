# PROTON CRM Enhancement — Requirements → Chatwoot Platform gap analysis (2026-07-27)

**Source requirements:**
- `docs/CRM System Enhancement 260414.pdf` — PROTON e.MAS "Customer Complaint
  Management System" business requirement (User Operation).
- `docs/CRM Process Flow (1).xlsx` — target per-channel process flows + SLAs
  (sheets: WhatsApp, Social Media, Email, IVR Call, SSI).

**Target platform:** Chatwoot Community (free self-hosted) + our first-party
`agent/` and vendored `backend/` services, closing feature gaps with custom
Dashboard Apps / SPA-fork patches + a backend — **not** paid Chatwoot tiers.

**Audit date:** 2026-07-27. **Supersedes** `proton-requirements-gap-analysis.md`
and `proton-vs-crm-requirements-comparison.md` (both 2026-07-17/18) — those
audited the *external* `proton-conversational-ai` repo before it was vendored
here, and a large body of their "🔴 gap / 🟡 partial" items has **since shipped
as real, tested code**. See §9 for the delta.

## Legend

| Mark | Meaning |
|------|---------|
| ✅ **Done** | Code + tests in this repo (may be behind a default-off feature flag). |
| 🟢 **Native** | Delivered by Chatwoot Community out-of-the-box; config-only. |
| 🟡 **Partial** | Core capability shipped; a bounded gap remains (usually UI surfacing or a provider). |
| 🟠 **Designed** | Spec written and accepted; no code yet. |
| 🔴 **Gap** | Not started; genuine greenfield. |

---

## 1. Executive summary

The platform now **satisfies the large majority of the PDF requirements**. Of the
six requirement clusters, four are substantially delivered (Inbound integration,
Agent Management, FAQ+AI, Escalation), one is delivered at the engine level with
report-surface work remaining (Reporting), and **one is a genuine greenfield gap
(Customer Profile / Customer 360 + DMS/TSP integration)**. RBAC is fully designed
but not yet built.

| # | Requirement cluster (PDF) | Verdict | Headline gap |
|---|---------------------------|---------|--------------|
| 1 | Integration of Inbound | 🟡 mostly Done | No procured PSTN/CTI line; social has no AI wiring |
| 2 | Agent Management | ✅ Done | Sound/desktop reminders lean on Chatwoot-native |
| 3 | FAQ template with AI support | ✅ Done | One-click *insert* is copy-to-clipboard (iframe sandbox) |
| 4 | Escalation Process | ✅ Done | Email attachments still unwired (no source); states not fully surfaced in agent UI |
| 5 | Customer Profile (Customer 360) | 🔴 **Gap** | **DMS + TSP two-way integration, 360 card — not started** |
| 6 | Reporting / BI | 🟡 Done engine | Tier-2 views exist in BigQuery but not yet exposed as report tabs/endpoints |
| — | RBAC / roles & permissions (implied) | 🟠 Designed | Own build spec'd (`2026-07-27-own-sla-audit-rbac`); no code |

**The two things to escalate to PROTON:** (a) **Customer 360 / DMS+TSP** is the
biggest missing piece and is blocked on Proton providing API access + data
contracts; (b) the process-flow FAQ source is specified as **Zendesk** in the
XLSX ("AI answers based on FAQ given to Zendesk"), which conflicts with our
Chatwoot + pgvector KB direction — this needs a decision (see §7).

---

## 2. Integration of Inbound (PDF p.3)

| Requirement | Status | Evidence / gap |
|-------------|--------|----------------|
| All channels (Call/Email/WA/Social) auto-integrated on one agent interface, single CRM view | 🟡 | Chatwoot omnichannel is native; backend ingests web/API, WhatsApp (`test_router_twilio_whatsapp.py`), email (`test_chatwoot_email.py`), phone (`features/chat/phone/`). **Gap:** Social (FB/IG) are Chatwoot-native inboxes with no AI wiring; "Call" is Twilio voice, not a procured PSTN line. |
| New inbound → pop-up notification (system alert) | 🟢 Native | Chatwoot Community desktop + audio notifications, config-only. |
| Voice recognition & change to text on received call | ✅ / 🟡 | Real-time Gemini Live µ-law STT: `backend/.../features/chat/phone/gemini_live.py` + `bridge.py` write the transcript into a Chatwoot conversation on hangup (`test_bridge.py`, `test_gemini_live.py`). **Gap:** only the PSTN/CTI provider hookup remains. |

---

## 3. Agent Management (PDF p.3)

| Requirement | Status | Evidence / gap |
|-------------|--------|----------------|
| Check who is on duty before escalating to agent | ✅ | `features/routing/presence.py` (`PresenceFetcher`: online/busy/offline via Chatwoot API), `test_routing_presence.py`. *(Old docs said 🔴 — now shipped.)* |
| Support channel priorities per agent (WA/Email/Call priority…) | ✅ | Per-agent `channel_priorities` in `features/routing/store.py`; CRUD in `router.py`; no-code UI = `chatwoot-routing-admin` dashboard app. |
| Automatic polling task assignment by agent status (idle/busy/offline) | ✅ | `features/routing/service.py` 3-tier assignment; `test_routing_assignment.py`. |
| Intelligent switch to idle agents when priority channel is busy | ✅ | Tier-3 overflow fallback to un-prioritized online agents in `routing/service.py`. Gated `routing_enabled` (default off). |
| "Reminder" + "Timeout Warning" with sound/desktop/in-system notifications | ✅ / 🟡 | `features/tasks/deadline.py` computes response/resolution deadlines + remaining time; `/tasks/mine` + `chatwoot-my-tasks` app render the live countdown; `tasks_reminder_whatsapp_enabled` sends a WA reminder near breach. **Gap:** sound/desktop cues still rely on Chatwoot-native notifications rather than a bespoke channel. |

---

## 4. FAQ template with AI support (PDF p.4)

| Requirement | Status | Evidence / gap |
|-------------|--------|----------------|
| Integrate FAQ/KB module, update FAQ content in real time | ✅ | Two coexisting stores: Firestore Live-FAQ CRUD (`faq_admin_router.py` + `chatwoot-faq-admin`) **and** new pgvector KB (`kb_documents_router.py`, `kb_knowledge_router.py`, `kb_ingest.py`, HNSW cosine search; `test_pgvector_knowledge.py`). Delivered as native SPA Knowledge views. Gated `KNOWLEDGE_PG_ENABLED` (default off). |
| Auto-match relevant FAQ by chat keywords → real-time pop-up suggested replies | ✅ | `features/assist/router.py` (`/assist/suggest`), `copilot_router.py`/`copilot_tools.py` (KB-grounded copilot), `kb_suggest_router.py`. `/suggest` was just rewritten (2026-07-27) to synthesize the whole thread + ground retrieval on customer intent (commits 4fc3986/f00c10b/5f479f7/419d6f5). |
| One-click FAQ reference + feedback/score FAQ quality | 🟡 | Feedback/scoring 👍/👎 shipped (`features/metrics/faq_feedback.py`). **Gap:** true one-click *insert* is copy-to-clipboard — blocked by the Chatwoot iframe sandbox (documented limitation). |

---

## 5. Escalation Process (PDF p.4)

| Requirement | Status | Evidence / gap |
|-------------|--------|----------------|
| Rule engine by classification/category + SOP → identify PIC | ✅ | AI `classify_ticket_tool` emits `category_/subcat_/division_/dept_/sla_*` labels; `features/chat/pic_registry.py` resolves `dept→PIC` from `PIC_MAP_JSON` (`test_pic_registry.py`, `test_escalation_pic_wiring.py`). Bot auto-categorization on resolution in `agent/app/services/categorize.py`. *(Old docs said 🟡 passive — now shipped.)* |
| Notify + CC relevant personnel by email | ✅ | `features/chat/escalation_notifier.py` emails resolved PIC (To) and CCs the department's `cc_emails` (the "relevant personnel"), configured per-dept in `PIC_MAP_JSON` and gated by `escalation_cc_pic` (`test_escalation_notifier.py`: `test_notify_ccs_pic_cc_emails_when_enabled`). Gated `escalation_email_enabled`. *(2026-07-27: CC was a hardcoded empty list — now wired per-department.)* |
| …with attachments (photos, videos, web link) | 🟡 | The `SmtpEmailSender` + notifier accept an `attachments` param, but the notifier passes `[]` — **there is no source of attachment bytes from the conversation yet**. Wiring needs a decision on where attachments come from (Chatwoot message attachments on the escalated conversation). |
| Trigger WhatsApp alert to PIC (optional) | ✅ | Same notifier sends a WA alert to the PIC's registered number at escalation time (`test_escalation_notifier.py`). *(Old docs said 🟡 breach-only — now fires at escalation.)* |
| **Fires in Chatwoot-only mode** (Zammad retired) | ✅ | *(2026-07-27)* The notifier previously fired only inside the Zammad-gated path, so with Zammad off **no escalation email fired at all**. Refactored `_escalate_to_zammad` → `_fire_escalation` in `adapters/chatwoot.py`: ticket creation stays gated behind direct Zammad ticketing, but email/CC/WA/`case_state` fire regardless; the email references the Chatwoot conversation when no ticket exists (`test_escalation_pic_wiring.py`: `test_escalation_notifies_pic_in_chatwoot_only_mode`). |
| Case statuses (WIP/Resolved…) + status changes record agent/time/remarks | ✅ / 🟡 | `features/chat/case_state.py` (`CaseState`: NEW/OPEN/WIP/PENDING/SOLVED + **Temp-Closed**, `test_case_state_temp_closed.py`) + Firestore `case_audit_log` (actor/time). **Gap:** states not fully surfaced in the Chatwoot agent UI; audit is per-case (`/cases/{id}/audit`), no global audit list yet. |
| Auto-escalation: 8h no-response → higher level; 48h unresolved alarm | ✅ | Our own engine `features/chat/sla.py`: `SLA_BREACH_NO_RESPONSE` + `SLA_BREACH_UNRESOLVED` + `TIER2_ESCALATION` (level-2 alert `escalation_tier2_hours` after first breach → `escalation_level2_whatsapp`), APScheduler scan, audit dedup (`test_sla_tier2.py`). *(Old docs' 🔴 "SLA is Chatwoot-enterprise" is resolved — we're building our own; enterprise SLA is being disabled per `2026-07-27-own-sla-audit-rbac`.)* |

---

## 6. Customer Profile / Customer 360 (PDF p.5) — 🔴 largest gap

| Requirement | Status | Evidence / gap |
|-------------|--------|----------------|
| Two-way data connection with **DMS** (Dealer Mgmt System) + **TSP** (Telematics) | 🔴 | **Not started.** No DMS/TSP client, no vehicle/service data code anywhere in the repo. |
| Auto-identify customer by caller number / WA number | 🟡 | Only Chatwoot contact-identity matching by phone exists; no enrichment from external systems. |
| Customer 360 View Card (personal / vehicle / service / call-center history) auto pop-up | 🔴 | No `customer360` surface. Vehicle (VRN/VIN/model/dealer), service history (RO status), and consolidated cross-channel history are all absent. |
| ≤3s data sync, async loading | 🔴 | N/A until the integration exists. |

**This is the single biggest requirement gap and is not addressed by any
2026-07-19→27 spec.** It is blocked on PROTON providing DMS + TSP API access,
auth, and data contracts. Recommend a dedicated design spike once access is
granted (`Customer 360` dashboard app + a backend enrichment service reading
DMS/TSP, cached to hit the ≤3s budget).

---

## 7. Reporting / BI (PDF p.6)

Engine is **Done**; the remaining work is surfacing Tier-2 metrics as report tabs.

| Requirement | Status | Evidence / gap |
|-------------|--------|----------------|
| Channel source analysis; case division (Apps/Sales/Aftersales/Charging); daily/weekly/monthly trend | ✅ | `features/metrics/` — BigQuery schema+views (`bigquery_schema.py`), Chatwoot→BQ `sync.py`, dashboard (`dashboard_router.py`). |
| Dept/PIC analysis: distribution, first-response/resolution/reopen rates, CRR ranking | ✅ / 🟡 | Core shipped. Per `2026-07-21-reports-proton-analytics-merge-design.md`, dealer-dim + live reopen_count views exist in BigQuery but a few are **not yet exposed over HTTP / as native report tabs** (Phase B). |
| Call Centre KPI: SLA achievement, tasks-per-agent, avg (first) response, complaint-type ranking + peak hours | 🟡 | SLA-achievement + per-agent volume shipped; tasks-per-agent (#33), first-response-by-channel (#34), complaint-type ranking + peak-hours (#35) views exist in `bigquery_schema.py` but are **Designed-only at the endpoint/tab layer**. |
| NPS for agent (rate Call & WA agent) | ✅ | `features/metrics/nps.py` (+ `csat.py`). |
| Case lifecycle tracking (creation→close time map; Higher-escalation/WIP/Temp-Closed/Closed trend) | ✅ | Backed by `case_state.py` states + BQ lifecycle views. |
| Export to PDF/Excel + auto-send to management on a schedule | ✅ | `export.py`/`export_router.py` (XLSX+PDF) + `scheduler.py` (scheduled email). |
| Configurable Anomaly Warning Dashboard (real-time channel-spike prompts) | ✅ | `features/metrics/anomaly.py` + `anomaly_router.py` (z-score). |
| Run report by request tag / key work | ✅ | Tag reports in dashboard. |
| Power BI (or equivalent) | 🟢 | BI-tool-agnostic; current tool is Looker Studio. Power BI is a connection choice, not a build gap. |

---

## 8. Process-flow (XLSX) conformance — per-channel SLAs & statuses

The XLSX defines concrete per-channel timers/statuses. Our SLA engine
(`features/chat/sla.py`, `sla_ack_minutes_by_channel_json`) and lifecycle
auto-close (`agent/app/services/lifecycle.py`, spec `2026-07-23-conversation-
lifecycle-autoclose`) already model most of these; the table maps requirement →
support.

| Process-flow rule (source) | Value | Support |
|----------------------------|-------|---------|
| WhatsApp — live-agent ACK after escalation | 2 min | ✅ per-channel ACK window (`test_channel_ack_sla.py`) |
| Social Media — agent ACK after transfer | 2 working hrs | ✅ per-channel ACK window |
| Email — status update to customer | 4 working hrs | ✅ per-channel ACK window |
| IVR — agent answers call | 20 sec | 🟡 modeled as ACK window; depends on procured telephony |
| WhatsApp — idle warning "close in 5 min" | after 10 min idle | 🟡 auto-close engine exists; confirm warning-then-grace timings wired |
| WhatsApp — auto-close "Close due to inactive" | 10 min (out-of-hrs) / 15 min (in-hrs) | 🟡 auto-close shipped; per-branch threshold split needs config |
| Resolution prompt "Does your case resolve? YES/NO" | — | 🟡 resolution/lifecycle messages exist (`lifecycle.py`); confirm the YES/NO survey step |
| Rating survey (AI perf / agent perf) | — | ✅ NPS/CSAT (`nps.py`, `csat.py`) |
| Bot assigns case category on resolution | — | ✅ `agent/app/services/categorize.py` |
| Email — one auto-ack per new email/ticket, not per reply | — | ✅ email auto-ack rules (`2026-07-24-sop...emailack...` spec) |
| IVR — female AI voice; ">10s rings" bilingual busy prompt (Non-RSA) | — | 🟠 voice STT done; IVR prompt/voice-persona config is telephony-side, not yet built |
| Operating hours (Mon–Fri 8:30–17:30; Sat/Sun/PH 9:00–17:00) | — | 🟠 business-hours model is in the no-code roadmap backlog; currently config-driven |
| Team-Leader manual reassignment (WA/Social/Email) | — | 🟢 Chatwoot-native reassignment |
| **SSI Survey mechanism** (11-day send, 14-day expiry, >90% target, appeals RESEND/REVISED/EXCLUSION) | — | 🔴 **Not modeled** — SSI is a separate e.MAS-app dealer-satisfaction workflow (SOP UO/CRM01), out of the current Chatwoot scope. Flag for scoping. |

**⚠️ Source ambiguities to confirm with PROTON** (surfaced during extraction):
1. WhatsApp out-of-hours branch lists both a "10 min" idle-warning and a "10 min"
   auto-close — verify the intended thresholds vs the in-hours 15 min.
2. The FAQ engine in the flows is named **Zendesk** ("AI answers based on FAQ
   given to Zendesk"; "CRM to furnish the FAQ") — this contradicts our
   Chatwoot + pgvector KB direction. **Decision needed.**
3. Email & Social rating surveys say "Call Agent performance" verbatim — likely a
   template carry-over; confirm they apply to the email/social agent.
4. All four complaint channels defer to an "Escalation email flow based on
   Escalation Policy" that is referenced (`*Escalation Policy`) but **not defined
   in the workbook** — request that document to validate §5.

---

## 9. RBAC / roles & permissions (implied requirement) — 🟠 Designed

No `authz` / `require_permission` / role-model code exists yet. The newest spec
`docs/superpowers/specs/2026-07-27-own-sla-audit-rbac-design.md` (status: design
for review) fully specifies a per-tenant Postgres role model, `/authz` API,
`require_permission` dependency, and a 3-phase rollout — replacing Chatwoot's
enterprise custom-roles UI (which is being disabled for licensing). **This is the
one acknowledged capability gap with an accepted design and no code.**

---

## 10. Delta vs the stale 2026-07-18 gap docs

Flag these in any traceability review — the prior docs mark them as gaps, but
they have **since shipped**:

| Capability | Old-doc verdict | Now |
|------------|-----------------|-----|
| Agent presence / on-duty check | 🔴 not used | ✅ `routing/presence.py` |
| Channel priority + status routing + overflow (4 items) | 🔴 greenfield | ✅ `routing/service.py` 3-tier |
| My-Tasks countdown UI | 🟡 missing | ✅ `chatwoot-my-tasks` + `tasks/deadline.py` |
| dept→PIC map | 🔴/🟡 passive | ✅ `pic_registry.py` |
| Email CC + WhatsApp-at-escalation | 🔴 / 🟡 breach-only | ✅ `escalation_notifier.py` |
| SLA tier-2 escalation + per-channel ACK | 🔴 enterprise-only | ✅ `sla.py` |
| Temp-Closed state | flagged missing | ✅ `case_state.py` |
| pgvector no-code KB | not conceived | ✅ `kb_*` routers |
| Persona-driven bot + WhatsApp brain-swap + language | not conceived | ✅ orchestrator + `chat_turn` |

**Still genuinely open:** Customer 360 / DMS-TSP (🔴 Not started), RBAC (🟠
Designed), reports Tier-2 tabs/endpoints (🟠 Designed), unified no-code settings
UI (backlog), SSI survey workflow (🔴 out of current scope).

---

## 11. Recommended next steps (priority order)

1. **Customer 360 / DMS + TSP** — escalate to PROTON for API access + data
   contracts; scope a design spike (biggest gap, external dependency).
2. **Resolve the Zendesk-vs-Chatwoot FAQ-source decision** (§8 item 2) — it
   affects the whole FAQ+AI cluster's grounding.
3. **RBAC** — begin Phase 1 of `2026-07-27-own-sla-audit-rbac` (design accepted).
4. **Reports Tier-2** — expose the existing BigQuery views (#33/#34/#35) as
   endpoints + native report tabs (Phase B of the reports-merge spec).
5. **Confirm the four XLSX source ambiguities** (§8) and request the Escalation
   Policy document before certifying §5 conformance.
6. **Wire escalation email attachments** (§5) — decide the attachment source
   (Chatwoot message attachments on the escalated conversation) and plumb them
   through the notifier's existing `attachments` param.
7. **Scope SSI** — decide whether the e.MAS SSI survey workflow is in or out of
   the Chatwoot platform's remit.
