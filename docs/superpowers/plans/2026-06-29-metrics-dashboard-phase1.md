# Bot-Metrics Dashboard Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sync existing Zendesk tickets into a BigQuery `conversations` table (+ 3 views) so a Looker Studio dashboard can show Volume, Transfer-vs-Closed, and CSAT per channel — no app instrumentation.

**Architecture:** A standalone sync (`scripts/sync_zendesk_metrics.py`) pulls tickets via Zendesk's Incremental Export API, maps each through a pure `map_ticket_to_row` function (channel from `external_id` prefix, `resolved_by` from status, CSAT from the `csat_N` tag), and `WRITE_TRUNCATE`-loads them into `lv-playground-genai.demo_proton.conversations`; three BigQuery views shape the metrics for Looker. The pure mapping + view DDL are unit-tested; the Zendesk fetch + BQ load are thin and proven by running the sync.

**Tech Stack:** Python 3.12, `google-cloud-bigquery`, httpx, Zendesk Incremental Export API, BigQuery + Looker Studio, pytest, ruff, mypy --strict.

## Global Constraints

- Backend commands run from `apps/backend/`. Quality gates per task: `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict`, `.venv/bin/pytest src/`.
- `asyncio_mode = "auto"`.
- 3 pre-existing mypy errors (firestore_session_service.py, service.py, test_service.py) + 1 pre-existing ruff error (vertex_search.py PLC0415) live in unrelated files — do not fix; introduce no NEW ones.
- BigQuery target: project `lv-playground-genai`, dataset `demo_proton`, table `conversations` (config defaults). ADC auth (same as Vertex/Firestore). Zendesk auth = HTTP Basic `base64("{zendesk_email}/token:{zendesk_api_token}")`.
- New code lives in `apps/backend/src/chatbot/features/metrics/`; the CLI in `apps/backend/scripts/`.
- The sync script makes live Zendesk + BigQuery calls — implementers must NOT run it against live services (syntax/ast-check only); it's verified by the user in the smoke step.
- Channel mapping (external_id prefix → channel): `whatsapp`→`WhatsApp`, `email`→`Email`, `phone`→`Phone`, `sim`→`Web`, `zendesk`→`Web`, `chatwoot`→`Web`, else `Other`.
- `resolved_by`: `agent` if status ∈ {new, open, pending, hold}, else `bot`.
- CSAT: first tag matching `^csat_([1-5])$` → int, else NULL.
- Conventional commits. Never print secrets.

---

### Task 1: Dependency + configuration

**Files:**
- Modify: `apps/backend/pyproject.toml` (add `google-cloud-bigquery`)
- Modify: `apps/backend/src/chatbot/platform/config.py`
- Test: `apps/backend/src/chatbot/platform/test_config_bigquery.py` (create)

**Interfaces:**
- Produces `Settings` fields: `bigquery_project_id: str`, `bigquery_dataset: str`, `bigquery_conversations_table: str`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/src/chatbot/platform/test_config_bigquery.py`:

```python
from chatbot.platform.config import get_settings


def test_bigquery_settings_defaults() -> None:
    s = get_settings()
    assert s.bigquery_project_id == "lv-playground-genai"
    assert s.bigquery_dataset == "demo_proton"
    assert s.bigquery_conversations_table == "conversations"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_bigquery.py -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Add config fields**

In `config.py`, after the Zendesk settings block:

```python
    # Bot-metrics dashboard (Zendesk -> BigQuery sync)
    bigquery_project_id: str = "lv-playground-genai"
    bigquery_dataset: str = "demo_proton"
    bigquery_conversations_table: str = "conversations"
```

- [ ] **Step 4: Add the dependency**

Run: `uv add google-cloud-bigquery`
Verify: `.venv/bin/python -c "from google.cloud import bigquery; print('bq ok')"` → `bq ok`.

- [ ] **Step 5: Run test + gates**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_bigquery.py -v && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: PASS, no new errors.

> Note: if `mypy --strict` flags `google.cloud.bigquery` as untyped, add a `[[tool.mypy.overrides]]` block for `module = ["google.cloud.*"]` only if one doesn't already exist (the repo already has an override for `google.cloud.*` — reuse it; add nothing if present).

