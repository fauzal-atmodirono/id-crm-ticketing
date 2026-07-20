# SLA Escalation and Case Audit Trail

This document covers the SLA escalation webhook, the case audit trail, and the Zendesk configuration required to wire them together (Item 3 of the CRM Enhancement plan).

---

## Overview

When a Zendesk SLA policy breach occurs, a time-based Automation fires an HTTP webhook to Proton. Proton records the state transition in its audit log and optionally sends a WhatsApp alert to the person-in-charge (PIC). The audit trail is queryable per ticket via a read endpoint.

---

## Data Model

### `AuditEntry` fields

| Field | Type | Description |
|---|---|---|
| `ticket_id` | `str` | Zendesk ticket ID (matches `SlaEscalationPayload.ticket_id`) |
| `session_id` | `str` | Session identifier of the originating conversation (may be empty) |
| `actor` | `str` | Who triggered the transition — `"sla-automation"` for webhook-driven entries |
| `from_state` | `str` | State before the transition (a `CaseState` value) |
| `to_state` | `str` | State after the transition (a `CaseState` value) |
| `at` | `str` | ISO-8601 timestamp of the transition (UTC) |
| `remark` | `str` | Human-readable annotation, e.g. `"SLA escalate"` |

### `CaseState` values

| Value | Meaning |
|---|---|
| `NEW` | Ticket just created; not yet triaged |
| `OPEN` | Ticket open and awaiting action |
| `WIP` | Work in progress — triggered by `level=escalate` (8 h breach) |
| `PENDING` | Pending further action — triggered by `level=alarm` (48 h breach) |
| `SOLVED` | Ticket resolved |

The SLA webhook maps `level=escalate` → `OPEN → WIP` and `level=alarm` → `OPEN → PENDING`.

---

## Audit Log Storage

### In-memory (default — dev/tests)

`InMemoryAuditLog` stores entries in a per-process dict. Entries are lost on restart. Used when `handoff_store` is `"memory"` (the default).

### Firestore (production)

`FirestoreAuditLog` stores each entry as an auto-ID document in the `case_audit_log` Firestore collection (configurable via `firestore_audit_collection`). Documents are queried and sorted by `ticket_id`. Auth uses Application Default Credentials (ADC).

| Config key | Default | Description |
|---|---|---|
| `handoff_store` | `"memory"` | Set to `"firestore"` to enable persistent Firestore-backed storage |
| `firestore_project_id` | `"lv-playground-genai"` | GCP project hosting the Firestore database |
| `firestore_database_id` | `"proton-db"` | Named Firestore database |
| `firestore_audit_collection` | `"case_audit_log"` | Firestore collection name for audit entries |

The `build_audit_log(settings)` factory falls back to in-memory if Firestore initialization fails, so the app never fails to start due to missing Firestore credentials.

---

## Endpoints

### `POST /webhooks/zendesk-sla-escalation`

Receives an SLA breach notification from Zendesk, records a case state transition in the audit log, and optionally sends a WhatsApp alert to the PIC.

**Authentication:** When `sla_webhook_secret` is set in `Settings`, the request must include a matching `x-proton-webhook-secret` header. Requests with a wrong or absent header are rejected with `401`. When `sla_webhook_secret` is empty (default), no authentication is enforced.

**Request payload:**

| Field | Type | Required | Description |
|---|---|---|---|
| `ticket_id` | `str` | Yes | Zendesk ticket ID |
| `session_id` | `str` | No (default `""`) | Session ID from the originating conversation |
| `level` | `str` | No (default `"escalate"`) | `"escalate"` (8 h) or `"alarm"` (48 h) |
| `pic_whatsapp` | `str` | No (default `""`) | E.164 phone number for the PIC WhatsApp alert (e.g. `"+60123456789"`) |
| `remark` | `str` | No (default `""`) | Custom remark stored on the audit entry |

**Example request:**

```json
{
  "ticket_id": "TKT-42",
  "session_id": "whatsapp-+60123456789",
  "level": "escalate",
  "pic_whatsapp": "+60198765432",
  "remark": ""
}
```

**Successful response (200):**

```json
{ "status": "recorded" }
```

**Error responses:**

| Code | Reason |
|---|---|
| `401` | Secret mismatch or absent when `sla_webhook_secret` is set |
| `503` | Audit log not wired (should not occur in production) |

---

### `GET /cases/{ticket_id}/audit`

Returns the full audit trail for a ticket, sorted by insertion order.

**Path parameter:**

| Parameter | Description |
|---|---|
| `ticket_id` | Zendesk ticket ID to retrieve audit entries for |

**Successful response (200):**

```json
{
  "ticket_id": "TKT-42",
  "audit": [
    {
      "ticket_id": "TKT-42",
      "session_id": "whatsapp-+60123456789",
      "actor": "sla-automation",
      "from_state": "OPEN",
      "to_state": "WIP",
      "at": "2026-07-16T08:00:00+00:00",
      "remark": "SLA escalate"
    }
  ]
}
```

When no entries exist for a ticket, `"audit"` is an empty list.

**Error responses:**

| Code | Reason |
|---|---|
| `503` | Audit log not wired (should not occur in production) |

---

## Optional WhatsApp Alert to PIC

When `pic_whatsapp` is set in the webhook payload and Twilio is configured, the webhook sends a WhatsApp message to the PIC:

```
⚠️ SLA escalate on case TKT-42. Please action.
```

The `pic_whatsapp` value may be prefixed with `whatsapp:` — the router normalizes it automatically. If `pic_whatsapp` is empty or Twilio is not wired, no alert is sent.

---

## Zendesk Configuration

### SLA Policy

Create or edit an SLA Policy in **Admin Center → Objects and rules → Business rules → Service Level Agreements**:

- **First reply time:** Set to your target (e.g. 1 h for Priority 1, 4 h for Priority 2).
- **Next reply time / Resolution time:** Drives the escalation automations below.

### Time-based Automations

Two Automations should be configured in **Admin Center → Objects and rules → Business rules → Automations**. Both fire once on matching tickets that have not been solved.

**Automation 1 — 8-hour SLA escalation (`level=escalate`)**

- **Conditions:** `Ticket: Hours since created` is `8` AND `Ticket: Status` is not `Solved`
- **Actions:** Notify by webhook → POST to `https://<your-backend>/webhooks/zendesk-sla-escalation`

Request body:

```json
{
  "ticket_id": "{{ticket.id}}",
  "session_id": "",
  "level": "escalate",
  "pic_whatsapp": "",
  "remark": "SLA 8h breach"
}
```

Add the header `x-proton-webhook-secret: <value of sla_webhook_secret>` when the secret is configured.

**Automation 2 — 48-hour SLA alarm (`level=alarm`)**

- **Conditions:** `Ticket: Hours since created` is `48` AND `Ticket: Status` is not `Solved`
- **Actions:** Notify by webhook → POST to `https://<your-backend>/webhooks/zendesk-sla-escalation`

Request body:

```json
{
  "ticket_id": "{{ticket.id}}",
  "session_id": "",
  "level": "alarm",
  "pic_whatsapp": "",
  "remark": "SLA 48h breach"
}
```

Both automations must reference the same webhook target, configured once under **Admin Center → Apps and integrations → Webhooks**.

---

## Deferred Follow-up

**BigQuery mirror:** Mirror audit entries to a `case_audit_events` BigQuery table for lifecycle reporting (alongside existing `conversations` and `turn_events`). A companion view `v_case_audit` would join ticket events to resolution timelines. Tracked as Item F in the metrics roadmap. Not implemented in this increment.
