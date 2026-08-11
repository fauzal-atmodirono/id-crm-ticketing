# Feature Guide v4 — verification ledger (internal)

**Not a client deliverable.** Companion to `feature-guide-v3-pending.md`.

v3 was cut back on 2026-08-09 because it described software that was not
running. This file exists so v4's claims can be audited rather than trusted:
one row per checkable claim, naming the probe that settled it.

## Live state, measured 2026-08-11

| Probe | Value |
|---|---|
| `docker exec proton-chatwoot-rails cat /app/.git_sha` | `0866fda` |
| Chatwoot image | `asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc6` |
| Rendered `/app/login` feature list | `ai_assist,nav_menu,copilot,knowledge,inbound_alerts,faq_suggestion_popup` |
| Backend `/openapi.json` path count | 113 |
| BigQuery views (P8) | Not "could not be probed" — a dataset named `demo_proton` **does** exist in project `lv-playground-genai` (the same project the Chatwoot image is hosted in) with **8 views**, not the 11 named in the guide: `v_bounce_rate`, `v_csat`, `v_fallback_rate`, `v_nps`, `v_quality`, `v_resolution_split`, `v_speed_of_response`, `v_volume_by_month_channel` — plus 3 base tables (`conversations`, `qa_labels`, `turn_events`). The `demo_` prefix on the dataset name means it is **not confirmed to be the production dataset** the guide's P8 claim refers to; Task 11 should treat this as "partially exists, under a name that looks like a demo/staging artifact" rather than either "created" or "not created," and decide the AI Cost & Performance section's fate accordingly. |

Full capture: see the `## Raw capture` section below.

## Sanctioned probes

1. **What the SPA ships** — the feature list from the rendered login page,
   read inside the container. Not the compose file, not the patch directory.
2. **What the backend serves** — `/openapi.json` listed **by prefix**. An
   exact-path check is banned: `/alerts/rules` reads as missing when the real
   paths are `/alerts/rules/{defaults,mine}`.
3. **Flag state** — `printenv` inside *both* `proton-agent` and
   `proton-chatwoot-rails`. They do not read the same source: the backend
   takes the tenant env file wholesale via `env_file:`, Rails gets only what
   the compose `x-chatwoot-env` block passes through, and the VM's compose
   file has been stale before.
4. **The browser** — for anything only a rendered page can settle.

## Ledger

| Chapter | Claim | Probe | Verdict | Date |
|---|---|---|---|---|

## Raw capture