- [ ] **Step 6: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/src/chatbot/platform/config.py apps/backend/src/chatbot/platform/test_config_bigquery.py
git commit -m "feat(metrics): google-cloud-bigquery dep + BigQuery config"
```

---

### Task 2: Pure ticket → row mapping

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/__init__.py` (empty)
- Create: `apps/backend/src/chatbot/features/metrics/mapping.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_mapping.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class ConversationRow` with `conversation_id: str`, `channel: str`, `created_at: str | None`, `updated_at: str | None`, `status: str`, `resolved_by: str`, `csat_score: int | None`.
- Produces: `def map_ticket_to_row(ticket: dict[str, object]) -> ConversationRow | None` (None = skip non-conversation).

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/metrics/test_mapping.py`:

```python
import pytest

from chatbot.features.metrics.mapping import ConversationRow, map_ticket_to_row


def _ticket(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 55,
        "external_id": "whatsapp-+60123",
        "status": "solved",
        "tags": [],
        "created_at": "2026-06-21T09:14:00Z",
        "updated_at": "2026-06-21T09:31:00Z",
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    "external_id,expected",
    [
        ("whatsapp-+60123", "WhatsApp"),
        ("email-55", "Email"),
        ("phone-CA1", "Phone"),
        ("sim-abc", "Web"),
        ("zendesk-conv-9", "Web"),
        ("chatwoot-conv-9", "Web"),
        ("weird-1", "Other"),
    ],
)
def test_channel_from_external_id(external_id, expected) -> None:
    row = map_ticket_to_row(_ticket(external_id=external_id))
    assert row is not None and row.channel == expected


@pytest.mark.parametrize(
    "status,expected",
    [
        ("new", "agent"),
        ("open", "agent"),
        ("pending", "agent"),
        ("hold", "agent"),
        ("solved", "bot"),
        ("closed", "bot"),
    ],
)
def test_resolved_by_from_status(status, expected) -> None:
    row = map_ticket_to_row(_ticket(status=status))
    assert row is not None and row.resolved_by == expected


def test_csat_from_tag() -> None:
    row = map_ticket_to_row(_ticket(tags=["foo", "csat_4", "bar"]))
    assert row is not None and row.csat_score == 4


def test_no_csat_tag_is_none() -> None:
    row = map_ticket_to_row(_ticket(tags=["foo"]))
    assert row is not None and row.csat_score is None


def test_csat_tag_out_of_range_ignored() -> None:
    row = map_ticket_to_row(_ticket(tags=["csat_9"]))
    assert row is not None and row.csat_score is None


def test_basic_fields_pass_through() -> None:
    row = map_ticket_to_row(_ticket())
    assert row == ConversationRow(
        conversation_id="55",
        channel="WhatsApp",
        created_at="2026-06-21T09:14:00Z",
        updated_at="2026-06-21T09:31:00Z",
        status="solved",
        resolved_by="bot",
        csat_score=None,
    )


def test_skip_non_conversation() -> None:
    # no external_id AND no csat tag → not a conversation
    assert map_ticket_to_row({"id": 1, "status": "closed", "tags": []}) is None


def test_keep_csat_only_ticket_even_without_external_id() -> None:
    row = map_ticket_to_row({"id": 2, "status": "solved", "tags": ["csat_5"]})
    assert row is not None and row.channel == "Other" and row.csat_score == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the mapping**

Create `apps/backend/src/chatbot/features/metrics/mapping.py`:

```python
"""Pure Zendesk-ticket → conversation-row mapping for the metrics sync."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CHANNEL_BY_PREFIX = {
    "whatsapp": "WhatsApp",
    "email": "Email",
    "phone": "Phone",
    "sim": "Web",
    "zendesk": "Web",
    "chatwoot": "Web",
}
_AGENT_STATUSES = {"new", "open", "pending", "hold"}
_CSAT_TAG = re.compile(r"^csat_([1-5])$")


@dataclass(frozen=True)
class ConversationRow:
    conversation_id: str
    channel: str
    created_at: str | None
    updated_at: str | None
    status: str
    resolved_by: str
    csat_score: int | None


def _channel(external_id: str | None) -> str:
    if not external_id:
        return "Other"
    prefix = external_id.split("-", 1)[0]
    return _CHANNEL_BY_PREFIX.get(prefix, "Other")


def _resolved_by(status: str) -> str:
    return "agent" if status in _AGENT_STATUSES else "bot"


def _csat_from_tags(tags: list[str]) -> int | None:
    for tag in tags:
        m = _CSAT_TAG.match(tag)
        if m:
            return int(m.group(1))
    return None


def map_ticket_to_row(ticket: dict[str, object]) -> ConversationRow | None:
    """Map one Zendesk ticket to a conversation row, or None to skip it."""
    external_id = ticket.get("external_id")
    external_id_str = str(external_id) if external_id else None
    raw_tags = ticket.get("tags") or []
    tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
    csat = _csat_from_tags(tags)
    if external_id_str is None and csat is None:
        return None
    status = str(ticket.get("status") or "")
    created = ticket.get("created_at")
    updated = ticket.get("updated_at")
    return ConversationRow(
        conversation_id=str(ticket.get("id")),
        channel=_channel(external_id_str),
        created_at=str(created) if created else None,
        updated_at=str(updated) if updated else None,
        status=status,
        resolved_by=_resolved_by(status),
        csat_score=csat,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/__init__.py src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/test_mapping.py
git commit -m "feat(metrics): pure Zendesk ticket -> conversation row mapping"
```

