# Bot-Metrics Dashboard — Phase 4 (Accuracy/Quality QA entry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Accuracy/Quality metrics via a manual QA-entry path — an authenticated `POST /qa/label` endpoint that writes a reviewer's per-conversation label to a dedicated BigQuery `qa_labels` table, surfaced through a `v_quality` view (per-channel avg accuracy/quality).

**Architecture:** Ports & adapters, mirroring the Phase-2 direct-BigQuery write. A pure frozen `QaLabel`; a `QaLabelPort` (`record_label`) with `NoOpQaLabels` (default) and `BigQueryQaLabels` (best-effort, non-blocking `insert_rows_json` via `asyncio.to_thread`; ensures the table + `v_quality` view on init). A standalone `qa_router` exposes `POST /qa/label` behind a constant-time `X-API-Key` gate. `v_quality` JOINs `qa_labels` to the Phase-1 `conversations` table for the per-channel breakdown.

**Tech Stack:** Python 3.12, FastAPI, pydantic, google-cloud-bigquery, structlog, `hmac` (stdlib), pytest / pytest-asyncio (`asyncio_mode="auto"`), ruff, mypy --strict.

## Global Constraints

- Run all backend commands from `apps/backend/`. Quality gates per task: `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict`, `.venv/bin/pytest src/`.
- `from __future__ import annotations` at the top of every new module.
- **mypy baseline:** exactly **3 tolerated pre-existing** errors (google.adk `Event` export ×2, one `no-any-return` in `service.py`). Every task keeps mypy at `Found 3 errors` — ZERO new.
- **ruff baseline:** **1 tolerated pre-existing** error (`PLC0415` in `vertex_search.py`). Add no new lint.
- **Scales (exact):** `accuracy` and `quality` are each an integer **0–100** (`Field(ge=0, le=100)`).
- **Auth (exact):** `POST /qa/label` requires header `X-API-Key` to equal `settings.qa_api_key` compared with `hmac.compare_digest`. Return **401** when `qa_api_key` is empty (locked), the header is missing, or it mismatches.
- **Default-off:** `qa_provider="noop"` → `NoOpQaLabels`; dev/tests/CI never touch BigQuery. The BQ write is best-effort and non-blocking (`asyncio.to_thread`), never raises into the response.
- **Reuse:** `qa_labels` and `v_quality` live in the same dataset as Phase 1/2 (`bigquery_project_id` / `bigquery_dataset`). `v_quality` JOINs the existing `conversations` table.
- DRY, YAGNI, TDD, frequent commits. Build only what each task specifies.

---

### Task 1: `QaLabel` + `QaLabelPort` + `NoOpQaLabels`

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/qa.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_qa.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) QaLabel` with fields `conversation_id: str`, `accuracy: int`, `quality: int`, `reviewer: str`, `notes: str`, `labeled_at: datetime`.
  - `QaLabelPort` Protocol: `async def record_label(self, label: QaLabel) -> None`.
  - `NoOpQaLabels` implementing it (no-op). Used by Tasks 4/5/6.

- [ ] **Step 1: Write the failing test**

Create `test_qa.py`:

```python
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from chatbot.features.metrics.qa import NoOpQaLabels, QaLabel


def test_qa_label_is_frozen_with_expected_fields() -> None:
    ts = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
    label = QaLabel(
        conversation_id="T-1",
        accuracy=88,
        quality=92,
        reviewer="alice",
        notes="missed one spec detail",
        labeled_at=ts,
    )
    assert label.conversation_id == "T-1"
    assert label.accuracy == 88
    assert label.quality == 92
    assert label.reviewer == "alice"
    assert label.notes == "missed one spec detail"
    assert label.labeled_at == ts
    with pytest.raises(FrozenInstanceError):
        label.accuracy = 0  # type: ignore[misc]


