# Proton Wave 2 deploy steps (NOT part of the Wave 1 window)

Wave 2 ships after 17 Nov 2026. Nothing here belongs in the 10 Nov Wave 1
window (see `proton-deploy-2026-11-10.md`).

## AI performance reports (Task 27, FIX-47)

Needs `METRICS_PROVIDER=bigquery` (Wave 1) and fork image `rc17` or later
(patch 0083 renders the new Bot report section).

The backend serves `/metrics/ai-performance` from BigQuery views. Three of them
are new and do not exist in the warehouse until the views are re-created:
`v_ai_accuracy`, `v_ai_root_cause`, `v_ai_kb_improvement`. Until then those
three blocks report `unavailable` on the Bot report page (the other five
reports work, they use views that already exist).

After the backend image is rebuilt and recreated, run on the VM in
`/opt/platform/deploy`:

```bash
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env \
  exec backend python -c \
  "from chatbot.platform.config import get_settings; \
from chatbot.features.metrics.sync import ensure_views; \
ensure_views(get_settings())"
```

`ensure_views` runs `CREATE OR REPLACE VIEW` for every reporting view, so it is
safe to repeat and also refreshes the existing ones. It does not touch the
`conversations` table. It needs the backend service account to have BigQuery
job and dataset write access (the same access the sync uses).

Check the three views exist:

```bash
bq ls proton_crm | grep -E "v_ai_(accuracy|root_cause|kb_improvement)"
```

Then open Reports > Bot in the CRM: the "AI Performance" section should show no
"temporarily unavailable" note.

## Data-scoped permissions (Task 28, FIX-48)

Off by default (`DATA_SCOPED_RBAC_ENABLED=false`). It requires `RBAC_ENABLED=true`
(the backend refuses to boot without it). Order:

1. Deploy the Chatwoot image carrying patch 0084 (`rc17` or later). The reports
   client then sends the user's Chatwoot session. Without it, `/metrics/*`
   returns 401 once the flag is on.
2. Rebuild and recreate the backend (the `role_scopes` table is created on boot).
3. Configure scopes per role on Roles & Permissions > Data scope.
4. Only after Proton signs off (next section), switch the flag on through an
   ops script under `scripts/ops/`. Agents never edit `tenants/*.env`.

## Data scope limitations for Proton sign-off

Proton must accept these limits before the flag is switched on.

- **The Cases list and Chatwoot's conversation views are not confined by data
  scopes.** Both read Chatwoot directly with the agent's own session, so they
  follow **Chatwoot inbox membership only**. An inbox scope there is only as
  good as the agent's inbox membership. Dealer and department scopes do not
  confine them at all: a dealer-scoped agent who is a member of an inbox sees
  every dealer's cases in that inbox in the Cases list and the inbox views.
  - Recommendation: for each scoped user, set Chatwoot inbox membership to
    match their inbox scope. Do not rely on dealer or department scopes to
    hide conversations in Chatwoot's own screens.
  - Wave 3 option: a backend-proxied Cases list (`GET /cases` on the backend,
    filtered by the same scope) that the fork's Cases page reads instead of
    Chatwoot `/conversations`.
- **What data scopes do confine**: Customer 360 results, the case fields panel,
  `/metrics/dealer-escalation` and `/metrics/case-aging`, escalation routing
  (dealer rows for dealer-only scopes, PIC rows for department-only scopes),
  RSA incidents (by their dealer) and the AI activity monitor.
- **Reports**: today, **inbox-, team- and department-scoped users get no
  reports at all.** Every `/metrics/*` report, the dashboard, the export and
  the anomaly and control-item reports return 403 for them. Only a
  **dealer-only** scoped user gets reports, and only two:
  `/metrics/dealer-escalation` and `/metrics/case-aging`, filtered to their
  dealers. A user scoped on dealers plus anything else also gets none. The
  reason is that no warehouse view has an inbox or team column, and the views
  with a department column sit next to views without one in the same report.
  Every other report is refused rather than shown unfiltered. Adding the
  columns to the views is a later change.
- **Admin surfaces not governed by data scope.** These are gated by their RBAC
  permission only. Anyone holding that permission sees and edits them in full,
  whatever their scope:
  - workforce / presence dashboard;
  - audit log;
  - SLA policies;
  - escalation-ladder policy and settings;
  - case taxonomy admin;
  - DMS/TSP integration admin;
  - custom features (superadmin switchboard).

  Grant those permissions only to unrestricted roles.
- **Dealer region edit point.** A dealer-only scoped user who holds
  `escalation.manage` can edit their own dealer's routing row, including its
  `region`. The region decides which PRO-NET regional contacts (Area/Regional
  Manager, HOD) are CC'd on that dealer's escalations. PRO-NET rows themselves
  are unrestricted-only. If dealers must not re-point their own region, do not
  give dealer roles `escalation.manage`.
- **Administrators**: the RBAC administrator role and platform superadmins are
  never restricted. To confine someone, take them off that role. Do not
  tick "Chatwoot access" on a role whose members are Chatwoot administrators:
  the Chatwoot-access mirror replaces their Chatwoot administrator role with a
  custom role.
