"""Sync Zendesk tickets into the BigQuery metrics table + refresh the views.

Run once for backfill, re-run any time to refresh:
    cd apps/backend
    .venv/bin/python scripts/sync_zendesk_metrics.py

Reads Zendesk creds + BigQuery target from settings/.env. Idempotent
(WRITE_TRUNCATE full reload). Makes live Zendesk + BigQuery calls.
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
        f"synced: {result['tickets']} tickets -> {result['rows']} rows "
        f"into {settings.bigquery_project_id}.{settings.bigquery_dataset}."
        f"{settings.bigquery_conversations_table}; views refreshed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
