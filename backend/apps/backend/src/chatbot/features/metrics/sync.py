"""Chatwoot -> BigQuery sync: fetch conversations, map, load the conversations table.

PROTON migrated its CRM from Zendesk to Chatwoot. The batch (Lane B) sync
that populates the BigQuery ``conversations`` table now pages the Chatwoot
conversations API instead of the Zendesk incremental-tickets export. The AI
classification (division/category/subcategory/department/sla) is read back off the
Chatwoot conversation LABELS that ``ChatwootAdapter`` writes at escalation time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from google.cloud import bigquery

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls
from chatbot.features.metrics.mapping import (
    ConversationRow,
    apply_working_hours,
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


def _inbox_scope(settings: Settings) -> list[int | None]:
    """Inbox ids the SLA scan should cover, from ``settings.sla_inbox_ids``.

    Empty (the default, and what a ``Settings``/stub without the attribute
    at all also resolves to via ``getattr``) preserves pre-existing
    behaviour exactly: scan only ``chatwoot_inbox_id``. ``"*"`` means "no
    inbox filter" (``[None]``) -- needed because the Email inbox a tenant
    needs the escalation timers to watch is normally NOT
    ``chatwoot_inbox_id``. A comma-separated list scans each id in turn.
    Garbage entries are dropped rather than raised on; if that empties the
    list entirely, fall back to the single-inbox default so a malformed
    value degrades safely instead of turning into an unbounded account-wide
    scan. Note ``str.isdigit()`` is true for non-ASCII digit characters
    (e.g. superscript "²") that ``int()`` cannot parse -- use
    ``.isdecimal()``, which only accepts characters ``int()`` accepts, so a
    stray Unicode character degrades instead of raising out of a background
    scan job. Duplicate ids (e.g. a copy-paste config mistake) are deduped,
    preserving order, so a repeated id doesn't get double-fetched -- this
    list also feeds ``/tasks/mine`` directly, where a duplicate would show
    up as visibly duplicated rows.
    """
    raw = (getattr(settings, "sla_inbox_ids", "") or "").strip()
    if not raw:
        return [settings.chatwoot_inbox_id]
    if raw == "*":
        return [None]
    ids: list[int | None] = [int(part) for part in raw.split(",") if part.strip().isdecimal()]
    ids = list(dict.fromkeys(ids))
    return ids or [settings.chatwoot_inbox_id]


def _fetch_conversations_for_inbox(
    base: str, inbox_id: int | None, get_page: Callable[[str], dict[str, Any]]
) -> list[dict[str, Any]]:
    """Page one inbox scope entry until an empty page. ``inbox_id=None``
    omits the query filter entirely (account-wide scan)."""
    conversations: list[dict[str, Any]] = []
    page_num = 1
    while True:
        filter_part = "" if inbox_id is None else f"&inbox_id={inbox_id}"
        url = f"{base}?status=all{filter_part}&page={page_num}"
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


def fetch_conversations(
    settings: Settings, *, get_page: Callable[[str], dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Page the Chatwoot account conversations API across the configured
    inbox scope (see ``_inbox_scope`` / ``sla_inbox_ids``).

    Auth MUST use the dashed ``Api-Access-Token`` header (some reverse proxies
    strip underscore headers, mirroring the adapter). Each scope entry pages
    1..N until a page returns fewer conversations than the previous
    non-empty one / an empty page; results accumulate across scope entries.
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
    conversations: list[dict[str, Any]] = []
    for inbox_id in _inbox_scope(settings):
        conversations.extend(_fetch_conversations_for_inbox(base, inbox_id, get_page))
    return conversations


def fetch_inbox_hours(
    settings: Settings, inbox_id: int, *, get_page: Callable[[str], dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """GET one inbox's business-hours config. Returns None on any failure
    (network error, 4xx/5xx, malformed response) — the caller then falls
    back to calendar-time for every row in that inbox, never raises."""
    if get_page is None:
        token = settings.chatwoot_api_token
        headers = {"Api-Access-Token": token, "api_access_token": token}

        def get_page(url: str) -> dict[str, Any]:
            with httpx.Client(timeout=15.0) as client:
                res = client.get(url, headers=headers)
                res.raise_for_status()
                return dict(res.json())

    url = (
        f"{settings.chatwoot_api_url.rstrip('/')}"
        f"/api/v1/accounts/{settings.chatwoot_account_id}/inboxes/{inbox_id}"
    )
    try:
        return get_page(url)
    except Exception as e:
        _log.warning("fetch_inbox_hours_failed", inbox_id=inbox_id, error=str(e))
        return None


def _is_demo_seed(conv: dict[str, Any]) -> bool:
    """True when this conversation carries the demo-seed marker written by
    ``deploy/scripts/seed_demo_data/`` onto every contact/conversation it
    creates (``custom_attributes.demo_seed = <batch_id>``). Chatwoot's
    conversations list already returns ``custom_attributes`` on each item —
    ``map_chatwoot_conversation_to_row`` already reads production fields
    (``case_category``, ``dealer_escalated_at``, ``case_type``,
    ``vehicle_model``) off it, so the field is confirmed present here."""
    custom_attrs = conv.get("custom_attributes")
    return isinstance(custom_attrs, dict) and bool(custom_attrs.get("demo_seed"))


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
    # Derived field-by-field from the dataclass rather than hand-listed.
    #
    # It WAS hand-listed, and P1 and P3 both added columns to the schema and to
    # ConversationRow while this dict silently kept dropping them -- twelve
    # columns that would have loaded as NULL forever, with the schema, the
    # mapper and the views all looking correct. Nothing failed; the data just
    # was not there. `test_every_conversation_row_field_is_loaded` is the guard
    # that makes the next omission impossible.
    #
    # `synced_at` is the one field with no row counterpart: it records when
    # THIS sync ran, not anything about the conversation.
    json_rows = [
        {**{f.name: getattr(r, f.name) for f in fields(r)}, "synced_at": now} for r in rows
    ]
    job_config = bigquery.LoadJobConfig(
        schema=CONVERSATIONS_SCHEMA, write_disposition="WRITE_TRUNCATE"
    )
    client.load_table_from_json(json_rows, table_id, job_config=job_config).result()


def ensure_views(settings: Settings) -> None:
    """Create/replace the Looker views (live).

    P8 task 6: `csat_by_agent_enabled` and `csat_ranking_min_samples` are
    threaded through here because a flag that never reaches `view_ddls` is a
    flag with no effect -- the per-agent CSAT view would simply never be
    created, with a passing unit test on the DDL builder either way. Off (the
    default) omits the view entirely, so a tenant who has not opted in gets
    the exact same warehouse it had before P8.
    """
    client = bigquery.Client(project=settings.bigquery_project_id)
    for ddl in view_ddls(
        settings.bigquery_project_id,
        settings.bigquery_dataset,
        settings.bigquery_conversations_table,
        settings.resolution_sla_targets_json,
        # P4: UTC unless the tenant deliberately changed it. Note this
        # RE-CREATES every view -- switching the zone re-buckets all history
        # on the next ensure_views() run, which is why it is an operator
        # decision, not a deploy-time default.
        settings.reporting_timezone,
        # `first_response_target_minutes` is skipped deliberately: there is no
        # setting for it (checked -- `config.py` has no such field), so it
        # keeps its 120-minute default rather than being invented here.
        csat_by_agent_enabled=settings.csat_by_agent_enabled,
        csat_ranking_min_samples=settings.csat_ranking_min_samples,
        # P8 task 9: `v_kb_coverage` prints the floor that was in force in its
        # `coverage_basis` column, so a coverage trend cannot be read across a
        # floor change without noticing.
        kb_score_floor=settings.kb_score_floor,
    ).values():
        client.query(ddl).result()


def run_sync(
    settings: Settings,
    *,
    fetch: Callable[[Settings], list[dict[str, Any]]] | None = None,
    fetch_inbox: Callable[[Settings, int], dict[str, Any] | None] | None = None,
    load: Callable[[Settings, list[ConversationRow]], None] | None = None,
) -> dict[str, int]:
    """Fetch conversations, map to rows, augment with business-hours timing,
    load. Returns counts. Injectable for tests.

    When ``settings.metrics_exclude_demo_seed`` is True, conversations
    carrying the ``demo_seed`` custom-attribute marker (stamped by
    ``deploy/scripts/seed_demo_data/``) are dropped before mapping, so they
    never reach BigQuery and never count in the returned totals. Default
    False keeps today's behavior byte-identical.

    Each unique inbox_id's business-hours config is fetched at most once per
    sync run (cached in ``inbox_cache``). A per-inbox fetch failure — whether
    ``fetch_inbox_fn`` returns None (the documented contract) or raises
    (defensive: a caller-supplied ``fetch_inbox`` isn't guaranteed to honor
    that contract) — degrades only that inbox's rows to calendar-time via
    ``apply_working_hours(row, None)``; it never aborts the whole sync.
    """
    conversations = (fetch or fetch_conversations)(settings)
    if settings.metrics_exclude_demo_seed:
        conversations = [c for c in conversations if not _is_demo_seed(c)]
    fetch_inbox_fn = fetch_inbox or fetch_inbox_hours
    inbox_cache: dict[int, dict[str, Any] | None] = {}

    rows: list[ConversationRow] = []
    for conv in conversations:
        row = map_chatwoot_conversation_to_row(conv)
        if row is None:
            continue
        inbox_id = conv.get("inbox_id")
        inbox: dict[str, Any] | None = None
        if isinstance(inbox_id, int):
            if inbox_id not in inbox_cache:
                try:
                    inbox_cache[inbox_id] = fetch_inbox_fn(settings, inbox_id)
                except Exception as e:
                    _log.warning("fetch_inbox_failed_in_sync", inbox_id=inbox_id, error=str(e))
                    inbox_cache[inbox_id] = None
            inbox = inbox_cache[inbox_id]
        rows.append(apply_working_hours(row, inbox))

    (load or load_conversations)(settings, rows)
    _log.info("metrics_sync_done", conversations=len(conversations), rows=len(rows))
    return {"conversations": len(conversations), "rows": len(rows)}
