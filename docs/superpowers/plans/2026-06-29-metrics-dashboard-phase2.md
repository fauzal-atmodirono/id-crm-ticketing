# Bot-Metrics Dashboard — Phase 2 (per-turn MetricsPort → BigQuery) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Instrument `handle_turn` to emit one `turn_events` row per text turn through a best-effort `MetricsPort`, so BigQuery views can derive speed-of-response, fallback rate, and bounce rate; and (2) automate the Phase-1 Zendesk→BigQuery sync with an in-app scheduler so the `conversations` table refreshes on a schedule.

**Architecture:** Ports & adapters. A pure `build_turn_event` builds an immutable `TurnEvent`; a `MetricsPort` protocol (`emit_turn`) has two adapters — `NoOpMetrics` (default) and `BigQueryMetricsAdapter` (streaming `insert_rows_json`, ensures table + 3 views on init). `OrchestratorService.handle_turn` times each turn and emits best-effort, never letting an instrumentation error affect the user-facing result. Three BigQuery views over `turn_events` feed Looker. Separately, an APScheduler `BackgroundScheduler` (default OFF) runs the unchanged Phase-1 `run_sync` + `ensure_views` on an interval in its own thread.

**Tech Stack:** Python 3.12, FastAPI, `google-cloud-bigquery` (already a dependency from Phase 1), `apscheduler` (new), pydantic-settings, structlog, pytest / pytest-asyncio (`asyncio_mode="auto"`), ruff, mypy --strict.

## Global Constraints

- Run all backend commands from `apps/backend/`. Quality gates per task: `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict`, `.venv/bin/pytest src/`.
- Target dataset (reuse Phase 1 config): project `lv-playground-genai`, dataset `demo_proton`. New table default name `turn_events`.
- `from __future__ import annotations` at the top of every new module (matches the repo).
- Emit is **best-effort fire-and-forget**: a metrics failure (BQ down, schema drift, no creds) must be logged via structlog and swallowed — it must never change `handle_turn`'s return value or raise.
- Default `metrics_provider="noop"` → `NoOpMetrics`; local/dev/tests never touch BigQuery.
- The sync scheduler is **default OFF** (`metrics_sync_enabled=false`); it must not start during tests/dev. The scheduled job reuses Phase-1 `run_sync`/`ensure_views` **unchanged** and is best-effort (a failed run is logged + swallowed, never crashes the app).
- DRY: there must be exactly **one** prefix→channel mapping (`channel_from_external_id`), reused by both Phase-1 `map_ticket_to_row` and Phase-2 `build_turn_event`.
- `TurnEvent` is immutable (`@dataclass(frozen=True)`) and `build_turn_event` is pure (no `datetime.now`, no `uuid`, no I/O — callers pass `occurred_at`; the adapter mints `event_id`).
- Never log or print secrets.

---

### Task 1: Promote `_channel` → public `channel_from_external_id` (DRY prep)

**Files:**
- Modify: `apps/backend/src/chatbot/features/metrics/mapping.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_mapping.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `channel_from_external_id(external_id: str | None) -> str` — prefix-before-first-`-` lookup (`whatsapp`→`WhatsApp`, `email`→`Email`, `phone`→`Phone`, `sim`/`zendesk`/`chatwoot`→`Web`, missing/unknown→`Other`). Reused by Task 2.

- [ ] **Step 1: Write the failing test**

Add to `test_mapping.py`:

```python
from chatbot.features.metrics.mapping import channel_from_external_id


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
        (None, "Other"),
    ],
)
def test_channel_from_external_id_public(external_id: str | None, expected: str) -> None:
    assert channel_from_external_id(external_id) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py::test_channel_from_external_id_public -v`
Expected: FAIL with `ImportError: cannot import name 'channel_from_external_id'`.

- [ ] **Step 3: Rename the function and update its caller**

In `mapping.py`, rename `_channel` to `channel_from_external_id` (keep the body identical) and update the call inside `map_ticket_to_row`:

```python
def channel_from_external_id(external_id: str | None) -> str:
    if not external_id:
        return "Other"
    prefix = external_id.split("-", 1)[0]
    return _CHANNEL_BY_PREFIX.get(prefix, "Other")
```

And in `map_ticket_to_row`, change `channel=_channel(external_id_str),` to:

```python
        channel=channel_from_external_id(external_id_str),
