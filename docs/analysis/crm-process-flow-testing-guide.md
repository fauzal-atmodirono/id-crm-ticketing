# CRM Process Flow Scenario Testing Guide — Chatwoot CRM

**Target Document:** `docs/CRM Process Flow (1).xlsx`  
**Target Platform:** Chatwoot CRM + `agent/` service (lifecycle state machine & auto-categorization) + `backend/` service (SLA engine & notification routing)  
**Date:** 2026-07-27  

---

## 1. Executive Overview & Architecture Alignment

This testing guide details how to validate and test all operational scenarios from `docs/CRM Process Flow (1).xlsx` across all 5 channel process sheets (**WhatsApp**, **Social Media**, **Email**, **IVR Call**, and **SSI Process**).

### Architecture Mapping

| Component | Responsibility | Relevant Files |
|---|---|---|
| **Chatwoot CRM** | Omnichannel inbox, contact routing, status changes, live agent interface | Native Chatwoot Community |
| **`agent/` Service** | Conversation lifecycle state machine, auto-disclaimer, email auto-ack, idle warnings, auto-close, CSAT/NPS survey, Gemini auto-categorization | `agent/app/services/lifecycle.py`<br>`agent/app/services/lifecycle_scanner.py`<br>`agent/app/services/categorize.py` |
| **`backend/` Service** | Per-channel first-response & resolution SLAs, PIC escalation router (Email & WhatsApp), routing presence | `backend/.../features/chat/sla.py`<br>`backend/.../features/chat/escalation_notifier.py`<br>`backend/.../features/routing/service.py` |

---

## 2. Configuration & Environment Prerequisites

To prepare your environment before executing testing:

### Tenant Environment (`deploy/tenants/<tenant>.env`)

```env
# Master Lifecycle & Automation Switches
LIFECYCLE_ENABLED=true
LIFECYCLE_SCAN_INTERVAL_SECONDS=60
LIFECYCLE_IDLE_WARN_MINUTES=10
LIFECYCLE_IDLE_CLOSE_GRACE_MINUTES=5
LIFECYCLE_CONFIRM_GRACE_MINUTES=10
LIFECYCLE_SURVEY_ENABLED=true
LIFECYCLE_DISCLAIMER_ENABLED=true
LIFECYCLE_AUTO_CATEGORIZE=true
LIFECYCLE_CATEGORY_LABELS="category_apps,category_sales,category_aftersales,category_charging,category_general"

# Email Auto-Ack
EMAIL_AUTOACK_ENABLED=true

# Per-Channel Ack SLAs (Minutes)
SLA_ACK_MINUTES_BY_CHANNEL_JSON='{"whatsapp":2,"call":0.33,"email":240,"facebook":120,"instagram":120}'

# Chatwoot API & Webhook Wiring
CHATWOOT_API_TOKEN=<admin_access_token>
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_WEBHOOK_SECRET=<webhook_secret>
```

### Webhook Registration in Chatwoot
1. Go to **Settings → Integrations → Webhooks** in Chatwoot UI.
2. Add Webhook URL: `http://<agent-host>/webhooks/chatwoot`
3. Subscribe to events:
   - `conversation_created`
   - `conversation_updated`
   - `conversation_status_changed`

---

## 3. Automated Test Execution (Pytest)

Run these automated test commands to verify code logic before executing end-to-end manual flows:

### Agent Service Tests (Lifecycle, Auto-Ack, Categorization)
```bash
agent/.venv/bin/pytest \
  agent/tests/test_lifecycle_disclaimer.py \
  agent/tests/test_email_autoack.py \
  agent/tests/test_lifecycle_scanner.py \
  agent/tests/test_lifecycle_replies.py \
  agent/tests/test_categorize.py \
  agent/tests/test_business_hours.py
```

### Backend Service Tests (Channel SLAs, Escalations, Routing)
```bash
backend/apps/backend/.venv/bin/pytest \
  backend/apps/backend/src/chatbot/features/chat/test_channel_ack_sla.py \
  backend/apps/backend/src/chatbot/features/chat/test_sla_tier2.py \
  backend/apps/backend/src/chatbot/features/chat/test_escalation_notifier.py \
  backend/apps/backend/src/chatbot/features/chat/test_routing_assignment.py \
  backend/apps/backend/src/chatbot/features/chat/test_routing_presence.py
```

---

## 4. Per-Sheet End-to-End Scenario Testing Guide

### 📱 Sheet 1: WhatsApp Process Flow

| # | Scenario | SOP Requirement | Verification Step in Chatwoot | Expected System Response |
|:---|:---|:---|:---|:---|
| WA-01 | **AI Disclaimer** | Auto-send AI disclaimer on new message in customer's language | Send a new message to WhatsApp inbox | Bot posts AI disclaimer/welcome text immediately |
| WA-02 | **Live Agent Request** | Auto-assign to active live agent with WA priority | Send message asking for human / live agent | Assigns to agent whose `channel_priorities` prioritizes `whatsapp` |
| WA-03 | **2-Min ACK SLA** | Call agent must acknowledge within 2 minutes | Leave escalated conversation unacknowledged for >2 minutes | SLA scanner triggers breach and notifies PIC via Email/WhatsApp |
| WA-04 | **Team Leader Reassign** | Case can be manually reassigned by Team Leader | Reassign conversation to another agent in Chatwoot UI | Assignee updates in Chatwoot and audit log records change |
| WA-05 | **Idle Warning & Auto-Close** | Warn after 10m idle; auto-close after grace | Leave conversation inactive past `WARN` + `GRACE` minutes | Bot sends idle warning, followed by auto-closing and resolution prompt |
| WA-06 | **Resolution Prompt (YES/NO)** | Prompt *"Is this case resolved? YES/NO"* | Reply **NO** or **YES** to prompt | **NO**: Reopens conversation to `ACTIVE`<br>**YES**: Triggers 1–5 CSAT/NPS rating survey |
| WA-07 | **CSAT Rating Survey** | Rating survey to evaluate agent performance | Customer submits 1–5 rating reply | Rating stored in `ai_actions` / CSAT store; conversation moves to `CLOSED` |
| WA-08 | **Auto-Categorization** | Bot assigns category label upon resolution | Complete bot resolution | Conversation tagged with a `category_<slug>` label |

