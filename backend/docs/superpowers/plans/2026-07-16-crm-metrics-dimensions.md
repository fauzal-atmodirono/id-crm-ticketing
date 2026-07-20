# CRM Metrics Dimensions Implementation Plan (Short-Term Item 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the division / agent / PIC / SLA / reopen / timing dimensions in the BigQuery `conversations` pipeline so the BRD block-F reports (by-division, dept/PIC rankings, SLA achievement, CRR, resolution-time, agent-NPS) become possible.

**Architecture:** Extend the pure `map_ticket_to_row` mapping to derive new columns from Zendesk ticket tags + sideloaded `metric_set`; widen the BigQuery schema and the `load_conversations` writer; sideload `metric_sets` in the fetch; emit classification tags at ticket creation; add report views. All new columns are NULLABLE so existing rows/views stay valid. The full sync is `WRITE_TRUNCATE`, so a single sync run backfills every column — no separate backfill job.

**Tech Stack:** Python 3.12, `google-cloud-bigquery`, `httpx`, `pytest`, `ruff`, `mypy --strict`. Run all backend commands from `apps/backend/`.

## Global Constraints

- Run from `apps/backend/`; venv at `.venv/`.
- Gates before each commit: `.venv/bin/ruff format .` · `.venv/bin/ruff check . --fix` · `.venv/bin/mypy src/ --strict` · `.venv/bin/pytest src/`.
- All new `conversations` columns are BigQuery mode NULLABLE (default). Existing views and endpoints must remain unchanged.
- `ConversationRow` new fields get defaults so unrelated construction sites keep compiling.
- Conventional commits: `feat(metrics): …`. Do not push; commit locally on branch `feature/crm-enhancement-short-term`.

---

### Task 1: Dimension tags → `ConversationRow`

**Files:**
- Modify: `src/chatbot/features/metrics/mapping.py`
- Test: `src/chatbot/features/metrics/test_mapping.py`

**Interfaces:**
- Produces: `ConversationRow` gains fields `category: str | None`, `subcategory: str | None`, `division: str | None`, `department: str | None`, `pic: str | None`, `agent_id: str | None`, `sla_minutes: int | None`, `sla_deadline: str | None` (all default `None`). New module-level `CATEGORY_TO_DIVISION: dict[str, str]`.

- [ ] **Step 1: Write failing tests** — append to `test_mapping.py`:

```python
from chatbot.features.metrics.mapping import CATEGORY_TO_DIVISION


def test_division_derived_from_category_tag() -> None:
    row = map_ticket_to_row(_ticket(tags=["category_aftersales"]))
    assert row is not None and row.category == "aftersales"
    assert row.division == "Aftersales"


def test_subcategory_and_department_and_pic_tags() -> None:
    row = map_ticket_to_row(
        _ticket(tags=["subcat_battery", "dept_service", "pic_alice"])
    )
    assert row is not None
    assert row.subcategory == "battery"
    assert row.department == "service"
    assert row.pic == "alice"


def test_pic_falls_back_to_assignee_when_no_pic_tag() -> None:
    row = map_ticket_to_row(_ticket(assignee_id=7007, tags=[]))
    assert row is not None and row.agent_id == "7007" and row.pic == "7007"


def test_sla_minutes_and_deadline_from_tag() -> None:
    row = map_ticket_to_row(
        _ticket(tags=["sla_480"], created_at="2026-06-21T09:00:00Z")
    )
    assert row is not None and row.sla_minutes == 480
    assert row.sla_deadline == "2026-06-21T17:00:00+00:00"


def test_dimension_fields_default_none() -> None:
    row = map_ticket_to_row(_ticket(tags=[]))
    assert row is not None
    assert row.category is None and row.division is None and row.sla_minutes is None
```

Also update the existing `test_basic_fields_pass_through` expected row to include the new defaulted fields:

```python
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
        nps_score=None,
    )
```

