# Bot-Metrics Dashboard — Phase 3 (NPS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Net Promoter Score (NPS) metric — a shared `record_nps_on_ticket` core, a web `POST /chat/nps` endpoint, and an `nps_score` column + `v_nps` view on the Phase-1 `conversations` table — mirroring the existing CSAT design.

**Architecture:** Tag-based, riding the Phase-1 Zendesk→BigQuery sync. A web POST records a 0–10 score onto the conversation's Zendesk ticket as an `nps_<score>` tag + comment via a channel-agnostic recorder; the Phase-1 sync parses the tag into a new `nps_score` column; a `v_nps` view derives respondents/promoters/passives/detractors and the NPS number. Parallels CSAT exactly (`csat.py` / `record_csat` / `/chat/csat` / `v_csat`).

**Tech Stack:** Python 3.12, FastAPI, pydantic, google-cloud-bigquery (Phase 1), structlog, pytest / pytest-asyncio (`asyncio_mode="auto"`), ruff, mypy --strict.

## Global Constraints

- Run all backend commands from `apps/backend/`. Quality gates per task: `.venv/bin/ruff format .`, `.venv/bin/ruff check . --fix`, `.venv/bin/mypy src/ --strict`, `.venv/bin/pytest src/`.
- `from __future__ import annotations` at the top of every new module (repo convention).
- **mypy baseline:** the repo has exactly **3 tolerated pre-existing** `mypy --strict` errors (google.adk `Event` export in `firestore_session_service.py` and `test_service.py`, and one `no-any-return` in `service.py`). Every task must keep mypy at `Found 3 errors` — ZERO new.
- **ruff baseline:** there is **1 tolerated pre-existing** ruff error (`PLC0415` in `vertex_search.py`). Add no new lint.
- **NPS scale & buckets (exact):** 0–10; detractors `nps_score <= 6`, passives `7–8`, promoters `>= 9`; `NPS = (%promoters − %detractors)`.
- **Tag format (exact):** `nps_<score>` for scores 0–10 (e.g. `nps_0` … `nps_10`). Comment copy (exact): `📣 Net Promoter Score: {score}/10 (via {channel})`.
- **Decoupling:** `record_nps` must NOT touch handoff / `awaiting_survey` / `handoff_triggered` / resume-AI state (unlike `record_csat`).
- DRY, YAGNI, TDD, frequent commits. Build only what each task specifies.

---

### Task 1: `record_nps_on_ticket` shared core

**Files:**
- Create: `apps/backend/src/chatbot/features/chat/nps.py`
- Test: `apps/backend/src/chatbot/features/chat/test_nps_helper.py`

**Interfaces:**
- Consumes: `ConversationLogPort` (existing — has `append_conversation_comment` + `add_ticket_tag`).
- Produces: `async def record_nps_on_ticket(port: ConversationLogPort, ticket_id: str, score: int, channel: str) -> None` — best-effort. Used by Task 2.

- [ ] **Step 1: Write the failing test**

Create `test_nps_helper.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock

from chatbot.features.chat.nps import record_nps_on_ticket


async def test_record_nps_on_ticket_posts_comment_and_tag() -> None:
    port = AsyncMock()
    await record_nps_on_ticket(port, "T-7", 9, "web")
    port.append_conversation_comment.assert_awaited_once_with(
        "T-7", "📣 Net Promoter Score: 9/10 (via web)"
    )
    port.add_ticket_tag.assert_awaited_once_with("T-7", "nps_9")


async def test_record_nps_on_ticket_swallows_errors() -> None:
    port = AsyncMock()
    port.append_conversation_comment.side_effect = RuntimeError("zendesk down")
    # must not raise
    await record_nps_on_ticket(port, "T-7", 3, "web")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_nps_helper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.chat.nps'`.

- [ ] **Step 3: Write minimal implementation**

Create `nps.py` (mirrors `csat.py`):