```
=== chatwoot .git_sha ===
0866fda
=== chatwoot image ===
asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc6
=== SPA feature list (rendered login page) ===
features: "ai_assist,nav_menu,copilot,knowledge,inbound_alerts,faq_suggestion_popup".split(',').filter(Boolean)
=== backend openapi paths ===
/
/admin/audit
/admin/customer360/search
/admin/escalation/dealers
/admin/escalation/dealers/{dealer}
/admin/escalation/pics
/admin/escalation/pics/{department}
/admin/integrations/dms
/admin/integrations/dms/test
/admin/sla-policy/default
/admin/sla-policy/inbox/{inbox_id}
/admin/taxonomy/coverage
/admin/taxonomy/node
/admin/taxonomy/node/{key}/retire
/admin/taxonomy/tree
/admin/workforce
/alerts/rules/defaults
/alerts/rules/defaults/{event}
/alerts/rules/mine
/alerts/rules/mine/{event}
/assist/ask
/assist/copilot
/assist/suggest
/assist/summarize
/assist/translate
/authz/check
/authz/permission-registry
/authz/permissions
/authz/roles
/authz/roles/{role_id}/assign
/authz/roles/{role_id}/permissions
/authz/roles/{role_id}/permissions/{permission_key}
/authz/roles/{role_id}/users
/calls/{conversation_id}/recording
/cases/{conv_id}/fields
/cases/{ticket_id}/audit
/chat/csat
/chat/nps
/chat/stream/{session_id}
/chat/turn
/escalation/acknowledge
/escalation/contacts
/escalation/departments
/escalation/notify
/healthz
/kb/assistants
/kb/assistants/{assistant_id}
/kb/documents
/kb/faq
/kb/faq/bulk
/kb/faq/{entry_id}
/kb/feedback
/kb/inboxes
/kb/inboxes/{inbox_id}
/kb/inboxes/{inbox_id}/timing
/kb/knowledge
/kb/knowledge/file
/kb/knowledge/text
/kb/knowledge/{document_id}
/kb/scenarios
/kb/scenarios/{scenario_id}
/kb/settings
/kb/suggest
/kb/tools
/kb/tools/builtins/{name}
/kb/tools/{slug}
/metrics/after-hours
/metrics/ai-cost
/metrics/anomalies
/metrics/anomalies/hourly
/metrics/by-tag
/metrics/callcenter
/metrics/case-aging
/metrics/case-aging/export
/metrics/control-items
/metrics/dashboard
/metrics/dealer-escalation
/metrics/dealer-escalation/export
/metrics/departments
/metrics/departments/export
/metrics/export
/metrics/freshness
/metrics/lifecycle
/metrics/sla-buckets
/metrics/sla-buckets/export
/metrics/volume-by-type
/metrics/volume-by-type/export
/qa/label
/routing/agents
/routing/assign
/routing/presence/status
/routing/presence/statuses
/routing/presence/statuses/{key}
/routing/priorities
/routing/priorities/{agent_id}
/rsa/incidents
/rsa/incidents/aggregate
/rsa/incidents/export
/rsa/incidents/{incident_id}
/tasks/mine
/voice/phone/incoming
/voice/phone/token
/voice/tts
/voice/turn
/webhooks/chatwoot
/webhooks/phone/recording-status
/webhooks/sunshine
/webhooks/twilio-whatsapp
/webhooks/zendesk
/webhooks/zendesk-email
/webhooks/zendesk-handback
/webhooks/zendesk-sla-escalation
/webhooks/zendesk-support
=== agent env ===
AGENT_MODE=auto
ASSIST_MEDIA_UNDERSTANDING_ENABLED=true
BOUNCE_HANDLING_ENABLED=true
CHAT_AGENT_ENABLED=true
DEPT_SUGGESTION_ENABLED=true
EMAIL_AUTOACK_ENABLED=false
EMAIL_ESCALATION_ACK_ENABLED=true
EMAIL_ESCALATION_ENABLED=true
ESCALATION_EMAIL_ENABLED=true
ESCALATION_REPLY_DRAFT_ENABLED=true
ESCALATION_REPLY_LINKING_ENABLED=true
FAQ_SUGGESTION_POPUP_ENABLED=true
INBOUND_ALERTS_ENABLED=true
KNOWLEDGE_PG_ENABLED=true
LIFECYCLE_DISCLAIMER_ENABLED=false
LIFECYCLE_ENABLED=true
LIFECYCLE_SURVEY_ENABLED=false
PHONE_LANGUAGE_NUDGE_ENABLED=true
RBAC_ENABLED=true
ROUTING_ENABLED=true
RSA_ENABLED=true
SLA_ALERT_EMAIL_ENABLED=true
SLA_ALERT_NOTE_ENABLED=true
SLA_ENGINE_ENABLED=true
SLA_INBOX_IDS=4
TRANSLATION_ENABLED=true
WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true
ZAMMAD_TICKETING_ENABLED=false
=== backend env ===
AGENT_MODE=auto
ASSIST_MEDIA_UNDERSTANDING_ENABLED=true
BOUNCE_HANDLING_ENABLED=true
CHAT_AGENT_ENABLED=true
DEPT_SUGGESTION_ENABLED=true
EMAIL_AUTOACK_ENABLED=false
EMAIL_ESCALATION_ACK_ENABLED=true
EMAIL_ESCALATION_ENABLED=true
ESCALATION_EMAIL_ENABLED=true
ESCALATION_REPLY_DRAFT_ENABLED=true
ESCALATION_REPLY_LINKING_ENABLED=true
FAQ_SUGGESTION_POPUP_ENABLED=true
GEMINI_MODEL=gemini-2.5-flash
INBOUND_ALERTS_ENABLED=true
KNOWLEDGE_PG_ENABLED=true
LIFECYCLE_DISCLAIMER_ENABLED=false
LIFECYCLE_ENABLED=true
LIFECYCLE_SURVEY_ENABLED=false
PHONE_LANGUAGE_NUDGE_ENABLED=true
PRESENCE_CUSTOM_STATUSES_ENABLED=true
PRESENCE_TRACKING_ENABLED=true
RBAC_ENABLED=true
ROUTING_ENABLED=true
RSA_ENABLED=true
SLA_ALERT_EMAIL_ENABLED=true
SLA_ALERT_NOTE_ENABLED=true
SLA_ENGINE_ENABLED=true
SLA_INBOX_IDS=4
TAXONOMY_ADMIN_ENABLED=true
TRANSLATION_ENABLED=true
WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true
ZAMMAD_TICKETING_ENABLED=false
=== rails env (x-chatwoot-env passthrough only) ===
FAQ_SUGGESTION_POPUP_ENABLED=true
INBOUND_ALERTS_ENABLED=true
PROTON_FEATURES=ai_assist,nav_menu,copilot,knowledge
```

### BigQuery probe (Step 2)

```
$ gcloud config get-value project
lv-playground-genai

$ bq ls --format=pretty | grep -i proton
| demo_proton                     |

$ bq ls --format=pretty demo_proton
+---------------------------+-------+--------+-------------------+------------------+
|          tableId          | Type  | Labels | Time Partitioning | Clustered Fields |
+---------------------------+-------+--------+-------------------+------------------+
| conversations             | TABLE |        |                   |                  |
| qa_labels                 | TABLE |        |                   |                  |
| turn_events               | TABLE |        |                   |                  |
| v_bounce_rate             | VIEW  |        |                   |                  |
| v_csat                    | VIEW  |        |                   |                  |
| v_fallback_rate           | VIEW  |        |                   |                  |
| v_nps                     | VIEW  |        |                   |                  |
| v_quality                 | VIEW  |        |                   |                  |
| v_resolution_split        | VIEW  |        |                   |                  |
| v_speed_of_response       | VIEW  |        |                   |                  |
| v_volume_by_month_channel | VIEW  |        |                   |                  |
+---------------------------+-------+--------+-------------------+------------------+
```

The full `bq ls --format=pretty` project-wide listing was long (100+
datasets, none else proton-named); only the `proton`-matching row and the
`demo_proton` dataset's contents are reproduced above per the brief's
two-attempt cap on BigQuery hunting.
