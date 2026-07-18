# Proton vs CRM Requirements — comparison matrix

**Purpose:** line-by-line traceability of the CRM enhancement requirements
(`docs/CRM System Enhancement 260414.pdf`) against what the
**`proton-conversational-ai`** repo already provides, so this CRM phase reuses what
exists and only builds the true gaps. Audit date: 2026-07-17. Companion doc:
`proton-requirements-gap-analysis.md` (Chatwoot Community feasibility + build plan).

**Legend:** ✅ Done · 🟡 Partial · 🔴 None (greenfield) · *"CW"* = Chatwoot Community native.

> **Deep-verified against `apps/backend` source (2026-07-17, second pass with file:line
> evidence).** The first pass under-counted two items — corrected below: **#3** (call
> transcript IS written into Chatwoot) and **#28** (daily/weekly views exist). All other
> verdicts were confirmed against the code.

## Scoreboard

| Section | ✅ Done | 🟡 Partial | 🔴 None |
|---|---|---|---|
| §1 Inbound integration | 2 | 1 | 0 |
| §2 Agent management | 0 | 1 | 4 |
| §3 FAQ + AI | 2 | 1 | 0 |
| §4 Escalation | 2 | 4 | 1 |
| §5 Customer 360 | 0 | 1 | 5 |
| §6 Reporting | 9 | 7 | 1 |
| **Total (41 items)** | **15** | **15** | **11** |

Reading: **§3, §4, §6 are largely covered** by the proton backend; **§2 (routing) and
§5 (Customer 360 / DMS-TSP) are the net-new work**. The genuine greenfield is only 11
items, and 8 of those are §2-routing + §5-Customer-360.

---

## §1 — Integration of Inbound