async def test_noop_qa_labels_record_is_a_noop() -> None:
    label = QaLabel(
        conversation_id="T-1",
        accuracy=1,
        quality=2,
        reviewer="bob",
        notes="",
        labeled_at=datetime.now(UTC),
    )
    # Must not raise and must return None.
    assert await NoOpQaLabels().record_label(label) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_qa.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.metrics.qa'`.

- [ ] **Step 3: Write minimal implementation**

Create `qa.py`:

```python
"""QA accuracy/quality label: the value object, the port, and the NoOp adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class QaLabel:
    conversation_id: str
    accuracy: int
    quality: int
    reviewer: str
    notes: str
    labeled_at: datetime


class QaLabelPort(Protocol):
    """Port for recording one manual QA label per reviewed conversation."""

    async def record_label(self, label: QaLabel) -> None:
        """Best-effort: persist a single QA label. Must never raise."""
        ...


class NoOpQaLabels:
    """Default QaLabelPort — drops every label. Used in dev/tests and when
    qa_provider != 'bigquery'."""

    async def record_label(self, label: QaLabel) -> None:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_qa.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/qa.py src/chatbot/features/metrics/test_qa.py
git commit -m "feat(metrics): QaLabel value object + QaLabelPort + NoOpQaLabels"
```

---

### Task 2: `qa_labels` schema + `v_quality` view DDL

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/qa_schema.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_qa_schema.py`

**Interfaces:**
- Produces:
  - `QA_LABELS_SCHEMA: list[bigquery.SchemaField]` — 6 fields in order: `conversation_id` STRING REQUIRED, `accuracy` INT64, `quality` INT64, `reviewer` STRING, `notes` STRING, `labeled_at` TIMESTAMP.
  - `qa_view_ddls(project, dataset, table="qa_labels", conversations_table="conversations") -> dict[str, str]` — key `v_quality`. Used by Task 4.

- [ ] **Step 1: Write the failing test**

Create `test_qa_schema.py`:

```python
from chatbot.features.metrics.qa_schema import QA_LABELS_SCHEMA, qa_view_ddls


def test_schema_field_names_and_order() -> None:
    assert [f.name for f in QA_LABELS_SCHEMA] == [
        "conversation_id",
        "accuracy",
        "quality",
        "reviewer",
        "notes",
        "labeled_at",
    ]


def test_schema_types() -> None:
    by_name = {f.name: f for f in QA_LABELS_SCHEMA}
    assert by_name["conversation_id"].field_type == "STRING"
    assert by_name["accuracy"].field_type == "INT64"
    assert by_name["quality"].field_type == "INT64"
    assert by_name["labeled_at"].field_type == "TIMESTAMP"


def test_v_quality_view_joins_conversations_with_avgs() -> None:
    ddls = qa_view_ddls("proj", "ds", "qa_labels", "conversations")
    assert set(ddls) == {"v_quality"}
    sql = ddls["v_quality"]
    assert "`proj.ds.v_quality`" in sql
    assert "`proj.ds.qa_labels`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "USING (conversation_id)" in sql
    assert "AVG(q.accuracy) AS avg_accuracy" in sql
    assert "AVG(q.quality) AS avg_quality" in sql
    assert "GROUP BY c.channel" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_qa_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.metrics.qa_schema'`.

- [ ] **Step 3: Write minimal implementation**

Create `qa_schema.py` (mirrors `turn_schema.py`, including the file-level noqa):

```python
"""BigQuery schema + view DDL for the manual QA accuracy/quality labels (Phase 4)."""

# ruff: noqa: S608  # DDL generation: project/dataset/table are internal config, not user input

from __future__ import annotations

from google.cloud import bigquery

QA_LABELS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("conversation_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("accuracy", "INT64"),
    bigquery.SchemaField("quality", "INT64"),
    bigquery.SchemaField("reviewer", "STRING"),
    bigquery.SchemaField("notes", "STRING"),
    bigquery.SchemaField("labeled_at", "TIMESTAMP"),
]


def qa_view_ddls(
    project: str,
    dataset: str,
    table: str = "qa_labels",
    conversations_table: str = "conversations",
) -> dict[str, str]:
    """The v_quality view: per-channel avg accuracy/quality, joined to conversations."""
    qa_fq = f"`{project}.{dataset}.{table}`"
    conv_fq = f"`{project}.{dataset}.{conversations_table}`"
    return {
        "v_quality": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_quality` AS "
            f"SELECT c.channel, "
            f"COUNT(*) AS labels, "
            f"AVG(q.accuracy) AS avg_accuracy, "
            f"AVG(q.quality) AS avg_quality "
            f"FROM {qa_fq} q "
            f"JOIN {conv_fq} c USING (conversation_id) "
            f"GROUP BY c.channel"
        ),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_qa_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/qa_schema.py src/chatbot/features/metrics/test_qa_schema.py
