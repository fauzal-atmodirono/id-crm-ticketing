"""Chatwoot -> BigQuery sync: fetch conversations, map, load the conversations table.

PROTON migrated its CRM from Zendesk to Chatwoot+Zammad. The batch (Lane B) sync
that populates the BigQuery ``conversations`` table now pages the Chatwoot
conversations API instead of the Zendesk incremental-tickets export. The AI
classification (division/category/subcategory/department/sla) is read back off the
Chatwoot conversation LABELS that ``ChatwootAdapter`` writes at escalation time.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from google.cloud import bigquery

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls
from chatbot.features.metrics.mapping import (
    ConversationRow,
    map_chatwoot_conversation_to_row,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def _conversations_from_page(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the conversation list from one Chatwoot list-response page.

    The account conversations endpoint wraps results as
    ``{"data": {"payload": [...], "meta": {...}}}``; tolerate a couple of shapes.
    """
    data = page.get("data")
    if isinstance(data, dict):
        payload = data.get("payload")
        if isinstance(payload, list):
            return [c for c in payload if isinstance(c, dict)]
    payload = page.get("payload")
    if isinstance(payload, list):
        return [c for c in payload if isinstance(c, dict)]
    return []


def fetch_conversations(
    settings: Settings, *, get_page: Callable[[str], dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Page the Chatwoot account conversations API, filtered to our inbox.

    Auth MUST use the dashed ``Api-Access-Token`` header (some reverse proxies
    strip underscore headers, mirroring the adapter). Pages 1..N until a page
    returns fewer conversations than the previous non-empty one / an empty page.
    """
    if get_page is None:
        token = settings.chatwoot_api_token
        headers = {
            "Api-Access-Token": token,
            "api_access_token": token,
        }

        def get_page(url: str) -> dict[str, Any]:
            with httpx.Client(timeout=30.0) as client:
                res = client.get(url, headers=headers)
                res.raise_for_status()
                return dict(res.json())

    base = (
        f"{settings.chatwoot_api_url.rstrip('/')}"
        f"/api/v1/accounts/{settings.chatwoot_account_id}/conversations"
    )
    inbox_id = settings.chatwoot_inbox_id
    conversations: list[dict[str, Any]] = []
    page_num = 1
    while True:
        url = f"{base}?status=all&inbox_id={inbox_id}&page={page_num}"
        page = get_page(url)
        batch = _conversations_from_page(page)
        if not batch:
            break
        # Defensive inbox filter: the API filter should already scope this, but a
        # shared instance may ignore an unknown param, so keep only our inbox.
        conversations.extend(
            c for c in batch if inbox_id in (0, None) or c.get("inbox_id") in (inbox_id, None)
        )
        page_num += 1
    return conversations


def load_conversations(settings: Settings, rows: list[ConversationRow]) -> None:
    """WRITE_TRUNCATE-load the conversation rows into BigQuery (live)."""
    client = bigquery.Client(project=settings.bigquery_project_id)
    client.create_dataset(
        f"{settings.bigquery_project_id}.{settings.bigquery_dataset}", exists_ok=True
    )
    table_id = (
        f"{settings.bigquery_project_id}.{settings.bigquery_dataset}."
        f"{settings.bigquery_conversations_table}"
    )
    now = datetime.now(UTC).isoformat()
    json_rows = [
        {
            "conversation_id": r.conversation_id,
            "channel": r.channel,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "status": r.status,
            "resolved_by": r.resolved_by,
            "csat_score": r.csat_score,
            "nps_score": r.nps_score,
            "synced_at": now,
            "division": r.division,
            "category": r.category,
            "subcategory": r.subcategory,
            "department": r.department,
            "agent_id": r.agent_id,
            "pic": r.pic,
            "sla_minutes": r.sla_minutes,
            "sla_deadline": r.sla_deadline,
            "first_response_at": r.first_response_at,
            "resolved_at": r.resolved_at,
            "reopen_count": r.reopen_count,
        }
        for r in rows
    ]
    job_config = bigquery.LoadJobConfig(
        schema=CONVERSATIONS_SCHEMA, write_disposition="WRITE_TRUNCATE"
    )
    client.load_table_from_json(json_rows, table_id, job_config=job_config).result()


def ensure_views(settings: Settings) -> None:
    """Create/replace the Looker views (live)."""
    client = bigquery.Client(project=settings.bigquery_project_id)
    for ddl in view_ddls(
        settings.bigquery_project_id,
        settings.bigquery_dataset,
        settings.bigquery_conversations_table,
    ).values():
        client.query(ddl).result()


def run_sync(
    settings: Settings,
    *,
    fetch: Callable[[Settings], list[dict[str, Any]]] | None = None,
    load: Callable[[Settings, list[ConversationRow]], None] | None = None,
) -> dict[str, int]:
    """Fetch conversations, map to rows, load. Returns counts. Injectable for tests."""
    conversations = (fetch or fetch_conversations)(settings)
    rows = [row for c in conversations if (row := map_chatwoot_conversation_to_row(c)) is not None]
    (load or load_conversations)(settings, rows)
    _log.info("metrics_sync_done", conversations=len(conversations), rows=len(rows))
    return {"conversations": len(conversations), "rows": len(rows)}