---

### Task 3: BigQuery schema + view DDL

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py`

**Interfaces:**
- Produces: `CONVERSATIONS_SCHEMA: list[bigquery.SchemaField]` (columns matching `ConversationRow` + `synced_at` TIMESTAMP).
- Produces: `def view_ddls(project: str, dataset: str, table: str = "conversations") -> dict[str, str]` returning the three `CREATE OR REPLACE VIEW` statements keyed by view name.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py`:

```python
from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA, view_ddls


def test_schema_has_expected_fields() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    assert names == {
        "conversation_id",
        "channel",
        "created_at",
        "updated_at",
        "status",
        "resolved_by",
        "csat_score",
        "synced_at",
    }


def test_view_ddls_keys_and_targets() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert set(ddls) == {"v_volume_by_month_channel", "v_resolution_split", "v_csat"}
    assert "`proj.ds.v_volume_by_month_channel`" in ddls["v_volume_by_month_channel"]
    assert "`proj.ds.conversations`" in ddls["v_volume_by_month_channel"]


def test_view_ddls_contain_expected_aggregates() -> None:
    ddls = view_ddls("proj", "ds")
    assert "COUNT(*)" in ddls["v_volume_by_month_channel"]
    assert "resolved_by='bot'" in ddls["v_resolution_split"].replace('"', "'")
    assert "transfer_to_agent" in ddls["v_resolution_split"]
    assert "satisfied_rate" in ddls["v_csat"]
    assert "csat_score >= 4" in ddls["v_csat"].replace(">=4", ">= 4")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement schema + views**

Create `apps/backend/src/chatbot/features/metrics/bigquery_schema.py`:

```python
"""BigQuery table schema + view DDL for the conversations metrics table."""

from __future__ import annotations

from google.cloud import bigquery

CONVERSATIONS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("conversation_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("channel", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("created_at", "TIMESTAMP"),
    bigquery.SchemaField("updated_at", "TIMESTAMP"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("resolved_by", "STRING"),
    bigquery.SchemaField("csat_score", "INT64"),
    bigquery.SchemaField("synced_at", "TIMESTAMP"),
]


def view_ddls(project: str, dataset: str, table: str = "conversations") -> dict[str, str]:
    """The three CREATE OR REPLACE VIEW statements for the Looker tiles."""
    fq = f"`{project}.{dataset}.{table}`"
    return {
        "v_volume_by_month_channel": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_month_channel` AS "
            f"SELECT FORMAT_DATE('%Y-%m', DATE(created_at)) AS month, channel, "
            f"COUNT(*) AS volume FROM {fq} GROUP BY month, channel"
        ),
        "v_resolution_split": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_resolution_split` AS "
            f"SELECT channel, "
            f"COUNTIF(resolved_by='bot') AS closed_by_bot, "
            f"COUNTIF(resolved_by='agent') AS transfer_to_agent, "
            f"COUNT(*) AS total, "
            f"SAFE_DIVIDE(COUNTIF(resolved_by='bot'), COUNT(*)) AS closed_by_bot_pct, "
            f"SAFE_DIVIDE(COUNTIF(resolved_by='agent'), COUNT(*)) AS transfer_to_agent_pct "
            f"FROM {fq} GROUP BY channel"
        ),
        "v_csat": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_csat` AS "
            f"SELECT channel, "
            f"COUNTIF(csat_score IS NOT NULL) AS respondents, "
            f"AVG(csat_score) AS avg_score, "
            f"SAFE_DIVIDE(COUNTIF(csat_score >= 4), COUNTIF(csat_score IS NOT NULL)) "
            f"AS satisfied_rate "
            f"FROM {fq} GROUP BY channel"
        ),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): BigQuery conversations schema + view DDL"
```

---

### Task 4: Zendesk fetch + sync orchestration

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/sync.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_sync.py`

**Interfaces:**
- Consumes: `map_ticket_to_row` (Task 2), `CONVERSATIONS_SCHEMA` / `view_ddls` (Task 3), `Settings`.
- Produces: `def fetch_tickets(settings, *, get_page=None) -> list[dict]` (pages the incremental export); `def run_sync(settings, *, fetch=None, load=None) -> dict[str, int]` (fetch → map → load, returns `{"tickets": n, "rows": m}`); thin live helpers `load_conversations(settings, rows)` and `ensure_views(settings)`.

- [ ] **Step 1: Write the failing tests**

Create `apps/backend/src/chatbot/features/metrics/test_sync.py`:

```python
from types import SimpleNamespace

from chatbot.features.metrics.sync import fetch_tickets, run_sync


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        zendesk_subdomain="proton",
        zendesk_email="me@x.com",
        zendesk_api_token="tok",
        bigquery_project_id="proj",
        bigquery_dataset="ds",
        bigquery_conversations_table="conversations",
    )


