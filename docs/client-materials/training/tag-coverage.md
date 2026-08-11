<!-- GENERATED FILE — do not edit by hand.
     Source: docs/client-materials/feature-guide-src-v3/ (the operator handbook)
     Regenerate: python3 docs/client-materials/build_crm_feature_guide.py --curricula
     Drift check: python3 docs/client-materials/build_crm_feature_guide.py --check
-->

# Training audience tag coverage

> **Generated from the operator handbook — do not edit.** Every line below is rendered from `feature-guide-src-v3/`; an edit here is overwritten by the next run. To change what a cohort is taught, change the handbook section this points at, or its `<!-- TRAINING: ... -->` marker, and regenerate.

**108 handbook sections across 14 chapters.** The audiences are cumulative (`agent` < `supervisor` < `admin`), so a section tagged `agent` is taught to all three cohorts.

| Curriculum | Topics | Exercises | Rule-derived length | Design target |
|---|---|---|---|---|
| Frontline agent | 54 | 12 | 5 h 31 min | 2 h |
| Supervisor / team leader | 71 | 20 | 7 h 49 min | 3 h |
| Administrator | 108 | 32 | 12 h 29 min | 4 h |

**Sections with no marker of their own or their chapter's: 0.** Those fall back to `admin`, the widest curriculum, so untagged content still reaches a cohort instead of vanishing from all three — and it is named here so the fallback is visible rather than assumed.

