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
| 01 | Brings WhatsApp, email, and phone/IVR conversations into a single inbox | raw capture: agent/backend env `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED`, `PHONE_LANGUAGE_NUDGE_ENABLED`; openapi prefixes `/voice`, `/webhooks` | verified | 2026-08-12 |
| 01 | AI assistant "that can draft or send replies" | raw capture: agent/backend env `AGENT_MODE=auto` | verified | 2026-08-12 |
| 01 | Roadside-assistance (RSA) incident logging is part of the platform | raw capture: agent/backend env `RSA_ENABLED=true`; openapi prefix `/rsa` | verified | 2026-08-12 |
| 01 | Vehicle/service lookup (Customer 360) is part of the platform | raw capture: openapi prefix `/admin/customer360` | verified | 2026-08-12 |
| 01 | Case tracking is part of the platform | raw capture: openapi prefix `/cases` | verified | 2026-08-12 |
| 01 | Reporting built around dealer and PIC escalation | raw capture: openapi prefixes `/metrics/dealer-escalation`, `/admin/escalation` | verified | 2026-08-12 |
| 01 | Admin-only pages Integrations, Escalation Routing, SLA Policies, Audit Log, Roles & Permissions sit in the sidebar | raw capture: openapi prefixes `/admin/integrations/dms`, `/admin/escalation`, `/admin/sla-policy`, `/admin/audit`, `/authz` | verified | 2026-08-12 |
| 01 | AI-assist buttons "Ask Copilot, Suggest a reply, Summarize" above the reply box | raw capture: openapi prefixes `/assist/copilot`, `/assist/suggest`, `/assist/summarize`; SPA feature list includes `copilot` | verified | 2026-08-12 |
| 01 | Administrators can be granted fine-grained permissions under Administration → Roles & Permissions | raw capture: backend env `RBAC_ENABLED=true`; openapi prefix `/authz/roles`; source `deploy/chatwoot-fork/patches/0059-roles-permissions-redesign.patch` | verified | 2026-08-12 |
| 01 | "As an agent, you see Conversations, Contacts, and Knowledge (read access) ... As an administrator, you additionally see Cases, the RSA Incident Log, Customer 360, Reports, and Administration" | source: `deploy/chatwoot-fork/patches/0009-knowledge-nav.patch` gives the Knowledge route `permissions: ['administrator', 'agent']` with no read-only gate on any editing control anywhere in patches 0009-0022, and `backend/apps/backend/src/chatbot/features/authz/seed.py` registers a `knowledge.edit` permission but no router enforces it — so Knowledge is not read-only for agents. Cases *is* correctly admin-only: `deploy/chatwoot-fork/patches/0043-cases-list.patch` gates `proton/cases` at `permissions: ['administrator']`; RSA is the same via `deploy/chatwoot-fork/patches/0035-rsa-incident-log.patch` | corrected | 2026-08-12 |
| 01 | Login screen offers a "Forgot password?" reset-email link | not settled by raw capture (only the SPA's compiled feature-flag list was captured, not the login page markup); would need a live browser check | unverifiable | 2026-08-12 |
| 01 | Interface language can be switched between English and Indonesian from the profile menu | not settled by raw capture (no locale env var or SPA evidence captured); would need a live browser check of the profile settings menu | unverifiable | 2026-08-12 |
| 01 | Sidebar includes a reachable "Campaigns/Help Center" entry | not settled by raw capture — no env/openapi/SPA-feature-list evidence for stock Chatwoot Campaigns/Help Center visibility on this tenant (see chapter 08 rows) | unverifiable | 2026-08-12 |
| 03 | Contacts area is a single cross-channel directory (WhatsApp, email, phone/IVR) | raw capture: same channel evidence as chapter 01's channel-unification row | verified | 2026-08-12 |
| 03 | Contacts from inbound WhatsApp/email/phone messages are added automatically, no manual step | raw capture: channels confirmed active; core Chatwoot contact-creation behavior | verified | 2026-08-12 |
| 03 | Contact profile shows custom attributes such as vehicle model | source: `backend/apps/backend/src/chatbot/features/chat/customer360_router.py` reads `custom_attributes.vehicle_model` off live Chatwoot conversations | verified | 2026-08-12 |
| 03 | Customer 360's conversation list is the same data the contact profile shows, "so the two views never disagree" | source: `customer360_router.py` calls the same `ChatwootAdapter.list_contact_conversations` / `list_conversations` methods the profile view reads through | verified | 2026-08-12 |
| 03 | Customer 360 is visible only to administrators granted the Customer 360 permission | source: `deploy/chatwoot-fork/patches/0041-customer360-admin.patch` gates the sidebar entry on `protonHasPermission('customer360.view')`; backend gates the route on `require_permission("customer360.view")` | verified | 2026-08-12 |
| 03 | Customer 360 search requires "at least two characters" | source: backend `Query(min_length=2)` and frontend `if (q.length < 2)` check, both in `customer360_router.py` / patch 0041 | verified | 2026-08-12 |
| 03 | Phone search is an exact match; vehicle-number search matches any conversation whose vehicle model contains the value | source: `customer360_router.py` — `_pick_best_contact` (digits-only exact match) vs. substring match against `vehicle_model` / `vehicle_no` | verified | 2026-08-12 |
| 03 | Customer 360 is read-only — "nothing here can be created, edited, or deleted" | source: `customer360_router.py` exposes only `GET /admin/customer360/search`; patch 0041 comment states "no create/update/delete here" | verified | 2026-08-12 |
| 03 | DMS/TSP block shows a "Not connected" notice when unreachable and a "Mock data" notice when the client is mocked | source: `deploy/chatwoot-fork/patches/0045-dms-integration-card.patch` adds exactly these two notices to `ProtonCustomer360Page.vue`, gated on `dms.status !== 'ok'` / `dms.mock` | verified | 2026-08-12 |
| 03 | Notes and segments exist as contact-level features | not settled by raw capture — stock Chatwoot contact features, no Proton env flag or backend route gates them; would need a live browser check | unverifiable | 2026-08-12 |
| 08 | Campaigns (one-off + ongoing) menu is present/reachable in the sidebar on this tenant | not settled by raw capture — Campaigns is a stock Rails feature with no Proton env flag or backend route; would need a live browser check with an administrator account | unverifiable | 2026-08-12 |
| 08 | Help Center portal menu is present/reachable (sidebar or account settings) on this tenant | not settled by raw capture; `deploy/chatwoot-fork/patches/0059-roles-permissions-redesign.patch` surfaces the native `knowledge_base_manage` permission ("Manage Help Center portals and articles"), confirming the capability is registered in the build but not that a portal is configured/visible for this tenant | unverifiable | 2026-08-12 |
| 08 | Knowledge → FAQs/Documents/Assistants is a separate feature from the Help Center portal | raw capture: openapi prefixes `/kb/faq`, `/kb/documents`, `/kb/assistants`; env `KNOWLEDGE_PG_ENABLED=true` | verified | 2026-08-12 |
| 08 | Campaigns send through whichever inbox/channel they're attached to, no separate setup | not settled by raw capture (depends on stock Rails Campaigns being reachable at all — see the reachability row above) | unverifiable | 2026-08-12 |

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