(The `_ticket` helper already defaults `tags=[]`; add `assignee_id` support by extending the helper base dict with `"assignee_id": None`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -q`
Expected: FAIL (`ImportError: CATEGORY_TO_DIVISION` / `AttributeError: ... 'division'`).

- [ ] **Step 3: Implement in `mapping.py`**

Add imports and constants near the top:

```python
from datetime import UTC, datetime, timedelta

CATEGORY_TO_DIVISION = {
    "apps": "Apps",
    "app": "Apps",
    "sales": "Sales",
    "lead": "Sales",
    "aftersales": "Aftersales",
    "service": "Aftersales",
    "charging": "Charging",
    "charger": "Charging",
}
_CATEGORY_TAG = re.compile(r"^category_(.+)$")
_SUBCAT_TAG = re.compile(r"^subcat_(.+)$")
_DEPT_TAG = re.compile(r"^dept_(.+)$")
_PIC_TAG = re.compile(r"^pic_(.+)$")
_SLA_TAG = re.compile(r"^sla_(\d+)$")


def _first_tag(tags: list[str], pattern: re.Pattern[str]) -> str | None:
    for tag in tags:
        m = pattern.match(tag)
        if m:
            return m.group(1)
    return None


def _sla_deadline(created_at: str | None, sla_minutes: int | None) -> str | None:
    if not created_at or sla_minutes is None:
        return None
    base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return (base + timedelta(minutes=sla_minutes)).astimezone(UTC).isoformat()
```

Add the fields to the dataclass (with defaults):

```python
@dataclass(frozen=True)
class ConversationRow:
    conversation_id: str
    channel: str
    created_at: str | None
    updated_at: str | None
    status: str
    resolved_by: str
    csat_score: int | None
    nps_score: int | None
    category: str | None = None
    subcategory: str | None = None
    division: str | None = None
    department: str | None = None
    pic: str | None = None
    agent_id: str | None = None
    sla_minutes: int | None = None
    sla_deadline: str | None = None
```

In `map_ticket_to_row`, after computing `tags`, derive the fields and pass them to the constructor:

```python
    category = _first_tag(tags, _CATEGORY_TAG)
    subcategory = _first_tag(tags, _SUBCAT_TAG)
    department = _first_tag(tags, _DEPT_TAG)
    sla_raw = _first_tag(tags, _SLA_TAG)
    sla_minutes = int(sla_raw) if sla_raw is not None else None
    assignee = ticket.get("assignee_id")
    agent_id = str(assignee) if assignee else None
    pic = _first_tag(tags, _PIC_TAG) or agent_id
    division = CATEGORY_TO_DIVISION.get(category.lower()) if category else None
```

Add `category=category, subcategory=subcategory, division=division, department=department, pic=pic, agent_id=agent_id, sla_minutes=sla_minutes, sla_deadline=_sla_deadline(str(created) if created else None, sla_minutes)` to the returned `ConversationRow(...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/test_mapping.py
git commit -m "feat(metrics): derive division/category/pic/sla dimensions from ticket tags"
```

---

### Task 2: `metric_set` → timing & reopen fields

**Files:**
- Modify: `src/chatbot/features/metrics/mapping.py`
- Test: `src/chatbot/features/metrics/test_mapping.py`

**Interfaces:**
- Consumes: `ConversationRow` from Task 1.
- Produces: `ConversationRow` gains `first_response_at: str | None`, `resolved_at: str | None`, `reopen_count: int | None` (defaults `None`). Reads `ticket["metric_set"]` (a dict) when present.

- [ ] **Step 1: Write failing tests**

```python
def test_metric_set_timing_and_reopens() -> None:
    row = map_ticket_to_row(
        _ticket(
            created_at="2026-06-21T09:00:00Z",
            metric_set={"solved_at": "2026-06-21T10:30:00Z",
                        "reopens": 2, "reply_time_in_minutes": {"calendar": 15}},
        )
    )
    assert row is not None
    assert row.resolved_at == "2026-06-21T10:30:00Z"
    assert row.reopen_count == 2
    assert row.first_response_at == "2026-06-21T09:15:00+00:00"


def test_no_metric_set_leaves_timing_none() -> None:
    row = map_ticket_to_row(_ticket())
    assert row is not None
    assert row.resolved_at is None and row.reopen_count is None
    assert row.first_response_at is None
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -k metric_set -q`
Expected: FAIL (`AttributeError: ... 'resolved_at'`).

- [ ] **Step 3: Implement**

Add fields to `ConversationRow` (after Task 1 fields):

```python
    first_response_at: str | None = None
    resolved_at: str | None = None
    reopen_count: int | None = None
```

Add a helper and wire it in `map_ticket_to_row`:

```python
def _timing_from_metric_set(
    metric_set: object, created_at: str | None
) -> tuple[str | None, str | None, int | None]:
    if not isinstance(metric_set, dict):
        return (None, None, None)
    resolved_at = metric_set.get("solved_at")
    reopens = metric_set.get("reopens")
    reopen_count = int(reopens) if reopens is not None else None
    first_response_at: str | None = None
    reply = metric_set.get("reply_time_in_minutes")
    if created_at and isinstance(reply, dict) and reply.get("calendar") is not None:
        base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        first_response_at = (
            base + timedelta(minutes=int(reply["calendar"]))
        ).astimezone(UTC).isoformat()
    return (
        str(resolved_at) if resolved_at else None,
        first_response_at,
        reopen_count,
    )
```

In `map_ticket_to_row`: `resolved_at, first_response_at, reopen_count = _timing_from_metric_set(ticket.get("metric_set"), str(created) if created else None)` and pass the three to the constructor.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/test_mapping.py
git commit -m "feat(metrics): derive first-response/resolved/reopen from ticket metric_set"
```

---

### Task 3: Widen BigQuery schema + writer

**Files:**
- Modify: `src/chatbot/features/metrics/bigquery_schema.py`
- Modify: `src/chatbot/features/metrics/sync.py` (`load_conversations`, lines 64-76)
- Test: `src/chatbot/features/metrics/test_bigquery_schema.py`, `src/chatbot/features/metrics/test_sync.py`

**Interfaces:**
- Consumes: `ConversationRow` fields from Tasks 1-2.
- Produces: `CONVERSATIONS_SCHEMA` includes the 11 new NULLABLE columns; `load_conversations` writes them.

- [ ] **Step 1: Write failing test** — append to `test_bigquery_schema.py`:

```python
from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA


def test_conversations_schema_has_dimension_columns() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    for col in [
        "division", "category", "subcategory", "agent_id", "pic", "department",
        "sla_minutes", "sla_deadline", "first_response_at", "resolved_at", "reopen_count",
    ]:
        assert col in names, col
    # new columns must be nullable
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert by_name["division"].mode in ("NULLABLE", "")
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -k dimension -q`
Expected: FAIL (`AssertionError: division`).

- [ ] **Step 3: Implement schema** — append to `CONVERSATIONS_SCHEMA` in `bigquery_schema.py`:

```python
    bigquery.SchemaField("division", "STRING"),
    bigquery.SchemaField("category", "STRING"),
    bigquery.SchemaField("subcategory", "STRING"),
    bigquery.SchemaField("department", "STRING"),
    bigquery.SchemaField("agent_id", "STRING"),
    bigquery.SchemaField("pic", "STRING"),
    bigquery.SchemaField("sla_minutes", "INT64"),
    bigquery.SchemaField("sla_deadline", "TIMESTAMP"),
    bigquery.SchemaField("first_response_at", "TIMESTAMP"),
    bigquery.SchemaField("resolved_at", "TIMESTAMP"),
    bigquery.SchemaField("reopen_count", "INT64"),
```

- [ ] **Step 4: Implement writer** — in `sync.py` `load_conversations`, add the new keys to each dict in the `json_rows` comprehension (after `"nps_score": r.nps_score,`):

```python
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
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py src/chatbot/features/metrics/test_sync.py -q`
Expected: PASS (existing `test_sync` still green — new keys are additive).

- [ ] **Step 6: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/sync.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): add dimension columns to conversations schema + writer"
```

---

### Task 4: Sideload `metric_sets` + assignee in the fetch

**Files:**
- Modify: `src/chatbot/features/metrics/sync.py` (`fetch_tickets`, line 42)
- Test: `src/chatbot/features/metrics/test_sync.py`

**Interfaces:**
- Produces: the incremental-export URL requests `include=metric_sets` so each ticket carries `metric_set`; `assignee_id` is already a native ticket field.

- [ ] **Step 1: Write failing test** — append to `test_sync.py`:

```python
def test_fetch_tickets_sideloads_metric_sets() -> None:
    seen: list[str] = []

    def fake_get_page(url: str) -> dict[str, object]:
        seen.append(url)
        return {"tickets": [], "end_of_stream": True}

    settings = _settings()  # existing helper in this test module
    fetch_tickets(settings, get_page=fake_get_page)
    assert "include=metric_sets" in seen[0]
```

(If `test_sync.py` lacks a `_settings()` helper, construct `Settings` the same way the other tests in the file do — reuse their fixture/among-imports pattern.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_sync.py -k sideload -q`
Expected: FAIL (`assert 'include=metric_sets' in ...`).

- [ ] **Step 3: Implement** — in `fetch_tickets`, change the initial URL (line 42) to:

```python
    url: str | None = (
        f"https://{sub}.zendesk.com/api/v2/incremental/tickets.json"
        f"?start_time=0&include=metric_sets"
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_sync.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/sync.py src/chatbot/features/metrics/test_sync.py
git commit -m "feat(metrics): sideload metric_sets in incremental ticket export"
```

---

### Task 5: Emit classification tags at ticket creation (write-side)

**Files:**
- Modify: `src/chatbot/features/chat/ports.py` (`TicketingPort.create_ticket`, lines 38-48)
- Modify: `src/chatbot/features/chat/adapters/zendesk.py` (`create_ticket`, lines 78-120)
- Modify: `src/chatbot/features/chat/service.py` (call site, line 1099)
- Modify: the mock ticketing adapter (`src/chatbot/features/chat/adapters/mock.py` — the class implementing `create_ticket`)
- Test: `src/chatbot/features/chat/test_zendesk.py` (or the adapter's existing co-located test)

**Interfaces:**
- Produces: `create_ticket(..., category=None, subcategory=None, division=None, department=None, sla_minutes=None)` optional keyword params; the Zendesk adapter appends tags `category_<x>`, `subcat_<x>`, `division_<x>`, `dept_<x>`, `sla_<n>` (lowercased, spaces→`_`) when present.

- [ ] **Step 1: Write failing test** — add to the Zendesk adapter test (mirror its existing HTTP-capture pattern; assert on the posted payload's `ticket["tags"]`):

```python
@pytest.mark.asyncio
async def test_create_ticket_emits_classification_tags(...):
    # arrange adapter with a captured httpx post (existing pattern in this file)
    await adapter.create_ticket(
        session_id="whatsapp-+60123", title="t", body="b", urgency="high",
        category="Aftersales", subcategory="Battery Issue", division="Aftersales",
        sla_minutes=480,
    )
    tags = captured_payload["ticket"]["tags"]
    assert "category_aftersales" in tags
    assert "subcat_battery_issue" in tags
    assert "division_aftersales" in tags
    assert "sla_480" in tags
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/chat/test_zendesk.py -k classification_tags -q`
Expected: FAIL (unexpected keyword argument / tag missing).

- [ ] **Step 3: Extend the Protocol** — in `ports.py`, add the optional params to `create_ticket`'s signature (after `customer_phone`):

```python
        category: str | None = None,
        subcategory: str | None = None,
        division: str | None = None,
        department: str | None = None,
        sla_minutes: int | None = None,
```

- [ ] **Step 4: Implement in the Zendesk adapter** — add matching params to `create_ticket`, then build the tag list. Replace the `if self._settings.zendesk_ticket_tag:` block (lines 119-120) with:

```python
        def _norm(v: str) -> str:
            return v.strip().lower().replace(" ", "_")

        tags: list[str] = []
        if self._settings.zendesk_ticket_tag:
            tags.append(self._settings.zendesk_ticket_tag)
        if category:
            tags.append(f"category_{_norm(category)}")
        if subcategory:
            tags.append(f"subcat_{_norm(subcategory)}")
        if division:
            tags.append(f"division_{_norm(division)}")
        if department:
            tags.append(f"dept_{_norm(department)}")
        if sla_minutes is not None:
            tags.append(f"sla_{sla_minutes}")
        if tags:
            ticket["tags"] = tags
```

- [ ] **Step 5: Update the mock adapter** — add the same optional params (default `None`) to its `create_ticket` so it still satisfies the Protocol; behaviour unchanged.

- [ ] **Step 6: Thread from the caller** — in `service.py` at the `create_ticket(...)` call (line 1099), add:

```python
                category=session_state.get("category"),
                subcategory=session_state.get("subcategory"),
                division=(
                    __import__("chatbot.features.metrics.mapping", fromlist=["CATEGORY_TO_DIVISION"])
                    .CATEGORY_TO_DIVISION.get(str(session_state.get("category")).lower())
                    if session_state.get("category") else None
                ),
                sla_minutes=session_state.get("sla_minutes"),
```

Prefer a top-of-file import instead of the inline `__import__`: add `from chatbot.features.metrics.mapping import CATEGORY_TO_DIVISION` to `service.py` imports and use `CATEGORY_TO_DIVISION.get(...)` directly. (Use the clean import; the inline form is shown only to make the dependency explicit.)

- [ ] **Step 7: Run tests**

Run: `.venv/bin/pytest src/chatbot/features/chat -q`
Expected: PASS (mock + adapter + service tests green).

- [ ] **Step 8: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/chat/ports.py src/chatbot/features/chat/adapters/zendesk.py src/chatbot/features/chat/adapters/mock.py src/chatbot/features/chat/service.py src/chatbot/features/chat/test_zendesk.py
git commit -m "feat(chat): emit division/category/sla classification tags on ticket create"
```

---

### Task 6: Report views

**Files:**
- Modify: `src/chatbot/features/metrics/bigquery_schema.py` (`view_ddls`)
- Test: `src/chatbot/features/metrics/test_bigquery_schema.py`

**Interfaces:**
- Consumes: the new columns from Task 3.
- Produces: `view_ddls(...)` returns the existing 5 keys plus `v_volume_by_division`, `v_dept_pic_performance`, `v_sla_achievement`, `v_reopen_rate`, `v_resolution_time`, `v_nps_by_agent`, `v_volume_daily`, `v_volume_weekly`.

- [ ] **Step 1: Write failing test** — append to `test_bigquery_schema.py`:

```python
from chatbot.features.metrics.bigquery_schema import view_ddls


def test_view_ddls_include_report_views() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    for name in [
        "v_volume_by_division", "v_dept_pic_performance", "v_sla_achievement",
        "v_reopen_rate", "v_resolution_time", "v_nps_by_agent",
        "v_volume_daily", "v_volume_weekly",
    ]:
        assert name in ddls
        assert name in ddls[name]  # DDL creates the view of that name
    assert "division" in ddls["v_volume_by_division"]
    assert "reopen_count" in ddls["v_reopen_rate"]
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -k report_views -q`
Expected: FAIL.

- [ ] **Step 3: Implement** — add these entries to the dict returned by `view_ddls` (same f-string style as existing views; `fq` is the fully-qualified table):

```python
        "v_volume_by_division": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_division` AS "
            f"SELECT FORMAT_DATE('%Y-%m', DATE(created_at)) AS month, "
            f"COALESCE(division, 'Unknown') AS division, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY month, division"
        ),
        "v_dept_pic_performance": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_dept_pic_performance` AS "
            f"SELECT COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(pic, 'Unassigned') AS pic, COUNT(*) AS cases, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_resolution_min, "
            f"SAFE_DIVIDE(COUNTIF(resolved_at IS NOT NULL), COUNT(*)) AS resolution_rate "
            f"FROM {fq} GROUP BY department, pic ORDER BY cases DESC"
        ),
        "v_sla_achievement": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_sla_achievement` AS "
            f"SELECT channel, COALESCE(division, 'Unknown') AS division, "
            f"COUNTIF(sla_deadline IS NOT NULL) AS with_sla, "
            f"COUNTIF(resolved_at IS NOT NULL AND resolved_at <= sla_deadline) AS met, "
            f"SAFE_DIVIDE(COUNTIF(resolved_at IS NOT NULL AND resolved_at <= sla_deadline), "
            f"COUNTIF(sla_deadline IS NOT NULL)) AS sla_achievement_rate "
            f"FROM {fq} GROUP BY channel, division"
        ),
        "v_reopen_rate": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_reopen_rate` AS "
            f"SELECT COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(pic, 'Unassigned') AS pic, COUNT(*) AS cases, "
            f"COUNTIF(reopen_count > 0) AS reopened, "
            f"SAFE_DIVIDE(COUNTIF(reopen_count > 0), COUNT(*)) AS reopen_rate "
            f"FROM {fq} GROUP BY department, pic"
        ),
        "v_resolution_time": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_resolution_time` AS "
            f"SELECT channel, COALESCE(division, 'Unknown') AS division, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE), 100)[OFFSET(50)] AS p50_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE), 100)[OFFSET(90)] AS p90_min "
            f"FROM {fq} WHERE resolved_at IS NOT NULL GROUP BY channel, division"
        ),
        "v_nps_by_agent": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_nps_by_agent` AS "
            f"SELECT COALESCE(agent_id, 'Unassigned') AS agent_id, channel, "
            f"COUNTIF(nps_score IS NOT NULL) AS respondents, "
            f"SAFE_DIVIDE("
            f"COUNTIF(nps_score >= 9) - COUNTIF(nps_score IS NOT NULL AND nps_score <= 6), "
            f"COUNTIF(nps_score IS NOT NULL)) * 100 AS nps "
            f"FROM {fq} WHERE channel IN ('Phone', 'WhatsApp') GROUP BY agent_id, channel"
        ),
        "v_volume_daily": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_daily` AS "
            f"SELECT DATE(created_at) AS day, channel, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY day, channel"
        ),
        "v_volume_weekly": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_weekly` AS "
            f"SELECT DATE_TRUNC(DATE(created_at), WEEK) AS week, channel, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY week, channel"
        ),
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): add division/dept-PIC/SLA/CRR/resolution/agent-NPS report views"
```

---

### Task 7: Seed demo data with new dimensions

**Files:**
- Modify: `scripts/seed_demo_metrics.py`
- Test: manual runnable check (script has no unit test harness).

**Interfaces:**
- Consumes: the widened schema (Task 3) + views (Task 6).
- Produces: demo conversation rows populate `division`, `agent_id`, `pic`, `department`, `sla_minutes`, `sla_deadline`, `first_response_at`, `resolved_at`, `reopen_count` so the new views render.

- [ ] **Step 1: Add dimension generation** — where the script builds each demo conversation row dict, add realistic values, e.g.:

```python
    divisions = ["Apps", "Sales", "Aftersales", "Charging"]
    agents = ["agent_ali", "agent_siti", "agent_raj", "agent_mei"]
    depts = ["service", "sales", "technical", "charging"]
    # per row (use index-based variation, NOT random, to stay deterministic):
    division = divisions[i % len(divisions)]
    agent_id = agents[i % len(agents)]
    row.update({
        "division": division,
        "category": division.lower(),
        "subcategory": f"{division.lower()}_general",
        "department": depts[i % len(depts)],
        "agent_id": agent_id,
        "pic": agent_id,
        "sla_minutes": 480,
        "sla_deadline": _iso(created + timedelta(minutes=480)),
        "first_response_at": _iso(created + timedelta(minutes=(i % 30) + 1)),
        "resolved_at": _iso(created + timedelta(minutes=(i % 300) + 30)) if resolved else None,
        "reopen_count": 1 if i % 11 == 0 else 0,
    })
