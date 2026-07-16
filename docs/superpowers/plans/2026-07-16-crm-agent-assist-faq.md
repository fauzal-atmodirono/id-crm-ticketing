# CRM Agent-Assist FAQ Implementation Plan (Short-Term Item 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give agents real-time keyword-matched FAQ suggestions with one-click reference and quality feedback, reusing the live `KnowledgePort`, plus a thin Zendesk sidebar app that consumes the endpoints.

**Architecture:** `GET /kb/suggest?q=…` reuses `KnowledgePort.search_kb` to return ranked article snippets (read-only, POC-open like the other metrics endpoints). A dedicated `faq_feedback` pipeline (value + `FaqFeedbackPort` + NoOp + BigQuery adapter + `faq_feedback` table + `v_faq_quality` view) mirrors the existing QA-label pattern — right grain for per-article helpful/score, rather than overloading `qa_labels` (which is per-conversation accuracy/quality). `POST /kb/feedback` records feedback (auth'd via the existing `x-api-key`/`qa_api_key`). A minimal Zendesk App Framework (ZAF) scaffold calls both endpoints.

**Design deviation (noted):** the design sketch said "reuse the QA-label pipeline." On inspection `QaLabel{conversation_id, accuracy, quality, reviewer, notes}` has the wrong grain for per-article FAQ feedback, so this plan builds a small dedicated `faq_feedback` path instead (same shape of pattern, correct fields). Cleaner and keeps QA accuracy/quality metrics uncontaminated.

**Tech Stack:** Python 3.12, FastAPI, `google-cloud-bigquery`, `pytest`; a static ZAF app (`manifest.json` + vanilla JS). Run backend commands from `apps/backend/`.

## Global Constraints

- Run from `apps/backend/`; venv `.venv/`.
- Gates before each commit: `.venv/bin/ruff format .` · `.venv/bin/ruff check . --fix` · `.venv/bin/mypy src/ --strict` · `.venv/bin/pytest src/`.
- Baselines (ZERO new): full suite currently **404** passing; mypy --strict **3** pre-existing (firestore_session_service.py:12, service.py:180, test_service.py:10); ruff **1** pre-existing (PLC0415 vertex_search.py).
- `GET /kb/suggest` is read-only + POC-open (matches `/metrics/dashboard`). `POST /kb/feedback` is auth'd via `x-api-key` compared to `settings.qa_api_key` (same trust class as `/qa/label`; reused to avoid new config).
- BigQuery adapter selection reuses `metrics_provider` (`noop`→NoOp, `bigquery`→BQ); new table name config `bigquery_faq_feedback_table: str = "faq_feedback"`. All new I/O behind ports with NoOp/mock.
- Conventional commits `feat(chat|metrics): …`. Branch `feature/crm-agent-assist-faq`. Do not push.

## File structure

- Create `src/chatbot/features/chat/kb_suggest_router.py` — `GET /kb/suggest`.
- Create `src/chatbot/features/metrics/faq_feedback.py` — `FaqFeedback` value + `FaqFeedbackPort` + `NoOpFaqFeedback`.
- Create `src/chatbot/features/metrics/faq_feedback_adapter.py` — `BigQueryFaqFeedback` + `build_faq_feedback_port`.
- Create `src/chatbot/features/metrics/faq_schema.py` — `FAQ_FEEDBACK_SCHEMA` + `v_faq_quality` DDL.
- Create `src/chatbot/features/metrics/faq_router.py` — `POST /kb/feedback`.
- Create `apps/zendesk-agent-app/` — `manifest.json` + `assets/iframe.html` (ZAF scaffold).
- Modify `platform/config.py`, `main.py`; docs `docs/dashboards/agent-assist-faq.md`.
- Co-located `test_*.py`.

---

### Task 1: GET /kb/suggest

**Files:**
- Create: `src/chatbot/features/chat/kb_suggest_router.py`
- Test: `src/chatbot/features/chat/test_kb_suggest_router.py`

**Interfaces:**
- Consumes: `KnowledgePort.search_kb(query, limit)` → `list[KbArticle]`.
- Produces: `build_kb_suggest_router(knowledge_port: KnowledgePort) -> APIRouter` with `GET /kb/suggest?q=<text>&limit=<int, default 3>` → `{"query": q, "suggestions": [{"title","snippet","url","source_type"}, ...]}`. `snippet` = first 280 chars of `content`. Empty/blank `q` → `{"query": "", "suggestions": []}` (no KB call).

- [ ] **Step 1: Write failing test** — `test_kb_suggest_router.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.kb_suggest_router import build_kb_suggest_router
from chatbot.features.chat.models import KbArticle


class _FakeKnowledge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        self.calls.append((query, limit))
        return [KbArticle(title="Reset battery", content="Long body " * 50, url="http://x/1")]


def _client(kb: _FakeKnowledge) -> TestClient:
    app = FastAPI()
    app.include_router(build_kb_suggest_router(kb))
    return TestClient(app)


def test_suggest_returns_snippets() -> None:
    kb = _FakeKnowledge()
    r = _client(kb).get("/kb/suggest?q=battery&limit=3")
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "battery"
    assert body["suggestions"][0]["title"] == "Reset battery"
    assert len(body["suggestions"][0]["snippet"]) <= 280
    assert kb.calls == [("battery", 3)]


def test_suggest_blank_query_skips_kb() -> None:
    kb = _FakeKnowledge()
    r = _client(kb).get("/kb/suggest?q=%20")
    assert r.status_code == 200
    assert r.json() == {"query": "", "suggestions": []}
    assert kb.calls == []
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_kb_suggest_router.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `kb_suggest_router.py`**

```python
"""GET /kb/suggest — real-time FAQ suggestions for agents (reuses KnowledgePort)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort

_SNIPPET = 280


def build_kb_suggest_router(knowledge_port: KnowledgePort) -> APIRouter:
    router = APIRouter(tags=["kb"])

    @router.get("/kb/suggest")
    async def suggest(q: str = "", limit: int = 3) -> dict[str, Any]:
        query = q.strip()
        if not query:
            return {"query": "", "suggestions": []}
        articles = await knowledge_port.search_kb(query, limit)
        return {
            "query": query,
            "suggestions": [
                {
                    "title": a.title,
                    "snippet": a.content[:_SNIPPET],
                    "url": a.url,
                    "source_type": a.source_type,
                }
                for a in articles
            ],
        }

    return router
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_kb_suggest_router.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/kb_suggest_router.py src/chatbot/features/chat/test_kb_suggest_router.py
git commit -m "feat(chat): add GET /kb/suggest agent FAQ suggestion endpoint"
```

---

### Task 2: FaqFeedback value + port + NoOp + config

**Files:**
- Create: `src/chatbot/features/metrics/faq_feedback.py`
- Modify: `src/chatbot/platform/config.py`, `.env.example`
- Test: `src/chatbot/features/metrics/test_faq_feedback.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) FaqFeedback{article_id:str, session_id:str, helpful:bool, score:int, at:datetime}`; `FaqFeedbackPort` Protocol `async record_feedback(self, fb: FaqFeedback) -> None`; `NoOpFaqFeedback` (drops, never raises). Config: `bigquery_faq_feedback_table: str = "faq_feedback"`.

- [ ] **Step 1: Write failing test** — `test_faq_feedback.py`:

```python
from datetime import UTC, datetime

import pytest

from chatbot.features.metrics.faq_feedback import FaqFeedback, NoOpFaqFeedback


@pytest.mark.asyncio
async def test_noop_never_raises() -> None:
    fb = FaqFeedback(
        article_id="A1", session_id="whatsapp-+60", helpful=True, score=5,
        at=datetime.now(UTC),
    )
    await NoOpFaqFeedback().record_feedback(fb)  # must not raise
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_faq_feedback.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `faq_feedback.py`** (mirror `metrics/qa.py`):

```python
"""FAQ feedback: value object, port, and NoOp adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class FaqFeedback:
    article_id: str
    session_id: str
    helpful: bool
    score: int
    at: datetime


class FaqFeedbackPort(Protocol):
    async def record_feedback(self, fb: FaqFeedback) -> None:
        """Best-effort: persist one FAQ-feedback row. Must never raise."""
        ...


class NoOpFaqFeedback:
    async def record_feedback(self, _fb: FaqFeedback) -> None:
        return None
```

- [ ] **Step 4: Config** — add `bigquery_faq_feedback_table: str = "faq_feedback"` near the other `bigquery_*` table fields in `Settings`; add a `.env.example` line.

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_faq_feedback.py -q`
Expected: PASS.

- [ ] **Step 6: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/faq_feedback.py src/chatbot/platform/config.py .env.example src/chatbot/features/metrics/test_faq_feedback.py
git commit -m "feat(metrics): add FaqFeedback value + port + NoOp adapter"
```

---

### Task 3: BigQuery FAQ-feedback adapter + schema + v_faq_quality view

**Files:**
- Create: `src/chatbot/features/metrics/faq_schema.py`
- Create: `src/chatbot/features/metrics/faq_feedback_adapter.py`
- Test: `src/chatbot/features/metrics/test_faq_schema.py`, `src/chatbot/features/metrics/test_faq_feedback_adapter.py`

**Interfaces:**
- Consumes: `FaqFeedback`, `bigquery_faq_feedback_table`.
- Produces: `FAQ_FEEDBACK_SCHEMA` (article_id STRING REQUIRED, session_id STRING, helpful BOOL, score INT64, at TIMESTAMP); `faq_view_ddls(project, dataset, table) -> dict[str,str]` with `v_faq_quality` (per article_id: feedback_count, avg_score, helpful_rate). `BigQueryFaqFeedback(settings, *, client=None)` `record_feedback` inserts a row (best-effort, logs on failure). `build_faq_feedback_port(settings) -> FaqFeedbackPort` (BQ when `metrics_provider=="bigquery"`, else `NoOpFaqFeedback`).

- [ ] **Step 1: Write failing tests** — `test_faq_schema.py`:

```python
from chatbot.features.metrics.faq_schema import FAQ_FEEDBACK_SCHEMA, faq_view_ddls


def test_schema_columns() -> None:
    names = {f.name for f in FAQ_FEEDBACK_SCHEMA}
    assert names == {"article_id", "session_id", "helpful", "score", "at"}


def test_faq_quality_view() -> None:
    ddls = faq_view_ddls("p", "d", "faq_feedback")
    assert "v_faq_quality" in ddls
    sql = ddls["v_faq_quality"]
    assert "article_id" in sql and "helpful_rate" in sql and "avg_score" in sql
```

`test_faq_feedback_adapter.py`:

```python
import pytest

from chatbot.features.metrics.faq_feedback import FaqFeedback
from chatbot.features.metrics.faq_feedback_adapter import BigQueryFaqFeedback
from datetime import UTC, datetime


@pytest.mark.asyncio
async def test_bq_adapter_inserts_row() -> None:
    inserted: list[dict[str, object]] = []

    class _Client:
        def insert_rows_json(self, table: str, rows: list[dict[str, object]]) -> list[object]:
            inserted.extend(rows)
            return []  # empty = no errors

    settings = _settings_stub()  # metrics_provider="bigquery", bigquery_* set
    adapter = BigQueryFaqFeedback(settings, client=_Client())
    await adapter.record_feedback(
        FaqFeedback("A1", "sim-1", helpful=True, score=4, at=datetime.now(UTC))
    )
    assert inserted[0]["article_id"] == "A1" and inserted[0]["helpful"] is True
```

(Build `_settings_stub` with a `SimpleNamespace` carrying `bigquery_project_id`/`bigquery_dataset`/`bigquery_faq_feedback_table`, mirroring how `test_query_adapter.py`/`test_qa_adapter.py` stub settings — copy their approach.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_faq_schema.py src/chatbot/features/metrics/test_faq_feedback_adapter.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `faq_schema.py`**

```python
"""BigQuery schema + view DDL for FAQ feedback."""

# ruff: noqa: S608

from __future__ import annotations

from google.cloud import bigquery

FAQ_FEEDBACK_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("article_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING"),
    bigquery.SchemaField("helpful", "BOOL"),
    bigquery.SchemaField("score", "INT64"),
    bigquery.SchemaField("at", "TIMESTAMP"),
]