```

- [ ] **Step 4: Run the full mapping test file to verify everything passes**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -v`
Expected: PASS (the new test plus all existing `map_ticket_to_row` tests — proves the rename didn't break the Phase-1 caller).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/test_mapping.py
git commit -m "refactor(metrics): promote _channel to public channel_from_external_id"
```

---

### Task 2: `TurnEvent` + pure `build_turn_event`

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/events.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_events.py`

**Interfaces:**
- Consumes: `channel_from_external_id` (Task 1).
- Produces:
  - `@dataclass(frozen=True) TurnEvent` with fields: `occurred_at: datetime`, `channel: str`, `session_id: str`, `latency_ms: int`, `is_first_turn: bool`, `is_fallback: bool`, `handed_off: bool`. (No `event_id` — the adapter mints it at emit.)
  - `build_turn_event(session_id: str, occurred_at: datetime, latency_ms: int, turn_count: int, is_fallback: bool, handed_off: bool) -> TurnEvent` — derives `channel` from `session_id` and `is_first_turn` as `turn_count == 1`. Used by Task 6 and serialized by Task 5.

- [ ] **Step 1: Write the failing test**

Create `test_events.py`:

```python
from datetime import UTC, datetime

import pytest

from chatbot.features.metrics.events import TurnEvent, build_turn_event

_T = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "session_id,expected_channel",
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
def test_channel_derived_from_session_id(session_id: str, expected_channel: str) -> None:
    ev = build_turn_event(session_id, _T, 1200, 1, False, False)
    assert ev.channel == expected_channel


@pytest.mark.parametrize(
    "turn_count,expected_first",
    [(1, True), (2, False), (7, False)],
)
def test_is_first_turn_from_turn_count(turn_count: int, expected_first: bool) -> None:
    ev = build_turn_event("sim-1", _T, 100, turn_count, False, False)
    assert ev.is_first_turn is expected_first


def test_fields_pass_through() -> None:
    ev = build_turn_event("sim-1", _T, 1234, 1, True, True)
    assert ev == TurnEvent(
        occurred_at=_T,
        channel="Web",
        session_id="sim-1",
        latency_ms=1234,
        is_first_turn=True,
        is_fallback=True,
        handed_off=True,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.metrics.events'`.

- [ ] **Step 3: Write minimal implementation**

Create `events.py`:

```python
"""Pure per-turn metrics event + builder for the Phase-2 instrumentation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chatbot.features.metrics.mapping import channel_from_external_id


@dataclass(frozen=True)
class TurnEvent:
    occurred_at: datetime
    channel: str
    session_id: str
    latency_ms: int
    is_first_turn: bool
    is_fallback: bool
    handed_off: bool


def build_turn_event(
    session_id: str,
    occurred_at: datetime,
    latency_ms: int,
    turn_count: int,
    is_fallback: bool,
    handed_off: bool,
) -> TurnEvent:
    """Build an immutable turn event. Pure: no clock, no uuid, no I/O."""
    return TurnEvent(
        occurred_at=occurred_at,
        channel=channel_from_external_id(session_id),
        session_id=session_id,
        latency_ms=latency_ms,
        is_first_turn=turn_count == 1,
        is_fallback=is_fallback,
        handed_off=handed_off,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_events.py -v`
Expected: PASS.

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/events.py src/chatbot/features/metrics/test_events.py
git commit -m "feat(metrics): pure TurnEvent + build_turn_event"
```

---

### Task 3: `turn_events` schema + the three view DDLs

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/turn_schema.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_turn_schema.py`

**Interfaces:**
- Produces:
  - `TURN_EVENTS_SCHEMA: list[bigquery.SchemaField]` — 8 fields in order: `event_id` STRING REQUIRED, `occurred_at` TIMESTAMP, `channel` STRING REQUIRED, `session_id` STRING REQUIRED, `latency_ms` INT64, `is_first_turn` BOOL, `is_fallback` BOOL, `handed_off` BOOL.
  - `turn_view_ddls(project: str, dataset: str, table: str = "turn_events") -> dict[str, str]` — keys `v_speed_of_response`, `v_fallback_rate`, `v_bounce_rate`. Used by Task 5.

- [ ] **Step 1: Write the failing test**

Create `test_turn_schema.py`:

```python
from chatbot.features.metrics.turn_schema import TURN_EVENTS_SCHEMA, turn_view_ddls


def test_schema_field_names_and_order() -> None:
    assert [f.name for f in TURN_EVENTS_SCHEMA] == [
        "event_id",
        "occurred_at",
        "channel",
        "session_id",
        "latency_ms",
        "is_first_turn",
        "is_fallback",
        "handed_off",
    ]


def test_schema_types() -> None:
    by_name = {f.name: f for f in TURN_EVENTS_SCHEMA}
    assert by_name["event_id"].field_type == "STRING"
    assert by_name["occurred_at"].field_type == "TIMESTAMP"
    assert by_name["latency_ms"].field_type == "INT64"
    assert by_name["is_fallback"].field_type in {"BOOL", "BOOLEAN"}


def test_view_keys_and_targets() -> None:
    ddls = turn_view_ddls("proj", "ds", "turn_events")
    assert set(ddls) == {"v_speed_of_response", "v_fallback_rate", "v_bounce_rate"}
    for name, sql in ddls.items():
        assert f"`proj.ds.{name}`" in sql
        assert "`proj.ds.turn_events`" in sql


def test_view_aggregates() -> None:
    ddls = turn_view_ddls("proj", "ds")
    assert "APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)]" in ddls["v_speed_of_response"]
    assert "is_first_turn" in ddls["v_speed_of_response"]
    assert "COUNTIF(is_fallback)" in ddls["v_fallback_rate"]
    assert "SAFE_DIVIDE" in ddls["v_fallback_rate"]
    # bounce derives from a per-session turn count, then single-turn sessions
    assert "COUNT(*)" in ddls["v_bounce_rate"]
    assert "session_id" in ddls["v_bounce_rate"]
    assert "SAFE_DIVIDE" in ddls["v_bounce_rate"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_turn_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.metrics.turn_schema'`.

- [ ] **Step 3: Write minimal implementation**

Create `turn_schema.py`:

```python
"""BigQuery schema + view DDL for the per-turn metrics (Phase 2)."""

# ruff: noqa: S608  # DDL generation: project/dataset/table are internal config, not user input

from __future__ import annotations

from google.cloud import bigquery

TURN_EVENTS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("event_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("occurred_at", "TIMESTAMP"),
    bigquery.SchemaField("channel", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("latency_ms", "INT64"),
    bigquery.SchemaField("is_first_turn", "BOOL"),
    bigquery.SchemaField("is_fallback", "BOOL"),
    bigquery.SchemaField("handed_off", "BOOL"),
]


def turn_view_ddls(project: str, dataset: str, table: str = "turn_events") -> dict[str, str]:
    """The three CREATE OR REPLACE VIEW statements for the Phase-2 Looker tiles."""
    fq = f"`{project}.{dataset}.{table}`"
    return {
        "v_speed_of_response": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_speed_of_response` AS "
            f"SELECT channel, is_first_turn, "
            f"APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)] AS p99_latency_ms, "
            f"AVG(latency_ms) AS avg_latency_ms, "
            f"COUNT(*) AS turns "
            f"FROM {fq} GROUP BY channel, is_first_turn"
        ),
        "v_fallback_rate": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_fallback_rate` AS "
            f"SELECT channel, "
            f"SAFE_DIVIDE(COUNTIF(is_fallback), COUNT(*)) AS fallback_rate, "
            f"COUNT(*) AS turns "
            f"FROM {fq} GROUP BY channel"
        ),
        "v_bounce_rate": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_bounce_rate` AS "
            f"WITH session_turns AS ("
            f"SELECT channel, session_id, COUNT(*) AS turns "
            f"FROM {fq} GROUP BY channel, session_id) "
            f"SELECT channel, "
            f"COUNTIF(turns = 1) AS bounced, "
            f"COUNT(*) AS total_sessions, "
            f"SAFE_DIVIDE(COUNTIF(turns = 1), COUNT(*)) AS bounce_rate "
            f"FROM session_turns GROUP BY channel"
        ),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_turn_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/turn_schema.py src/chatbot/features/metrics/test_turn_schema.py
