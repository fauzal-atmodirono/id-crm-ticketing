# Feature Guide — Outline & Evidence Map (internal working file)

Internal only — not part of the shipped guide, so it deliberately keeps
patch numbers / code paths as evidence citations for the drafting tasks.
The client-facing chapter files must NOT contain this level of detail
(no patch numbers, file paths, or env vars in chapter body text).

Chapters mirror `docs/superpowers/specs/2026-08-05-crm-feature-guide-design.md`
("Approach A — menu-mirror"). Every `##` heading below is now locked for the
skeleton: a later drafting task must not add/remove a `##` without updating
this table first.

## Skeleton template rule

Every `##` that documents an actual CRM feature/menu gets the five fixed
`###` headings (What it is / Where to find it / How to use it / Example
scenario / Integrations & automation) plus at least one `[[SCREENSHOT]]`
marker, per the plan's Global Constraints. Two chapters are structurally
different and are exempted from the 5-heading template, per the plan's
Task 7 (their content is prescribed differently there):
- **11-scenarios.md** — each `##` is a numbered end-to-end walkthrough
  narrative (not a single feature), so it gets a screenshot marker but not
  the 5 sub-headings.
- **13-glossary.md** — a single `##` holding a term/definition table; not a
  feature, so no 5-heading template and no screenshot marker.

## Corrections vs. the design spec draft (found during Step 1 research)

- **"DMS integration card" on the conversation view (spec Ch2) does not
  exist in the code.** Patch `0045-dms-integration-card.patch` (despite its
  filename) adds: (a) a permission-gated top-level "Integrations" sidebar
  page + a "DMS / TSP" sub-page for *configuring* the connection
  (admin-only — see `09-administration.md`), and (b) a "DMS / TSP" results
  block *inside the Customer 360 lookup page* (vehicle + service history),
  not a card inside the conversation view. No conversation-view file is
  touched by 0045. Ch2 therefore has no "DMS" section; the vehicle/service
  data is covered under Ch3's Customer 360 section instead.
- **Patch 0003's `PROTON_NAV` i18n keys (`REPORTS`/`FAQ_ADMIN`/`AGENTS`/`CASES`)
  are unused scaffolding** — grep confirms no other patch references them;
  every later Proton nav item (SLA Policies, Audit Log, Roles & Permissions,
  Escalation Routing, Customer 360, Cases, Integrations) hardcodes its own
  label string instead. There is no separate "FAQ Admin" page distinct from
  Knowledge → FAQs — that surface is covered once, in `04-knowledge.md`.
- **Reports extensions are not literally "Agent reports / Department
  reports / Case list report"** (the spec draft's approximate names).
  Patch `0020-reports-native-merge.patch` adds three new native-reports-page
  siblings with these exact sidebar labels: "Anomaly", "Departments & PIC",
  "Case Lifecycle" — plus embeds Proton-specific sections inside the
  existing native Agent/Bot/CSAT/SLA report pages. `0044-weekly-report.patch`
  adds a separate "Weekly Report" page. Ch7 uses the exact labels.
- Patches 0008, 0029, 0030, 0031, 0032, 0033, 0018, 0019 are internal
  plumbing/bugfixes/enterprise-cruft-removal with no distinct operator-visible
  feature surface of their own — not given their own `##` section, but 0008/
  0029/0032 are the reason certain native Chatwoot menus (Enterprise limits
  banner, Audit Logs/Custom Roles nav under Settings pre-Proton-RBAC,
  Security settings) are absent from this guide.
- **Mandated addition (Task 1 review): `02-conversations.md` gained a
  `## Macros` section** that Task 1's skeleton had omitted despite the
  design spec listing macros as a conversation-level feature. Macros are
  native Chatwoot; there is no Proton patch for them. The split mirrors
  Chatwoot's own UI: an agent *runs* an existing macro from the
  conversation view (covered here, `ch02-macros`); *creating/editing* a
  macro's steps is admin-only work, cross-referenced to the Macros section
  of `09-administration.md` rather than duplicated.

## Chapter → section → evidence → screenshot map

