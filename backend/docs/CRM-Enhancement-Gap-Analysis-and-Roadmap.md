# PROTON e.MAS — CRM System Enhancement: Gap Analysis & Next-Phase Roadmap

**Date:** 2026-07-16
**Source requirement:** `docs/CRM System Enhancement 260414.pdf` (PROTON e.MAS × PRO-NET, "Business Requirement — CRM System Enhancement")
**Analysed against:** current `proton-conversational-ai` monorepo (FastAPI/Gemini-ADK backend + Vue 3 frontend, Zendesk-centric ticketing, BigQuery/Looker metrics)
**Related doc:** `docs/superpowers/specs/2026-07-15-crm-enhancement-technical-proposal-deck.md` (the customer-facing proposal deck). **This document is the engineering-honest counterpart** — see the reconciliation note in §3.

---

## 1. Executive summary

The current system is a **production-grade omni-channel AI chatbot** (WhatsApp, email, phone/VOIP, web chat & voice) with **Zendesk ticketing** and a **BigQuery/Looker bot-metrics dashboard**. That foundation covers the *hardest and riskiest* parts of the BRD — real-time voice-to-text, multi-channel ingestion, KB-grounded AI answers, and an analytics warehouse.

However, the BRD describes an **agent-facing complaint-management CRM**, and the current product is a **customer-facing conversational bot**. The delta is not small:

- There is **no human-agent layer** — no roster, presence, channel-priority routing, task assignment, or reminders (BRD block B is essentially greenfield).
- There is **no unified agent inbox / single CRM view** — channels fan out into separate Zendesk surfaces (BRD block A's headline requirement is unmet).
- There is **no DMS/TSP integration and no Customer 360** (BRD block E is net-new, and gated on external API access).
- The **analytics warehouse is live, but the specific complaint reports the BRD asks for are mostly not built** — division, department/PIC, CRR, SLA achievement, agent-level NPS, export, and anomaly alerting are absent from the schema (BRD block F).

**Honest coverage headline:** of the six requirement blocks, **one is substantially live** (channels + STT + bot-side FAQ retrieval), **three have strong foundations but need real build-out** (agent FAQ surfacing, escalation timers/audit, complaint reporting), **one is thin foundation → mostly greenfield** (agent management), and **one is fully net-new and externally blocked** (Customer 360 / DMS+TSP).

---

## 2. Requirement-by-requirement gap analysis

Legend: ✅ **Live** · 🟡 **Partial (foundation exists, needs build)** · 🔴 **Net-new / Absent**

### A. Integration of Inbound — 🟡 Partial

| BRD line item | Verdict | Evidence / gap |
|---|---|---|
| All channels (Call/Email/WA/Social) auto-integrated | 🟡 | WA, email, phone, web chat & voice are **live** (`features/chat/router.py:261-301`, adapters `twilio_channel.py`, `zendesk.py`, phone `gemini_live.py`). **Social media (FB/IG/X) is absent** — no adapter or config. |
| Displayed in a **single CRM view** | 🔴 | **No unified inbox.** Each channel dispatches to a separate Zendesk surface (Support tickets vs. Sunshine Conversations messaging). Agents must check multiple places. This is the BRD's headline ask and it is unmet. |
| New inbound → **pop-up notification / system alert** | 🔴 | No new-inbound alert concept in backend or frontend. SSE stream (`router.py:787`) fans out *agent* replies post-handoff only. |
| Call → **voice recognition & change to text** | ✅ | Real-time STT is **live** via Gemini Live on the phone channel (`phone/gemini_live.py:87`, `input_audio_transcription`); user transcription surfaced in `models.py:76`. |

**Bottom line:** channels and STT are done; the *unified agent view* and *inbound alerting* — the actual CRM-desktop experience — are not.

### B. Agent Management — 🔴 Mostly absent (thin AI-triage foundation)

| BRD line item | Verdict | Evidence / gap |
|---|---|---|
| Check who is **on duty** before escalating | 🔴 | No agent roster / presence concept anywhere in the codebase. |
| **Channel priority per agent** (WA/Email/Call) | 🔴 | No agent affinity/priority config. |
| **Polling assignment** by agent status (idle/busy/offline) | 🔴 | No status model, no queue/assignment engine. Zendesk picks up tickets manually today. |
| Priority-channel busy → **switch to idle agents** | 🔴 | No overflow routing logic. |
| **Reminder + Timeout Warning** (sound/desktop/in-system) | 🔴 | None. SLA minutes are *captured* (`agents.py:48`) but never enforced or surfaced. |
| *(Foundation)* AI urgency/type classification before escalation | 🟡 | `classify_ticket_tool` captures category/subcategory/priority/SLA (`agents.py:43-68`) — a useful **pre-triage input** for a future router. |

**Bottom line:** this block is essentially greenfield. The one asset is that the AI already classifies urgency/type, which any future assignment engine can consume.

### C. FAQ template with AI support — 🟡 Partial

| BRD line item | Verdict | Evidence / gap |
|---|---|---|
| Integrate FAQ knowledge base | ✅ | `KnowledgePort` (`ports.py:68`) with Vertex AI Search (`adapters/vertex_search.py`) and Zendesk Guide (`zendesk.py:414`) adapters. |
| Auto-match FAQ by keywords, **real-time suggestion pop-ups** | 🟡 | The **bot** matches KB every turn (`agents.py:8`, phone `kb_tool.py`). But there is **no agent-facing suggestion panel** — this is a bot capability, not an agent-assist surface. |
| **One-click** agent reference / insert | 🔴 | No UI for an agent to pull matched articles into a reply. |
| Agent **feedback & scoring** on FAQ quality | 🔴 | No feedback capture on KB results. (A QA-label pipeline exists for conversation quality — `POST /qa/label` — which could be extended.) |
| **Real-time KB editing** by CRM team | 🔴 | KB ingest is an **offline scraper** (`src/scraper/`) → Vertex/Zendesk index. No live-edit endpoint. |

**Bottom line:** retrieval is strong; the *agent-assist experience* (suggest → one-click insert → rate) is unbuilt.

### D. Escalation Process — 🟡 Partial

| BRD line item | Verdict | Evidence / gap |
|---|---|---|
| Ticket creation + escalation | ✅ | `TicketingPort.create_ticket` (`ports.py:38`, `zendesk.py:78`); AI-triggered handoff (`service.py:227`, `agents.py:127`); detection-gated open/solved (`detection.py:8-20`). |
| **Rule engine** by category/dept + SOP → identify **PIC** | 🔴 | Classification is **prompt-based**, not a data-driven routing engine (`prompts.py:46`). No PIC concept; Zendesk assigns post-creation via its own rules. |
| Auto notify & **CC personnel by email**, with **attachments** | 🔴 | No CC field in `TicketingPort`; no attachment upload path in ports/adapters. |
| **WhatsApp alert to PIC** (optional) | 🔴 | Twilio WA send exists for customers, but no PIC-alert flow. |
| Case statuses **WIP / Resolved**; escalate → auto-remind higher level | 🟡 | Open/solved decided at creation; Zendesk manages workflow. No explicit WIP state or higher-level reminder. |
| Status changes log **agent / time / remarks** | 🟡 | Timestamps + author exist on comments/notes (`zendesk.py:239`, `handoff_store.py:57`); **no structured audit log** ("Agent A moved to WIP at 10:15, remark …"). |
| Configurable **auto-escalation: 8h no-response, 48h unresolved** | 🔴 | No SLA timer engine. SLA minutes captured but not acted on. **This is the core new mechanism.** |

**Bottom line:** the ticketing rails and notification channels exist; the **SLA-timer escalation engine, PIC routing, structured audit trail, and email CC/attachments** are the build.

### E. Customer Profile (Customer 360 via DMS + TSP) — 🔴 Net-new, externally blocked

| BRD line item | Verdict | Evidence / gap |
|---|---|---|
| Two-way DMS + TSP integration via API | 🔴 | **Absent.** No DMS/TSP adapter, config var, or code. `crm_provider` only supports chatwoot/zendesk (`config.py`). |
| Auto-identify customer by **caller / WA number** | 🔴 | Phone channel treats callers as anonymous (`session_id = phone-<CallSid>`); no identity resolution. |
| Pull customer / vehicle (VIN/VRN) / service-history / call-center data | 🔴 | No vehicle, dealer, or service-history data model anywhere. |
| **Customer 360 view card** (4 sections), auto-pop on inbound | 🔴 | No profile/360 UI in the frontend. |
| **≤3s sync, async progressive load** | 🔴 | N/A until the integration exists. |

**Bottom line:** this is the single largest net-new item **and it is gated on Proton providing DMS/TSP API access, schemas, auth, and rate limits.** Nothing here can be finished without that.

### F. Reporting & Analytics — 🟡 Partial (warehouse live, complaint reports mostly unbuilt)

| BRD required report | Verdict | Evidence / gap |
|---|---|---|
| Channel source analysis (Call/WA/Email/Social) | 🟡 | `v_volume_by_month_channel` exists (`metrics/bigquery_schema.py:26`). **No "Social" channel.** |
| **By case division** (Apps/Sales/Aftersales/Charging) | 🔴 | Category exists in tickets but is **not captured in the metrics schema** — no division column. |
| Daily / weekly / monthly trends | 🟡 | **Monthly only.** No daily/weekly views. |
| **Dept & PIC** analysis (distribution, response/resolution rankings) | 🔴 | **No agent_id / department / PIC dimension** in the conversations schema. |
| First-response / resolution-time rankings | 🟡 | First-response latency exists **by channel only** (`turn_schema.py:25`), not per agent; no resolution-time view. |
| **Complaint Reopen Rate (CRR)** | 🔴 | No reopen tracking in schema. |
| **SLA achievement rate** | 🔴 | No SLA fields persisted to metrics. |
| Call-centre KPI (tasks/agent, peak-hour, popular types) | 🔴 | No agent dimension; no hourly bucketing; complaint-type not extracted. |
| **NPS for agent** (Call & WA) | 🟡 | NPS pipeline is **live but channel-level** (`nps.py`, `POST /chat/nps`, `v_nps`). Not linked to an agent. |
| Case-lifecycle tracking (creation→close, state trends) | 🟡 | `created_at/updated_at/status/resolved_by` exist; no computed resolution-time or temp-closed state. |
| **Export PDF/Excel + auto-send to management** | 🔴 | No export or email-delivery mechanism. Sync scheduler exists (`scheduler.py`) but only refreshes BigQuery. |
| **Anomaly-warning dashboard** (channel spike) | 🔴 | No anomaly detection / alerting layer. |
| Run report by request tag/keyword | 🟡 | Tag parsing works for CSAT/NPS; no query API by arbitrary tag/keyword. |

**Bottom line:** the warehouse, ingestion, scheduler, and a channel-level bot dashboard are **live and provisioned** — but the **agent/department/complaint-oriented report set the BRD specifies is largely not modelled yet.** Much of it is unlocked cheaply by adding a few dimensions to the schema (see §4).

---

## 3. Reconciliation with the existing proposal deck ⚠️

The proposal deck (`2026-07-15-...-technical-proposal-deck.md`) markets **Reporting (block F) as "✅ Live"** covering dept/PIC analysis, CRR, SLA achievement, tasks-per-agent, and NPS-for-agent, and implies Escalation/FAQ are further along than the code supports. **The code does not back those claims:**

- Reporting F is **🟡, not ✅** — division, dept/PIC, CRR, SLA, agent-NPS, export, and anomaly reports are **absent from the schema**, not merely "configuration." The *warehouse* is live; the *reports* are not.
- Escalation D's audit trail and rule-engine/PIC are **🟡/🔴**, not implied-done.

This matters for planning and for what we commit to Proton. **Recommendation:** keep the deck's optimistic *foundation* framing (the risky infra is proven) but correct the coverage badges so the report set and audit/SLA work are shown as *build*, not *live*. Otherwise P4 gets scoped as "config" when it is real engineering.

---

## 4. Next phase — SHORT TERM (≈ next 2–6 weeks, no external dependency)

These are high-leverage, self-contained, and unblock demoable value without waiting on DMS/TSP. Ordered by leverage-to-effort.

1. **Instrument the metrics schema with the missing dimensions** *(highest leverage, low effort).*
   Add `division/category`, `agent_id/pic`, `department`, `sla_deadline`, `reopened`, and `first_response_at/resolved_at` to the `conversations` table, and backfill from the classification data the AI *already produces*. This single change unlocks: by-division analysis, dept/PIC rankings, CRR, SLA achievement, resolution-time, and agent-level NPS — the bulk of block F — because the data already flows, it just isn't landed. Also add daily/weekly rollup views (trivial).
   *Files:* `metrics/bigquery_schema.py`, `metrics/turn_schema.py`, `metrics/mapping.py`, `scripts/sync_zendesk_metrics.py`.

2. **Scheduled export + anomaly views.**
   Add PDF/Excel export of the dashboard and a scheduled email-to-management job (extend the existing `scheduler.py`), plus threshold-based anomaly views (channel-volume spike). Closes the remaining block-F line items.

3. **SLA-timer escalation engine + structured status audit log.**
   A timer service that reads the captured SLA minutes and fires 8h-no-response escalation and 48h-unresolved alarms, writing an append-only audit record (agent, from→to state, time, remark) on every status change. Builds directly on detection-gated ticketing. Add WIP as an explicit state. *(Block D core.)*

4. **Unified agent view (thin slice) + new-inbound alerting.**
   Either (a) surface all channels in one Vue "agent inbox" view backed by the existing SSE stream, or (b) standardise on the Zendesk agent workspace and wire desktop/sound/in-system new-inbound notifications. Decide build-vs-Zendesk-native early (see §6). *(Block A headline + inbound alert.)*

5. **Agent-assist FAQ panel.**
   Reuse the live `KnowledgePort` retrieval to render real-time keyword-matched suggestions in the agent view, with one-click insert and a thumbs-up/down that feeds the existing QA-label pipeline. *(Block C agent surface.)*

**Short-term outcome:** a demoable, agent-facing complaint system — unified view, AI-FAQ assist, SLA-driven escalation with audit trail, and the full BRD report set — **before the DMS/TSP integration even starts.**

---

## 5. Next phase — LONG TERM (≈ 1–4 months, includes externally-gated & larger builds)

1. **Customer 360 via DMS + TSP two-way integration** *(the primary net-new build; externally blocked).*
   New connector service behind a `CustomerProfilePort`, identity resolution keyed on caller/WA number, parallel API fan-out with caching for the ≤3s async target, and a 4-section Customer 360 card in the frontend that auto-pops on inbound. **Blocked on Proton delivering DMS/TSP API access, schemas, auth, and rate limits** — confirm these first (see §6). Build the port + stubbed adapter now so the UI proceeds in parallel.

2. **Full agent-management fabric.**
   Roster/presence, per-agent channel priority, status-based polling assignment (idle/busy/offline), priority-channel overflow switching, and reminder/timeout surfaces (sound/desktop/in-system). This is block B end-to-end and is a significant build — strongly consider Zendesk-native routing/omnichannel add-ons vs. bespoke.

3. **Rule-engine PIC routing + email CC/attachments + WA-to-PIC alerts.**
   Data-driven routing (category/department + SOP → PIC), email CC with attachment support, and optional WhatsApp PIC alerting. Completes block D beyond the SLA/audit slice done short-term.

4. **Social-media inbound channels** (Facebook/Instagram/X) to fully satisfy block A's "social media."

5. **Real-time KB editing** admin surface for the CRM team, closing the last block-C gap.

---

## 6. Critical dependencies & decisions needed

| Dependency / decision | Blocks | Action |
|---|---|---|
| **DMS/TSP API access, schemas, auth, rate limits** | All of Customer 360 (block E, long-term #1) | **Confirm with Proton before committing P3 dates.** Stub the port so UI/short-term work proceeds in parallel. |
| **Build vs. Zendesk-native** for agent management & unified inbox | Blocks A & B scope/effort | Decide early — Zendesk omnichannel routing may deliver presence/priority/overflow far cheaper than a bespoke engine. |
| **PDPA / PII handling** for customer & vehicle data | Block E go-live | Data-isolation + retention review before Customer 360 ships. |
| **Agent-desktop notification constraints** (if Zendesk) | Block A pop-up alerts | Validate desktop/sound notification support on the Zendesk agent app early. |

---

## 7. Effort-at-a-glance

| BRD block | Current state | Short-term (2–6 wks) | Long-term (1–4 mo) |
|---|---|---|---|
| A. Omni-channel inbound + STT | 🟡 channels+STT live, no unified view | Unified view slice + inbound alerts | Social channels |
| B. Agent management & routing | 🔴 greenfield (AI triage only) | — | Full presence/priority/polling/overflow/reminders |
| C. AI-FAQ | 🟡 bot retrieval live | Agent-assist panel + one-click + feedback | Real-time KB editing |
| D. Escalation & rule engine | 🟡 ticketing live | SLA timers (8h/48h) + audit log + WIP | PIC routing + email CC/attachments + WA-to-PIC |
| E. Customer 360 (DMS/TSP) | 🔴 net-new, **externally blocked** | Port + stub (UI proceeds) | Full DMS/TSP integration + 360 card |
| F. Reporting & analytics | 🟡 warehouse live, reports unbuilt | **Schema dimensions → most reports** + export + anomaly | Deeper lifecycle/CRR analytics |

---

*The riskiest engineering (real-time voice STT, multi-channel ingestion, AI-FAQ retrieval, the analytics warehouse) is already proven in production. The remaining work is mostly surfacing that foundation into an agent-facing CRM experience — plus one genuinely net-new, externally-gated integration (Customer 360). Start short-term work immediately; unblock DMS/TSP access in parallel so the long-term critical path can begin on time.*