git commit -m "feat(metrics): turn_events schema + speed/fallback/bounce view DDLs"
```

---

### Task 4: `MetricsPort` protocol + `NoOpMetrics`

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/ports.py`
- Create: `apps/backend/src/chatbot/features/chat/adapters/bigquery_metrics.py` (NoOp only in this task; the BigQuery adapter lands in Task 5)
- Test: `apps/backend/src/chatbot/features/chat/adapters/test_bigquery_metrics.py`

**Interfaces:**
- Produces:
  - `MetricsPort` Protocol: `async def emit_turn(self, event: TurnEvent) -> None`.
  - `NoOpMetrics` implementing it (does nothing). Default port for the service (Task 6) and the `noop` provider (Task 7).

- [ ] **Step 1: Write the failing test**

Create `adapters/test_bigquery_metrics.py`:

```python
from datetime import UTC, datetime

from chatbot.features.chat.adapters.bigquery_metrics import NoOpMetrics
from chatbot.features.metrics.events import build_turn_event


async def test_noop_emit_turn_is_a_noop() -> None:
    ev = build_turn_event("sim-1", datetime.now(UTC), 100, 1, False, False)
    # Must not raise and must return None.
    assert await NoOpMetrics().emit_turn(ev) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_bigquery_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.chat.adapters.bigquery_metrics'`.

- [ ] **Step 3: Add the port, then the NoOp adapter**

In `ports.py`, add the import and the protocol. At the top, extend the models import block with `TurnEvent`'s module (import directly to avoid a cycle — `events.py` only imports from `metrics.mapping`, so this is safe):

```python
from chatbot.features.metrics.events import TurnEvent
```

Add at the end of `ports.py`:

```python
class MetricsPort(Protocol):
    """Port for emitting one analytics event per conversational turn."""

    async def emit_turn(self, event: TurnEvent) -> None:
        """Best-effort: record a single turn event. Must never raise."""
        ...
```

Create `adapters/bigquery_metrics.py`:

```python
"""MetricsPort adapters: NoOp (default) and BigQuery streaming (Phase 2)."""

from __future__ import annotations

from chatbot.features.metrics.events import TurnEvent


class NoOpMetrics:
    """Default MetricsPort — drops every event. Used in dev/tests and when
    metrics_provider != 'bigquery'."""

    async def emit_turn(self, event: TurnEvent) -> None:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_bigquery_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/ports.py src/chatbot/features/chat/adapters/bigquery_metrics.py src/chatbot/features/chat/adapters/test_bigquery_metrics.py
git commit -m "feat(metrics): MetricsPort protocol + NoOpMetrics adapter"
```

---

### Task 5: `BigQueryMetricsAdapter` + `build_metrics_port` factory

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/adapters/bigquery_metrics.py`
- Test: `apps/backend/src/chatbot/features/chat/adapters/test_bigquery_metrics.py`

**Interfaces:**
- Consumes: `TURN_EVENTS_SCHEMA`, `turn_view_ddls` (Task 3); `TurnEvent` (Task 2); `Settings`.
- Produces:
  - `BigQueryMetricsAdapter(settings, *, client=None)` — on init ensures dataset + `turn_events` table + 3 views; `emit_turn` does a best-effort `insert_rows_json` (mints `event_id`, serializes `occurred_at` to ISO-8601), swallowing+logging any error. A `client` kwarg makes it unit-testable.
  - `build_metrics_port(settings) -> MetricsPort` — returns `BigQueryMetricsAdapter(settings)` when `settings.metrics_provider == "bigquery"`, else `NoOpMetrics()`. Used by Task 7 (`main.py`). (This task references `settings.metrics_provider` / `settings.bigquery_turn_events_table`, which Task 7 adds; the factory test below uses a `SimpleNamespace`, so it does not depend on Task 7 landing first.)

- [ ] **Step 1: Write the failing test**

Append to `adapters/test_bigquery_metrics.py`:

```python
from types import SimpleNamespace
from typing import Any, cast

from chatbot.features.chat.adapters.bigquery_metrics import (
    BigQueryMetricsAdapter,
    build_metrics_port,
)
from chatbot.platform.config import Settings


class _FakeBQ:
    """Captures table/view/insert calls instead of hitting BigQuery."""

    def __init__(self) -> None:
        self.created_datasets: list[str] = []
        self.created_tables: list[str] = []
        self.queries: list[str] = []
        self.inserted: list[tuple[str, list[dict[str, Any]]]] = []
        self.insert_error: Exception | None = None

    def create_dataset(self, ref: str, exists_ok: bool = False) -> None:
        self.created_datasets.append(ref)

    def create_table(self, table: Any, exists_ok: bool = False) -> None:
        self.created_tables.append(str(table))

    def query(self, ddl: str) -> Any:
        self.queries.append(ddl)
        return SimpleNamespace(result=lambda: None)

    def insert_rows_json(self, table_id: str, rows: list[dict[str, Any]]) -> list[Any]:
        if self.insert_error is not None:
            raise self.insert_error
        self.inserted.append((table_id, rows))
        return []


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            bigquery_project_id="proj",
            bigquery_dataset="ds",
            bigquery_turn_events_table="turn_events",
            metrics_provider="bigquery",
        ),
    )


def test_init_ensures_dataset_table_and_views() -> None:
    fake = _FakeBQ()
    BigQueryMetricsAdapter(_settings(), client=fake)
    assert fake.created_datasets == ["proj.ds"]
    assert fake.created_tables == ["proj.ds.turn_events"]
    assert len(fake.queries) == 3  # the three views


