# PROTON e.MAS CRM Enhancement — What We Have vs. What Proton Asks

**Date:** 2026-07-16
**Requirement source:** `docs/CRM System Enhancement 260414.pdf` (BRD — CRM System Enhancement)
**Codebase:** `proton-conversational-ai` (FastAPI/Gemini-ADK backend, Vue 3 frontend, Zendesk ticketing, BigQuery/Looker metrics)

> This document is a **direct side-by-side**: for every requirement in the BRD, what concretely exists in *this* codebase (with `file:line` evidence), and how close it is. It is capability-focused, not a roadmap.

Match legend: ✅ **Have it** · 🟡 **Have part of it** · 🔴 **Don't have it**

---

## What this codebase actually is today

A **customer-facing conversational AI system**, not yet an agent-facing CRM desktop:

- **Backend** — FastAPI + Google ADK over Gemini. Feature-sliced: `features/chat` (text + orchestration) and `features/metrics` (BigQuery analytics). I/O behind ports (`ChatPort`, `TicketingPort`, `KnowledgePort`, `TextToSpeechPort` in `features/chat/ports.py`).
- **Channels live end-to-end** — Web chat, web voice, WhatsApp (Twilio), phone/VOIP (Twilio Media Streams + Gemini Live), email (Zendesk-native).
- **Ticketing** — Zendesk-centric: detection-gated ticket creation + Sunshine Conversations live-agent handoff.
- **Knowledge** — `KnowledgePort` over Vertex AI Search / Zendesk Guide, fed by an offline scraper (`src/scraper/`).
- **Analytics** — BigQuery warehouse (`conversations`, `turn_events`, `qa_labels`) + Looker dashboards + an in-app Vue dashboard (`DashboardView.vue`), refreshed by a scheduler.
- **Frontend** — Vue 3 SPA with exactly two views: a **customer** chat/voice/phone widget (`HomeView.vue`) and a **bot-metrics** dashboard (`DashboardView.vue`). No agent console.

The key framing: we own the **conversational + analytics + ticketing plumbing**. The BRD wants that plumbing wrapped in an **agent-operations CRM** (unified inbox, agent routing, SLA escalation, Customer 360). Below is exactly how each requirement lands against what we have.

---

## Requirement A — Integration of Inbound

| BRD requirement | What we have | Match |
|---|---|---|
| All channels (Call/Email/WA/Social) auto-integrated on one agent interface | WA, email, phone, web chat & voice all wired: `chat/router.py:261-301`; adapters `twilio_channel.py`, `zendesk.py`, `phone/gemini_live.py`. **Social media (FB/IG/X): none.** | 🟡 |
| Displayed in a **single CRM view** | **Not present.** Channels land in *separate* Zendesk surfaces (Support tickets vs. Sunshine messaging). No unified agent inbox exists in backend or frontend. | 🔴 |
| New inbound → **pop-up notification (system alert)** | **Not present.** The SSE stream (`router.py:787`) only pushes *agent* replies after handoff; no new-inbound alert. | 🔴 |
| Call → **voice recognition & change to text** | **Present & live.** Gemini Live real-time STT on phone: `phone/gemini_live.py:87` (`input_audio_transcription`); transcript surfaced via `models.py:76`. | ✅ |

**Have:** the channels themselves + real-time speech-to-text. **Missing:** the *single unified view* and *inbound alerting* — i.e., the actual agent-desktop surface.

---

## Requirement B — Agent Management

| BRD requirement | What we have | Match |
|---|---|---|
| Check who is **on duty** before escalating | Nothing. No agent/roster/presence concept anywhere. | 🔴 |
| **Channel priority per agent** (WA/Email/Call) | Nothing. | 🔴 |
| **Polling assignment** by agent status (idle/busy/offline) | Nothing. Tickets are picked up manually in Zendesk today. | 🔴 |
| Priority-channel busy → **switch to idle agents** | Nothing. | 🔴 |
| **Reminder + Timeout Warning** (sound/desktop/in-system) | Nothing enforced. SLA minutes are *captured* (`agents.py:48`) but never acted on or surfaced. | 🔴 |
| *(Adjacent asset)* AI pre-classifies urgency & type | `classify_ticket_tool` captures category/subcategory/priority/SLA at `agents.py:43-68` — a usable input for a future router. | 🟡 |