### 01-introduction.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| What is Proton e.MAS CRM | `CLAUDE.md` project overview; design spec | ch01-overview |
| Logging in | Native Chatwoot login (upstream v4) — `<!-- VERIFY-LIVE -->` | ch01-login |
| Screen layout | Native shell + `0003` (Proton nav host), `0009` (Knowledge nav) | ch01-dashboard-layout |
| Roles: agent vs administrator | Native Chatwoot roles + Proton RBAC overlay (`0027`, `0028`) | ch01-roles |
| Language (English / Indonesian) | `en`/`id` locale files touched across patches (e.g. `0003`) | ch01-language-toggle |

### 02-conversations.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Conversation inbox & views | Native `ChatList.vue`; `0037` (default "All" assignee tab), `0038` (default "All" status) | ch02-inbox-views |
| Assignment & teams | Native — `<!-- VERIFY-LIVE -->` | ch02-assignment |
| Labels | Native — `<!-- VERIFY-LIVE -->` (also carries `escalate`/`dealer_<slug>`, detailed in ch10) | ch02-labels |
| Priorities | Native + `0024-agent-priorities.patch` (`AgentPrioritiesEditor`) | ch02-priorities |
| Private notes | Native — `<!-- VERIFY-LIVE -->` | ch02-private-note |
| Canned responses | Native — `<!-- VERIFY-LIVE -->` | ch02-canned-responses |
| Macros | Native Chatwoot macros feature — `<!-- VERIFY-LIVE -->` (mandated addition, see corrections note below; running a macro is conversation-level, creating/editing one is admin work in `09-administration.md`) | ch02-macros |
| Mentions | Native — `<!-- VERIFY-LIVE -->` | ch02-mentions |
| Ask Copilot panel | `0005-ask-copilot-panel.patch` (panel UI), `0006-kb-sources.patch` (source citations); backend `assist/router.py` `POST /assist/ask` | ch02-copilot-panel |
| Suggest-a-reply | `0007-suggest-sources.patch` (reply-box suggestion + sources line); backend `POST /assist/suggest` | ch02-suggest-reply |
| Summarize conversation | `0002-ai-assist-backend.patch` (`ReplyTopPanel.vue`/`ReplyBox.vue` summarize action); backend `POST /assist/summarize` | ch02-summarize |
| AI auto-draft and suggest-vs-auto mode | `agent/app/services/orchestrator.py` (`AGENT_MODE` suggest = private note + reopen, auto = direct send) | ch02-ai-draft-note |
| Contact side panel | `0004-contact-panel-default.patch` (defaults open) | ch02-contact-panel |
| Resolving, snoozing & transcripts | Native — `<!-- VERIFY-LIVE -->` | ch02-resolve-snooze |

### 03-contacts.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Contacts list & search | Native — `<!-- VERIFY-LIVE -->` | ch03-contacts-list |
| Contact profile & history | Native — `<!-- VERIFY-LIVE -->` | ch03-contact-profile |
| Notes & segments | Native — `<!-- VERIFY-LIVE -->` | ch03-segments |
| Customer 360 | `0041-customer360-admin.patch` (`GET /admin/customer360/search`, permission `customer360.view`); DMS/TSP vehicle & service-history block added by `0045-dms-integration-card.patch` | ch03-customer360, ch03-customer360-dms |

### 04-knowledge.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| FAQs | `0009` (nav), `0010-knowledge-faqs-native.patch`, `0040-faq-bulk-csv-upload.patch` (bulk CSV import), `0033` (404 handling) | ch04-faqs, ch04-faq-bulk-upload |
| Documents | `0011-knowledge-documents-native.patch`, `0021-knowledge-uploads-native.patch` | ch04-documents |
| Assistants | `0012-knowledge-assistants.patch` | ch04-assistants |
| Scenarios | `0016-knowledge-scenarios.patch` | ch04-scenarios |
| Playground | `0014-knowledge-playground.patch` | ch04-playground |
| Tools | `0015-knowledge-tools.patch` | ch04-tools |
| Inboxes (assignment) | `0017-knowledge-inboxes.patch` | ch04-inboxes |
| Settings (persona, language, lifecycle messages, guardrails) | `0013-knowledge-settings.patch`, `0022-knowledge-persona-language-messages.patch`; backend `assistants_store.py` `AssistantConfig` | ch04-settings |

(`0009` = nav shell for all of the above; `0018`/`0019` are minor fixes with no new operator-visible surface, not separately listed.)