```

(Match the script's existing row-construction style and its timestamp helper; `resolved` mirrors the existing bot/agent status split. Keep it deterministic — reuse the script's existing seeded-index pattern, no new randomness.)

- [ ] **Step 2: Dry-run the generator** — if the script supports a no-write/dry flag, run it; otherwise import-and-call its row builder in a throwaway REPL to confirm rows include the new keys.

Run: `.venv/bin/python -c "import scripts.seed_demo_metrics as s; print('ok')"`
Expected: imports cleanly (no syntax/type error).

- [ ] **Step 3: Gates + commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix && .venv/bin/mypy src/ --strict
git add scripts/seed_demo_metrics.py
git commit -m "feat(metrics): seed demo data with division/agent/SLA/reopen dimensions"
```

---

### Task 8: Full-suite green + provision note

**Files:**
- Test: entire backend suite.

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/pytest src/ -q`
Expected: all green (previously 328+ tests, plus the new ones).

- [ ] **Step 2: Final gates**

Run: `.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: clean.

- [ ] **Step 3: Note (no commit needed)** — record for the ops runbook: after merge, run the metrics sync once (`.venv/bin/python scripts/sync_zendesk_metrics.py` — or trigger the scheduler) so the widened `WRITE_TRUNCATE` load backfills every new column and `ensure_views` creates the new report views. Seeded demo env: run `scripts/seed_demo_metrics.py`.