async def test_emit_turn_inserts_one_row_with_event_id() -> None:
    fake = _FakeBQ()
    adapter = BigQueryMetricsAdapter(_settings(), client=fake)
    ev = build_turn_event("whatsapp-+60", datetime(2026, 6, 29, tzinfo=UTC), 1500, 1, True, False)
    await adapter.emit_turn(ev)
    assert len(fake.inserted) == 1
    table_id, rows = fake.inserted[0]
    assert table_id == "proj.ds.turn_events"
    row = rows[0]
    assert row["event_id"]  # uuid minted at emit
    assert row["channel"] == "WhatsApp"
    assert row["session_id"] == "whatsapp-+60"
    assert row["latency_ms"] == 1500
    assert row["is_first_turn"] is True
    assert row["is_fallback"] is True
    assert row["handed_off"] is False
    assert row["occurred_at"].startswith("2026-06-29T")


async def test_emit_turn_swallows_insert_errors() -> None:
    fake = _FakeBQ()
    fake.insert_error = RuntimeError("BQ down")
    adapter = BigQueryMetricsAdapter(_settings(), client=fake)
    ev = build_turn_event("sim-1", datetime.now(UTC), 100, 1, False, False)
    # Must not raise.
    assert await adapter.emit_turn(ev) is None


def test_build_metrics_port_selects_noop_by_default() -> None:
    s = cast(Settings, SimpleNamespace(metrics_provider="noop"))
    assert build_metrics_port(s).__class__.__name__ == "NoOpMetrics"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_bigquery_metrics.py -v`
Expected: FAIL with `ImportError: cannot import name 'BigQueryMetricsAdapter'`.

- [ ] **Step 3: Implement the adapter + factory**

Replace the contents of `adapters/bigquery_metrics.py` with:

```python
"""MetricsPort adapters: NoOp (default) and BigQuery streaming (Phase 2)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.events import TurnEvent
from chatbot.features.metrics.turn_schema import TURN_EVENTS_SCHEMA, turn_view_ddls

if TYPE_CHECKING:
    from chatbot.features.chat.ports import MetricsPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class NoOpMetrics:
    """Default MetricsPort — drops every event. Used in dev/tests and when
    metrics_provider != 'bigquery'."""

    async def emit_turn(self, event: TurnEvent) -> None:
        return None


class BigQueryMetricsAdapter:
    """Streams one row per turn into BigQuery. Ensures the table + views exist on
    init; emit is best-effort and never raises."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._project = settings.bigquery_project_id
        self._dataset = settings.bigquery_dataset
        self._table = settings.bigquery_turn_events_table
        self._table_id = f"{self._project}.{self._dataset}.{self._table}"
        self._client = client or bigquery.Client(project=self._project)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            self._client.create_dataset(f"{self._project}.{self._dataset}", exists_ok=True)
            self._client.create_table(
                bigquery.Table(self._table_id, schema=TURN_EVENTS_SCHEMA), exists_ok=True
            )
            for ddl in turn_view_ddls(self._project, self._dataset, self._table).values():
                self._client.query(ddl).result()
        except Exception as e:  # best-effort bootstrap
            _log.error("metrics_ensure_schema_failed", error=str(e))

    async def emit_turn(self, event: TurnEvent) -> None:
        try:
            row = {
                "event_id": uuid.uuid4().hex,
                "occurred_at": event.occurred_at.isoformat(),
                "channel": event.channel,
                "session_id": event.session_id,
                "latency_ms": event.latency_ms,
                "is_first_turn": event.is_first_turn,
                "is_fallback": event.is_fallback,
                "handed_off": event.handed_off,
            }
            errors = self._client.insert_rows_json(self._table_id, [row])
            if errors:
                _log.warning("metrics_insert_returned_errors", errors=str(errors))
        except Exception as e:  # best-effort: never break the turn
            _log.error("metrics_emit_turn_failed", error=str(e))


def build_metrics_port(settings: Settings) -> MetricsPort:
    """Pick the MetricsPort implementation from settings."""
    if settings.metrics_provider == "bigquery":
        return BigQueryMetricsAdapter(settings)
    return NoOpMetrics()
```

- [ ] **Step 4: Run the adapter test file to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/adapters/test_bigquery_metrics.py -v`
Expected: PASS (all five tests, including the NoOp test from Task 4).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/adapters/bigquery_metrics.py src/chatbot/features/chat/adapters/test_bigquery_metrics.py
git commit -m "feat(metrics): BigQueryMetricsAdapter streaming insert + build_metrics_port"
```

---

### Task 6: Time the turn and emit from `OrchestratorService.handle_turn`

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py`
- Test: `apps/backend/src/chatbot/features/chat/test_service_metrics.py`

**Interfaces:**
- Consumes: `MetricsPort`, `NoOpMetrics` (Task 4/5); `build_turn_event` (Task 2).
- Produces: `OrchestratorService.__init__` gains a keyword param `metrics_port: MetricsPort | None = None` (defaults to `NoOpMetrics()`); `handle_turn` emits one `TurnEvent` at each terminal bot-turn return. Wired by Task 7.

Notes for the implementer (read before coding):
- `handle_turn` (service.py:266) has **two** terminal returns that represent a real bot turn: the technical-error early return (service.py:346) and the normal final return (service.py:379). The two early returns *above* them — the active-handoff relay (`forwarded_to_agent=True`) and the AI-paused short-circuit (`reply=None`) — are **not** bot turns and must **not** emit.
- The two fallback reply strings are `_EMPTY_REPLY_FALLBACK` (service.py:80) and the inline technical-error string at service.py:346. Extract the latter into a module constant so both `handle_turn` and the fallback set can reference it.
- On handoff, `reply_text` is set to `None` (service.py:361) before the final return, so capture fallback-ness from the bot's text *before* that, and set `handed_off` from whether a handoff fired.
- `turn_count` = number of user messages in `self._history[session_id]` at emit time (the user message was appended at service.py:312). First turn → 1.