| Chapter | Section | Audience | Tagged by | Exercise | Agent | Supervisor | Admin |
|---|---|---|---|---|---|---|---|
| `01-introduction.md` | What is Proton e.MAS CRM | agent | chapter | — | yes | yes | yes |
| `01-introduction.md` | Logging in | agent | chapter | — | yes | yes | yes |
| `01-introduction.md` | Screen layout | agent | chapter | — | yes | yes | yes |
| `01-introduction.md` | Roles: agent vs administrator | agent | chapter | — | yes | yes | yes |
| `01-introduction.md` | Language (English / Indonesian) | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Conversation inbox & views | agent | section | yes | yes | yes | yes |
| `02-conversations.md` | Assignment & teams | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Labels | agent | section | yes | yes | yes | yes |
| `02-conversations.md` | AI-suggested escalation department | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Escalation replies | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | SLA breach alerts | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Priorities | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Private notes | agent | section | yes | yes | yes | yes |
| `02-conversations.md` | Canned responses | agent | section | yes | yes | yes | yes |
| `02-conversations.md` | Macros | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Mentions | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Ask Copilot panel | agent | section | yes | yes | yes | yes |
| `02-conversations.md` | Suggest-a-reply | agent | section | yes | yes | yes | yes |
| `02-conversations.md` | Summarize conversation | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | AI auto-draft and suggest-vs-auto mode | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Contact side panel | agent | chapter | — | yes | yes | yes |
| `02-conversations.md` | Resolving, snoozing & transcripts | agent | section | yes | yes | yes | yes |
| `03-contacts.md` | Contacts list & search | agent | section | yes | yes | yes | yes |
| `03-contacts.md` | Contact profile & history | agent | chapter | — | yes | yes | yes |
| `03-contacts.md` | Notes & segments | agent | chapter | — | yes | yes | yes |
| `03-contacts.md` | Customer 360 | supervisor | section | — | — | yes | yes |
| `04-knowledge.md` | FAQs | admin | section | yes | — | — | yes |
| `04-knowledge.md` | Documents | admin | section | yes | — | — | yes |
| `04-knowledge.md` | Assistants | admin | chapter | — | — | — | yes |
| `04-knowledge.md` | Scenarios | admin | chapter | — | — | — | yes |
| `04-knowledge.md` | Playground | admin | section | yes | — | — | yes |
| `04-knowledge.md` | Tools | admin | chapter | — | — | — | yes |
| `04-knowledge.md` | Inboxes (assignment) | admin | chapter | — | — | — | yes |
| `04-knowledge.md` | Settings (persona, language, lifecycle messages, guardrails) | admin | chapter | — | — | — | yes |
| `05-cases.md` | Case list | supervisor | section | yes | — | yes | yes |
| `05-cases.md` | Case categorisation (five fields) | agent | section | yes | yes | yes | yes |
| `05-cases.md` | Case lifecycle & status | agent | section | — | yes | yes | yes |
| `05-cases.md` | How cases relate to conversations | agent | section | — | yes | yes | yes |
| `05-cases.md` | Escalation status on a case | agent | section | — | yes | yes | yes |
| `06-rsa.md` | Logging an RSA incident | supervisor | section | yes | — | yes | yes |
| `06-rsa.md` | Incident statuses & updates | supervisor | chapter | — | — | yes | yes |
| `06-rsa.md` | RSA in Customer 360 & reports | supervisor | chapter | — | — | yes | yes |
| `07-reports.md` | Standard reports (Overview, Conversation, CSAT, Agent, Label, Inbox, Bot) | supervisor | chapter | — | — | yes | yes |
| `07-reports.md` | Anomaly report | supervisor | section | yes | — | yes | yes |
| `07-reports.md` | Departments & PIC report | supervisor | chapter | — | — | yes | yes |
| `07-reports.md` | Case Lifecycle report | supervisor | chapter | — | — | yes | yes |
| `07-reports.md` | Weekly Report | supervisor | section | yes | — | yes | yes |
| `07-reports.md` | SLA reports | supervisor | section | yes | — | yes | yes |
| `07-reports.md` | Dealer escalation turnaround | supervisor | chapter | — | — | yes | yes |
| `08-campaigns-helpcenter.md` | Campaigns | admin | chapter | — | — | — | yes |
| `08-campaigns-helpcenter.md` | Help Center portal | admin | chapter | — | — | — | yes |
| `09-administration.md` | Agents | admin | section | yes | — | — | yes |
| `09-administration.md` | Teams | admin | chapter | — | — | — | yes |
| `09-administration.md` | Inboxes (incl. inactivity timing) | admin | section | yes | — | — | yes |
| `09-administration.md` | Labels | admin | chapter | — | — | — | yes |
| `09-administration.md` | Custom Attributes | admin | section | yes | — | — | yes |
| `09-administration.md` | Automation | admin | chapter | — | — | — | yes |
| `09-administration.md` | Macros | admin | chapter | — | — | — | yes |
| `09-administration.md` | Canned Responses | admin | chapter | — | — | — | yes |
| `09-administration.md` | Integrations (incl. DMS / TSP connection) | admin | chapter | — | — | — | yes |
| `09-administration.md` | SLA Policies | supervisor | section | yes | — | yes | yes |
| `09-administration.md` | Audit Log | admin | section | yes | — | — | yes |
| `09-administration.md` | Roles & Permissions | admin | section | yes | — | — | yes |
| `09-administration.md` | Escalation Routing | admin | section | yes | — | — | yes |
| `09-administration.md` | Account settings | admin | chapter | — | — | — | yes |
| `10-ai-behaviour.md` | When the AI replies vs. hands off to a human | agent | chapter | — | yes | yes | yes |
| `10-ai-behaviour.md` | Suggest mode vs. Auto mode | agent | chapter | — | yes | yes | yes |
| `10-ai-behaviour.md` | Escalation labels & the escalation email | agent | chapter | — | yes | yes | yes |
| `10-ai-behaviour.md` | Lifecycle messages | agent | chapter | — | yes | yes | yes |
| `10-ai-behaviour.md` | Voice bot behaviour (the AI-answered part of a call) | agent | chapter | — | yes | yes | yes |
| `10-ai-behaviour.md` | Phone handoff behaviour (the human side) | agent | chapter | — | yes | yes | yes |
| `11-scenarios.md` | Scenario 1: WhatsApp inquiry to resolution | agent | section | yes | yes | yes | yes |
| `11-scenarios.md` | Scenario 2: Complaint escalation, the dealer's reply, and the turnaround report | agent | section | yes | yes | yes | yes |
| `11-scenarios.md` | Scenario 3: RSA call to Customer 360 follow-up | supervisor | section | — | — | yes | yes |
| `11-scenarios.md` | Scenario 4: FAQ batch import to live bot answer | admin | section | yes | — | — | yes |
| `11-scenarios.md` | Scenario 5: Weekly reporting routine | supervisor | section | yes | — | yes | yes |
| `11-scenarios.md` | Scenario 6: New agent onboarding | admin | section | yes | — | — | yes |
| `11-scenarios.md` | Scenario 7: A customer replies to their own acknowledgement email | agent | chapter | — | yes | yes | yes |
| `11-scenarios.md` | Scenario 8: Maintaining a dealer group | admin | section | yes | — | — | yes |
| `11-scenarios.md` | Scenario 9: An SLA breach reaches the PIC group and the case | supervisor | section | — | — | yes | yes |
| `11-scenarios.md` | Scenario 10: Adjusting SLA thresholds ahead of a launch event | supervisor | section | yes | — | yes | yes |
| `11-scenarios.md` | Scenario 11: Editing a customer-facing email template | admin | section | — | — | — | yes |
| `11-scenarios.md` | Scenario 12: A WhatsApp case looks escalated, but nothing was sent | agent | chapter | — | yes | yes | yes |
| `11-scenarios.md` | Scenario 13: The same limitation on a web chat case | agent | chapter | — | yes | yes | yes |
| `11-scenarios.md` | Scenario 14: An after-hours breakdown call, and an unanswered transfer | agent | chapter | — | yes | yes | yes |
| `11-scenarios.md` | Scenario 15: Categorising a case through all five taxonomy dropdowns | agent | section | yes | yes | yes | yes |
| `11-scenarios.md` | Scenario 16: An AI-suggested escalation department — accepted, and ignored | agent | chapter | — | yes | yes | yes |
| `11-scenarios.md` | Scenario 17: Verifying escalation now reaches all six departments and a dealer group | admin | section | — | — | — | yes |
| `12-channel-playbooks.md` | How the channels map to the CRM | agent | chapter | — | yes | yes | yes |
| `12-channel-playbooks.md` | The toolkit that is identical on every channel | agent | chapter | — | yes | yes | yes |
| `12-channel-playbooks.md` | WhatsApp | agent | section | — | yes | yes | yes |
| `12-channel-playbooks.md` | Web chatbot | agent | chapter | — | yes | yes | yes |
| `12-channel-playbooks.md` | Voice bot — the AI-answered part of a call | agent | chapter | — | yes | yes | yes |
| `12-channel-playbooks.md` | Phone — the human side of the same call | agent | chapter | — | yes | yes | yes |
| `12-channel-playbooks.md` | Email | agent | section | — | yes | yes | yes |
| `12-channel-playbooks.md` | One customer, four touchpoints | agent | chapter | — | yes | yes | yes |
| `12-channel-playbooks.md` | Quick reference | agent | chapter | — | yes | yes | yes |
| `12-channel-playbooks.md` | Known limitations, and what to tell the customer | agent | chapter | — | yes | yes | yes |
| `13-integrations.md` | WhatsApp | admin | chapter | — | — | — | yes |
| `13-integrations.md` | Web chatbot | admin | chapter | — | — | — | yes |
| `13-integrations.md` | Voice bot | admin | chapter | — | — | — | yes |
| `13-integrations.md` | Phone | admin | chapter | — | — | — | yes |
| `13-integrations.md` | Email (incl. escalation emails) | admin | chapter | — | — | — | yes |
| `13-integrations.md` | Gemini AI | admin | chapter | — | — | — | yes |
| `13-integrations.md` | DMS / TSP | admin | chapter | — | — | — | yes |
| `13-integrations.md` | Knowledge base (Vertex corpus) | admin | chapter | — | — | — | yes |
| `13-integrations.md` | BI / reporting exports | admin | chapter | — | — | — | yes |
| `14-glossary.md` | Terms | agent | chapter | — | yes | yes | yes |