**Have:** effectively nothing agent-facing; only the AI's up-front urgency/type classification. **This block is the largest capability gap.**

---

## Requirement C — FAQ Template with AI Support

| BRD requirement | What we have | Match |
|---|---|---|
| Integrate FAQ knowledge base module | **Present.** `KnowledgePort` (`ports.py:68`) → Vertex AI Search (`adapters/vertex_search.py`) + Zendesk Guide (`zendesk.py:414`). | ✅ |
| Auto-match FAQ by chat keywords, **real-time suggestion pop-ups** | The **bot** matches KB every turn (`agents.py:8`; phone `kb_tool.py`). But there is **no agent-facing suggestion pop-up** — it drives bot replies, not an agent assist panel. | 🟡 |
| **One-click** agent reference / insert into reply | Not present — no agent UI to pull a matched article into a reply. | 🔴 |
| Agent **feedback & scoring** on FAQ quality | Not present for KB. (A conversation-quality QA-label pipeline exists — `POST /qa/label` — that could be reused.) | 🔴 |
| **Real-time KB editing** by CRM team | Not present. KB ingest is an **offline scraper** (`src/scraper/`) → search index; no live-edit endpoint. | 🔴 |

**Have:** solid KB retrieval powering the bot. **Missing:** the whole *agent-assist* experience (suggest → insert → rate) and live KB editing.

---

## Requirement D — Escalation Process

| BRD requirement | What we have | Match |
|---|---|---|
| Ticket creation & escalation | **Present.** `TicketingPort.create_ticket` (`ports.py:38`, `zendesk.py:78`); AI handoff (`service.py:227`, `agents.py:127`); open/solved gate (`detection.py:8-20`). | ✅ |
| **Rule engine** by category/dept + SOP → identify **PIC** | Classification is **prompt-based** (`prompts.py:46`), not a data-driven routing engine. No PIC concept; Zendesk assigns after creation. | 🔴 |
| Auto **notify & CC** personnel by email, with **attachments** | No CC field in `TicketingPort`; no attachment upload path in any adapter. | 🔴 |
| **WhatsApp alert to PIC** (optional) | Twilio WA send exists for *customers* (`twilio_channel.py`) but no PIC-alert flow. | 🔴 |
| Case statuses **WIP / Resolved**; escalate → auto-remind higher level | Open/solved decided at creation; Zendesk owns workflow. No explicit WIP state or higher-level reminder. | 🟡 |
| Status changes log **agent / time / remarks** | Timestamps + author on comments/notes (`zendesk.py:239`, `handoff_store.py:57`), but **no structured audit log**. | 🟡 |
| Configurable **auto-escalation: 8h no-response, 48h unresolved** | Not present. No SLA-timer engine. | 🔴 |

**Have:** ticket creation, detection-gated open/solved, and basic author/timestamp logging. **Missing:** the SLA-timer engine, PIC routing, structured audit trail, and email CC/attachments — the parts that make it a managed escalation *process*.

---

## Requirement E — Customer Profile (Customer 360 via DMS + TSP)

| BRD requirement | What we have | Match |
|---|---|---|
| **Two-way** DMS + TSP integration via API | Nothing. `crm_provider` only supports chatwoot/zendesk (`config.py`); no DMS/TSP adapter or config. | 🔴 |
| Auto-identify customer by **caller / WA number** | Nothing. Phone callers are anonymous (`session_id = phone-<CallSid>`); no identity resolution. | 🔴 |
| Pull customer / vehicle (VIN/VRN) / service-history / call-center data | Nothing. No vehicle, dealer, or service-history model exists. | 🔴 |
| **Customer 360 view card** (4 sections), auto-pop on inbound | Nothing. No profile/360 UI in the frontend. | 🔴 |
| **≤3s sync, async progressive load** | N/A — nothing to sync yet. | 🔴 |