### 05-cases.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Case list | `0043-cases-list.patch` (`ProtonCasesPage`, `proton_cases` route, sidebar label "Cases") | ch05-case-list |
| Case categories | `0036-case-category-hierarchy.patch` (`case_category`/`case_subcategory` cascading custom attributes) | ch05-case-categories |
| Case lifecycle & status | `0043-cases-list.patch` | ch05-case-lifecycle |
| How cases relate to conversations | `0036-case-category-hierarchy.patch` (categories are conversation custom attributes) | ch05-case-conversation-link |

### 06-rsa.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Logging an RSA incident | `0035-rsa-incident-log.patch` (`ProtonRsaPage`, incident_date/vehicle_no/cause fields); backend `features/rsa/rsa_router.py` | ch06-rsa-new-incident |
| Incident statuses & updates | `0035-rsa-incident-log.patch`; backend `features/rsa/rsa_repository.py` | ch06-rsa-status |
| RSA in Customer 360 & reports | `0041-customer360-admin.patch` (RSA incidents surfaced by vehicle number); Ch7 Departments & PIC / Case Lifecycle reports | ch06-rsa-customer360-link |

### 07-reports.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Standard reports (Overview, Conversation, CSAT, Agent, Label, Inbox, Bot) | Native report pages — `<!-- VERIFY-LIVE -->`; enriched by Proton sections added in `0020-reports-native-merge.patch` (`ProtonAgentsSection.vue`, `ProtonBotSection.vue`, `ProtonCsatSection.vue`) | ch07-standard-reports |
| Anomaly report | `0020-reports-native-merge.patch` (`ProtonAnomaly.vue`, label "Anomaly"); backend `features/metrics/anomaly_router.py` | ch07-anomaly-report |
| Departments & PIC report | `0020-reports-native-merge.patch` (`ProtonDepartments.vue`, label "Departments & PIC") | ch07-departments-report |
| Case Lifecycle report | `0020-reports-native-merge.patch` (`ProtonCaseLifecycle.vue`, label "Case Lifecycle") | ch07-case-lifecycle-report |
| Weekly Report | `0044-weekly-report.patch` (label "Weekly Report", `proton_weekly_report` route); backend `features/metrics/insights_router.py` | ch07-weekly-report |
| SLA reports | Native `SLAReports.vue` + `ProtonSlaSection.vue` extension (`0020`); distinct from the SLA Policies admin page (Ch9) | ch07-sla-reports |
| Dealer escalation turnaround | `agent/app/services/sync.py::maybe_stamp_dealer_escalation` (stamps `dealer_escalated_at`, diffed against resolution time in the Departments & PIC / Case Lifecycle reports) | ch07-dealer-turnaround |

### 08-campaigns-helpcenter.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Campaigns | Native Chatwoot (one-off + ongoing) — `<!-- VERIFY-LIVE -->` | ch08-campaigns |
| Help Center portal | Native Chatwoot — `<!-- VERIFY-LIVE -->` | ch08-helpcenter |