git commit -m "feat(metrics): qa_labels schema + v_quality view DDL"
```

---

### Task 3: Config — `qa_provider`, `bigquery_qa_labels_table`, `qa_api_key`

**Files:**
- Modify: `apps/backend/src/chatbot/platform/config.py`
- Test: `apps/backend/src/chatbot/platform/test_config_qa.py`

**Interfaces:**
- Produces: `Settings.qa_provider: Literal["noop","bigquery"]` (default `"noop"`), `Settings.bigquery_qa_labels_table: str` (default `"qa_labels"`), `Settings.qa_api_key: str` (default `""`). Used by Tasks 4–6.

(Done before the adapter/router tasks so `mypy --strict` never sees `settings.qa_provider`/`settings.qa_api_key` before the field exists.)

- [ ] **Step 1: Write the failing test**

Create `test_config_qa.py`:

```python
from chatbot.platform.config import Settings


def test_qa_defaults() -> None:
    s = Settings()
    assert s.qa_provider == "noop"
    assert s.bigquery_qa_labels_table == "qa_labels"
    assert s.qa_api_key == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_qa.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'qa_provider'`.

- [ ] **Step 3: Add the settings**

In `config.py`, add after the Phase-2 scheduler block (`metrics_sync_interval_hours: int = 6`):

```python
    # Bot-metrics Phase 4 (manual QA accuracy/quality entry)
    qa_provider: Literal["noop", "bigquery"] = "noop"
    bigquery_qa_labels_table: str = "qa_labels"
    qa_api_key: str = ""
```

(`Literal` is already imported in `config.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/platform/test_config_qa.py -v`
Expected: PASS.

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/platform/config.py src/chatbot/platform/test_config_qa.py
git commit -m "feat(metrics): qa_provider + bigquery_qa_labels_table + qa_api_key config"
```

---

### Task 4: `BigQueryQaLabels` adapter + `build_qa_label_port`

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/qa_adapter.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_qa_adapter.py`

**Interfaces:**
- Consumes: `QaLabel`, `NoOpQaLabels`, `QaLabelPort` (Task 1); `QA_LABELS_SCHEMA`, `qa_view_ddls` (Task 2); `Settings.qa_provider`/`bigquery_qa_labels_table` (Task 3).
- Produces:
  - `BigQueryQaLabels(settings, *, client=None)` — ensures dataset + `qa_labels` table + `v_quality` view on init (best-effort); `record_label` does a best-effort, non-blocking `await asyncio.to_thread(insert_rows_json, ...)`.
  - `build_qa_label_port(settings) -> QaLabelPort` — `BigQueryQaLabels` when `qa_provider=="bigquery"` (NoOp fallback on init failure), else `NoOpQaLabels`. Used by Task 6.

- [ ] **Step 1: Write the failing test**

Create `test_qa_adapter.py` (mirrors `test_bigquery_metrics.py`):

```python
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from chatbot.features.metrics.qa import QaLabel
from chatbot.features.metrics.qa_adapter import BigQueryQaLabels, build_qa_label_port
from chatbot.platform.config import Settings


class _FakeBQ:
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
            bigquery_qa_labels_table="qa_labels",
            qa_provider="bigquery",
        ),
    )


