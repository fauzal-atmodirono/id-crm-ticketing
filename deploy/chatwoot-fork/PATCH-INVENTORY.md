# Patch inventory — deploy/chatwoot-fork/patches/

**58 patches**, applied in shell-glob (numeric) order by
`deploy/chatwoot-fork/Dockerfile` at image build time. Pinned upstream:
**v4.15.1** (`UPSTREAM_VERSION`).

> **This file is generated.** Run `./rebase.sh --inventory` to regenerate it
> after adding a patch. Every column below is read out of the patch files
> themselves — nothing here is hand-written prose that could drift away from
> what the patches do.
>
> * **Summary** is the patch's own `Subject:` line. The older patches carry no
>   mail header; theirs is derived from the filename and marked as such.
> * **+/-** are added and removed line counts from the diff.
> * **Files** are the paths the diff touches; **new** means the patch creates
>   the file.
> * **Upstream conflict risk** is derived, not editorial:
>   **low** = every file it touches was created by a patch in this series, so
>   upstream cannot conflict with a file it has never heard of;
>   **upstream-owned** = it modifies at least one file upstream owns, so any
>   upstream release can conflict with it. A high line count on an
>   upstream-owned file is the expensive combination.
>
> Risk here is about *textual conflict on rebase only*. It says nothing about
> whether a patch still works after an upstream refactor — a patch can apply
> cleanly and break the Vite build or the runtime behaviour. See
> `docs/runbooks/environments.md` for what to verify after a rebase.