```python
"""Shared NPS recording: the channel-agnostic Zendesk write (comment + tag).

Mirrors csat.py. Posts the identical `nps_<score>` tag + comment that the
metrics dashboard's Phase-1 sync reads (parsed into the conversations.nps_score
column and surfaced via the v_nps view).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.features.chat.ports import ConversationLogPort

_log = structlog.get_logger(__name__)


async def record_nps_on_ticket(
    port: ConversationLogPort, ticket_id: str, score: int, channel: str
) -> None:
    """Post the NPS comment + `nps_<score>` tag to a ticket. Best-effort."""
    try:
        await port.append_conversation_comment(
            ticket_id, f"📣 Net Promoter Score: {score}/10 (via {channel})"
        )
        await port.add_ticket_tag(ticket_id, f"nps_{score}")
    except Exception as e:
        _log.error("record_nps_on_ticket_failed", ticket_id=ticket_id, error=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_nps_helper.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/nps.py src/chatbot/features/chat/test_nps_helper.py
git commit -m "feat(metrics): record_nps_on_ticket shared NPS recorder"
```

---

### Task 2: `record_nps` service method

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/service.py`
- Test: `apps/backend/src/chatbot/features/chat/test_nps.py`

**Interfaces:**
- Consumes: `record_nps_on_ticket` (Task 1); the existing `OrchestratorService` session machinery (`self._adk_sessions`, `self._conversation_log_port`, `self._persist_session_state`).
- Produces: `async def record_nps(self, session_id: str, score: int, channel: str = "web") -> bool`. Used by Task 3.

- [ ] **Step 1: Write the failing test**

Create `test_nps.py` (reuses the same doubles as `test_csat.py`):

```python
from __future__ import annotations

import pytest

from chatbot.features.chat.test_capture_conversation import (  # reuse doubles
    _FakeLog,
    _LiveSessions,
    _orchestrator,
)


@pytest.mark.asyncio
async def test_record_nps_writes_comment_tag_and_state() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot",
        user_id="sim-abc",
        session_id="sim-abc",
        state={"conversation_ticket_id": "T1"},
    )

    ok = await orch.record_nps("sim-abc", 9, channel="web")

    assert ok is True
    assert session.state["nps_score"] == 9
    assert any("9/10" in text for _t, text, _s in log.appended)
    assert ("T1", "nps_9") in log.tags


@pytest.mark.asyncio
async def test_record_nps_returns_false_when_no_session() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    assert await orch.record_nps("missing-session", 8) is False


@pytest.mark.asyncio
async def test_record_nps_does_not_touch_handoff_state() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot",
        user_id="sim-xyz",
        session_id="sim-xyz",
        state={
            "conversation_ticket_id": "T2",
            "whatsapp_handoff_state": "paused",
            "handoff_triggered": True,
        },
    )

    await orch.record_nps("sim-xyz", 10, channel="web")

    # NPS is decoupled: handoff/survey state must be left exactly as it was.
    assert session.state["whatsapp_handoff_state"] == "paused"
    assert session.state["handoff_triggered"] is True
    assert session.state["nps_score"] == 10