- [ ] **Step 1: Write the failing test**

Create `test_service_metrics.py` (mirrors the `_FakeRunner` harness in `test_service.py`):

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.service import _EMPTY_REPLY_FALLBACK, OrchestratorService
from chatbot.features.metrics.events import TurnEvent
from chatbot.platform.config import get_settings


@pytest.fixture(autouse=True)
def force_memory_session_store() -> None:
    get_settings().session_store = "memory"


class _CapturingMetrics:
    def __init__(self) -> None:
        self.events: list[TurnEvent] = []

    async def emit_turn(self, event: TurnEvent) -> None:
        self.events.append(event)


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeEvent:
    def __init__(self, text: str) -> None:
        self.content = _FakeContent(text)

    def is_final_response(self) -> bool:
        return True


class _FakeRunner:
    def __init__(
        self, reply: str, session_service: Any, session_id: str, trigger_handoff: bool = False
    ) -> None:
        self._reply = reply
        self._session_service = session_service
        self._session_id = session_id
        self._trigger_handoff = trigger_handoff

    async def run_async(self, **_: Any) -> AsyncIterator[_FakeEvent]:
        if self._trigger_handoff:
            session = self._session_service.sessions["chatbot"][self._session_id][self._session_id]
            session.state["handoff_triggered"] = True
            session.state["handoff_reason"] = "help_request"
        yield _FakeEvent(self._reply)


def _service(metrics: _CapturingMetrics, reply: str, session_id: str, *, handoff: bool = False):
    svc = OrchestratorService(
        settings=get_settings(),
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        metrics_port=metrics,
        runner_factory=lambda _a: _FakeRunner(
            reply=reply,
            session_service=svc._adk_sessions,
            session_id=session_id,
            trigger_handoff=handoff,
        ),
    )
    return svc


async def test_normal_turn_emits_non_fallback_first_turn() -> None:
    metrics = _CapturingMetrics()
    svc = _service(metrics, "Here is your answer.", "sim-a")
    await svc.handle_turn(session_id="sim-a", text="hello")
    assert len(metrics.events) == 1
    ev = metrics.events[0]
    assert ev.channel == "Web"
    assert ev.is_fallback is False
    assert ev.handed_off is False
    assert ev.is_first_turn is True
    assert ev.latency_ms >= 0


async def test_empty_reply_emits_fallback() -> None:
    metrics = _CapturingMetrics()
    svc = _service(metrics, "", "sim-b")  # empty reply → retry → _EMPTY_REPLY_FALLBACK
    result = await svc.handle_turn(session_id="sim-b", text="hello")
    assert result.reply == _EMPTY_REPLY_FALLBACK
    assert metrics.events[-1].is_fallback is True


async def test_handoff_turn_emits_handed_off() -> None:
    metrics = _CapturingMetrics()
    svc = _service(metrics, "ok", "sim-c", handoff=True)
    await svc.handle_turn(session_id="sim-c", text="I want a human")
    ev = metrics.events[-1]
    assert ev.handed_off is True
    assert ev.is_fallback is False


async def test_second_turn_is_not_first() -> None:
    metrics = _CapturingMetrics()
    svc = _service(metrics, "answer", "sim-d")
    await svc.handle_turn(session_id="sim-d", text="one")
    await svc.handle_turn(session_id="sim-d", text="two")
    assert metrics.events[0].is_first_turn is True
    assert metrics.events[1].is_first_turn is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_service_metrics.py -v`
Expected: FAIL — `OrchestratorService.__init__() got an unexpected keyword argument 'metrics_port'`.

- [ ] **Step 3a: Add imports + the technical-error constant + fallback set**

In `service.py`, add to the imports near the other adapter imports:

```python
from chatbot.features.chat.adapters.bigquery_metrics import NoOpMetrics
from chatbot.features.metrics.events import build_turn_event
```

and add `MetricsPort` to the existing `from chatbot.features.chat.ports import (...)` block.

Add to the top-of-module imports:

```python
from time import perf_counter
```

Below the `_EMPTY_REPLY_FALLBACK` definition (after service.py:83), add:

```python
# Shown when the ADK execution loop raises (Gemini/transport error). Kept as a
# module constant so the metrics layer can classify the turn as a fallback.
_TECH_ERROR_FALLBACK = "Maaf, terjadi kendala teknis. Mohon coba beberapa saat lagi."

# A turn counts as a fallback when the bot produced one of these canned replies
# (no real answer) rather than a genuine response.
_FALLBACK_REPLIES = frozenset({_EMPTY_REPLY_FALLBACK, _TECH_ERROR_FALLBACK})
```

- [ ] **Step 3b: Accept and store the metrics port**

In `__init__`, add the parameter (after `runner_factory`):

```python
        runner_factory: Callable[[Any], Any] | None = None,
        metrics_port: MetricsPort | None = None,
    ) -> None:
```

and store it alongside the other ports (after the `conversation_log_port` assignment):

```python
        self._metrics: MetricsPort = metrics_port or NoOpMetrics()
```

- [ ] **Step 3c: Add the emit helper**

Add this method to `OrchestratorService` (e.g. just above `handle_turn`):

```python
    async def _emit_turn_metrics(
        self,
        session_id: str,
        t0: float,
        *,
        bot_reply: str | None,
        handed_off: bool,
    ) -> None:
        """Best-effort: emit one turn event. Never raises into the turn."""
        try:
            turn_count = sum(
                1 for m in self._history.get(session_id, []) if m.role == "user"
            )
            event = build_turn_event(
                session_id=session_id,
                occurred_at=datetime.now(UTC),
                latency_ms=int((perf_counter() - t0) * 1000),
                turn_count=turn_count,
                is_fallback=bot_reply in _FALLBACK_REPLIES,
                handed_off=handed_off,
            )
            await self._metrics.emit_turn(event)
        except Exception as e:  # instrumentation must never break the turn
            _log.error("emit_turn_metrics_failed", session_id=session_id, error=str(e))