---

## Self-review

**Spec coverage (Item 1 of the design):**
- New `conversations` columns (division/category/subcategory/agent_id/pic/department/sla_minutes/sla_deadline/first_response_at/resolved_at/reopen_count) → Tasks 1, 2, 3. ✓
- Write-side tag emission from classification → Task 5. ✓
- Sync sideload of assignee + metric_sets → Tasks 1 (assignee), 4 (metric_sets). ✓
- `CATEGORY_TO_DIVISION` map → Task 1. ✓
- Report views (v_volume_by_division, v_dept_pic_performance, v_sla_achievement, v_reopen_rate, v_resolution_time, v_nps_by_agent, daily/weekly) → Task 6. ✓
- Backfill → covered implicitly by `WRITE_TRUNCATE` sync (Task 8 note); no separate job (YAGNI). ✓
- Seed demo data → Task 7. ✓

**Placeholder scan:** Task 5 caller shows both an explicit-dependency form and the preferred clean import; Task 4/7 reference "existing helper pattern" because the exact fixture name must match the file — the concrete assertion/values are given. No TBD/TODO left.

**Type consistency:** `ConversationRow` field names/types are identical across Tasks 1-3 and the `load_conversations` writer keys. View names in Task 6 match the Task 6 test. `create_ticket` new params match across ports/adapter/mock/caller in Task 5.

## Deferred to later plans (other short-term items)
- Item 2 (export + anomaly), Item 3 (SLA escalation + audit), Item 4 (Zendesk config doc), Item 5 (agent-assist FAQ) — each gets its own plan after this one lands.