@pytest.mark.asyncio
async def test_record_nps_state_survives_log_failure() -> None:
    class _FailLog(_FakeLog):
        async def append_conversation_comment(
            self, ticket_id: str, text: str, status: str | None = None
        ) -> None:
            raise RuntimeError("Zendesk connection failed")

    log = _FailLog()
    sessions = _LiveSessions()
    orch = _orchestrator(log, sessions)
    session = await sessions.create_session(
        app_name="chatbot",
        user_id="sim-fail",
        session_id="sim-fail",
        state={"conversation_ticket_id": "T3"},
    )

    ok = await orch.record_nps("sim-fail", 7, channel="web")

    assert ok is True
    assert session.state["nps_score"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_nps.py -v`
Expected: FAIL — `AttributeError: 'OrchestratorService' object has no attribute 'record_nps'`.

- [ ] **Step 3: Add the import and the method**

In `service.py`, add the import next to the existing CSAT import (`from chatbot.features.chat.csat import record_csat_on_ticket`, line 20):

```python
from chatbot.features.chat.nps import record_nps_on_ticket
```

Add the method immediately after `record_csat` (after the `return True` that closes `record_csat`, ~line 589):

```python
    async def record_nps(self, session_id: str, score: int, channel: str = "web") -> bool:
        """Record an NPS score: comment + `nps_<score>` tag + session state.

        Decoupled from the handoff/CSAT survey flow — does NOT touch handoff
        state. Tags the conversation ticket only when one exists (web `sim-`
        sessions without a ticket store the score in state but post no tag).
        """
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return False
        state = session.state
        ticket_id = state.get("conversation_ticket_id")
        if ticket_id:
            await record_nps_on_ticket(self._conversation_log_port, ticket_id, score, channel)
        state["nps_score"] = score
        await self._persist_session_state(session)
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_nps.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/service.py src/chatbot/features/chat/test_nps.py
git commit -m "feat(metrics): OrchestratorService.record_nps (decoupled from handoff state)"
```

---

### Task 3: `POST /chat/nps` web endpoint

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py`
- Test: `apps/backend/src/chatbot/features/chat/test_chat_nps_endpoint.py`

**Interfaces:**
- Consumes: `OrchestratorService.record_nps` (Task 2).
- Produces: `NpsRequest{session_id:str, score:int (0–10)}` + `POST /chat/nps`. No downstream task depends on this.

- [ ] **Step 1: Write the failing test**

Create `test_chat_nps_endpoint.py` (mirrors `test_chat_csat_endpoint.py`):

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from chatbot.features.chat.router import build_chat_router
from chatbot.platform.config import get_settings
from chatbot.platform.server import create_app


def _client(orch: MagicMock) -> TestClient:
    app = create_app(get_settings())
    app.include_router(build_chat_router(orch))
    return TestClient(app)


def test_nps_endpoint_records_web_score() -> None:
    orch = MagicMock()
    orch.record_nps = AsyncMock(return_value=True)
    client = _client(orch)

    res = client.post("/chat/nps", json={"session_id": "sim-abc", "score": 9})

    assert res.status_code == 200
    orch.record_nps.assert_awaited_once_with("sim-abc", 9, channel="web")


def test_nps_endpoint_rejects_too_high() -> None:
    orch = MagicMock()
    client = _client(orch)
    res = client.post("/chat/nps", json={"session_id": "sim-abc", "score": 11})
    assert res.status_code == 422


def test_nps_endpoint_rejects_negative() -> None:
    orch = MagicMock()
    client = _client(orch)
    res = client.post("/chat/nps", json={"session_id": "sim-abc", "score": -1})
    assert res.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_chat_nps_endpoint.py -v`
Expected: FAIL — `test_nps_endpoint_records_web_score` returns 404 (route not registered).

- [ ] **Step 3a: Add the request model**

In `router.py`, add immediately after `CsatRequest` (the class ending at `score: int = Field(ge=1, le=5)`, line 59):

```python
class NpsRequest(BaseModel):
    session_id: str
    score: int = Field(ge=0, le=10)
```

- [ ] **Step 3b: Register the route**

In `router.py`, after the `/chat/csat` registration line (`self.router.add_api_route("/chat/csat", self.chat_csat, methods=["POST"])`, line 243), add:

```python
        self.router.add_api_route("/chat/nps", self.chat_nps, methods=["POST"])
```

- [ ] **Step 3c: Add the handler**

In `router.py`, add immediately after the `chat_csat` method (after its `return {...}` block, ~line 739):

```python
    async def chat_nps(self, payload: NpsRequest) -> dict[str, str]:
        await self.orchestrator.record_nps(payload.session_id, payload.score, channel="web")
        return {
            "status": "ok",
            "message": "Thank you for your feedback!",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_chat_nps_endpoint.py -v`
Expected: PASS (all three tests — the records test plus both 422 validations).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/router.py src/chatbot/features/chat/test_chat_nps_endpoint.py
git commit -m "feat(metrics): POST /chat/nps web endpoint (0-10, 422 on out-of-range)"
```

---

### Task 4: parse `nps_<N>` tag into `ConversationRow.nps_score`

**Files:**
- Modify: `apps/backend/src/chatbot/features/metrics/mapping.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_mapping.py`

**Interfaces:**
- Produces: `ConversationRow` gains an `nps_score: int | None` field; `map_ticket_to_row` populates it from an `nps_<N>` tag and keeps a ticket that has only an nps tag. Used by Task 5's sync row dict.

- [ ] **Step 1: Write the failing test**

Add to `test_mapping.py`:

```python
import pytest as _pytest_unused  # noqa: F401  (pytest already imported at top)


@_pytest_unused.mark.parametrize(
    "tag,expected",
    [("nps_0", 0), ("nps_6", 6), ("nps_7", 7), ("nps_9", 9), ("nps_10", 10)],
)
def test_nps_from_tag(tag: str, expected: int) -> None:
    row = map_ticket_to_row(_ticket(tags=[tag]))
    assert row is not None and row.nps_score == expected


def test_nps_tag_out_of_range_ignored() -> None:
    row = map_ticket_to_row(_ticket(tags=["nps_11"]))
    assert row is not None and row.nps_score is None


def test_no_nps_tag_is_none() -> None:
    row = map_ticket_to_row(_ticket(tags=["foo"]))
    assert row is not None and row.nps_score is None


def test_keep_nps_only_ticket_even_without_external_id() -> None:
    row = map_ticket_to_row({"id": 3, "status": "solved", "tags": ["nps_8"]})
    assert row is not None and row.channel == "Other" and row.nps_score == 8
```

Note: if your repo's `test_mapping.py` already imports `pytest` at the top (it does), use `@pytest.mark.parametrize` directly and delete the `_pytest_unused` alias line above — it's only there to make this snippet self-contained.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -k nps -v`
Expected: FAIL — `AttributeError: 'ConversationRow' object has no attribute 'nps_score'`.

- [ ] **Step 3a: Add the tag regex and parser**

In `mapping.py`, add the regex next to `_CSAT_TAG` (after `_CSAT_TAG = re.compile(r"^csat_([1-5])$")`):

```python
# 10|[0-9] ordering matters so "nps_10" matches "10", not a truncated "1".
_NPS_TAG = re.compile(r"^nps_(10|[0-9])$")
```

Add the parser next to `_csat_from_tags`:

```python
def _nps_from_tags(tags: list[str]) -> int | None:
    for tag in tags:
        m = _NPS_TAG.match(tag)
        if m:
            return int(m.group(1))
    return None
```

- [ ] **Step 3b: Add the field and populate it**

In `mapping.py`, add `nps_score` to `ConversationRow` (after `csat_score: int | None`):

```python
    csat_score: int | None
    nps_score: int | None
```

In `map_ticket_to_row`, compute the nps value, widen the skip condition, and pass the field. Replace the body from the `csat = ...` line through the `return ConversationRow(...)`:

```python
    csat = _csat_from_tags(tags)
    nps = _nps_from_tags(tags)
    if external_id_str is None and csat is None and nps is None:
        return None
    status = str(ticket.get("status") or "")
    created = ticket.get("created_at")
    updated = ticket.get("updated_at")
    return ConversationRow(
        conversation_id=str(ticket.get("id")),
        channel=channel_from_external_id(external_id_str),
        created_at=str(created) if created else None,
        updated_at=str(updated) if updated else None,
        status=status,
        resolved_by=_resolved_by(status),
        csat_score=csat,
        nps_score=nps,
    )
```

- [ ] **Step 3c: Update the existing direct-construction test**

`test_basic_fields_pass_through` constructs a `ConversationRow` literally and will now be missing `nps_score`. Add `nps_score=None` to that expected row:

```python
    assert row == ConversationRow(
        conversation_id="55",
        channel="WhatsApp",
        created_at="2026-06-21T09:14:00Z",
        updated_at="2026-06-21T09:31:00Z",
        status="solved",
        resolved_by="bot",
        csat_score=None,
        nps_score=None,
    )
```

- [ ] **Step 4: Run the full mapping test file to verify it passes**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -v`
Expected: PASS (new nps tests + all existing csat/channel/skip tests, including the updated `test_basic_fields_pass_through`).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/test_mapping.py
git commit -m "feat(metrics): parse nps_<N> tag into ConversationRow.nps_score"
```

---

### Task 5: `nps_score` column + `v_nps` view + sync load

**Files:**
- Modify: `apps/backend/src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `apps/backend/src/chatbot/features/metrics/sync.py`
- Test: `apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py`

**Interfaces:**
- Consumes: `ConversationRow.nps_score` (Task 4).
- Produces: `CONVERSATIONS_SCHEMA` gains `nps_score INT64`; `view_ddls` gains a `v_nps` key; `load_conversations` writes `nps_score` per row.

- [ ] **Step 1: Write the failing test**

Add to `test_bigquery_schema.py`:

```python
def test_schema_includes_nps_score() -> None:
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert "nps_score" in by_name
    assert by_name["nps_score"].field_type == "INT64"


def test_v_nps_view_present_with_buckets_and_formula() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert "v_nps" in ddls
    sql = ddls["v_nps"]
    assert "`proj.ds.v_nps`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "COUNTIF(nps_score >= 9)" in sql  # promoters
    assert "COUNTIF(nps_score BETWEEN 7 AND 8)" in sql  # passives
    assert "nps_score <= 6" in sql  # detractors
    assert "SAFE_DIVIDE" in sql and "* 100 AS nps" in sql
```

(`CONVERSATIONS_SCHEMA` and `view_ddls` are already imported at the top of this test file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -k nps -v`
Expected: FAIL — `nps_score` not in schema / `v_nps` not in `view_ddls`.

- [ ] **Step 3a: Add the schema field**

In `bigquery_schema.py`, add to `CONVERSATIONS_SCHEMA` after the `csat_score` field:

```python
    bigquery.SchemaField("csat_score", "INT64"),
    bigquery.SchemaField("nps_score", "INT64"),
```

- [ ] **Step 3b: Add the `v_nps` view**

In `bigquery_schema.py`, add this entry to the dict returned by `view_ddls` (after the `v_csat` entry):

```python
        "v_nps": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_nps` AS "
            f"SELECT channel, "
            f"COUNTIF(nps_score IS NOT NULL) AS respondents, "
            f"COUNTIF(nps_score >= 9) AS promoters, "
            f"COUNTIF(nps_score BETWEEN 7 AND 8) AS passives, "
            f"COUNTIF(nps_score IS NOT NULL AND nps_score <= 6) AS detractors, "
            f"SAFE_DIVIDE("
            f"COUNTIF(nps_score >= 9) - COUNTIF(nps_score IS NOT NULL AND nps_score <= 6), "
            f"COUNTIF(nps_score IS NOT NULL)) * 100 AS nps "
            f"FROM {fq} GROUP BY channel"
        ),
```

- [ ] **Step 3c: Write `nps_score` in the sync load**

In `sync.py`, inside `load_conversations`'s `json_rows` comprehension, add `nps_score` after `csat_score` (keep it aligned with `CONVERSATIONS_SCHEMA`):

```python
            "csat_score": r.csat_score,
            "nps_score": r.nps_score,
            "synced_at": now,
```

(This 1-line addition is exercised end-to-end by the documented live smoke; the unit tests above lock the schema + view, and Task 4's tests lock the `r.nps_score` source.)

- [ ] **Step 4: Run the schema tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -v`
Expected: PASS (new nps tests + existing schema/view tests).

- [ ] **Step 5: Quality gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/sync.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): nps_score column + v_nps view + sync load"
```

---

### Task 6: Docs — changelog, README, Looker NPS tile note

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `docs/dashboards/looker-bot-metrics-phase1.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a changelog entry**

In `CHANGELOG.md`, add a Phase-3 entry near the top, mirroring the existing "### Added — Bot-metrics dashboard Phase 1 (2026-06-29)" block. Summarize: NPS recording core (`record_nps_on_ticket`, `nps_<score>` tag, 0–10), the web `POST /chat/nps` endpoint, and the dashboard addition (`conversations.nps_score` column + `v_nps` view with promoter/passive/detractor buckets and `NPS = %promoters − %detractors`). Note that WhatsApp/email/phone NPS prompts and a frontend widget are deferred.

- [ ] **Step 2: Update the README metrics section**

In `README.md`, extend the existing Metrics/Dashboard section with a brief NPS note: a 0–10 score recorded via `POST /chat/nps` (`{session_id, score}`), surfaced through the `v_nps` view; reuses the existing `BIGQUERY_*` config (no new env vars). Keep it to a few lines, matching the section's style.

- [ ] **Step 3: Add the NPS tile to the Looker guide**

In `docs/dashboards/looker-bot-metrics-phase1.md`, add a short NPS subsection: create a data source from the `v_nps` view and build an **NPS** scorecard (the `nps` column) plus an optional promoters/passives/detractors breakdown, per channel. Mention the live-enable step: submit scores via `POST /chat/nps` against a session that has a ticket, then re-run `scripts/sync_zendesk_metrics.py` (the `WRITE_TRUNCATE` reload adds the `nps_score` column + `v_nps` view).

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md README.md docs/dashboards/looker-bot-metrics-phase1.md
git commit -m "docs(metrics): phase 3 NPS changelog, readme, Looker tile note"
```

---

## Self-Review

**Spec coverage:**
- `features/chat/nps.py` `record_nps_on_ticket` → Task 1 ✓
- `record_nps` service method (decoupled from handoff state) → Task 2 ✓
- `NpsRequest` + `POST /chat/nps` (0–10, 422 on out-of-range) → Task 3 ✓
- `mapping.py` `nps_score` + `_nps_from_tags` (`^nps_(10|[0-9])$`) + keep-if-nps → Task 4 ✓
- `bigquery_schema.py` `nps_score INT64` + `v_nps` view (buckets + NPS formula) → Task 5 ✓
- `sync.py` row dict includes `nps_score` → Task 5 ✓
- Testing (core, service incl. no-session + handoff-untouched, endpoint incl. 422s, mapping incl. nps-only-kept, schema/view, sync row) → Tasks 1–5 ✓
- Docs / live smoke documented → Task 6 ✓
- Out of scope (WA/email/phone prompts, frontend widget, per-turn NPS, parse_nps, sync scheduling) → untouched ✓
- No new config → confirmed (Task list adds none) ✓

**Type consistency:** `record_nps_on_ticket(port, ticket_id, score, channel) -> None` (Task 1) is called with the same args in Task 2. `record_nps(session_id, score, channel="web") -> bool` (Task 2) is called by the endpoint in Task 3 with `channel="web"` and asserted in its test. `ConversationRow.nps_score: int | None` (Task 4) is read as `r.nps_score` in Task 5's sync load and matches the `nps_score INT64` schema field (Task 5). `_nps_from_tags` / `_NPS_TAG` naming consistent within Task 4. The `v_nps` column names (`respondents`/`promoters`/`passives`/`detractors`/`nps`) are asserted in Task 5's test.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the only prose-only task (Task 6) is documentation, where prose is the deliverable.
