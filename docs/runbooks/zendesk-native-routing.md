# Zendesk-Native Routing & Agent Management — Configuration Runbook (Short-Term Item 4)

**Date:** 2026-07-16
**BRD source:** `docs/CRM System Enhancement 260414.pdf` — blocks **A** (Integration of Inbound: single view, new-inbound pop-up) and **B** (Agent Management: on-duty check, per-channel priority, status-based/polling assignment, overflow, reminders/timeouts).
**Decision:** the agent surface is **Zendesk-native** (Agent Workspace + omnichannel routing), not a custom console. This repo's job is to **feed Zendesk the metadata routing needs**; the routing, presence, and notifications themselves are Zendesk admin configuration, documented here.

> Why this is mostly configuration: the ticket-metadata contract that native routing keys on (priority + classification tags) is already emitted by the backend (shipped in short-term Item 1). See the contract below and `apps/backend/src/chatbot/features/chat/adapters/zendesk.py` `create_ticket`.

> **Note on admin paths:** the Zendesk Admin Center reorganizes menus periodically and some features (Omnichannel routing, skills-based routing) require specific Suite/Support plan tiers. Treat the menu paths below as a guide and confirm exact locations + plan availability against your live Admin Center; the *concepts* (presence, capacity, skills, queues, SLA policies, notifications) are stable.

---

## 1. The ticket-metadata contract (what the backend emits)

Every escalated ticket the backend creates carries the fields Zendesk routing, SLA, and views key on. This is the integration seam — Zendesk config below relies on it.

| Field on the Zendesk ticket | Source | Values |
|---|---|---|
| `priority` | AI urgency → `priority_map` (`zendesk.py`) | `low` / `normal` / `high` / `urgent` (from urgency `low`/`medium`/`high`/`urgent`; default `normal`) |
| tag `division_<x>` | AI category → `CATEGORY_TO_DIVISION` | `division_apps` / `division_sales` / `division_aftersales` / `division_charging` |
| tag `category_<x>` | AI `classify_ticket_tool` | e.g. `category_aftersales` |
| tag `subcat_<x>` | AI `classify_ticket_tool` | e.g. `subcat_battery_issue` |
| tag `sla_<minutes>` | AI `classify_ticket_tool` | e.g. `sla_480` |
| `external_id` | session id | e.g. `whatsapp-+60123`, `email-…`, `phone-…`, `sim-…` (channel-encoded) |

The channel is encoded in `external_id`'s prefix, so **views and routing can segment by channel** without extra fields. Tags are lowercased with spaces→underscores (`_norm`). The contract is locked by `test_zendesk.py::test_create_ticket_emits_classification_tags` and `test_create_ticket_priority_matches_urgency`.

**PIC / department note:** the classifier does not currently produce `department`/`pic`; those tags are emitted only when supplied, so today production routes on **division + priority + channel**. Per-PIC assignment happens inside Zendesk (group/assignee) and is read back into metrics via the ticket `assignee_id` (short-term Item 1). If SOP-driven PIC routing is later required, add a `department`/`pic` classifier output — tracked as a follow-up.

---

## 2. Block A — Single view + new-inbound alerts

**Requirement:** all channels (Call/Email/WhatsApp/Social) in one agent interface; every new inbound raises a pop-up/system alert; calls are transcribed to text.

**Zendesk config:**
1. **Enable Agent Workspace** (Admin Center → Workspaces → Agent Workspace). This unifies email, messaging (WhatsApp via the messaging/Sunshine channel), talk, and social messaging into one ticket interface — satisfying "single CRM view."
2. **New-inbound notifications** (per agent + admin default):
   - **Desktop notifications**: Agent Workspace → notification settings → enable browser/desktop notifications for new tickets and new messages.
   - **Sound alerts**: enable the incoming-message/omnichannel sound in the agent's notification preferences.
   - **In-app toasts**: native to Agent Workspace for newly assigned/updated conversations.
3. **Voice→text**: already handled upstream — the phone channel streams to Gemini Live for real-time STT (see the phone/VOIP feature); the transcript reaches the agent as ticket text. No Zendesk config needed.

**Coverage:** single view ✅ (Agent Workspace) · new-inbound pop-up ✅ (desktop/sound/in-app) · STT ✅ (backend). Social-media *inbound channels* beyond WhatsApp remain a backend follow-up (Item A gap), not a routing-config item.