def test_fetch_tickets_pages_until_end_of_stream() -> None:
    pages = {
        "https://proton.zendesk.com/api/v2/incremental/tickets.json?start_time=0": {
            "tickets": [{"id": 1}],
            "next_page": "PAGE2",
            "end_of_stream": False,
        },
        "PAGE2": {"tickets": [{"id": 2}], "next_page": None, "end_of_stream": True},
    }
    seen: list[str] = []

    def get_page(url: str) -> dict:
        seen.append(url)
        return pages[url]

    tickets = fetch_tickets(_settings(), get_page=get_page)
    assert [t["id"] for t in tickets] == [1, 2]
    assert len(seen) == 2  # stopped at end_of_stream


def test_run_sync_maps_and_loads() -> None:
    tickets = [
        {"id": 1, "external_id": "whatsapp-+60", "status": "solved", "tags": ["csat_5"]},
        {"id": 2, "status": "closed", "tags": []},  # skipped (no external_id, no csat)
    ]
    loaded: list = []

    result = run_sync(
        _settings(),
        fetch=lambda _s: tickets,
        load=lambda _s, rows: loaded.extend(rows),
    )
    assert result == {"tickets": 2, "rows": 1}
    assert len(loaded) == 1
    assert loaded[0].channel == "WhatsApp" and loaded[0].csat_score == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_sync.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the sync module**

Create `apps/backend/src/chatbot/features/metrics/sync.py`:

```python
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

        def get_page(url: str) -> dict[str, Any]:  # type: ignore[misc]
            with httpx.Client(timeout=30.0) as client:
                res = client.get(url, headers=headers)
                res.raise_for_status()
                return dict(res.json())

    sub = settings.zendesk_subdomain
    url: str | None = (
        f"https://{sub}.zendesk.com/api/v2/incremental/tickets.json?start_time=0"
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
            "synced_at": now,
        }
        for r in rows
    ]
    job_config = bigquery.LoadJobConfig(
        schema=CONVERSATIONS_SCHEMA, write_disposition="WRITE_TRUNCATE"
    )
    client.load_table_from_json(json_rows, table_id, job_config=job_config).result()


def ensure_views(settings: Settings) -> None:
    """Create/replace the three Looker views (live)."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_sync.py -v`