---

### 💬 Sheet 2: Social Media (Facebook & Instagram) Flow

| # | Scenario | SOP Requirement | Verification Step in Chatwoot | Expected System Response |
|:---|:---|:---|:---|:---|
| SM-01 | **Inbound Ticket Log** | Log ticket ID on FB/IG customer post/DM | Customer sends DM on Facebook or Instagram page | New conversation created in Chatwoot with channel `facebook` or `instagram` |
| SM-02 | **Outside Business Hours** | Auto-reply when outside operating hours (Mon-Fri 8:30-17:30, Sat-Sun/PH 9:00-17:00) | Send message outside configured inbox Business Hours | Bot sends standard Out-of-Hours template message with schedule info |
| SM-03 | **Next Business Hour Assignment** | Ticket assigned to agent for next business hour | Log message out-of-hours, wait for business hours start | Conversation moves to agent queue at start of next business hour |
| SM-04 | **2-Hour ACK SLA** | Agent must acknowledge within 2 working hours | Leave conversation unacknowledged for >120 working minutes | Per-channel SLA engine flags 2-hour SLA breach |
| SM-05 | **Rating Survey** | System triggers rating survey upon resolution | Agent marks conversation as **Resolved** in Chatwoot UI | Agent-performance rating survey (1–5) sent to customer DM |

---

### 📧 Sheet 3: Email Process Flow

| # | Scenario | SOP Requirement | Verification Step in Chatwoot | Expected System Response |
|:---|:---|:---|:---|:---|
| EM-01 | **Single Auto-Ack per Thread** | One auto-reply per new email thread; no duplicates on customer replies | Customer sends new email to `e.mascentre@pronet.my` | Auto-acknowledgement email sent ONCE to customer |
| EM-02 | **Thread Reply Deduplication** | Customer replies to same thread → no additional auto-reply | Customer replies to the existing email thread | System suppresses auto-reply; conversation appends message |
| EM-03 | **Agent Reply Suppression** | Agent reply disables auto-ack | Agent sends reply from Chatwoot UI | Customer receives agent reply with no additional auto-reply |
| EM-04 | **New Subject / Thread** | Customer sends email with new subject → auto-ack sent again | Customer sends email with a new subject line | New conversation created → auto-acknowledgement sent ONCE |
| EM-05 | **4-Hour Status Update SLA** | Status update to customer within 4 working hours | Leave email unreplied for >240 working minutes | SLA engine triggers email ACK breach notification |

---

### 📞 Sheet 4: IVR Call Flow

| # | Scenario | SOP Requirement | Verification Step in Chatwoot | Expected System Response |
|:---|:---|:---|:---|:---|
| IVR-01 | **Inbound Call Logging & STT** | Female AI voice / Twilio Gemini Live STT transcript | Simulate inbound phone call via telephony bridge | Call record and real-time STT transcript posted into Chatwoot conversation |
| IVR-02 | **20-Second Answer SLA** | Agent must attend call within 20 seconds | Leave incoming call unanswered for >20 seconds (`0.33` min) | SLA breach registered for call channel |
| IVR-03 | **Queue Busy Prompt (>10s)** | Bilingual busy prompt (EN/BM) for Non-RSA when queues busy | Simulate call queue waiting time >10 seconds | Plays bilingual audio prompt asking caller to wait or use e.MAS App |

---

### 📋 Sheet 5: SSI Process (e.MAS App Survey SOP UO/CRM01)

| # | Scenario | SOP Requirement | Verification / Integration Scope |
|:---|:---|:---|:---|
| SSI-01 | **11-Day Survey Delivery** | Survey sent 11 days after vehicle delivery via e.MAS App; expires in 14 days | Managed via e.MAS App & core database backend |
| SSI-02 | **Dealer Friday Review** | Dealers review survey status every Friday in portal | Dealer portal background reporting |
| SSI-03 | **Appeals Workflow** | Appeals (RESEND, REVISED, EXCLUSION) submitted by Wednesday, reviewed by PRO-NET | Managed via dealer portal / e.MAS app admin |

---

## 5. Live Log Monitoring Commands

To verify background execution during testing, run the following streaming log commands on your host VM:

```bash
# Monitor agent conversation lifecycle scanner & auto-categorization logs
docker logs -f <tenant>-agent | grep -E "lifecycle_|email_autoack|categorize"

# Monitor backend per-channel SLA scanner & PIC escalations
docker logs -f <tenant>-backend | grep -E "sla_|escalation_"
```