def _label() -> QaLabel:
    return QaLabel(
        conversation_id="T-9",
        accuracy=88,
        quality=92,
        reviewer="alice",
        notes="ok",
        labeled_at=datetime(2026, 6, 29, tzinfo=UTC),
    )


def test_init_ensures_dataset_table_and_view() -> None:
    fake = _FakeBQ()
    BigQueryQaLabels(_settings(), client=fake)
    assert fake.created_datasets == ["proj.ds"]
    assert fake.created_tables == ["proj.ds.qa_labels"]
    assert len(fake.queries) == 1  # the v_quality view


async def test_record_label_inserts_row_with_all_fields() -> None:
    fake = _FakeBQ()
    adapter = BigQueryQaLabels(_settings(), client=fake)
    await adapter.record_label(_label())
    assert len(fake.inserted) == 1
    table_id, rows = fake.inserted[0]
    assert table_id == "proj.ds.qa_labels"
    row = rows[0]
    assert row["conversation_id"] == "T-9"
    assert row["accuracy"] == 88
    assert row["quality"] == 92
    assert row["reviewer"] == "alice"
    assert row["notes"] == "ok"
    assert row["labeled_at"].startswith("2026-06-29T")


async def test_record_label_swallows_insert_errors() -> None:
    fake = _FakeBQ()
    fake.insert_error = RuntimeError("BQ down")
    adapter = BigQueryQaLabels(_settings(), client=fake)
    # Must not raise.
    assert await adapter.record_label(_label()) is None


def test_build_qa_label_port_selects_noop_by_default() -> None:
    s = cast(Settings, SimpleNamespace(qa_provider="noop"))
    assert build_qa_label_port(s).__class__.__name__ == "NoOpQaLabels"


def test_build_qa_label_port_falls_back_to_noop_on_init_failure(monkeypatch: Any) -> None:
    from chatbot.features.metrics import qa_adapter

    def _boom(_settings: Any) -> Any:
        raise RuntimeError("no creds")

    monkeypatch.setattr(qa_adapter, "BigQueryQaLabels", _boom)
    s = cast(Settings, SimpleNamespace(qa_provider="bigquery"))
    assert build_qa_label_port(s).__class__.__name__ == "NoOpQaLabels"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_qa_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.metrics.qa_adapter'`.

- [ ] **Step 3: Write minimal implementation**

Create `qa_adapter.py` (mirrors `adapters/bigquery_metrics.py`):

```python
"""QaLabelPort BigQuery adapter (Phase 4) + the port factory."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.qa import NoOpQaLabels, QaLabel
from chatbot.features.metrics.qa_schema import QA_LABELS_SCHEMA, qa_view_ddls

if TYPE_CHECKING:
    from chatbot.features.metrics.qa import QaLabelPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class BigQueryQaLabels:
    """Writes one row per manual QA label into BigQuery. Ensures the table +
    v_quality view on init; record is best-effort, non-blocking, and never raises."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._project = settings.bigquery_project_id
        self._dataset = settings.bigquery_dataset
        self._table = settings.bigquery_qa_labels_table
        self._table_id = f"{self._project}.{self._dataset}.{self._table}"
        self._client = client or bigquery.Client(project=self._project)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            self._client.create_dataset(f"{self._project}.{self._dataset}", exists_ok=True)
            self._client.create_table(
                bigquery.Table(self._table_id, schema=QA_LABELS_SCHEMA), exists_ok=True
            )
            for ddl in qa_view_ddls(self._project, self._dataset, self._table).values():
                self._client.query(ddl).result()
        except Exception as e:  # best-effort bootstrap (v_quality needs conversations to exist)
            _log.error("qa_ensure_schema_failed", error=str(e))

    async def record_label(self, label: QaLabel) -> None:
        try:
            row = {
                "conversation_id": label.conversation_id,
                "accuracy": label.accuracy,
                "quality": label.quality,
                "reviewer": label.reviewer,
                "notes": label.notes,
                "labeled_at": label.labeled_at.isoformat(),
            }
            errors = await asyncio.to_thread(self._client.insert_rows_json, self._table_id, [row])
            if errors:
                _log.warning("qa_insert_returned_errors", errors=str(errors))
        except Exception as e:  # best-effort: never break the response
            _log.error("qa_record_label_failed", error=str(e))


def build_qa_label_port(settings: Settings) -> QaLabelPort:
    """Pick the QaLabelPort implementation from settings."""
    if settings.qa_provider == "bigquery":
        try:
            return BigQueryQaLabels(settings)
        except Exception as e:  # never let QA init crash the app
            _log.error("qa_adapter_init_failed_falling_back_to_noop", error=str(e))
            return NoOpQaLabels()
    return NoOpQaLabels()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_qa_adapter.py -v`