**Have:** nothing. This is **fully net-new** and depends on Proton supplying DMS/TSP API access, schemas, auth, and rate limits — it cannot be built from this codebase alone.

---

## Requirement F — Reporting & Analytics

BigQuery + Looker is our "Power BI or equivalent." The **warehouse and pipeline are live**; the specific reports are mostly not modelled yet.

| BRD required report | What we have | Match |
|---|---|---|
| Channel source analysis (Call/WA/Email/Social) | `v_volume_by_month_channel` (`metrics/bigquery_schema.py:26`). No "Social" channel. | 🟡 |
| **By case division** (Apps/Sales/Aftersales/Charging) | Category exists in tickets but **is not landed in the metrics schema** — no division column. | 🔴 |
| Daily / weekly / monthly trends | **Monthly only**; no daily/weekly views. | 🟡 |
| **Dept & PIC** analysis (distribution, rankings) | **No agent_id / department / PIC** dimension in the schema. | 🔴 |
| First-response / resolution-time rankings | First-response latency **by channel only** (`turn_schema.py:25`); no per-agent, no resolution-time view. | 🟡 |
| **Complaint Reopen Rate (CRR)** | No reopen tracking. | 🔴 |
| **SLA achievement rate** | No SLA fields persisted to metrics. | 🔴 |
| Call-centre KPI (tasks/agent, peak-hour, popular types) | No agent dimension, no hourly bucketing, no complaint-type taxonomy. | 🔴 |
| **NPS for agent** (Call & WA) | NPS pipeline live but **channel-level** (`nps.py`, `POST /chat/nps`, `v_nps`); not linked to an agent. | 🟡 |
| Case-lifecycle (creation→close, state trends) | `created_at/updated_at/status/resolved_by` present; no computed resolution-time or temp-closed state. | 🟡 |
| **Export PDF/Excel + auto-send to management** | No export or email delivery. Scheduler (`scheduler.py`) only refreshes BigQuery. | 🔴 |
| **Anomaly-warning dashboard** (channel spike) | No anomaly/alerting layer. | 🔴 |
| Run report by request tag/keyword | Tag parsing works for CSAT/NPS; no query API by arbitrary tag/keyword. | 🟡 |

**Have:** the warehouse, ingestion, scheduler, and a channel-level **bot** dashboard (volume, resolution, CSAT, NPS, latency, fallback, bounce, quality — `DashboardView.vue`). **Missing:** the **agent/department/complaint-oriented report set** the BRD specifies. Much of it is a schema-dimension problem — the AI already produces category/priority/SLA data; it just isn't landed in BigQuery.

---

## Coverage scorecard

| BRD block | Have (✅) | Partial (🟡) | Missing (🔴) | One-line verdict |
|---|---|---|---|---|
| **A. Inbound integration** | Voice→text | Channels (no social) | Single view, inbound pop-up | Channels + STT done; **no unified agent view** |
| **B. Agent management** | — | AI urgency/type triage | Roster, presence, priority, polling, overflow, reminders | **Largest gap — essentially greenfield** |
| **C. AI-FAQ** | KB module | Bot keyword match | Agent panel, one-click, feedback, live-edit | Retrieval strong; **no agent-assist surface** |
| **D. Escalation** | Ticket creation | WIP/Resolved, audit basics | Rule/PIC routing, SLA timers, email CC/attach, WA-to-PIC | Rails exist; **the process engine is missing** |
| **E. Customer 360 (DMS/TSP)** | — | — | Everything | **Fully net-new; externally blocked on DMS/TSP APIs** |
| **F. Reporting** | Warehouse + channel bot dashboard | Trends, agent-NPS, lifecycle, tag-query | Division, dept/PIC, CRR, SLA, export, anomaly | **Infra live; the complaint report set is not built** |

