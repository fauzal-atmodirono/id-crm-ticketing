"""Zendesk -> BigQuery sync: fetch tickets, map, load the conversations table."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from google.cloud import bigquery

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls
from chatbot.features.metrics.mapping import ConversationRow, map_ticket_to_row

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def _auth_header(settings: Settings) -> str:
    raw = f"{settings.zendesk_email}/token:{settings.zendesk_api_token}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def fetch_tickets(
    settings: Settings, *, get_page: Callable[[str], dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Page the Zendesk Incremental Ticket Export from the beginning."""
    if get_page is None:
        headers = {"Authorization": _auth_header(settings)}

        def get_page(url: str) -> dict[str, Any]:
            with httpx.Client(timeout=30.0) as client:
                res = client.get(url, headers=headers)
                res.raise_for_status()
                return dict(res.json())

    sub = settings.zendesk_subdomain
    url: str | None = (
        f"https://{sub}.zendesk.com/api/v2/incremental/tickets.json"
        f"?start_time=0&include=metric_sets"
    )
    tickets: list[dict[str, Any]] = []
    while url:
        page = get_page(url)
        tickets.extend(page.get("tickets", []))
        if page.get("end_of_stream"):
            break
        url = page.get("next_page")
    return tickets


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
    """Fetch tickets, map to rows, load. Returns counts. Injectable for tests."""
    tickets = (fetch or fetch_tickets)(settings)
    rows = [row for t in tickets if (row := map_ticket_to_row(t)) is not None]
    (load or load_conversations)(settings, rows)
    _log.info("metrics_sync_done", tickets=len(tickets), rows=len(rows))
    return {"tickets": len(tickets), "rows": len(rows)}