Expected: PASS (all five tests).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/qa_adapter.py src/chatbot/features/metrics/test_qa_adapter.py
git commit -m "feat(metrics): BigQueryQaLabels adapter + build_qa_label_port"
```

---

### Task 5: `POST /qa/label` router with API-key auth

**Files:**
- Create: `apps/backend/src/chatbot/features/metrics/qa_router.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_qa_router.py`

**Interfaces:**
- Consumes: `QaLabel`, `QaLabelPort` (Task 1); `Settings.qa_api_key` (Task 3).
- Produces: `QaLabelRequest{conversation_id:str, accuracy:int(0-100), quality:int(0-100), reviewer:str, notes:str=""}`; `build_qa_router(qa_port: QaLabelPort, settings: Settings) -> APIRouter` exposing `POST /qa/label`. Used by Task 6.

- [ ] **Step 1: Write the failing test**

Create `test_qa_router.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from chatbot.features.metrics.qa import QaLabel
from chatbot.features.metrics.qa_router import build_qa_router
from chatbot.platform.config import Settings
from chatbot.platform.server import create_app


def _client(qa_port: Any, api_key: str) -> TestClient:
    settings = Settings(qa_api_key=api_key)
    app = create_app(settings)
    app.include_router(build_qa_router(qa_port, settings))
    return TestClient(app)


def _body() -> dict[str, Any]:
    return {
        "conversation_id": "T-9",
        "accuracy": 88,
        "quality": 92,
        "reviewer": "alice",
        "notes": "ok",
    }


def test_qa_label_records_with_valid_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/qa/label", json=_body(), headers={"X-API-Key": "secret"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    port.record_label.assert_awaited_once()
    label = port.record_label.await_args.args[0]
    assert isinstance(label, QaLabel)
    assert label.conversation_id == "T-9"
    assert label.accuracy == 88
    assert label.quality == 92
    assert label.reviewer == "alice"
    assert label.notes == "ok"


def test_qa_label_rejects_wrong_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/qa/label", json=_body(), headers={"X-API-Key": "nope"})
    assert res.status_code == 401
    port.record_label.assert_not_awaited()


def test_qa_label_rejects_missing_key() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    res = client.post("/qa/label", json=_body())
    assert res.status_code == 401


def test_qa_label_locked_when_api_key_unset() -> None:
    port = AsyncMock()
    client = _client(port, "")
    res = client.post("/qa/label", json=_body(), headers={"X-API-Key": ""})
    assert res.status_code == 401


def test_qa_label_rejects_out_of_range_score() -> None:
    port = AsyncMock()
    client = _client(port, "secret")
    high = {**_body(), "accuracy": 101}
    low = {**_body(), "quality": -1}
    assert client.post("/qa/label", json=high, headers={"X-API-Key": "secret"}).status_code == 422
    assert client.post("/qa/label", json=low, headers={"X-API-Key": "secret"}).status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_qa_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.metrics.qa_router'`.

- [ ] **Step 3: Write minimal implementation**

Create `qa_router.py`:

```python
"""POST /qa/label — authenticated manual QA-label entry endpoint (Phase 4)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.metrics.qa import QaLabel

if TYPE_CHECKING:
    from chatbot.features.metrics.qa import QaLabelPort
    from chatbot.platform.config import Settings


class QaLabelRequest(BaseModel):
    conversation_id: str
    accuracy: int = Field(ge=0, le=100)
    quality: int = Field(ge=0, le=100)
    reviewer: str
    notes: str = ""


def build_qa_router(qa_port: QaLabelPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["qa"])

    @router.post("/qa/label")
    async def label(
        payload: QaLabelRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        key = settings.qa_api_key
        if not key or x_api_key is None or not hmac.compare_digest(x_api_key, key):
            raise HTTPException(status_code=401, detail="Unauthorized")
        await qa_port.record_label(
            QaLabel(
                conversation_id=payload.conversation_id,
                accuracy=payload.accuracy,
                quality=payload.quality,
                reviewer=payload.reviewer,
                notes=payload.notes,
                labeled_at=datetime.now(UTC),
            )
        )
        return {"status": "ok", "message": "QA label recorded."}

    return router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_qa_router.py -v`
Expected: PASS (all five tests).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/qa_router.py src/chatbot/features/metrics/test_qa_router.py
git commit -m "feat(metrics): POST /qa/label endpoint with X-API-Key auth"
```

---

### Task 6: Wire the QA port + router into `main.py`

**Files:**
- Modify: `apps/backend/src/chatbot/main.py`
- Test: (covered by `import chatbot.main` boot check + the existing suite; no new unit test — `build_qa_label_port`/`build_qa_router` are already unit-tested)

**Interfaces:**
- Consumes: `build_qa_label_port` (Task 4), `build_qa_router` (Task 5).
- Produces: the running app exposes `POST /qa/label` (NoOp-backed by default).

- [ ] **Step 1: Add the imports**

In `main.py`, add near the other metrics imports:

```python
from chatbot.features.metrics.qa_adapter import build_qa_label_port
from chatbot.features.metrics.qa_router import build_qa_router
```

- [ ] **Step 2: Build the port and include the router**

In `main.py`, after the existing `app.include_router(build_chat_router(...))` call, add:

```python
    qa_port = build_qa_label_port(settings)
    app.include_router(build_qa_router(qa_port, settings))
```

- [ ] **Step 3: Verify boot + route + full suite**

Run: `.venv/bin/python -c "import chatbot.main"`
Expected: no error (default `qa_provider="noop"` → `NoOpQaLabels`, no BigQuery client at import).

Run: `.venv/bin/python -c "import chatbot.main as m; print([r.path for r in m.app.routes if getattr(r, 'path', '') == '/qa/label'])"`
Expected: `['/qa/label']`.

Run: `.venv/bin/pytest src/ -q`
Expected: PASS (full suite).

- [ ] **Step 4: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/main.py
git commit -m "feat(metrics): wire QA label port + /qa/label router into bootstrap"
```

---

### Task 7: Docs — env example, changelog, README, Looker v_quality tile

