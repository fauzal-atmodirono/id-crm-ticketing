"""Sync Chatwoot conversations into the BigQuery metrics table + refresh the views.

PROTON runs on Chatwoot+Zammad. This pages the Chatwoot conversations API, maps
each conversation (reading the AI-classification LABELS the adapter wrote at
escalation time) to a conversation row, and WRITE_TRUNCATE-loads the BigQuery
``conversations`` table, then refreshes the Looker views.

Run once for backfill, re-run any time to refresh:
    cd apps/backend
    .venv/bin/python scripts/sync_chatwoot_metrics.py

Reads Chatwoot creds + BigQuery target from settings/.env. Idempotent
(WRITE_TRUNCATE full reload). Makes live Chatwoot + BigQuery calls.
"""

from __future__ import annotations

import sys

from chatbot.features.metrics.sync import ensure_views, run_sync
from chatbot.platform.config import get_settings


def main() -> int:
    settings = get_settings()
    result = run_sync(settings)
    ensure_views(settings)
    print(
        f"synced: {result['conversations']} conversations -> {result['rows']} rows "
        f"into {settings.bigquery_project_id}.{settings.bigquery_dataset}."
        f"{settings.bigquery_conversations_table}; views refreshed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