**Net:** the risky infrastructure (real-time voice STT, multi-channel ingestion, KB retrieval, analytics warehouse) is **already ours and proven in production**. What the BRD asks for on top is largely the **agent-operations layer** — unified inbox, agent routing/SLA, and Customer 360 — plus landing the report data we already generate. One item (Customer 360 / DMS+TSP) is genuinely net-new and cannot start until Proton provides the external API access.

---

## Roadmap — closing the gaps

Two horizons: **short term** delivers a demoable agent CRM with no external dependency; **long term** covers the larger builds and the externally-gated Customer 360.

### Short term (≈ 2–6 weeks, no external dependency)

Ordered by leverage-to-effort:

1. **Land the missing metrics dimensions** *(highest leverage, low effort).* Add `division/category`, `agent_id/pic`, `department`, `sla_deadline`, `reopened`, `first_response_at/resolved_at` to the `conversations` table and backfill from the classification data the AI **already produces** (`agents.py:43-68`). Unlocks most of block F at once — by-division, dept/PIC rankings, CRR, SLA achievement, resolution-time, agent-NPS — plus trivial daily/weekly rollup views. *Files:* `metrics/bigquery_schema.py`, `metrics/turn_schema.py`, `metrics/mapping.py`, `scripts/sync_zendesk_metrics.py`.
2. **Scheduled export + anomaly views.** PDF/Excel export + scheduled email-to-management (extend `scheduler.py`) and threshold-based channel-spike views. Closes the remaining block-F items.
3. **SLA-timer escalation + structured audit log.** Timer service on the captured SLA minutes → 8h-no-response escalation + 48h-unresolved alarm, writing an append-only audit record (agent, from→to state, time, remark) on every status change. Add an explicit **WIP** state. *(Block D core.)*
4. **Unified agent view (thin slice) + new-inbound alerting.** Either a Vue "agent inbox" over the existing SSE stream, or standardise on the Zendesk agent workspace and wire desktop/sound/in-system alerts. *(Block A headline + inbound pop-up.)*
5. **Agent-assist FAQ panel.** Reuse live `KnowledgePort` retrieval to render keyword-matched suggestions in the agent view, with one-click insert and thumbs-up/down feeding the existing QA-label pipeline. *(Block C agent surface.)*

**Outcome:** a demoable agent CRM — unified view, AI-FAQ assist, SLA-driven escalation with audit trail, and the full BRD report set — **before DMS/TSP work even starts.**

### Long term (≈ 1–4 months, includes larger & externally-gated builds)

1. **Customer 360 via DMS + TSP** *(primary net-new; externally blocked).* New `CustomerProfilePort` + connector, identity resolution on caller/WA number, parallel API fan-out + cache for the ≤3s async target, and a 4-section 360 card that auto-pops on inbound. **Build the port + stub adapter now** so the UI proceeds in parallel while DMS/TSP access is confirmed.
2. **Full agent-management fabric.** Roster/presence, per-agent channel priority, status-based polling (idle/busy/offline), priority-channel overflow, reminder/timeout surfaces. Block B end-to-end — evaluate **Zendesk-native routing vs. bespoke**.
3. **Rule-engine PIC routing + email CC/attachments + WA-to-PIC alerts.** Completes block D beyond the short-term SLA/audit slice.
4. **Social-media inbound channels** (Facebook/Instagram/X) — finishes block A's "social media."
5. **Real-time KB editing** admin surface for the CRM team — last block-C gap.

### Critical dependencies & decisions

| Dependency / decision | Blocks | Action |
|---|---|---|
| **DMS/TSP API access, schemas, auth, rate limits** | Customer 360 (E, long-term #1) | **Confirm with Proton before committing dates.** Stub the port so other work proceeds in parallel. |
| **Build vs. Zendesk-native** for agent management & unified inbox | A & B scope/effort | Decide early — Zendesk omnichannel routing may deliver presence/priority/overflow far cheaper. |
| **PDPA / PII handling** for customer & vehicle data | E go-live | Data-isolation + retention review before Customer 360 ships. |
| **Agent-desktop notification limits** (if Zendesk) | A pop-up alerts | Validate desktop/sound support on the Zendesk agent app early. |