**Files:**
- Modify: `apps/backend/.env.example`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `docs/dashboards/looker-bot-metrics-phase1.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add the env vars to `.env.example`**

Append (after the Phase-2 metrics block):

```bash
# Bot-metrics Phase 4 (manual QA accuracy/quality entry). "noop" (default)
# disables the BigQuery write; "bigquery" writes labels to the qa_labels table.
# QA_API_KEY gates POST /qa/label (X-API-Key header); empty = endpoint locked.
QA_PROVIDER=noop
BIGQUERY_QA_LABELS_TABLE=qa_labels
QA_API_KEY=
```

- [ ] **Step 2: Add a changelog entry**

In `CHANGELOG.md`, add a Phase-4 entry near the top, mirroring the existing Phase-1/2/3 blocks. Cover: the `POST /qa/label` endpoint (X-API-Key auth, accuracy/quality 0–100), the dedicated `qa_labels` BigQuery table, and the `v_quality` view (per-channel `avg_accuracy`/`avg_quality`, joined to `conversations`). Note the `QA_PROVIDER`/`QA_API_KEY` flags (default off/locked) and that a frontend QA UI + CLI bulk-import are deferred.

- [ ] **Step 3: Update the README metrics section**

In `README.md`, extend the Metrics/Dashboard section with a brief Accuracy/Quality note: labels submitted via `POST /qa/label` (`{conversation_id, accuracy, quality, reviewer, notes}`, 0–100, `X-API-Key`), surfaced through the `v_quality` view; the new `QA_PROVIDER` / `BIGQUERY_QA_LABELS_TABLE` / `QA_API_KEY` settings. Keep it short, matching the section's style.

- [ ] **Step 4: Add the v_quality tile to the Looker guide**

In `docs/dashboards/looker-bot-metrics-phase1.md`, add a short Accuracy/Quality subsection: create a data source from the `v_quality` view; build **Accuracy** and **Quality** scorecards (the `avg_accuracy` / `avg_quality` columns, as percentages) + an optional by-channel breakdown. Document the live-enable order: run the Phase-1 sync FIRST (so `conversations` exists for the JOIN), then start the backend with `QA_PROVIDER=bigquery` + a `QA_API_KEY` set, `POST /qa/label` a few labels for real ticket ids, and confirm `v_quality` returns rows.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/.env.example CHANGELOG.md README.md docs/dashboards/looker-bot-metrics-phase1.md
git commit -m "docs(metrics): phase 4 QA accuracy/quality env, changelog, readme, Looker tile"
```

---

## Self-Review

**Spec coverage:**
- `features/metrics/qa.py` (QaLabel + QaLabelPort + NoOpQaLabels) → Task 1 ✓
- `qa_schema.py` (QA_LABELS_SCHEMA + v_quality DDL) → Task 2 ✓
- config (`qa_provider`, `bigquery_qa_labels_table`, `qa_api_key`) → Task 3 ✓
- `qa_adapter.py` (BigQueryQaLabels ensure+best-effort non-blocking insert; build_qa_label_port noop/fallback) → Task 4 ✓
- `qa_router.py` (`POST /qa/label`, X-API-Key constant-time auth 401, 0–100 validation 422) → Task 5 ✓
- `main.py` wiring (build port + include router) → Task 6 ✓
- docs (env, changelog, readme, Looker v_quality tile) → Task 7 ✓
- `qa_labels` 6 columns → Task 2 schema ✓
- `v_quality` per-channel JOIN avg accuracy/quality → Task 2 ✓
- best-effort + non-blocking write; default-off; NoOp fallback → Tasks 1/4 ✓
- auth: 401 on unset/missing/mismatch via hmac.compare_digest → Task 5 ✓
- testing (QaLabel shape, schema+view, adapter ensure/insert/swallow/factory/fallback, endpoint 200/401×3/422, NoOp) → Tasks 1–5 ✓
- Out of scope (frontend UI, CLI bulk-import, multi-rater, auto-sampling, per-turn QA) → untouched ✓

**Type consistency:** `QaLabel(conversation_id, accuracy, quality, reviewer, notes, labeled_at)` (Task 1) is constructed identically in Task 4 (adapter row) and Task 5 (router). `QaLabelPort.record_label(label)` (Task 1) is implemented by `NoOpQaLabels` (Task 1) and `BigQueryQaLabels` (Task 4), consumed by the router (Task 5). `build_qa_label_port(settings) -> QaLabelPort` (Task 4) consumed in Task 6. `qa_view_ddls(project, dataset, table, conversations_table)` (Task 2) called by the adapter (Task 4) with `(project, dataset, table)` (conversations_table defaults to `"conversations"`). `qa_provider`/`bigquery_qa_labels_table`/`qa_api_key` defined in Task 3, read in Tasks 4/5. The row dict keys (Task 4) match `QA_LABELS_SCHEMA` field names (Task 2). The `v_quality` column names (`channel`/`labels`/`avg_accuracy`/`avg_quality`) are asserted in Task 2's test and referenced in Task 7's docs.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the only prose-only task (Task 7) is documentation, where prose is the deliverable.