```

- [ ] **Step 3d: Time the turn and emit at the two bot-turn returns**

At the very start of `handle_turn` (right after the docstring / the first `_log.info`), capture the start time:

```python
        t0 = perf_counter()
```

In the `except Exception` block (currently service.py:344-346), emit before returning:

```python
        except Exception as e:
            _log.exception("adk_execution_loop_failed", session_id=session_id, error=str(e))
            await self._emit_turn_metrics(
                session_id, t0, bot_reply=_TECH_ERROR_FALLBACK, handed_off=False
            )
            return TurnResult(reply=_TECH_ERROR_FALLBACK)
```

(Note: this replaces the inline string at service.py:346 with the new `_TECH_ERROR_FALLBACK` constant.)

Just before the final `return TurnResult(...)` (service.py:379), capture the bot text before it can be cleared and emit. The handoff branch at service.py:354-361 already sets `reply_text = None`; to know what the bot actually produced, read it before the return using the handoff flag:

```python
        await self._emit_turn_metrics(
            session_id,
            t0,
            bot_reply=reply_text,
            handed_off=handoff_payload is not None,
        )
        return TurnResult(
            reply=reply_text,
            language=session_state.get("language", "unknown"),
            sentiment=session_state.get("sentiment"),
            handoff=handoff_payload,
            products=products,
        )
```

(On handoff, `reply_text` is `None` → `is_fallback=False`, and `handed_off=True`. On a normal fallback, `reply_text == _EMPTY_REPLY_FALLBACK` → `is_fallback=True`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_service_metrics.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Run the existing service tests to confirm no regression**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_service.py -v`
Expected: PASS (the new constant/param are backward-compatible; `metrics_port` defaults to NoOp).

- [ ] **Step 6: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/service.py src/chatbot/features/chat/test_service_metrics.py
git commit -m "feat(metrics): emit per-turn TurnEvent from handle_turn (best-effort)"
```

---

### Task 7: Config settings + `main.py` wiring

**Files:**
- Modify: `apps/backend/src/chatbot/platform/config.py`
- Modify: `apps/backend/src/chatbot/main.py`
- Test: `apps/backend/src/chatbot/platform/test_config_metrics.py`

**Interfaces:**
- Consumes: `build_metrics_port` (Task 5).
- Produces: `Settings.metrics_provider: Literal["noop","bigquery"]` (default `"noop"`), `Settings.bigquery_turn_events_table: str` (default `"turn_events"`); `main.py` injects `build_metrics_port(settings)` into `OrchestratorService(metrics_port=...)`.

- [ ] **Step 1: Write the failing test**

Create `platform/test_config_metrics.py`:

```python
from chatbot.platform.config import Settings


def test_metrics_defaults_are_noop() -> None:
    s = Settings()
    assert s.metrics_provider == "noop"
    assert s.bigquery_turn_events_table == "turn_events"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_metrics.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'metrics_provider'`.

- [ ] **Step 3: Add the settings**

In `config.py`, under the `# Bot-metrics dashboard (Zendesk -> BigQuery sync)` block (after `bigquery_conversations_table`), add:

```python
    # Bot-metrics Phase 2 (per-turn MetricsPort -> BigQuery streaming)
    metrics_provider: Literal["noop", "bigquery"] = "noop"
    bigquery_turn_events_table: str = "turn_events"
```

- [ ] **Step 4: Run the config test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the adapter into `main.py`**

In `main.py`, add the import near the other adapter imports:

```python
from chatbot.features.chat.adapters.bigquery_metrics import build_metrics_port
```

and build + inject it. Before the `orchestrator = OrchestratorService(` call (main.py:115), add:

```python
    metrics_port = build_metrics_port(settings)
```

and pass it into the constructor call:

```python
        conversation_log_port=conversation_log_port,
        metrics_port=metrics_port,
    )
```

- [ ] **Step 6: Verify the app still imports/boots and the suite is green**

Run: `.venv/bin/python -c "import chatbot.main"`
Expected: no error (default `noop` → `NoOpMetrics`, no BigQuery client constructed at import).
Run: `.venv/bin/pytest src/ -q`
Expected: PASS (full suite).

- [ ] **Step 7: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/platform/config.py src/chatbot/platform/test_config_metrics.py src/chatbot/main.py
git commit -m "feat(metrics): wire metrics_provider config + build_metrics_port into bootstrap"
```

---

### Task 8: `apscheduler` dependency + `run_sync_job` + scheduler config

**Files:**
- Modify: `apps/backend/pyproject.toml` (add `apscheduler` dependency)
- Modify: `apps/backend/src/chatbot/platform/config.py`
- Create: `apps/backend/src/chatbot/features/metrics/scheduler.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_scheduler.py`

**Interfaces:**
- Consumes: `run_sync`, `ensure_views` (Phase-1 `features/metrics/sync.py`, unchanged).
- Produces:
  - `Settings.metrics_sync_enabled: bool` (default `False`), `Settings.metrics_sync_interval_hours: int` (default `6`).
  - `run_sync_job(settings, *, sync=None, ensure=None) -> dict[str, int]` — best-effort: runs `run_sync` then `ensure_views`, logs, swallows errors (returns `{}` on failure). `sync`/`ensure` are injectable for tests. Used by Task 9.

- [ ] **Step 1: Add the dependency**

Run: `uv add apscheduler`
Expected: `apscheduler` added to `pyproject.toml` `[project].dependencies` and `uv.lock` updated.

- [ ] **Step 2: Write the failing test**

Create `test_scheduler.py`:

```python
from types import SimpleNamespace
from typing import Any, cast

from chatbot.features.metrics.scheduler import run_sync_job
from chatbot.platform.config import Settings


def _settings() -> Settings:
    return cast(Settings, SimpleNamespace(metrics_sync_enabled=True, metrics_sync_interval_hours=6))


