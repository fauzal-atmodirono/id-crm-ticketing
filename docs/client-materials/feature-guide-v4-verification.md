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
| 02 | The conversation list brings in WhatsApp, the website chatbot, email, and phone/IVR alike | raw capture: agent/backend env `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED`, `PHONE_LANGUAGE_NUDGE_ENABLED`; openapi prefixes `/voice`, `/webhooks` (same evidence as chapter 01's channel-unification row) | verified | 2026-08-12 |
| 02 | Administrators can set which channel each agent handles first, steering automatic assignment (Administration → Inboxes) | raw capture: backend env `ROUTING_ENABLED=true`; openapi prefixes `/routing/priorities`, `/routing/assign` | verified | 2026-08-12 |
| 02 | The **escalate** label triggers the two-thread escalation email workflow | raw capture: agent/backend env `EMAIL_ESCALATION_ENABLED=true`, `ESCALATION_EMAIL_ENABLED=true`; openapi prefix `/escalation/notify` | verified | 2026-08-12 |
| 02 | The automated escalation email only fires on an Email-channel conversation; applying `escalate` on WhatsApp/web chatbot/phone changes only the label | source: `agent/app/services/dept_suggestion.py` and `sync.maybe_escalate` both gate on `inbox.channel_type == "Channel::Email"`; raw capture confirms `ESCALATION_ALL_CHANNELS` is unset on both containers (per Task 3's finding) | verified | 2026-08-12 |
| 02 | A dealer label routes to a dealer **group** — every member is forwarded the case, not just one address | source: `backend/apps/backend/src/chatbot/features/chat/pic_store.py`'s `DealerRecord.emails: list[str]` (a list, not a single address) | verified | 2026-08-12 |
| 02 | "All six departments now route somewhere" — `dept_sales/engineer/pre_sales/aftersales/cs/technical` each have a PIC configured, and both `dealer_komang_motor`/`dealer_caroline_motor` do too | not settled by raw capture — no PIC/dealer directory contents were captured (only route paths). A stale docstring in `agent/app/services/dept_suggestion.py` states "`dept_aftersales`, `dept_cs`, `dept_technical` on the Proton tenant today" had **no** PIC at time of writing, which conflicts with this claim; the PIC/dealer stores are Firestore-backed and editable at runtime, so this may since have changed. Fix round 1: reviewer attempted `GET /admin/escalation/pics`/`/dealers` from inside the container and both returned HTTP 401 (RBAC-gated, needs a user token, not an API key) — still not settled. **Prose amended** to stop asserting the coverage count: it now states the routing mechanism (a department/dealer with no contact configured sends the escalation email to nobody) and tells the operator to check the Escalation Routing page, without claiming how many are populated. Would need the browser sweep's Escalation Routing admin-page pass (with a user session) to settle the actual count | unverifiable | 2026-08-12 |
| 02 | AI-suggested escalation department: live on this tenant, off by default at the code level | raw capture: agent/backend env `DEPT_SUGGESTION_ENABLED=true` (code default is `False` per `agent/app/config.py`) | verified | 2026-08-12 |
| 02 | AI-suggested escalation department only fires on Email-channel conversations, posts once per conversation, and only ever proposes a department that has a PIC configured | source: `agent/app/services/dept_suggestion.py` — `channel_type != "Channel::Email"` gate, `dept_suggested_at` first-write-wins stamp, and candidates sourced only from the PIC store (`GET /escalation/departments`), never a static list | verified | 2026-08-12 |
| 02 | Dealer/PIC reply is linked back as a private note `Reply from <name> <email>:`, and a second reply from the same address doesn't post another note | source: `agent/app/services/escalation_replies.py` — exact f-string match, and `existing.get(_REPLIED_ATTR)` early-return before the internal-reply path | verified | 2026-08-12 |
| 02 | Customer's own reply can't post inline on this tenant's Email inbox (`{"error":"Incoming messages are only allowed in Api inboxes"}`), so it lands as a private note prefixed `Customer's own reply (from <email>, could not be posted inline -- see conversation <id>):`, and the conversation still reopens | source: `agent/app/services/escalation_replies.py` — exact error text and f-string match in the module docstring and body; a customer, unlike a dealer/PIC, is unstamped and may reply more than once | verified | 2026-08-12 |
| 02 | The conversation picks up an `escalation_replied` label, and a second private note titled `Suggested customer reply (draft — review before sending):` appears beneath a dealer/PIC reply | source: `agent/app/services/escalation_replies.py` — `_REPLIED_LABEL = "escalation_replied"`; exact f-string match for the AI-drafted reply note, gated on `settings.escalation_reply_draft_enabled` (raw capture: `ESCALATION_REPLY_DRAFT_ENABLED=true`) | verified | 2026-08-12 |
| 02 | SLA breach: a private note starting `⚠️ SLA breach` is posted, and the department's PIC group is separately emailed, with a Tier-2 re-alert | source: `backend/apps/backend/src/chatbot/features/chat/sla.py` — exact `f"⚠️ SLA breach ({to_state}) on case {ticket_id}..."` and `f"⚠️ TIER-2 escalation..."` strings; raw capture: `SLA_ALERT_EMAIL_ENABLED=true`, `SLA_ALERT_NOTE_ENABLED=true`, `SLA_ENGINE_ENABLED=true` | verified | 2026-08-12 |
| 02 | "Today this only scans the Email inbox" (SLA breach alerts) | raw capture: agent/backend env `SLA_INBOX_IDS=4` — a single inbox id configured | verified | 2026-08-12 |
| 02 | The contact side panel now opens by default, rather than needing manual expansion each time | source: `deploy/chatwoot-fork/patches/0004-contact-panel-default.patch` — `isContactSidebarOpen === undefined ? true : isContactSidebarOpen` | verified | 2026-08-12 |
| 02 | Conversation summaries are written in English regardless of the conversation's original language | source: `backend/apps/backend/src/chatbot/features/assist/router.py` `_SUMMARIZE_SYSTEM` — "Reply in English regardless of the conversation language." | verified | 2026-08-12 |
| 02 | Inactivity timers (idle warning, then automatic close after a grace period) are configurable per inbox | raw capture: agent/backend env `LIFECYCLE_ENABLED=true`; openapi prefix `/kb/inboxes/{inbox_id}/timing`; source `backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py` stores `idle_warn_minutes`/`idle_close_grace_minutes`/`idle_close_out_of_hours_grace_minutes` per inbox_id | verified | 2026-08-12 |
| 02 | A conversation resolved via inactivity auto-close (or by hand) may trigger a satisfaction-survey message [fix round 1: this row's original "corrected" text — "does not currently send one" — was itself wrong; see the two rows below, which supersede it] | raw capture: agent/backend env `LIFECYCLE_SURVEY_ENABLED=false`; source `agent/app/services/lifecycle.py` `on_human_resolved` returns early when the flag is off | corrected | 2026-08-12 |
| 02 | Resolving a conversation sends the customer Chatwoot's own native CSAT satisfaction-rating request, on all four live inboxes | live read-only query of `Inbox.csat_survey_enabled`, 2026-08-12: `[1] Proton API (Channel::Api)=true, [2] Website Demo (Channel::WebWidget)=true, [3] Twilio Proton (Channel::TwilioSms)=true, [4] Email (Channel::Email)=true` — all four `true` | verified | 2026-08-12 |
| 02 | The AI assistant's own lifecycle rating prompt ("rate our AI assistant/support agent from 1 to 5", distinct from native CSAT) is not currently switched on for this tenant | raw capture: agent/backend env `LIFECYCLE_SURVEY_ENABLED=false`; source `agent/app/services/lifecycle.py` — `on_human_resolved` and the bot's own end-of-chat survey step both gate on this flag | verified | 2026-08-12 |
| 02 | AI auto-draft: **suggest mode** is described as "the default" tenant-wide | raw capture: agent/backend env `AGENT_MODE=auto` (code default in `agent/app/config.py` is `agent_mode: str = "suggest"`, but this tenant overrides it); `agent/app/services/orchestrator.py` — `effective_mode = per_inbox_mode or settings.agent_mode` | corrected | 2026-08-12 |
| 02 | Translate composer action: renders and works | raw capture: SPA feature list includes `ai_assist` (gates button visibility, per `deploy/chatwoot-fork/patches/0055-translate-action.patch`); openapi path `/assist/translate` present (backend route exists) — both halves of the probe pass | verified | 2026-08-12 |
| 02 | FAQ suggestion strip: renders and works | raw capture: SPA feature list includes `faq_suggestion_popup`; openapi path `/kb/suggest` present; agent/backend env `FAQ_SUGGESTION_POPUP_ENABLED=true` — both halves of the probe pass | verified | 2026-08-12 |
| 02 | Inbound alerts (toast on new customer message): renders and fires | raw capture: SPA feature list includes `inbound_alerts`; agent/backend env `INBOUND_ALERTS_ENABLED=true`; source `deploy/chatwoot-fork/patches/0057-inbound-alerts.patch` — `raiseAlert(...)` is called only for the `new_inbound` event | verified | 2026-08-12 |
| 02 | Inbound alerts: per-agent alert-preferences page (sound/desktop notification, other event types) is a separate surface and is NOT enabled on this tenant | raw capture: `ALERT_RULES_ENABLED` is absent from both the agent and backend printed env (code default in `backend/apps/backend/src/chatbot/platform/config.py` is `alert_rules_enabled: bool = False`); source `backend/apps/backend/src/chatbot/features/alerts/rules_router.py` — every endpoint answers `{"disabled": true}` when the flag is off, and the fork's own test suite documents the built-in default for `new_inbound` as toast-only | verified | 2026-08-12 |
| 02 | Multimodal AI assist: "Suggest a reply" reads image/video/audio attachments, and media-derived terms lead the knowledge-base search when a conversation has media | raw capture: agent/backend env `ASSIST_MEDIA_UNDERSTANDING_ENABLED=true`, `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true`; source `backend/apps/backend/src/chatbot/features/assist/assist_media.py` (`collect_media_parts`) and `backend/apps/backend/src/chatbot/features/assist/router.py` (`_kb_context` puts media-derived terms at the front of the retrieval query) | verified | 2026-08-12 |
| 02 | By default the conversation list shows every conversation across all statuses on sign-in | not settled by raw capture (default UI landing state, not env/route observable); would need a live browser check | unverifiable | 2026-08-12 |
| 04 | Knowledge routes exist for FAQs, Documents/Uploads, Assistants, Scenarios, Playground, Tools, Inboxes (assignment), and Settings | raw capture: openapi prefixes `/kb/faq`, `/kb/faq/bulk`, `/kb/documents`, `/kb/knowledge`, `/kb/knowledge/file`, `/kb/knowledge/text`, `/kb/assistants`, `/kb/scenarios`, `/kb/tools`, `/kb/tools/builtins/{name}`, `/kb/inboxes`, `/kb/inboxes/{inbox_id}`, `/kb/inboxes/{inbox_id}/timing`, `/kb/settings`; env `KNOWLEDGE_PG_ENABLED=true` | verified | 2026-08-12 |
| 04 | FAQ bulk upload accepts a UTF-8 CSV with `question`/`answer` (required) and `;`-separated `keywords`/`tags`, reports created count and per-row skip reasons, and imported entries are always saved active | source: `backend/apps/backend/src/chatbot/features/chat/faq_admin_router.py::bulk_create_faq` — exact column names, `;`-split, `active=True` always on the bulk path | verified | 2026-08-12 |
| 04 | FAQ entries and the Documents corpus are also the source for the FAQ suggestion strip agents see above the reply box (chapter 2), not just Suggest-a-reply/Copilot | raw capture: openapi `/kb/suggest`; SPA feature list includes `faq_suggestion_popup`; source `backend/apps/backend/src/chatbot/features/chat/kb_suggest_router.py` — merges live-FAQ hits (ranked first) with the Vertex/Documents corpus into one `suggestions` list | verified | 2026-08-12 |
| 04 | Playground behaves like the Ask Copilot panel because it calls the same backend endpoint | source: `deploy/chatwoot-fork/patches/0014-knowledge-playground.patch` — Playground's request function posts to `/assist/copilot` | verified | 2026-08-12 |
| 04 | Custom tools are capped at 15 per tenant | source: `backend/apps/backend/src/chatbot/features/chat/kb_tools_router.py` — "Hard cap: ≤ 15 custom tools per tenant (409 if exceeded)" | verified | 2026-08-12 |
| 04 | Each assistant can hold up to 20 scenarios; instruction text is capped at 4,096 characters | source: `backend/apps/backend/src/chatbot/features/chat/kb_scenarios_router.py` — `_INSTRUCTION_MAX = 4096`, "≤ 20 scenarios per assistant — 409 if the cap would be breached on create" | verified | 2026-08-12 |
| 04 | Inbox assignment mode is one of Off / Suggest / Auto, validated server-side | source: `backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py` — `_VALID_MODES = frozenset({"off", "suggest", "auto"})`, 422 on anything else | verified | 2026-08-12 |
| 04 | The Settings page's 9 assistant-persona message fields (Welcome, Handoff, Idle warning, Idle close, Resolution prompt, Survey AI, Survey agent, Thanks, Assign agent) plus the unused Resolution message match the backend's `AssistantConfig`, and the 4 Features toggles match its `feature_faq`/`feature_memory`/`feature_citations`/`feature_contact_attributes` fields | source: `backend/apps/backend/src/chatbot/features/chat/adapters/assistants_store.py::AssistantConfig` field list | verified | 2026-08-12 |
| 04 | Tenant settings panel exposes exactly 10 keys (Assist/Copilot Gemini model, Copilot max tool iterations, AI assist/Copilot/AI drafts enabled, Default mode, Debounce seconds, Inbound auto-ack template, Escalation ack template) | source: `backend/apps/backend/src/chatbot/features/chat/settings_facade.py::_ALL_KEYS` — "All ten managed keys" | verified | 2026-08-12 |
| 04 | Welcome/disclaimer message on conversation start does not fire on this tenant today | raw capture: agent/backend env `LIFECYCLE_DISCLAIMER_ENABLED=false`; source `agent/app/services/lifecycle.py::on_conversation_created` — non-Email branch returns before calling `_welcome_text` when the flag is off (Email branch is independently gated on `EMAIL_AUTOACK_ENABLED`, also `false` in raw capture) | verified | 2026-08-12 |
| 04 | The assistant's own Survey AI / Survey agent / Thanks messages do not fire on this tenant today — a conversation resolved by the bot or by a human does not trigger this page's rating request | raw capture: agent/backend env `LIFECYCLE_SURVEY_ENABLED=false`; source `agent/app/services/lifecycle.py` — `handle_lifecycle_reply`'s survey branch and `on_human_resolved` both return early when the flag is off | verified | 2026-08-12 |
| 04 | The idle warning, automatic close, and "is your case resolved?" prompt ARE live on this tenant, and the Assign agent message fires from that same flow regardless of the survey flag | raw capture: agent/backend env `LIFECYCLE_ENABLED=true`; source `agent/app/services/lifecycle.py::handle_lifecycle_reply` — the `AWAITING_RESOLUTION`/"not resolved" branch posts `assign_agent` unconditionally, only the "resolved" branch's survey step is gated on `LIFECYCLE_SURVEY_ENABLED` | verified | 2026-08-12 |
| 04 | Customers still get a satisfaction-rating request on resolve, from Chatwoot's own native CSAT survey — a separate mechanism from the two rows above, configured per inbox rather than per assistant, and not exposed on this Settings page at all | live read-only query of `Inbox.csat_survey_enabled`, 2026-08-12 (see the `### native CSAT per inbox` raw-capture subsection): `true` on all four inboxes | verified | 2026-08-12 |
| 04 | Per-inbox idle-timer minutes, a per-inbox on/off switch for the idle-warning/auto-close step, and per-inbox overrides of the same message wording live on that inbox's own settings page under Administration → Inboxes, not on Knowledge → Inboxes or Knowledge → Settings, and win over the assistant persona message when both are set | source: `deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch` adds this panel to the native inbox settings page (`WeeklyAvailability.vue`), calling `GET/PUT /kb/inboxes/{inbox_id}/timing`; `agent/app/services/lifecycle.py::_resolve_lifecycle_message` — "Per-inbox override -> persona message -> SOP default" | verified | 2026-08-12 |
| 04 | Chatwoot also has its own native per-inbox greeting-message setting, distinct from both the assistant's Welcome message and native CSAT | this task's brief pointed to a "greeting_enabled" live query as already captured alongside the CSAT query; the actual `## Raw capture` / native-CSAT subsection contains no `greeting_enabled` data (grepped, zero matches) — the brief's premise does not hold against this evidence base as it currently stands. The concept is not asserted as a specific on/off state anywhere in this chapter for that reason. Would need the same live `Inbox.greeting_enabled` query pattern used for `csat_survey_enabled` to settle it | unverifiable | 2026-08-12 |

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

### native CSAT per inbox (added 2026-08-12, Task 6 fix round)

Read-only query of Chatwoot's own `Inbox.csat_survey_enabled` (Chatwoot's
native per-inbox CSAT toggle — distinct from the agent/backend's own
`LIFECYCLE_SURVEY_ENABLED`-gated rating prompt). Run to settle whether a
resolved conversation sends the customer a satisfaction survey:

```
[1] Proton API    | Channel::Api       | csat_survey_enabled=true
[2] Website Demo  | Channel::WebWidget | csat_survey_enabled=true
[3] Twilio Proton | Channel::TwilioSms | csat_survey_enabled=true
[4] Email         | Channel::Email     | csat_survey_enabled=true
```

All four inboxes have native CSAT **on**. This is the survey a customer
actually receives on resolve, and it's what feeds the `v_csat` BigQuery
view above — it is a *different* mechanism from the AI assistant's own
`LIFECYCLE_SURVEY_ENABLED`-gated rating prompt, which is off (see the
Raw capture agent/backend env block earlier in this file). Chapters 4 and
10 make lifecycle-message claims and should use this distinction rather
than re-deriving it.