### 09-administration.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Agents | Native — `<!-- VERIFY-LIVE -->` | ch09-agents |
| Teams | Native — `<!-- VERIFY-LIVE -->` | ch09-teams |
| Inboxes (incl. inactivity timing) | Native + `0023-inbox-inactivity-timing.patch` (idle-warn / close-grace / resolution-confirm-grace settings) | ch09-inboxes |
| Labels | Native — `<!-- VERIFY-LIVE -->` | ch09-labels |
| Custom Attributes | Native (also backs Ch5's case categories, `0036`) | ch09-custom-attributes |
| Automation | Native — `<!-- VERIFY-LIVE -->` | ch09-automation |
| Macros | Native — `<!-- VERIFY-LIVE -->` | ch09-macros |
| Canned Responses | Native — `<!-- VERIFY-LIVE -->` | ch09-canned-responses |
| Integrations (incl. DMS / TSP connection) | Native Settings → Integrations + `0045-dms-integration-card.patch` (`ProtonIntegrationsPage`, `ProtonDmsIntegrationPage`, permission `integration.manage`) | ch09-integrations |
| SLA Policies | `0025-sla-policies-admin.patch` (permission `sla.manage`) | ch09-sla-policies |
| Audit Log | `0026-audit-log-admin.patch` (permission `audit.view`) | ch09-audit-log |
| Roles & Permissions | `0027-roles-permissions-admin.patch`, `0028-chatwoot-access-permissions.patch` (permission list), `0031-permissions-no-poison-retry.patch` | ch09-roles-permissions |
| Escalation Routing | `0039-escalation-routing-admin.patch` (PIC per department, dealer directory, permission `escalation.manage`) | ch09-escalation-routing |
| Account settings | Native — `<!-- VERIFY-LIVE -->` | ch09-account-settings |

### 10-ai-behaviour.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| When the AI replies vs. hands off to a human | `agent/app/services/orchestrator.py` (agent-bot only acts on `pending` conversations; debounce) | ch10-ai-reply-vs-handoff |
| Suggest mode vs. Auto mode | `agent/app/services/orchestrator.py` (`AGENT_MODE`: suggest = private note + reopen; auto = direct send) | ch10-suggest-vs-auto |
| Escalation labels & the escalation email | `agent/app/services/sync.py::maybe_escalate` (`escalate` label → two-thread EM-7 email), `maybe_stamp_dealer_escalation` (`dealer_<slug>` label) | ch10-escalation-label |
| Lifecycle messages | `agent/app/services/lifecycle.py` (7 lifecycle messages); backend `ProtonConfigClient.get_assistant_messages` | ch10-lifecycle-messages |
| Phone / IVR touchpoint | `deploy/twilio/ivr-studio-flow.json`; backend `features/chat/` phone endpoints | ch10-phone-ivr |

### 11-scenarios.md (narrative walkthroughs — no 5-heading template)
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Scenario 1: WhatsApp inquiry to resolution | Ch2 (suggest-a-reply), Ch10 (suggest mode) | ch11-scenario1-whatsapp |
| Scenario 2: Complaint escalation to turnaround report | Ch2 (labels), Ch10 (escalation email), Ch7 (turnaround) | ch11-scenario2-escalation |
| Scenario 3: RSA call to Customer 360 follow-up | Ch6 (RSA), Ch3 (Customer 360) | ch11-scenario3-rsa |
| Scenario 4: FAQ batch import to live bot answer | Ch4 (FAQs bulk upload, Playground) | ch11-scenario4-faq-csv |
| Scenario 5: Weekly reporting routine | Ch7 (Weekly Report) | ch11-scenario5-weekly-report |
| Scenario 6: New agent onboarding | Ch9 (Roles, Teams, Inboxes) | ch11-scenario6-onboarding |

### 12-integrations.md
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| WhatsApp | Native Chatwoot channel — `<!-- VERIFY-LIVE -->` | ch12-whatsapp |
| Email (incl. escalation emails) | Native Chatwoot channel; `agent/app/services/sync.py` EM-7 escalation email | ch12-email |
| Phone / IVR | `deploy/twilio/ivr-studio-flow.json`; backend `features/chat/` phone endpoints | ch12-phone-ivr |
| Gemini AI | `agent/app/ai/gemini.py`, `app/ai/tools.py` | ch12-gemini-ai |
| DMS / TSP | `backend/.../features/chat/dms_client.py`; `0045-dms-integration-card.patch` | ch12-dms |
| Knowledge base (Vertex corpus) | `backend/.../features/chat` KB retrieval; pgvector operator KB (`/kb/knowledge`) | ch12-knowledge-base |
| BI / reporting exports | `backend/.../features/metrics/export_router.py`, `bigquery_schema.py` | ch12-bi-reporting |

### 13-glossary.md (single term/definition table — no 5-heading template, no screenshot)
| `##` section | Evidence | Screenshot id(s) |
|---|---|---|
| Terms | Plan Task 7's ~20-term list, cross-referenced against chapters above | (none — reference table, not a feature) |

Terms sourced from the plan's Task 7 list plus cross-references gathered above:
handoff, escalation, PIC, dealer slug, SLA, CSAT, RSA, DMS/TSP, IVR, persona,
guardrails, lifecycle message, segment, macro, canned response, agent-bot,
debounce (customer-facing: "brief wait before the AI answers"), private note,
Customer 360, case category, audit log, role & permission, weekly report.