def faq_view_ddls(project: str, dataset: str, table: str = "faq_feedback") -> dict[str, str]:
    fq = f"`{project}.{dataset}.{table}`"
    return {
        "v_faq_quality": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_faq_quality` AS "
            f"SELECT article_id, COUNT(*) AS feedback_count, AVG(score) AS avg_score, "
            f"SAFE_DIVIDE(COUNTIF(helpful), COUNT(*)) AS helpful_rate "
            f"FROM {fq} GROUP BY article_id"
        ),
    }
```

- [ ] **Step 4: Implement `faq_feedback_adapter.py`** (mirror `qa_adapter.py`):

```python
"""BigQuery FAQ-feedback adapter + factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.faq_feedback import FaqFeedback, FaqFeedbackPort, NoOpFaqFeedback

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class BigQueryFaqFeedback:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._table = (
            f"{settings.bigquery_project_id}.{settings.bigquery_dataset}."
            f"{settings.bigquery_faq_feedback_table}"
        )
        self._client = client or bigquery.Client(project=settings.bigquery_project_id)

    async def record_feedback(self, fb: FaqFeedback) -> None:
        try:
            errors = self._client.insert_rows_json(
                self._table,
                [
                    {
                        "article_id": fb.article_id,
                        "session_id": fb.session_id,
                        "helpful": fb.helpful,
                        "score": fb.score,
                        "at": fb.at.isoformat(),
                    }
                ],
            )
            if errors:
                _log.error("faq_feedback_insert_errors", errors=errors)
        except Exception as e:  # best-effort: feedback must never break the request
            _log.error("faq_feedback_insert_failed", error=str(e))


def build_faq_feedback_port(settings: Settings) -> FaqFeedbackPort:
    if settings.metrics_provider == "bigquery":
        try:
            return BigQueryFaqFeedback(settings)
        except Exception as e:
            _log.error("faq_feedback_init_failed_falling_back_to_noop", error=str(e))
    return NoOpFaqFeedback()
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_faq_schema.py src/chatbot/features/metrics/test_faq_feedback_adapter.py -q`
Expected: PASS.

- [ ] **Step 6: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/faq_schema.py src/chatbot/features/metrics/faq_feedback_adapter.py src/chatbot/features/metrics/test_faq_schema.py src/chatbot/features/metrics/test_faq_feedback_adapter.py
git commit -m "feat(metrics): add BigQuery FAQ-feedback adapter, schema, v_faq_quality view"
```

---

### Task 4: POST /kb/feedback

**Files:**
- Create: `src/chatbot/features/metrics/faq_router.py`
- Test: `src/chatbot/features/metrics/test_faq_router.py`

**Interfaces:**
- Consumes: `FaqFeedbackPort`, `settings.qa_api_key`.
- Produces: `build_faq_router(faq_port, settings) -> APIRouter` with `POST /kb/feedback` body `{article_id:str, session_id:str="", helpful:bool, score:int (Field ge=1 le=5)}`; auth via `x-api-key` vs `settings.qa_api_key` (`hmac.compare_digest`, 401 on mismatch, mirroring `qa_router.py`); records `FaqFeedback(..., at=datetime.now(UTC))`; returns `{"status":"ok"}`.

- [ ] **Step 1: Write failing test** — `test_faq_router.py` (mirror `test_qa_router.py`): build a fake `FaqFeedbackPort` recording calls; assert (a) valid `x-api-key` + body → 200 and one recorded feedback with `score`/`helpful`; (b) missing/wrong key → 401; (c) `score=9` → 422 (validation).

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_faq_router.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `faq_router.py`** (mirror `qa_router.py`'s auth):

```python
"""POST /kb/feedback — record an agent's FAQ-quality feedback (auth'd)."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.metrics.faq_feedback import FaqFeedback

if TYPE_CHECKING:
    from chatbot.features.metrics.faq_feedback import FaqFeedbackPort
    from chatbot.platform.config import Settings


class FaqFeedbackRequest(BaseModel):
    article_id: str
    session_id: str = ""
    helpful: bool
    score: int = Field(ge=1, le=5)


def build_faq_router(faq_port: FaqFeedbackPort, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["kb"])

    @router.post("/kb/feedback")
    async def feedback(
        payload: FaqFeedbackRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        key = settings.qa_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode("utf-8"), key.encode("utf-8"))
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        await faq_port.record_feedback(
            FaqFeedback(
                article_id=payload.article_id,
                session_id=payload.session_id,
                helpful=payload.helpful,
                score=payload.score,
                at=datetime.now(UTC),
            )
        )
        return {"status": "ok"}

    return router
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_faq_router.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/faq_router.py src/chatbot/features/metrics/test_faq_router.py
git commit -m "feat(metrics): add authed POST /kb/feedback endpoint"
```

---

### Task 5: Zendesk sidebar app scaffold

**Files:**
- Create: `apps/zendesk-agent-app/manifest.json`
- Create: `apps/zendesk-agent-app/assets/iframe.html`
- Create: `apps/zendesk-agent-app/README.md`

**Interfaces:** static ZAF app; no automated tests. Consumes `GET /kb/suggest` + `POST /kb/feedback`.

- [ ] **Step 1: `manifest.json`** — minimal ZAF v2 manifest with a `ticket_sidebar` location pointing at `assets/iframe.html`, and a `settings`/`requirements` block minimal. Include a `frameworkVersion` and a settable `backendBaseUrl` parameter.

```json
{
  "name": "Proton Agent-Assist FAQ",
  "author": { "name": "PRO-NET" },
  "defaultLocale": "en",
  "location": { "support": { "ticket_sidebar": "assets/iframe.html" } },
  "version": "1.0.0",
  "frameworkVersion": "2.0",
  "parameters": [
    { "name": "backendBaseUrl", "type": "text", "required": true,
      "default": "http://localhost:8000" },
    { "name": "apiKey", "type": "text", "secure": true, "required": false }
  ]
}
```

- [ ] **Step 2: `assets/iframe.html`** — vanilla JS (via ZAF `client`) that: reads the ticket's latest comment / subject as the query, calls `GET {backendBaseUrl}/kb/suggest?q=…`, renders each suggestion (title + snippet + "Insert" + 👍/👎), where 👍/👎 POST `/kb/feedback` with the `x-api-key`. Keep it a single self-contained file with inline `<script>`/`<style>`; no build step. This is a functional scaffold, not polished UI.

- [ ] **Step 3: `README.md`** — how to zip + upload as a private Zendesk app, the two required settings (`backendBaseUrl`, `apiKey`), and the CORS note (the backend must allow the Zendesk app origin for the fetch calls — reference `platform/config.py` CORS).

- [ ] **Step 4: Sanity check** — `python -c "import json; json.load(open('apps/zendesk-agent-app/manifest.json'))"` parses cleanly. Note in the report that the app is a scaffold (manual Zendesk upload; no automated test).

- [ ] **Step 5: Commit**

```bash
git add apps/zendesk-agent-app/
git commit -m "feat(zendesk-app): thin agent-assist FAQ sidebar scaffold"
```

---

### Task 6: Wiring + full suite + docs

**Files:**
- Modify: `src/chatbot/main.py`
- Create: `docs/dashboards/agent-assist-faq.md`
- Test: existing wiring/integration test (extend)

**Interfaces:** Consumes `build_kb_suggest_router`, `build_faq_router`, `build_faq_feedback_port`, and the existing `knowledge_port`.

- [ ] **Step 1: Wire in `main.py`** — register the two routers. In `_wire_metrics_features` (or wherever `knowledge_port` is available for the chat side), add:
  - `app.include_router(build_kb_suggest_router(knowledge_port))` — use the `knowledge_port` already constructed for the chat feature (find where it's built; grep `KnowledgePort`/`knowledge_port`/`build_knowledge` in main.py).
  - `faq_port = build_faq_feedback_port(settings)` then `app.include_router(build_faq_router(faq_port, settings))`.
  Add the imports. Match the surrounding wiring style.

- [ ] **Step 2: Boot + smoke** — extend the app-boot integration test: `GET /kb/suggest?q=battery` → 200 with a `suggestions` key; `POST /kb/feedback` without `x-api-key` → 401. Reuse the boot harness (force `HANDOFF_STORE=memory` + `get_settings.cache_clear()` pattern if it boots the full app, matching `test_bootstrap_whatsapp.py`).

Run: `.venv/bin/pytest src/chatbot -k "wiring or main or kb or faq" -q`
Expected: PASS.

- [ ] **Step 3: Full suite + gates**

Run: `.venv/bin/pytest src/ -q` → all green.
Run: `.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict` → 1 ruff / 3 mypy baseline only.

- [ ] **Step 4: Docs** — `docs/dashboards/agent-assist-faq.md`: `GET /kb/suggest` (params, POC-open, KnowledgePort reuse), `POST /kb/feedback` (auth via `x-api-key`/`qa_api_key`, score 1-5), `v_faq_quality` view, the Zendesk sidebar app (link to `apps/zendesk-agent-app/README.md`), and the `metrics_provider`/`bigquery_faq_feedback_table` config. Note the deferred enhancement: derive `/kb/suggest` query from `session_id` (currently `?q=` only).

- [ ] **Step 5: Commit**

```bash
git add src/chatbot/main.py docs/dashboards/agent-assist-faq.md
git commit -m "feat(chat): wire kb-suggest + faq-feedback routers; agent-assist docs"
```

---

## Self-review

**Spec coverage (Item 5 of the design):**
- Real-time keyword-matched suggestions reusing KnowledgePort → Task 1 (`GET /kb/suggest`). ✓
- One-click reference (title+snippet+url for the agent to insert) → Task 1 payload + Task 5 app "Insert". ✓
- Feedback/scoring → Tasks 2-4 (`faq_feedback` pipeline + `POST /kb/feedback`). ✓
- `v_faq_quality` view → Task 3. ✓
- Zendesk sidebar app → Task 5. ✓
- Wiring + docs → Task 6. ✓

**Placeholder scan:** Task 3/4 tests reference "mirror `test_qa_adapter.py`/`test_qa_router.py`" for the settings/port stub style — the concrete assertions are given. Task 5 iframe.html is described (functional scaffold) rather than pasted verbatim, since it's a static non-tested artifact; the required behaviors (query from ticket, call suggest, render, 👍/👎 → feedback) are explicit. No TBD/TODO in tested code.

**Type consistency:** `FaqFeedback` fields (article_id/session_id/helpful/score/at) identical across `faq_feedback.py`, the BQ adapter row dict, the schema columns, and the router. `build_faq_feedback_port`/`build_faq_router`/`build_kb_suggest_router` names consistent across creation + wiring.

## Deferred / follow-ups
- Derive `/kb/suggest` query from `session_id` (session-state transcript) — currently `?q=` only.
- Polish the ZAF app UI; add auth to `/kb/suggest` if it leaves POC.
- This is the LAST short-term item; after merge the short-term roadmap (Items 1-5) is complete.