def test_run_sync_job_runs_sync_then_ensure() -> None:
    calls: list[str] = []
    result = run_sync_job(
        _settings(),
        sync=lambda _s: (calls.append("sync"), {"tickets": 2, "rows": 1})[1],
        ensure=lambda _s: calls.append("ensure"),
    )
    assert result == {"tickets": 2, "rows": 1}
    assert calls == ["sync", "ensure"]  # sync before ensure


def test_run_sync_job_swallows_errors() -> None:
    def boom(_s: Any) -> dict[str, int]:
        raise RuntimeError("zendesk down")

    assert run_sync_job(_settings(), sync=boom, ensure=lambda _s: None) == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.metrics.scheduler'`.

- [ ] **Step 4: Add the config flags**

In `config.py`, under the Phase-2 block added in Task 7 (after `bigquery_turn_events_table`), add:

```python
    # Bot-metrics Phase 2 (in-app Zendesk -> BQ sync scheduler / the "trigger")
    metrics_sync_enabled: bool = False
    metrics_sync_interval_hours: int = 6
```

- [ ] **Step 5: Implement `run_sync_job`**

Create `scheduler.py`:

```python
"""In-app scheduler that periodically runs the Phase-1 Zendesk -> BigQuery sync."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.metrics.sync import ensure_views, run_sync

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def run_sync_job(
    settings: Settings,
    *,
    sync: Callable[[Settings], dict[str, int]] | None = None,
    ensure: Callable[[Settings], None] | None = None,
) -> dict[str, int]:
    """Run the Zendesk->BQ sync + refresh views. Best-effort: never raises."""
    try:
        result = (sync or run_sync)(settings)
        (ensure or ensure_views)(settings)
        _log.info("metrics_sync_job_done", **result)
        return result
    except Exception as e:  # a failed scheduled run must never crash the app
        _log.error("metrics_sync_job_failed", error=str(e))
        return {}
```

(The `datetime`, `Any`, and APScheduler imports are added in Task 9 when `start_metrics_scheduler` lands; add only what `run_sync_job` needs now to keep ruff/mypy green.)

- [ ] **Step 6: Run the test + config check to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_scheduler.py -v`
Expected: PASS.
Run: `.venv/bin/python -c "from chatbot.platform.config import Settings; s=Settings(); print(s.metrics_sync_enabled, s.metrics_sync_interval_hours)"`
Expected: `False 6`.

- [ ] **Step 7: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add pyproject.toml uv.lock src/chatbot/platform/config.py src/chatbot/features/metrics/scheduler.py src/chatbot/features/metrics/test_scheduler.py
git commit -m "feat(metrics): apscheduler dep + run_sync_job + sync scheduler config"
```

---

### Task 9: `start_metrics_scheduler` + `main.py` wiring

**Files:**
- Modify: `apps/backend/src/chatbot/features/metrics/scheduler.py`
- Modify: `apps/backend/src/chatbot/main.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_scheduler.py`

**Interfaces:**
- Consumes: `run_sync_job` (Task 8); `Settings.metrics_sync_enabled` / `metrics_sync_interval_hours` (Task 8).
- Produces: `start_metrics_scheduler(settings, *, scheduler=None, job=None) -> Any | None` — returns `None` when disabled; otherwise adds an interval job (running `run_sync_job`) with a near-immediate first run, starts the scheduler, and returns it. `scheduler`/`job` are injectable for tests. Wired into `main.py` bootstrap with a shutdown hook.

- [ ] **Step 1: Write the failing test**

Append to `test_scheduler.py`:

```python
from chatbot.features.metrics.scheduler import start_metrics_scheduler


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.started = False

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.jobs.append({"func": func, **kwargs})

    def start(self) -> None:
        self.started = True


def test_scheduler_disabled_returns_none() -> None:
    s = cast(Settings, SimpleNamespace(metrics_sync_enabled=False, metrics_sync_interval_hours=6))
    assert start_metrics_scheduler(s) is None


def test_scheduler_enabled_adds_interval_job_and_starts() -> None:
    s = cast(Settings, SimpleNamespace(metrics_sync_enabled=True, metrics_sync_interval_hours=3))
    fake = _FakeScheduler()
    result = start_metrics_scheduler(s, scheduler=fake)
    assert result is fake
    assert fake.started is True
    assert len(fake.jobs) == 1
    job = fake.jobs[0]
    assert job["trigger"] == "interval"
    assert job["hours"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_scheduler.py -v`
Expected: FAIL with `ImportError: cannot import name 'start_metrics_scheduler'`.

- [ ] **Step 3: Implement `start_metrics_scheduler`**

In `scheduler.py`, update the imports at the top to add the scheduler import (place it after the existing imports):

```python
from apscheduler.schedulers.background import BackgroundScheduler
```

Append the function:

```python
def start_metrics_scheduler(
    settings: Settings,
    *,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the in-app Zendesk->BQ sync scheduler when enabled; else return None."""
    if not settings.metrics_sync_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_sync_job(settings))
    sched.add_job(
        run,
        trigger="interval",
        hours=settings.metrics_sync_interval_hours,
        id="zendesk_metrics_sync",
        next_run_time=datetime.now(UTC),  # first run shortly after startup
        replace_existing=True,
    )
    sched.start()
    _log.info(
        "metrics_sync_scheduler_started",
        interval_hours=settings.metrics_sync_interval_hours,
    )
    return sched
```

- [ ] **Step 4: Run the scheduler tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_scheduler.py -v`
Expected: PASS (all four tests; the fake scheduler avoids starting a real thread).

- [ ] **Step 5: Wire the scheduler into `main.py`**

In `main.py`, add the import near the other metrics import:

```python
from chatbot.features.metrics.scheduler import start_metrics_scheduler
```

After `app.include_router(...)` and the `health_check` definition, before `return app` (main.py:144), add:

```python
    metrics_scheduler = start_metrics_scheduler(settings)
    if metrics_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_metrics_scheduler() -> None:
            metrics_scheduler.shutdown(wait=False)