| # | Summary | Files | +/- | Upstream conflict risk |
|---|---|---|---|---|
| 0001 | runtime config (from filename) | 2 file(s)<br>`app/javascript/dashboard/composables/useProtonConfig.js` (new)<br>`app/views/layouts/vueapp.html.erb` | +15 / -0 | upstream-owned |
| 0002 | ai assist backend (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonAssist.js` (new)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue`<br>`app/javascript/dashboard/components/widgets/conversation/ReplyBox.vue` | +93 / -8 | upstream-owned |
| 0003 | proton nav menu (from filename) | 4 file(s)<br>`app/javascript/dashboard/i18n/locale/en/settings.json`<br>`app/javascript/dashboard/i18n/locale/id/settings.json`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonAppHost.vue` (new) | +57 / -0 | upstream-owned |
| 0004 | contact panel default (from filename) | 1 file(s)<br>`app/javascript/dashboard/routes/dashboard/conversation/ConversationView.vue` | +3 / -1 | upstream-owned |
| 0005 | ask copilot panel (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonCopilot.js` (new)<br>`app/javascript/dashboard/components/copilot/ProtonCopilotPanel.vue` (new)<br>`app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue`<br>`app/javascript/dashboard/routes/dashboard/conversation/ConversationView.vue` | +197 / -0 | upstream-owned |
| 0006 | kb sources (from filename) | 1 file(s)<br>`app/javascript/dashboard/components/copilot/ProtonCopilotPanel.vue` | +18 / -0 | low |
| 0007 | suggest sources (from filename) | 2 file(s)<br>`app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue`<br>`app/javascript/dashboard/components/widgets/conversation/ReplyBox.vue` | +22 / -2 | upstream-owned |
| 0008 | strip enterprise cruft (from filename) | 1 file(s)<br>`app/javascript/dashboard/store/modules/accounts.js` | +3 / -9 | upstream-owned |
| 0009 | knowledge nav (from filename) | 3 file(s)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` (new) | +78 / -0 | upstream-owned |
| 0010 | knowledge faqs native (from filename) | 3 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js` (new)<br>`app/javascript/dashboard/components/proton/KnowledgeFaqs.vue` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +366 / -19 | low |
| 0011 | knowledge documents native (from filename) | 3 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components/proton/KnowledgeDocuments.vue` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +131 / -0 | low |
| 0012 | knowledge assistants (from filename) | 6 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/components/proton/AssistantSelector.vue` (new)<br>`app/javascript/dashboard/components/proton/KnowledgeAssistants.vue` (new)<br>`app/javascript/dashboard/composables/useKnowledgeAssistant.js` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +402 / -1 | upstream-owned |
| 0013 | knowledge settings (from filename) | 3 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components/proton/KnowledgeSettings.vue` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +703 / -0 | low |
| 0014 | knowledge playground (from filename) | 3 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components/proton/KnowledgePlayground.vue` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +234 / -0 | low |
| 0015 | knowledge tools (from filename) | 3 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components/proton/KnowledgeTools.vue` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +801 / -0 | low |
| 0016 | knowledge scenarios (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/components/proton/KnowledgeScenarios.vue` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +447 / -0 | upstream-owned |
| 0017 | knowledge inboxes (from filename) | 3 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components/proton/KnowledgeInboxes.vue` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +202 / -0 | low |
| 0018 | knowledge review fixes (from filename) | 6 file(s)<br>`app/javascript/dashboard/components/proton/AssistantSelector.vue`<br>`app/javascript/dashboard/components/proton/KnowledgeAssistants.vue`<br>`app/javascript/dashboard/components/proton/KnowledgeInboxes.vue`<br>`app/javascript/dashboard/components/proton/KnowledgeScenarios.vue`<br>`app/javascript/dashboard/components/proton/KnowledgeSettings.vue`<br>`app/javascript/dashboard/components/proton/KnowledgeTools.vue` | +53 / -28 | low |
| 0019 | knowledge page bg (from filename) | 1 file(s)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +1 / -1 | low |
| 0020 | reports native merge (from filename) | 16 file(s)<br>`app/javascript/dashboard/api/protonMetrics.js` (new)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/AgentReportsIndex.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/BotReports.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/CsatResponses.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/ProtonAnomaly.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/ProtonCaseLifecycle.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/ProtonDepartments.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/SLAReports.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonAgentsSection.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonBotSection.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonCsatSection.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonSlaSection.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/reports.routes.js`<br>`app/javascript/shared/components/charts/DoughnutChart.vue` (new)<br>`app/javascript/shared/components/charts/LineChart.vue` (new) | +2047 / -79 | upstream-owned |
| 0021 | knowledge uploads native (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/components/proton/KnowledgeUploads.vue` (new)<br>`app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | +374 / -0 | upstream-owned |
| 0022 | knowledge persona language messages (from filename) | 1 file(s)<br>`app/javascript/dashboard/components/proton/KnowledgeSettings.vue` | +112 / -0 | low |
| 0023 | inbox inactivity timing (from filename) | 2 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/routes/dashboard/settings/inbox/components/WeeklyAvailability.vue` | +183 / -0 | upstream-owned |
| 0024 | agent priorities (from filename) | 3 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components/proton/AgentPrioritiesEditor.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/inbox/settingsPage/CollaboratorsPage.vue` | +203 / -0 | upstream-owned |
| 0025 | sla policies admin (from filename) | 5 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js` (new)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/composables/useProtonPermissions.js` (new)<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonSlaPoliciesPage.vue` (new) | +259 / -0 | upstream-owned |
| 0026 | audit log admin (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonAuditLogPage.vue` (new) | +145 / -0 | upstream-owned |
| 0027 | roles permissions admin (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonRolesPermissionsPage.vue` (new) | +276 / -0 | upstream-owned |
| 0028 | chatwoot access permissions (from filename) | 1 file(s)<br>`app/javascript/dashboard/views/ProtonRolesPermissionsPage.vue` | +94 / -1 | low |
| 0029 | remove dead enterprise nav (from filename) | 1 file(s)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue` | +0 / -18 | upstream-owned |
| 0030 | fix admin request auth (from filename) | 1 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js` | +16 / -6 | low |
| 0031 | permissions no poison retry (from filename) | 1 file(s)<br>`app/javascript/dashboard/composables/useProtonPermissions.js` | +19 / -1 | low |
| 0032 | hide security settings nav (from filename) | 1 file(s)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue` | +0 / -6 | upstream-owned |
| 0033 | faq uploads 404 handling (from filename) | 1 file(s)<br>`app/javascript/dashboard/components/proton/KnowledgeUploads.vue` | +8 / -0 | low |
| 0034 | reporting extensions (from filename) | 9 file(s)<br>`app/javascript/dashboard/api/protonMetrics.js`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/AgentReportsIndex.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/ProtonCaseLifecycle.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/ProtonDepartments.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/SLAReports.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonCallCentrePlaceholder.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonDealerEscalationSection.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonSlaComplianceSection.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonWipAgingSection.vue` (new) | +502 / -0 | upstream-owned |
| 0035 | rsa incident log (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonRsa.js` (new)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonRsaPage.vue` (new) | +671 / -0 | upstream-owned |
| 0036 | case category hierarchy (from filename) | 1 file(s)<br>`app/javascript/dashboard/routes/dashboard/conversation/customAttributes/CustomAttributes.vue` | +20 / -1 | upstream-owned |
| 0037 | default all conversations tab (from filename) | 1 file(s)<br>`app/javascript/dashboard/components/ChatList.vue` | +1 / -1 | upstream-owned |
| 0038 | default status all not open (from filename) | 1 file(s)<br>`app/javascript/dashboard/components/ChatList.vue` | +2 / -2 | upstream-owned |
| 0039 | escalation routing admin (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonEscalationRoutingPage.vue` (new) | +561 / -0 | upstream-owned |
| 0040 | faq bulk csv upload (from filename) | 2 file(s)<br>`app/javascript/dashboard/api/protonKnowledge.js`<br>`app/javascript/dashboard/components/proton/KnowledgeFaqs.vue` | +76 / -0 | low |
| 0041 | customer360 admin (from filename) | 4 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonCustomer360Page.vue` (new) | +277 / -0 | upstream-owned |
| 0043 | feat(chatwoot-fork): Cases list with the client's WIP-table | 5 file(s)<br>`app/javascript/dashboard/api/protonCases.js` (new)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/components/widgets/ProtonCasesTable.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonCasesPage.vue` (new) | +528 / -5 | upstream-owned |
| 0044 | weekly report (from filename) | 10 file(s)<br>`app/javascript/dashboard/api/protonCases.js`<br>`app/javascript/dashboard/api/protonMetrics.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/ProtonWeeklyReport.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonDealerEscalationSection.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonScopeBadge.vue` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonSlaComplianceSection.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonWipAgingSection.vue`<br>`app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/reportScope.js` (new)<br>`app/javascript/dashboard/routes/dashboard/settings/reports/reports.routes.js` | +1532 / -17 | upstream-owned |
| 0045 | feat(integrations): add DMS/TSP integration card + config | 6 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonCustomer360Page.vue`<br>`app/javascript/dashboard/views/ProtonDmsIntegrationPage.vue` (new)<br>`app/javascript/dashboard/views/ProtonIntegrationsPage.vue` (new) | +713 / -51 | upstream-owned |
| 0046 | escalation groups (from filename) | 1 file(s)<br>`app/javascript/dashboard/views/ProtonEscalationRoutingPage.vue` | +32 / -21 | low |
| 0047 | sla policy thresholds (from filename) | 1 file(s)<br>`app/javascript/dashboard/views/ProtonSlaPoliciesPage.vue` | +20 / -0 | low |
| 0048 | feat(chatwoot-fork): show the assigned agent in the Cases | 2 file(s)<br>`app/javascript/dashboard/api/protonCases.js`<br>`app/javascript/dashboard/components/widgets/ProtonCasesTable.vue` | +18 / -5 | low |
| 0049 | feat(chatwoot-fork): operator-editable email acknowledgement | 1 file(s)<br>`app/javascript/dashboard/components/proton/KnowledgeSettings.vue` | +64 / -0 | low |
| 0050 | feat(chatwoot-fork): generalize case-category cascade to a | 1 file(s)<br>`app/javascript/dashboard/routes/dashboard/conversation/customAttributes/CustomAttributes.vue` | +25 / -10 | upstream-owned |
| 0051 | page scroll (from filename) | 9 file(s)<br>`app/javascript/dashboard/views/ProtonAuditLogPage.vue`<br>`app/javascript/dashboard/views/ProtonCasesPage.vue`<br>`app/javascript/dashboard/views/ProtonCustomer360Page.vue`<br>`app/javascript/dashboard/views/ProtonDmsIntegrationPage.vue`<br>`app/javascript/dashboard/views/ProtonEscalationRoutingPage.vue`<br>`app/javascript/dashboard/views/ProtonIntegrationsPage.vue`<br>`app/javascript/dashboard/views/ProtonRolesPermissionsPage.vue`<br>`app/javascript/dashboard/views/ProtonRsaPage.vue`<br>`app/javascript/dashboard/views/ProtonSlaPoliciesPage.vue` | +9 / -9 | low |
| 0052 | escalation manager contact (from filename) | 1 file(s)<br>`app/javascript/dashboard/views/ProtonEscalationRoutingPage.vue` | +37 / -0 | low |
| 0053 | P6 task 9: the workforce dashboard admin page | 4 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonWorkforceDashboardPage.vue` (new) | +235 / -0 | upstream-owned |
| 0054 | P6 C1 fix: the agent availability-status selector | 4 file(s)<br>`app/javascript/dashboard/api/protonAdmin.js`<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonMyStatusPage.vue` (new) | +341 / -0 | upstream-owned |
| 0055 | P7 task 3: agent-facing translate action | 1 file(s)<br>`app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue` | +52 / -0 | upstream-owned |
| 0056 | P7 task 7: FAQ-suggestion strip with a one-click Apply | 1 file(s)<br>`app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue` | +118 / -2 | upstream-owned |
| 0057 | P9 tasks 2/3/6: new-inbound alerting in the main Chatwoot UI | 6 file(s)<br>`app/javascript/dashboard/api/protonAlerts.js` (new)<br>`app/javascript/dashboard/components-next/sidebar/Sidebar.vue`<br>`app/javascript/dashboard/composables/useProtonInboundAlerts.js` (new)<br>`app/javascript/dashboard/helper/protonAlerts.js` (new)<br>`app/javascript/dashboard/routes/dashboard/dashboard.routes.js`<br>`app/javascript/dashboard/views/ProtonAlertPreferencesPage.vue` (new) | +1097 / -0 | upstream-owned |
| 0058 | P9 task 7: one switch per feature, not two | 1 file(s)<br>`app/views/layouts/vueapp.html.erb` | +28 / -1 | upstream-owned |
| 0059 | Roles & Permissions: rail+detail redesign, staged saves | 2 file(s)<br>`app/javascript/dashboard/api/protonChatwootAgents.js` (new)<br>`app/javascript/dashboard/views/ProtonRolesPermissionsPage.vue` | +1145 / -212 | low |

## What the totals mean for pricing the fork

- **58 patches** in the series.
- **36** modify at least one upstream-owned file, so each one
  is exposed to every upstream release.
- **22** touch only files this series created, so upstream cannot
  conflict with them textually.

Tooling reduces the cost of this liability; it does not remove it. A patch
series against a fast-moving upstream is a standing commitment and should be
priced as one — see the note in
`docs/superpowers/plans/2026-08-08-rfp-p13-ops-hardening.md` task 6.

Generated by `deploy/chatwoot-fork/rebase.sh --inventory`.
