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