```

- [ ] **Step 6: Verify import/boot + full suite**

Run: `.venv/bin/python -c "import chatbot.main"`
Expected: no error (default `metrics_sync_enabled=False` → scheduler not started, no thread, no GCP/Zendesk calls at import).
Run: `.venv/bin/pytest src/ -q`
Expected: PASS (full suite).

- [ ] **Step 7: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/scheduler.py src/chatbot/features/metrics/test_scheduler.py src/chatbot/main.py
git commit -m "feat(metrics): start in-app Zendesk->BQ sync scheduler from bootstrap"
```

---

### Task 10: Docs — env example, Looker guide addendum, changelog

**Files:**
- Modify: `apps/backend/.env.example`
- Create: `docs/dashboards/looker-bot-metrics-phase2.md`
- Modify: `CHANGELOG.md` (if present) and `README.md` metrics section (if present)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add the new env vars to `.env.example`**

Append under the existing BigQuery block:

```bash
# Bot-metrics Phase 2 (per-turn streaming). "noop" (default) disables per-turn
# emit; "bigquery" streams one row per turn into the turn_events table.
METRICS_PROVIDER=noop
BIGQUERY_TURN_EVENTS_TABLE=turn_events

# Bot-metrics Phase 2 (in-app Zendesk->BQ sync scheduler / the "trigger").
# false (default) = no scheduler; true = refresh the conversations table every
# METRICS_SYNC_INTERVAL_HOURS while the backend runs. Needs the same Zendesk +
# BigQuery creds the manual sync uses.
METRICS_SYNC_ENABLED=false
METRICS_SYNC_INTERVAL_HOURS=6
```

- [ ] **Step 2: Write the Looker Phase-2 guide**

Create `docs/dashboards/looker-bot-metrics-phase2.md` documenting:
- Prerequisite: deploy with `METRICS_PROVIDER=bigquery` so `turn_events` accrues (empty until then — note no historical backfill exists).
- The three new BigQuery views and the tiles they back:
  - `v_speed_of_response` → two scorecards/bars: **Initial response speed** (`is_first_turn = true`, `p99_latency_ms`) and **Subsequent response speed** (`is_first_turn = false`), split by `channel`.
  - `v_fallback_rate` → **Fallback Rate** by channel (`fallback_rate`).
  - `v_bounce_rate` → **Bounce Rate** by channel (`bounce_rate`, single-turn sessions ÷ total sessions).
- Live smoke: set `METRICS_PROVIDER=bigquery`, run a few turns across channels (web `sim-…`, plus WhatsApp/email if available), confirm rows land in `turn_events` and each view returns data, then add the tiles.

- [ ] **Step 3: Update the changelog / README metrics section**

Add a Phase-2 entry summarizing: (a) the per-turn MetricsPort, the `turn_events` table, the three views/tiles, and the `METRICS_PROVIDER` flag (default `noop`); and (b) the in-app sync scheduler — the `METRICS_SYNC_ENABLED` / `METRICS_SYNC_INTERVAL_HOURS` flags (default off) that automate the Phase-1 Zendesk→BQ refresh. Match the format of the existing Phase-1 entry.

- [ ] **Step 4: Commit**

```bash
git add apps/backend/.env.example docs/dashboards/looker-bot-metrics-phase2.md CHANGELOG.md README.md
git commit -m "docs(metrics): phase 2 env example, Looker guide, changelog"
```

---

## Self-Review

**Spec coverage:**
- `features/metrics/events.py` (TurnEvent + build_turn_event) → Task 2 ✓
- promote `_channel` → `channel_from_external_id` (DRY) → Task 1 ✓
- `MetricsPort` protocol → Task 4 ✓
- `BigQueryMetricsAdapter` + `NoOpMetrics` → Tasks 4 (NoOp) + 5 (BQ) ✓
- `turn_schema.py` (TURN_EVENTS_SCHEMA + turn_view_ddls) → Task 3 ✓
- `service.py` emit + timing → Task 6 ✓
- per-turn config + main.py wiring → Task 7 ✓
- `turn_events` 8 columns → Task 3 schema ✓
- three views (speed/fallback/bounce, with the exact aggregates) → Task 3 ✓
- error handling best-effort (adapter + handle_turn) → Tasks 5 + 6 ✓
- testing (pure build_turn_event, channel reuse, schema/views, handle_turn emit with fake port, NoOp) → Tasks 1–6 ✓
- per-turn config (`metrics_provider`, `bigquery_turn_events_table`) → Task 7 ✓
- **sync automation / the "trigger":** `apscheduler` dep + `run_sync_job` + scheduler config → Task 8; `start_metrics_scheduler` + bootstrap wiring + shutdown hook → Task 9; reuses Phase-1 `run_sync`/`ensure_views` unchanged ✓
- **scheduler config** (`metrics_sync_enabled`, `metrics_sync_interval_hours`, default off) → Task 8 ✓
- Looker guide / live smoke documented + scheduler env vars → Task 10 ✓
- Out of scope (voice/phone per-turn, NPS, accuracy/quality, Looker build, backfill, Cloud Scheduler, changing Phase-1 sync logic) → untouched ✓

**Type consistency:** `build_turn_event(session_id, occurred_at, latency_ms, turn_count, is_fallback, handed_off)` is defined in Task 2 and called with the same keyword arguments in Tasks 5 (tests) and 6. `TurnEvent` field set (no `event_id`) is consistent: the adapter mints `event_id` at emit (Task 5) — matches the schema's `event_id` column (Task 3). `MetricsPort.emit_turn(event)` signature consistent across Tasks 4/5/6. `build_metrics_port(settings)` defined in Task 5, consumed in Task 7. `metrics_provider` / `bigquery_turn_events_table` referenced in Task 5's factory and defined in Task 7 (Task 5's tests use a `SimpleNamespace`, so ordering is safe). Scheduler: `run_sync_job(settings, *, sync, ensure)` defined in Task 8, consumed by `start_metrics_scheduler` in Task 9; `metrics_sync_enabled` / `metrics_sync_interval_hours` defined in Task 8, read in Task 9; both reuse Phase-1 `run_sync`/`ensure_views` signatures verbatim.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the only prose-only task (Task 10) is documentation, where prose is the deliverable.