---

## 3. Block B — Agent management & routing

**Requirement:** check who is on duty before escalating; per-agent channel priority (WhatsApp/Email/Call); automatic status-based (idle/busy/offline) polling assignment; overflow to idle agents when the priority channel is busy; reminders + timeout warnings.

**Zendesk config (Omnichannel routing — Admin Center → Objects and rules → Omnichannel routing):**

| BRD requirement | Zendesk feature | How |
|---|---|---|
| Check who is **on duty** before escalating | **Agent status / presence** | Omnichannel routing only assigns to agents whose unified status is *Online*; *Offline*/*Away* agents are skipped. |
| **Per-channel priority** per agent | **Routing configuration + capacity rules** | Set per-channel capacity per agent (e.g. Talk 1, Messaging 3, Email 5) so an agent's availability is weighted per channel; use skills so WhatsApp-priority agents receive messaging first. |
| **Status-based polling assignment** (idle/busy/offline) | **Omnichannel routing auto-assignment** | Routing auto-assigns queued tickets to the eligible agent with the most spare capacity; busy (at capacity) and offline agents are not selected — this is the "polling by status." |
| Priority channel busy → **switch to idle agents** | **Capacity overflow + skills fallback** | When all skilled/priority agents are at capacity, routing falls through to the next eligible online agent with spare capacity. Configure a fallback group so overflow lands on idle generalists. |
| Route by **division/type** | **Skills-based routing** on the `division_*` tag | Create skills per division (Apps/Sales/Aftersales/Charging); a trigger maps the incoming `division_*` tag → skill so the ticket routes to the right team. |
| Prioritize urgent work | **Queues ordered by `priority`** | Order routing queues by ticket `priority` (urgent→low) so high-priority cases are assigned first. |
| **Reminders + Timeout warnings** | **SLA policies + time-based Automations** | Define SLA targets (first-response/resolution) — the backend already emits `sla_<minutes>`. Time-based Automations fire reminders and escalate on breach; these also call the backend SLA-escalation webhook (short-term Item 3) which records the audit trail + optional WhatsApp-to-PIC alert. |

### Setup order (recommended)
1. Turn on **Agent Workspace** + **Omnichannel routing**.
2. Create **skills** for the four divisions; add a **trigger**: *if tag contains `division_aftersales` → add skill "Aftersales"* (repeat per division).
3. Set **per-agent capacity** per channel (this encodes channel priority).
4. Create **routing queues** ordered by `priority`, with a **fallback queue** for overflow.
5. Configure **SLA policies** (targets by priority) + the **8h/48h time-based Automations** from the [SLA-escalation runbook](../dashboards/sla-escalation-audit.md).
6. Enable **desktop + sound notifications** per Block A.

---

## 4. BRD requirement → Zendesk feature → backend input (traceability)

| BRD line | Zendesk feature | Backend input |
|---|---|---|
| Single CRM view | Agent Workspace | `external_id` channel prefix; unified ticket |
| New inbound pop-up | Desktop/sound/in-app notifications | ticket create event |
| Call → text | (upstream) Gemini Live STT | transcript in ticket body |
| On-duty check | Agent presence | — |
| Channel priority | Per-channel capacity + skills | — |
| Status-based assignment | Omnichannel auto-assignment | — |
| Overflow to idle | Capacity overflow + fallback queue | — |
| Route by type | Skills routing on `division_*` | `division_*` tag |
| Priority ordering | Queue order by `priority` | `priority` field |
| Reminders/timeout | SLA policies + Automations | `sla_<minutes>` tag + SLA webhook (Item 3) |

---

## 5. What stays in code vs. Zendesk

- **In code (done):** ticket-metadata contract (priority + `division_/category_/subcat_/sla_` tags + channel-encoded `external_id`), locked by contract tests. SLA-escalation webhook + audit + WA-to-PIC (Item 3).
- **In Zendesk (this runbook):** Agent Workspace, omnichannel routing (presence, per-channel capacity, skills, queues, overflow), SLA policies + Automations, notifications.
- **Deferred follow-ups:** `department`/`pic` classifier output for SOP-driven PIC routing; social-media inbound channels (FB/IG/X) beyond WhatsApp.