Expected: PASS (note: `run_sync`'s test injects `load`, so `ensure_views`/BigQuery is never touched).

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/sync.py src/chatbot/features/metrics/test_sync.py
git commit -m "feat(metrics): Zendesk incremental-export fetch + sync orchestration"
```

---

### Task 5: Sync CLI script

**Files:**
- Create: `apps/backend/scripts/sync_zendesk_metrics.py`

**Interfaces:**
- Consumes: `run_sync`, `ensure_views`, `get_settings`.

- [ ] **Step 1: Write the script**

Create `apps/backend/scripts/sync_zendesk_metrics.py`:

```python
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
```

- [ ] **Step 2: Syntax-check WITHOUT running it (it makes live calls)**

Run: `.venv/bin/python -c "import ast; ast.parse(open('scripts/sync_zendesk_metrics.py').read()); print('ok')"`
Expected: `ok`. Do NOT execute the script (live Zendesk + BigQuery).

- [ ] **Step 3: Gates (lint the script) + commit**

Run: `.venv/bin/ruff check scripts/sync_zendesk_metrics.py`
Expected: clean.

```bash
git add scripts/sync_zendesk_metrics.py
git commit -m "feat(metrics): sync_zendesk_metrics CLI (backfill + refresh)"
```

---

### Task 6: Looker Studio setup guide

**Files:**
- Create: `docs/dashboards/looker-bot-metrics-phase1.md`

- [ ] **Step 1: Write the guide**

Create `docs/dashboards/looker-bot-metrics-phase1.md` covering:
- **Prerequisites:** the sync has been run (table + views exist in `lv-playground-genai.demo_proton`); access to Looker Studio with the GCP project.
- **Run the sync:** `cd apps/backend && .venv/bin/python scripts/sync_zendesk_metrics.py` (backfill; re-run to refresh).
- **Data sources:** in Looker Studio, create three BigQuery data sources from the views `v_volume_by_month_channel`, `v_resolution_split`, `v_csat`.
- **Tiles to build (mapping to the XLSMART board):**
  - *Monthly Volume Trend* — time-series/bar from `v_volume_by_month_channel` (dimension `month`, breakdown `channel`, metric `volume`).
  - *Session Composition* — donut from `v_resolution_split` (`closed_by_bot` vs `transfer_to_agent`); plus scorecards for `closed_by_bot_pct` / `transfer_to_agent_pct`.
  - *CSAT* — scorecard `satisfied_rate` + `respondents` from `v_csat`, optionally split by `channel`.
- **Not in Phase 1 (greyed-out tiles):** NPS, Fallback Rate, Bounce Rate, Speed of Response, Accuracy, Quality — call out that these arrive with the Phase 2/3/4 instrumentation.
- **Refresh:** re-run the sync; Looker re-reads the views (note Looker's data-freshness/cache setting).

- [ ] **Step 2: Commit**

```bash
git add docs/dashboards/looker-bot-metrics-phase1.md
git commit -m "docs(metrics): Looker Studio setup guide for phase 1 tiles"
```

---

### Task 7: Docs — CHANGELOG, README, .env.example

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `apps/backend/.env.example`

- [ ] **Step 1: CHANGELOG** — add under `## [Unreleased]` (or dated `## [2026-06-29]`) in *Added*:

```markdown
- **Bot-metrics dashboard (Phase 1).** A Zendesk → BigQuery sync
  (`scripts/sync_zendesk_metrics.py`) lands every conversation (ticket) into
  `lv-playground-genai.demo_proton.conversations` with channel, resolution
  (bot/agent), and CSAT, plus three views (`v_volume_by_month_channel`,
  `v_resolution_split`, `v_csat`) for a Looker Studio dashboard (Volume,
  Transfer-vs-Closed, CSAT). NPS/fallback/bounce/speed and accuracy/quality are
  later phases. See `docs/dashboards/looker-bot-metrics-phase1.md`.
```

- [ ] **Step 2: README** — add a short **Metrics / Dashboard** subsection: the sync command, the BigQuery dataset/table/views, and a pointer to the Looker guide. Add `BIGQUERY_PROJECT_ID`, `BIGQUERY_DATASET`, `BIGQUERY_CONVERSATIONS_TABLE` to the Configuration table.

- [ ] **Step 3: .env.example** — add:

```bash
# Bot-metrics dashboard (Zendesk -> BigQuery sync)
BIGQUERY_PROJECT_ID=lv-playground-genai
BIGQUERY_DATASET=demo_proton
BIGQUERY_CONVERSATIONS_TABLE=conversations
```

- [ ] **Step 4: Final full suite + gates**

Run (from `apps/backend/`): `.venv/bin/pytest src/ && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: PASS; only the documented pre-existing errors remain.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md apps/backend/.env.example
git commit -m "docs(metrics): changelog, readme, env example for phase 1 dashboard"
```

---

## Self-Review Notes

- **Spec coverage:** config + dep → Task 1; pure mapping (channel/resolved_by/csat/skip) → Task 2; BQ table schema + 3 views → Task 3; Zendesk incremental-export fetch + map + load orchestration → Task 4; CLI (backfill + refresh, idempotent WRITE_TRUNCATE) → Task 5; Looker guide → Task 6; docs → Task 7. All spec sections covered.
- **Type consistency:** `ConversationRow` fields used identically in Tasks 2/4; `map_ticket_to_row(ticket) -> ConversationRow | None`, `view_ddls(project, dataset, table)`, `CONVERSATIONS_SCHEMA`, `fetch_tickets(settings, *, get_page=...)`, `run_sync(settings, *, fetch=, load=)`, `load_conversations`/`ensure_views` consistent across tasks.
- **Live isolation:** all unit tests inject fakes (`get_page`, `fetch`, `load`) so no test touches Zendesk or BigQuery; the live path is the CLI (Task 5), verified by the user running the sync, then building the Looker tiles (Task 6).
- **Out of scope honored:** no per-event MetricsPort, no NPS/fallback/bounce/speed, no Accuracy/Quality, no scheduling, no Looker-dashboard code (guide only).