| # | CRM requirement | Status | What Proton / the platform already provides | Remaining gap |
|---|---|---|---|---|
| 1 | All channels (Call/Email/WA/Social) in one agent interface & single view | 🟡 | CW omnichannel native; backend ingests web/API/email (`/webhooks/chatwoot`), WA (`/webhooks/twilio-whatsapp`), phone (`/voice/phone/*`) — all land in Chatwoot | Social (FB/IG/Telegram) have **no backend wiring** (CW-native inbox only, no AI); calls visible in Chatwoot only **after** hang-up (no live in-call presence) |
| 2 | New inbound → pop-up notification (system alert) | ✅ | CW native desktop + audio notifications (configure per agent) | — (config only) |
| 3 | On call → voice recognition → text | ✅ | **Gemini Live** real-time STT (`phone/gemini_live.py`, `AudioTranscriptionConfig`); on hang-up `PhoneBridge.finalize()` → `ConversationLogPort.ensure_conversation_ticket()` + `append_conversation_comment()` **writes the full transcript into a Chatwoot conversation** | (PSTN/CTI provider still needed for §1 #1 — the STT+transcript-landing itself is done) |

## §2 — Agent Management

| # | CRM requirement | Status | Provided | Remaining gap |
|---|---|---|---|---|
| 4 | Check who is on duty before escalating | 🔴 | CW exposes agent availability via API; not used (proton assigns a static team) | Read availability before routing |
| 5 | Channel priorities per agent | 🔴 | — | Full build (config app + routing service) |
| 6 | Polling assignment by agent status (idle/busy/offline) | 🔴 | Static single-team assignment only | Status-aware assignment service |
| 7 | Intelligent switch to idle agents when priority channel busy | 🔴 | — | Fallback routing tier |
| 8 | Reminder & Timeout Warning (sound/desktop/in-system) | 🟡 | Server-side SLA timers (8h/48h) + WA breach alert (`sla.py`) | Agent-facing "My Tasks" countdown UI + in-Chatwoot push |

## §3 — FAQ template with AI support

| # | CRM requirement | Status | Provided | Remaining gap |
|---|---|---|---|---|
| 9 | FAQ knowledge base, real-time updates | ✅ | `chatwoot-faq-admin` app (CRUD) + Firestore live store (`live_faq.py`) | — |
| 10 | Auto-match FAQ by keywords + real-time suggestion pop-ups | ✅ | `chatwoot-agent-app` (Assist) + `GET /kb/suggest` (Vertex `text-embedding-004` semantic search, floor 0.55) | — |
| 11 | One-click refer + feedback/score | 🟡 | `POST /kb/feedback` (👍/👎) done; copy-to-clipboard | True one-click insert blocked by CW iframe sandbox (documented limitation) |

## §4 — Escalation Process

| # | CRM requirement | Status | Provided | Remaining gap |
|---|---|---|---|---|
| 12 | Rule engine by classification/category (+SOP) → identify PIC | 🟡 | AI `classify_ticket_tool` → labels `category_/subcat_/division_/dept_/sla_*` + `sla_minutes`. A `pic_*` label convention exists but is **passive** (metrics only read it) | No dept→PIC lookup/SOP table; nothing auto-assigns a PIC at escalation |
| 13 | Notify & CC personnel by email (+attachments) | 🔴 | Zammad ticket + Chatwoot private note only | Templated email CC to PIC |
| 14 | Trigger WhatsApp alert to PIC (optional) | 🟡 | Single global `sla_pic_whatsapp` alert (Twilio) — fires **only on SLA breach**, not at escalation creation | Per-dept PIC WA sent **at escalation time** |
| 15 | Escalate → reminder of higher-level responsible person | 🟡 | 8h `SLA_BREACH_NO_RESPONSE` event fires | Explicit higher-level routing/reminder target |
| 16 | Records of WIP and Resolved operations | 🟡 | `CaseState` enum (NEW/OPEN/WIP/PENDING/SOLVED) in `case_state.py` + `_CHATWOOT_STATUS_TO_STATE` map + Firestore `case_audit_log` + `GET /cases/{id}/audit` | No **Temp-Closed** state; states not surfaced in the Chatwoot agent UI |
| 17 | Status changes record agent/time/remarks | ✅ | Audit log + Zammad ticket history + CW activity/private notes | — |
| 18 | Auto-escalation 8h → higher level, 48h unresolved alarm | ✅ | SLA engine (`sla_response_hours=8`, `sla_resolution_hours=48`, APScheduler) | — |

## §5 — Customer Profile (Customer 360 View)

| # | CRM requirement | Status | Provided | Remaining gap |
|---|---|---|---|---|
| 19 | Two-way data connection with DMS + TSP | 🔴 | — | Full DMS/TSP integration service |
| 20 | Auto-identify customer by caller / WA number | 🟡 | `_find_or_create_contact()` matches by phone/session | Enrichment from DMS/TSP |
| 21 | Pull Customer / Vehicle / Service / Call-Center info | 🔴 | Call-center history exists in CW/BQ; no vehicle/service data | DMS/TSP data fetch |
| 22 | Customer 360 View Card auto-pops on agent page | 🔴 | — | Sidebar Dashboard App (copy the FAQ-app scaffold) |
| 23 | Card 4 panels (personal / vehicle / service / call-center) | 🔴 | — | The 360 app UI |
| 24 | Data sync ≤3s, async loading | 🔴 | — | Backend caching + async skeleton render |

## §6 — Reporting

| # | CRM requirement | Status | Provided | Remaining gap |
|---|---|---|---|---|
| 25 | Power BI (or equivalent) integration | 🟡 | BigQuery views + **Looker Studio** (BI-tool-agnostic data) | Connect Power BI to the BQ views |
| 26 | Channel source analysis by case | ✅ | `v_volume_by_month_channel` | — |
| 27 | By case division (Apps/Sales/Aftersales/Charging) | ✅ | `v_volume_by_division` + `division_*` label | — |
| 28 | Daily/weekly/monthly trend chart | ✅ | `v_volume_daily` + `v_volume_weekly` (`DATE_TRUNC(...,WEEK)`) + `v_volume_by_month_channel` all in `bigquery_schema.py` | (daily/weekly not surfaced in the in-app dashboard payload, but exist as BQ views for Power BI) |
| 29 | Dept/PIC case distribution + resolution stats | ✅ | `v_dept_pic_performance` | — |
| 30 | First-response / resolution rate & time ranking | ✅ | `v_dept_pic_performance` + `v_resolution_time` | — |
| 31 | Complaint Reopen Rate (CRR) by dealer/dept/PIC | 🟡 | `v_reopen_rate` (by dept/PIC) | No **dealer** dimension; **`reopen_count` is NULL on the Chatwoot path** (`mapping.py` TODO) — CRR only populates from legacy Zendesk, so needs Zammad reopen timing wired |
| 32 | Call-Centre KPI — SLA response achievement rate | ✅ | `v_sla_achievement` | — |
| 33 | Tasks per agent + avg response time | 🟡 | `agent_id`, `turn_events` latency streamed | Explicit per-agent tasks/response view |
| 34 | Avg first-response + avg closure time by channel | 🟡 | **Closure-by-channel done** (`v_resolution_time` by channel+division); first-response only in `v_dept_pic_performance` | Add first-response-time **by channel** view |
| 35 | Complaint-type ranking + peak-complaint hours | 🔴 | Category labels captured (data only) | The two BQ views (ranking + peak-hours) |
| 36 | NPS for Agent (Call & WA) | ✅ | `POST /chat/nps` → `v_nps`, `v_nps_by_agent` | (channel scoping is config) |
| 37 | Case life-cycle map (creation → closing) | 🟡 | Timestamps + `v_resolution_time` | Lifecycle visualization |
| 38 | State trend analysis (escalation/WIP/temp-closed/closed) | 🟡 | `v_resolution_split` + audit-log states | WIP/temp-closed dimensions |
| 39 | Export PDF/Excel + auto-send to management on schedule | ✅ | `export.py` (XLSX+PDF) + `/metrics/export` + scheduled email (`run_report_job`) | — |
| 40 | Configurable Anomaly Warning Dashboard | ✅ | `anomaly.py` (z-score channel volume) + `/metrics/anomalies` + alert email | — |
| 41 | Run report by request/tag key work | 🟡 | CW Label reports native; labels in BQ | Ad-hoc tag-report surface |

---

## Bottom line for this CRM phase

- **Directly reusable (port as-is):** FAQ system (§3), classification + Zammad ticketing +
  audit/CaseState + SLA-timer engine (§4 core), real-time call STT + transcript-to-Chatwoot
  (§1 #3), and the whole BigQuery metrics/export/anomaly pipeline (§6 core) — with **Power BI
  simply pointed at the existing BQ views**.
- **Finish (hours–days):** dept→PIC map + email-CC + WA-at-escalation (§4 #12–14); the report
  views still missing — complaint-type-ranking + peak-hours (#35), first-response-by-channel (#34),
  tasks-per-agent (#33), and wiring `reopen_count`/dealer for CRR on the Chatwoot path (#31);
  the "My Tasks" countdown app (§2 #8); social-channel inboxes (§1 #1, CW-native config).
- **Net-new (greenfield):** **Customer 360 / DMS-TSP** (§5, 5 items) and **agent
  routing/presence** (§2 #4–7, 4 items), plus procuring a **PSTN/CTI provider** to feed the
  (already-built) call STT. Customer 360 is the highest-value greenfield item and reuses the
  established Dashboard-App + backend-adapter patterns.
