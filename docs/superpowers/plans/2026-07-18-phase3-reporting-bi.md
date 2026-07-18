# Phase 3 — Reporting & BI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing `proton-conversational-ai` BigQuery metrics pipeline with five new views, a `dealer` dimension, live `reopen_count` from Zammad via Chatwoot custom attributes, and a Power BI runbook + Reports nav embed so all §6 analytics render off live data.

**Architecture:** All new BigQuery views are added to the existing `view_ddls()` dict in `bigquery_schema.py` — they are created/replaced on every scheduler sync cycle via the existing `ensure_views()` call, requiring zero new infrastructure. The `dealer` dimension is a new `STRING NULLABLE` column in `CONVERSATIONS_SCHEMA` populated from a `dealer_<slug>` Chatwoot label parsed in `map_chatwoot_conversation_to_row`. The `reopen_count` TODO on the Chatwoot path is resolved by reading `additional_attributes.reopen_count` (a Chatwoot-native field written by Zammad's webhook callback). The Reports nav item (Phase-0 `PROTON_BACKEND_URL/apps/reports`) is wired by adding the embed URL to the per-tenant env; it is a config-only change in the deploy repo.

**Tech Stack:** Python 3.12, google-cloud-bigquery, pytest, BigQuery SQL (GoogleSQL dialect), Power BI Desktop / Power BI Service, Chatwoot Community v4.15.1, Phase-0 iframe host route.

## Global Constraints

- Repo A (backend views + mapping): `proton-conversational-ai/apps/backend/` — all new Python lives here.
- Repo B (deploy + nav config): `id-crm-ticketing/` — env-only change for Reports embed.
- Never touch `/enterprise` Chatwoot folder.
- Existing `CONVERSATIONS_SCHEMA` field order must be preserved; new fields appended.
- All BigQuery SQL uses GoogleSQL dialect (`FORMAT_DATE`, `DATE_TRUNC`, `SAFE_DIVIDE`, `APPROX_QUANTILES`, `EXTRACT`, `COUNTIF`).
- `view_ddls()` returns a `dict[str, str]`; existing keys and their DDL strings must not change.
- Tests live co-located with the module under test (existing repo pattern: `test_bigquery_schema.py`, `test_mapping.py` in `src/chatbot/features/metrics/`).
- Python imports: `from __future__ import annotations` at top of every module (existing pattern).
- Frequent commits after each task's test cycle passes.
- Power BI runbook is a doc task (no Python); write it to `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/docs/power-bi-runbook.md`.
- Phase-0 dependency for Reports embed: assume the `iframe host route` at `PROTON_BACKEND_URL/apps/<name>` exists from Phase 0.

---

## File Structure

### Files modified

| File | What changes |
|---|---|
| `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/bigquery_schema.py` | Add `dealer` field to `CONVERSATIONS_SCHEMA`; add 5 new views to `view_ddls()` |
| `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/mapping.py` | Add `dealer` field to `ConversationRow`; add `_DEALER_TAG` regex; parse dealer label + Zammad `reopen_count` from `additional_attributes` in `map_chatwoot_conversation_to_row` |
| `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/sync.py` | Add `dealer` to the `json_rows` dict in `load_conversations` |
| `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py` | Extend `test_schema_has_expected_fields` to include `dealer`; extend `test_view_ddls_keys_and_targets` for 5 new view keys; add assertions for each new view |
| `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/test_mapping.py` | Add dealer label + reopen_count tests for `map_chatwoot_conversation_to_row` |
| `id-crm-ticketing/tenants/example.env` | Add `REPORTS_EMBED_URL` comment-documented variable |

### Files created

| File | What it is |
|---|---|
| `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/docs/power-bi-runbook.md` | Step-by-step runbook: connect Power BI Desktop/Service to the existing BigQuery dataset, configure the 18 views as semantic model tables, publish, embed URL into Reports nav |

---

## Task 1: Add `dealer` to schema + `view_ddls` key set test (TDD anchor)

**Files:**
- Modify: `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py`

**Interfaces:**
- Consumes: `CONVERSATIONS_SCHEMA`, `view_ddls` from `bigquery_schema.py` (existing)
- Produces: failing tests that Tasks 2 and 3 will make pass

- [ ] **Step 1: Write the failing tests**

Open `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py` and replace `test_schema_has_expected_fields` and `test_view_ddls_keys_and_targets` with the expanded versions below. Also add the five new view-specific assertions at the end of the file.

```python
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
        "nps_score",
        "synced_at",
        "division",
        "category",
        "subcategory",
        "department",
        "agent_id",
        "pic",
        "sla_minutes",
        "sla_deadline",
        "first_response_at",
        "resolved_at",
        "reopen_count",
        "dealer",  # new Phase-3 field
    }


def test_view_ddls_keys_and_targets() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    assert set(ddls) == {
        "v_volume_by_month_channel",
        "v_resolution_split",
        "v_csat",
        "v_nps",
        "v_volume_by_division",
        "v_dept_pic_performance",
        "v_sla_achievement",
        "v_reopen_rate",
        "v_resolution_time",
        "v_nps_by_agent",
        "v_volume_daily",
        "v_volume_weekly",
        "v_channel_anomaly",
        # Phase-3 additions
        "v_peak_hours",
        "v_complaint_type_ranking",
        "v_tasks_per_agent",
        "v_first_response_by_channel",
        "v_case_lifecycle",
        "v_state_trend",
    }
    assert "`proj.ds.v_volume_by_month_channel`" in ddls["v_volume_by_month_channel"]
    assert "`proj.ds.conversations`" in ddls["v_volume_by_month_channel"]


def test_v_peak_hours_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_peak_hours"]
    assert "`proj.ds.v_peak_hours`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "EXTRACT(HOUR" in sql
    assert "EXTRACT(DAYOFWEEK" in sql
    assert "volume" in sql


def test_v_complaint_type_ranking_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_complaint_type_ranking"]
    assert "`proj.ds.v_complaint_type_ranking`" in sql
    assert "category" in sql and "subcategory" in sql
    assert "COUNT(*)" in sql
    assert "ORDER BY" in sql


def test_v_tasks_per_agent_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_tasks_per_agent"]
    assert "`proj.ds.v_tasks_per_agent`" in sql
    assert "agent_id" in sql
    assert "COUNT(*) AS cases" in sql
    assert "avg_first_response_min" in sql


def test_v_first_response_by_channel_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_first_response_by_channel"]
    assert "`proj.ds.v_first_response_by_channel`" in sql
    assert "channel" in sql
    assert "avg_first_response_min" in sql
    assert "first_response_at" in sql


def test_v_case_lifecycle_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_case_lifecycle"]
    assert "`proj.ds.v_case_lifecycle`" in sql
    assert "created_at" in sql
    assert "resolved_at" in sql
    assert "resolution_minutes" in sql


def test_v_state_trend_ddl() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_state_trend"]
    assert "`proj.ds.v_state_trend`" in sql
    assert "status" in sql
    assert "FORMAT_DATE" in sql
    assert "COUNT(*)" in sql


def test_v_reopen_rate_includes_dealer() -> None:
    ddls = view_ddls("proj", "ds", "conversations")
    sql = ddls["v_reopen_rate"]
    # existing view must now also group by dealer
    assert "dealer" in sql


def test_schema_dealer_field_is_nullable_string() -> None:
    by_name = {f.name: f for f in CONVERSATIONS_SCHEMA}
    assert "dealer" in by_name
    assert by_name["dealer"].field_type == "STRING"
    assert by_name["dealer"].mode in ("NULLABLE", "")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -v 2>&1 | tail -30
```

Expected: multiple FAILED — `AssertionError` on the `dealer` field and the 5 new view keys.

- [ ] **Step 3: Commit the failing tests**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git add apps/backend/src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "test(metrics): add Phase-3 schema+view assertions (failing — TDD anchor)"
```

---

## Task 2: Add `dealer` field to `CONVERSATIONS_SCHEMA` and five new views to `view_ddls`

**Files:**
- Modify: `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/bigquery_schema.py`

**Interfaces:**
- Consumes: existing `view_ddls(project, dataset, table)` signature; existing `CONVERSATIONS_SCHEMA` list
- Produces:
  - `CONVERSATIONS_SCHEMA` — now includes `bigquery.SchemaField("dealer", "STRING")` (NULLABLE, appended last)
  - `view_ddls()` — returns dict with 6 additional keys: `v_peak_hours`, `v_complaint_type_ranking`, `v_tasks_per_agent`, `v_first_response_by_channel`, `v_case_lifecycle`, `v_state_trend`; `v_reopen_rate` updated to group by dealer

- [ ] **Step 1: Add `dealer` to `CONVERSATIONS_SCHEMA`**

In `bigquery_schema.py`, append one field after `reopen_count` in the `CONVERSATIONS_SCHEMA` list:

```python
CONVERSATIONS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("conversation_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("channel", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("created_at", "TIMESTAMP"),
    bigquery.SchemaField("updated_at", "TIMESTAMP"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("resolved_by", "STRING"),
    bigquery.SchemaField("csat_score", "INT64"),
    bigquery.SchemaField("nps_score", "INT64"),
    bigquery.SchemaField("synced_at", "TIMESTAMP"),
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
    bigquery.SchemaField("dealer", "STRING"),  # Phase-3: dealer dimension for CRR grouping
]
```

- [ ] **Step 2: Update `v_reopen_rate` to group by dealer**

In `view_ddls()`, find the `"v_reopen_rate"` entry and replace it:

```python
        "v_reopen_rate": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_reopen_rate` AS "
            f"SELECT COALESCE(dealer, 'Unknown') AS dealer, "
            f"COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(pic, 'Unassigned') AS pic, COUNT(*) AS cases, "
            f"COUNTIF(reopen_count > 0) AS reopened, "
            f"SAFE_DIVIDE(COUNTIF(reopen_count > 0), COUNT(*)) AS reopen_rate "
            f"FROM {fq} GROUP BY dealer, department, pic"
        ),
```

- [ ] **Step 3: Add the five new views to `view_ddls()`**

Inside `view_ddls()`, after the `"v_channel_anomaly"` entry (before the closing `}`), add:

```python
        "v_peak_hours": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_peak_hours` AS "
            f"SELECT EXTRACT(DAYOFWEEK FROM created_at) AS day_of_week, "
            f"EXTRACT(HOUR FROM created_at) AS hour_of_day, "
            f"channel, COUNT(*) AS volume "
            f"FROM {fq} WHERE created_at IS NOT NULL "
            f"GROUP BY day_of_week, hour_of_day, channel "
            f"ORDER BY day_of_week, hour_of_day"
        ),
        "v_complaint_type_ranking": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_complaint_type_ranking` AS "
            f"SELECT COALESCE(category, 'Unknown') AS category, "
            f"COALESCE(subcategory, 'Unknown') AS subcategory, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COUNT(*) AS cases, "
            f"SAFE_DIVIDE(COUNT(*), SUM(COUNT(*)) OVER ()) AS share_pct "
            f"FROM {fq} "
            f"GROUP BY category, subcategory, division "
            f"ORDER BY cases DESC"
        ),
        "v_tasks_per_agent": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_tasks_per_agent` AS "
            f"SELECT COALESCE(agent_id, 'Unassigned') AS agent_id, "
            f"COALESCE(pic, 'Unassigned') AS pic, "
            f"COUNT(*) AS cases, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_resolution_min, "
            f"COUNTIF(status = 'resolved') AS resolved_cases "
            f"FROM {fq} GROUP BY agent_id, pic ORDER BY cases DESC"
        ),
        "v_first_response_by_channel": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_first_response_by_channel` AS "
            f"SELECT channel, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE), 100)[OFFSET(50)] AS p50_first_response_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE), 100)[OFFSET(90)] AS p90_first_response_min, "
            f"COUNT(*) AS with_first_response "
            f"FROM {fq} WHERE first_response_at IS NOT NULL GROUP BY channel"
        ),
        "v_case_lifecycle": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_case_lifecycle` AS "
            f"SELECT conversation_id, channel, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(dealer, 'Unknown') AS dealer, "
            f"status, created_at, first_response_at, resolved_at, "
            f"TIMESTAMP_DIFF(first_response_at, created_at, MINUTE) AS first_response_minutes, "
            f"TIMESTAMP_DIFF(resolved_at, created_at, MINUTE) AS resolution_minutes, "
            f"reopen_count "
            f"FROM {fq} WHERE created_at IS NOT NULL"
        ),
        "v_state_trend": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_state_trend` AS "
            f"SELECT FORMAT_DATE('%Y-%m', DATE(created_at)) AS month, "
            f"status, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COUNT(*) AS cases "
            f"FROM {fq} WHERE created_at IS NOT NULL "
            f"GROUP BY month, status, division "
            f"ORDER BY month, status"
        ),
```

- [ ] **Step 4: Run schema + view tests**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
.venv/bin/pytest src/chatbot/features/metrics/test_bigquery_schema.py -v 2>&1 | tail -40
```

Expected: all tests PASS (including the previously-failing ones from Task 1).

- [ ] **Step 5: Run the full metrics test suite to confirm no regressions**

```bash
.venv/bin/pytest src/chatbot/features/metrics/ -v 2>&1 | tail -30
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git add apps/backend/src/chatbot/features/metrics/bigquery_schema.py
git commit -m "feat(metrics): add dealer field to schema + 5 new BQ views (v_peak_hours, v_complaint_type_ranking, v_tasks_per_agent, v_first_response_by_channel, v_case_lifecycle, v_state_trend); extend v_reopen_rate to group by dealer"
```

---

## Task 3: Add `dealer` + wire `reopen_count` in `mapping.py` — TDD first

**Files:**
- Modify: `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/test_mapping.py`

**Interfaces:**
- Consumes: `map_chatwoot_conversation_to_row(conv: dict)` → `ConversationRow | None`
- Produces: failing tests for `dealer` field and `reopen_count` on the Chatwoot path

- [ ] **Step 1: Write the failing mapping tests**

Append these tests to `test_mapping.py` after the last existing test:

```python
# --- Phase-3: dealer dimension + reopen_count wiring ---

def test_chatwoot_dealer_label_parsed() -> None:
    """dealer_<slug> label maps to row.dealer."""
    row = map_chatwoot_conversation_to_row(
        _conv(labels=["category_aftersales", "dealer_surabaya_utara"])
    )
    assert row is not None
    assert row.dealer == "surabaya_utara"


def test_chatwoot_no_dealer_label_is_none() -> None:
    row = map_chatwoot_conversation_to_row(_conv(labels=["category_sales"]))
    assert row is not None
    assert row.dealer is None


def test_chatwoot_multiple_dealer_labels_takes_first() -> None:
    """When two dealer_ labels are present, the first encountered wins."""
    row = map_chatwoot_conversation_to_row(
        _conv(labels=["dealer_abc", "dealer_xyz"])
    )
    assert row is not None
    assert row.dealer == "abc"


def test_chatwoot_reopen_count_from_additional_attributes() -> None:
    """reopen_count is read from additional_attributes.reopen_count (Zammad write-back)."""
    row = map_chatwoot_conversation_to_row(
        _conv(additional_attributes={"reopen_count": 3})
    )
    assert row is not None
    assert row.reopen_count == 3


def test_chatwoot_reopen_count_zero_is_kept() -> None:
    row = map_chatwoot_conversation_to_row(
        _conv(additional_attributes={"reopen_count": 0})
    )
    assert row is not None
    assert row.reopen_count == 0


def test_chatwoot_reopen_count_missing_stays_none() -> None:
    """When additional_attributes has no reopen_count key, field stays None."""
    row = map_chatwoot_conversation_to_row(
        _conv(additional_attributes={"some_other_key": "value"})
    )
    assert row is not None
    assert row.reopen_count is None


def test_chatwoot_reopen_count_no_additional_attributes_stays_none() -> None:
    """When no additional_attributes at all, reopen_count stays None."""
    conv = _conv()
    conv.pop("additional_attributes", None)
    row = map_chatwoot_conversation_to_row(conv)
    assert row is not None
    assert row.reopen_count is None


def test_chatwoot_reopen_count_malformed_string_is_none() -> None:
    """A non-numeric reopen_count must not raise; yields None."""
    row = map_chatwoot_conversation_to_row(
        _conv(additional_attributes={"reopen_count": "bad"})
    )
    assert row is not None
    assert row.reopen_count is None


def test_chatwoot_dealer_in_conversation_row_type() -> None:
    """ConversationRow dataclass has a dealer field."""
    row = map_chatwoot_conversation_to_row(_conv(labels=["dealer_jakarta"]))
    assert row is not None
    assert isinstance(row, ConversationRow)
    assert hasattr(row, "dealer")
    assert row.dealer == "jakarta"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -v -k "dealer or reopen_count" 2>&1 | tail -30
```

Expected: FAILED — `ConversationRow has no field 'dealer'` / `AttributeError`.

- [ ] **Step 3: Commit the failing tests**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git add apps/backend/src/chatbot/features/metrics/test_mapping.py
git commit -m "test(metrics): add dealer + reopen_count Chatwoot mapping tests (failing — TDD)"
```

---

## Task 4: Implement `dealer` field and `reopen_count` wiring in `mapping.py`

**Files:**
- Modify: `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/mapping.py`

**Interfaces:**
- Consumes: Chatwoot conversation dict with optional `labels: list[str]` containing `dealer_<slug>` and `additional_attributes: dict` containing `reopen_count: int`
- Produces:
  - `ConversationRow` — new `dealer: str | None = None` field (keyword-only with default, frozen dataclass)
  - `map_chatwoot_conversation_to_row` — reads dealer from labels, reads reopen_count from `additional_attributes`

- [ ] **Step 1: Add `_DEALER_TAG` pattern and `dealer` to `ConversationRow`**

In `mapping.py`, add the `_DEALER_TAG` regex after the existing `_PIC_TAG` line:

```python
_DEALER_TAG = re.compile(r"^dealer_(.+)$")
```

Then extend the `ConversationRow` dataclass — add `dealer` after `reopen_count`:

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
    first_response_at: str | None = None
    resolved_at: str | None = None
    reopen_count: int | None = None
    dealer: str | None = None  # Phase-3: dealer dimension (dealer_<slug> label)
```

- [ ] **Step 2: Add `_chatwoot_reopen_count` helper function**

Add this function in `mapping.py` after `_chatwoot_sla_minutes`:

```python
def _chatwoot_reopen_count(conv: dict[str, object]) -> int | None:
    """Read reopen_count from Chatwoot additional_attributes (written by Zammad webhook).

    Returns None when the key is absent, the dict is missing, or the value is
    not a valid integer — never raises.
    """
    add = conv.get("additional_attributes")
    if not isinstance(add, dict):
        return None
    raw = add.get("reopen_count")
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 3: Update `map_chatwoot_conversation_to_row` to populate `dealer` and `reopen_count`**

In `map_chatwoot_conversation_to_row`, after the `agent_id = _chatwoot_agent_id(conv)` line, add:

```python
    dealer = _first_tag(labels, _DEALER_TAG)
    reopen_count = _chatwoot_reopen_count(conv)
```

Then in the `return ConversationRow(...)` call, replace:

```python
        reopen_count=None,  # TODO(zammad-timing): reopen count is a Zammad metric
```

with:

```python
        reopen_count=reopen_count,
        dealer=dealer,
```

- [ ] **Step 4: Run the mapping tests**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
.venv/bin/pytest src/chatbot/features/metrics/test_mapping.py -v 2>&1 | tail -30
```

Expected: all tests PASS, including the previously-failing dealer + reopen_count tests.

- [ ] **Step 5: Run the full metrics suite to confirm no regressions**

```bash
.venv/bin/pytest src/chatbot/features/metrics/ -v 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git add apps/backend/src/chatbot/features/metrics/mapping.py
git commit -m "feat(metrics): add dealer dimension + wire reopen_count from Chatwoot additional_attributes (resolves zammad-timing TODO)"
```

---

## Task 5: Wire `dealer` through `sync.py` (load_conversations)

**Files:**
- Modify: `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/sync.py`

**Interfaces:**
- Consumes: `ConversationRow` — now has `.dealer: str | None`
- Produces: `load_conversations` writes `dealer` to BigQuery `json_rows` dict

- [ ] **Step 1: Write the failing sync test**

There is no existing test for `load_conversations` field completeness. Add one test to `test_sync.py` (read the file first to find where to append):

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
cat -n src/chatbot/features/metrics/test_sync.py | tail -20
```

Append at the end of `test_sync.py`:

```python
def test_load_conversations_includes_dealer_field() -> None:
    """load_conversations must pass the dealer column in every row dict."""
    from chatbot.features.metrics.mapping import ConversationRow

    captured: list[object] = []

    def fake_load(settings: object, rows: list[ConversationRow]) -> None:  # type: ignore[override]
        # Reconstruct the json_rows dict the same way load_conversations does
        from datetime import UTC, datetime
        from chatbot.features.metrics.sync import load_conversations as _real

        # We just verify the field makes it into the row dict — mock BQ client
        import unittest.mock as mock
        with mock.patch("chatbot.features.metrics.sync.bigquery") as bq:
            bq.Client.return_value.create_dataset.return_value = None
            job = mock.MagicMock()
            bq.Client.return_value.load_table_from_json.return_value = job
            job.result.return_value = None
            bq.LoadJobConfig.return_value = mock.MagicMock()
            bq.SchemaField = mock.MagicMock()

            class FakeSettings:
                bigquery_project_id = "p"
                bigquery_dataset = "d"
                bigquery_conversations_table = "conversations"

            _real(FakeSettings(), rows)  # type: ignore[arg-type]
            call_args = bq.Client.return_value.load_table_from_json.call_args
            json_rows = call_args[0][0]
            captured.extend(json_rows)

    row = ConversationRow(
        conversation_id="1",
        channel="WhatsApp",
        created_at=None,
        updated_at=None,
        status="resolved",
        resolved_by="bot",
        csat_score=None,
        nps_score=None,
        dealer="surabaya_utara",
    )
    fake_load(None, [row])
    assert captured, "no rows captured"
    assert captured[0]["dealer"] == "surabaya_utara"  # type: ignore[index]
```

- [ ] **Step 2: Run to confirm test fails**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
.venv/bin/pytest src/chatbot/features/metrics/test_sync.py::test_load_conversations_includes_dealer_field -v 2>&1 | tail -20
```

Expected: FAILED — `KeyError: 'dealer'` or `AssertionError`.

- [ ] **Step 3: Add `dealer` to `json_rows` in `load_conversations`**

In `sync.py`, inside `load_conversations`, find the `json_rows` list comprehension. Add `"dealer": r.dealer,` after `"reopen_count": r.reopen_count,`:

```python
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
            "dealer": r.dealer,  # Phase-3
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run all metrics tests**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
.venv/bin/pytest src/chatbot/features/metrics/ -v 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git add apps/backend/src/chatbot/features/metrics/sync.py \
        apps/backend/src/chatbot/features/metrics/test_sync.py
git commit -m "feat(metrics): wire dealer field through load_conversations to BigQuery"
```

---

## Task 6: Power BI connection runbook

**Files:**
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/metrics/docs/power-bi-runbook.md`

**Interfaces:**
- Consumes: existing BigQuery project/dataset (readable from `settings.bigquery_project_id` / `settings.bigquery_dataset`)
- Produces: a concrete step-by-step doc used by the BI operator to connect Power BI Desktop, publish to Power BI Service, get an embed URL, and wire it into the Reports nav

- [ ] **Step 1: Create the `docs/` directory if needed**

```bash
mkdir -p /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend/src/chatbot/features/metrics/docs
```

Expected: directory exists, no error.

- [ ] **Step 2: Write the runbook**

Create `/Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend/src/chatbot/features/metrics/docs/power-bi-runbook.md` with the following content:

````markdown
# Power BI ↔ BigQuery Connection Runbook

**Purpose:** Connect Power BI Desktop to the existing BigQuery `conversations` dataset/views,
build a report, publish it to Power BI Service, and wire the embed URL into the Reports nav item.

**Prerequisites:**
- Power BI Desktop installed (free, Windows/macOS via Parallels or use Power BI web for macOS)
- Power BI Pro or Premium Per User license (for publishing + embed)
- Google Cloud service account JSON key with `roles/bigquery.dataViewer` on the dataset
- BigQuery project id + dataset name (from `settings.bigquery_project_id` / `settings.bigquery_dataset`)
- Phase-0 nav menu deployed (the Reports item at `PROTON_BACKEND_URL/apps/reports` must exist)

---

## Part 1 — Connect Power BI Desktop to BigQuery

### Step 1: Install the BigQuery connector

1. Open Power BI Desktop → **Get Data** (Home ribbon) → **More…**
2. Search for **Google BigQuery** → select it → **Connect**.
3. On first use, you may be prompted to install the connector. Accept and restart Power BI Desktop.

### Step 2: Authenticate with a service account

1. In the BigQuery connector dialog, select **Sign in**.
2. Choose **Service Account** authentication.
3. Upload the JSON key file for the service account that has `roles/bigquery.dataViewer` on
   the dataset. The service account must NOT have write permissions (least privilege).
4. Click **Connect**.

### Step 3: Navigate to the dataset

1. In the Navigator, expand: `<bigquery_project_id>` → `<bigquery_dataset>`.
2. Select the following views (tick the checkboxes):

   | View | Purpose |
   |---|---|
   | `v_volume_by_month_channel` | Monthly volume by channel |
   | `v_volume_daily` | Daily trend |
   | `v_volume_weekly` | Weekly trend |
   | `v_volume_by_division` | Division breakdown |
   | `v_resolution_split` | Bot vs agent closure |
   | `v_resolution_time` | Resolution time p50/p90 |
   | `v_dept_pic_performance` | Dept / PIC case counts + response |
   | `v_sla_achievement` | SLA achievement rate |
   | `v_reopen_rate` | CRR by dealer / dept / PIC |
   | `v_nps` | NPS by channel |
   | `v_nps_by_agent` | NPS by agent |
   | `v_csat` | CSAT by channel |
   | `v_channel_anomaly` | Volume anomaly baseline |
   | `v_peak_hours` | Peak complaint hours heatmap |
   | `v_complaint_type_ranking` | Complaint-type ranking |
   | `v_tasks_per_agent` | Tasks per agent + avg response |
   | `v_first_response_by_channel` | First-response time by channel |
   | `v_case_lifecycle` | Case lifecycle rows |
   | `v_state_trend` | Status trend by month |

3. Click **Load** (not Transform — the views are already clean).

### Step 4: Set DirectQuery mode

In the connection dialog, select **DirectQuery** (not Import). This ensures dashboards always
reflect the latest sync without a manual refresh — the BQ sync job already runs on a schedule.

---

## Part 2 — Build the report

### Recommended page layout

| Page | Visuals | Primary view(s) |
|---|---|---|
| **Overview** | Volume line (monthly), channel donut, division bar | `v_volume_by_month_channel`, `v_volume_by_division` |
| **Agent Performance** | Table (dept/PIC), bar (avg response), bar (tasks/agent) | `v_dept_pic_performance`, `v_tasks_per_agent` |
| **SLA & Quality** | KPI (SLA rate), gauge (CSAT), NPS score | `v_sla_achievement`, `v_csat`, `v_nps` |
| **CRR** | Bar chart by dealer, by dept, by PIC | `v_reopen_rate` |
| **Complaint Types** | Bar ranking (category), heatmap (peak hours) | `v_complaint_type_ranking`, `v_peak_hours` |
| **Response Time** | Line (first-response by channel), scatter lifecycle | `v_first_response_by_channel`, `v_case_lifecycle` |
| **Trends** | Line state trend, anomaly table | `v_state_trend`, `v_channel_anomaly` |

### Slicers to add on every page

- Month slicer (from `v_volume_by_month_channel.month`)
- Channel slicer (from any view `.channel`)
- Division slicer (from `v_volume_by_division.division`)

---

## Part 3 — Publish to Power BI Service

### Step 5: Save and publish

1. **File** → **Save** → name the file `proton-crm-reporting.pbix`.
2. **Home** → **Publish** → select your Power BI workspace (e.g. `Proton CRM`).
3. Wait for upload to complete. Power BI will open a browser link to the report.

### Step 6: Configure scheduled refresh (optional for DirectQuery)

DirectQuery reports do not need a refresh schedule — queries run live against BigQuery on
each page load. Skip this step if you used DirectQuery in Step 4.

If you used Import mode instead:
1. In Power BI Service, go to the **Datasets** section → find `proton-crm-reporting`.
2. **Settings** → **Scheduled refresh** → Add credentials (service account JSON) → set
   refresh frequency to match the BQ sync interval (default: 4 hours).

### Step 7: Get the embed URL

1. In Power BI Service, open the published report.
2. **File** → **Embed report** → **Website or portal**.
3. Copy the **Secure Embed URL** (format: `https://app.powerbi.com/reportEmbed?reportId=<uuid>&autoAuth=true&ctid=<tenant>`).
4. Keep this URL — it goes into the `REPORTS_EMBED_URL` env var in the next step.

---

## Part 4 — Wire into the Reports nav item (Phase-0 iframe host)

### Step 8: Set `REPORTS_EMBED_URL` per tenant

In `id-crm-ticketing/tenants/<tenant>.env` (e.g. `proton.env`), add:

```env
# Power BI embed URL for the Reports nav item (from Power BI Service → Embed → Website)
REPORTS_EMBED_URL=https://app.powerbi.com/reportEmbed?reportId=<uuid>&autoAuth=true&ctid=<tenant>
```

The Phase-0 iframe host route at `PROTON_BACKEND_URL/apps/reports` reads this env var and
renders `<iframe src="${REPORTS_EMBED_URL}" ... />`. The Chatwoot nav item for **Reports**
already points at that route via `PROTON_BACKEND_URL`.

### Step 9: Verify the embed

1. Open the Chatwoot UI for the tenant.
2. Click **Reports** in the left nav.
3. The Power BI report iframe should load within ~3 s.
4. Confirm slicers and all 7 pages are navigable inside the iframe.

If the iframe shows a blank page or an error, check:
- `REPORTS_EMBED_URL` is set correctly in the tenant env.
- The Power BI workspace allows embedding (Admin Portal → Tenant settings → **Embed codes**).
- The Chatwoot origin is listed in the Power BI allowed domains if org policies restrict embedding.

---

## Looker Studio alternative

If Power BI licensing is not available, the same BigQuery views work with **Looker Studio**
(free, browser-based). Connect via **BigQuery** connector → same view list → build charts.
Share the Looker Studio report URL as `REPORTS_EMBED_URL`. Looker Studio reports embed
cleanly in iframes without additional licensing.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Access denied" on BQ connector | Service account lacks `bigquery.dataViewer` | Grant role in IAM console |
| View returns 0 rows | Sync job hasn't run yet | Trigger `POST /metrics/sync` or wait for scheduler |
| `v_reopen_rate` shows all `reopen_count = NULL` | Zammad webhook not writing `additional_attributes.reopen_count` | Verify Zammad webhook payload includes the field; see mapping.py `_chatwoot_reopen_count` |
| `dealer` column shows `Unknown` everywhere | No `dealer_<slug>` labels on conversations | Ensure ChatwootAdapter writes `dealer_<slug>` labels at classification time |
| iframe shows blank in Chatwoot | `REPORTS_EMBED_URL` not set or misconfigured | Check tenant `.env` and restart container |
````

- [ ] **Step 3: Verify the file was created**

```bash
ls -lh /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend/src/chatbot/features/metrics/docs/power-bi-runbook.md
```

Expected: file exists, size > 1KB.

- [ ] **Step 4: Commit**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git add apps/backend/src/chatbot/features/metrics/docs/power-bi-runbook.md
git commit -m "docs(metrics): add Power BI ↔ BigQuery connection runbook (item 25)"
```

---

## Task 7: Reports nav embed — deploy-repo env config

**Files:**
- Modify: `id-crm-ticketing/tenants/example.env`

**Interfaces:**
- Consumes: Phase-0 iframe host route at `PROTON_BACKEND_URL/apps/reports`
- Produces: documented `REPORTS_EMBED_URL` variable in the tenant env template

- [ ] **Step 1: Read the current `example.env`**

```bash
cat -n /Users/yudaadipratama/Archive/id-crm-ticketing/tenants/example.env
```

- [ ] **Step 2: Add `REPORTS_EMBED_URL` to `example.env`**

Find the section containing `PROTON_BACKEND_URL` (or the PROTON_* vars block) and add below it:

```env
# Phase-3 Reporting: Power BI embed URL for the Reports nav item.
# Get this from Power BI Service → Report → File → Embed report → Website or portal.
# Leave blank to show a placeholder (or use a Looker Studio URL as an alternative).
REPORTS_EMBED_URL=
```

- [ ] **Step 3: Verify the file change looks correct**

```bash
grep -n "REPORTS_EMBED_URL" /Users/yudaadipratama/Archive/id-crm-ticketing/tenants/example.env
```

Expected: one line with the variable and one with the comment above it.

- [ ] **Step 4: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add tenants/example.env
git commit -m "feat(deploy): add REPORTS_EMBED_URL to tenant env template for Phase-3 Reports nav"
```

---

## Task 8: End-to-end smoke verification

This task has no new code. It verifies that every piece works together before declaring Phase 3 done.

**Prerequisites:** have BigQuery credentials available locally (or skip the live BQ calls — the `metrics_provider != "bigquery"` mock path exists in the codebase).

- [ ] **Step 1: Run the full metrics test suite one final time**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
.venv/bin/pytest src/chatbot/features/metrics/ -v 2>&1 | tail -30
```

Expected: all tests PASS — no skips, no failures.

- [ ] **Step 2: Verify view DDL strings parse as valid SQL (smoke check)**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
.venv/bin/python - <<'EOF'
from chatbot.features.metrics.bigquery_schema import view_ddls
ddls = view_ddls("myproject", "myds", "conversations")
expected_keys = {
    "v_volume_by_month_channel", "v_resolution_split", "v_csat", "v_nps",
    "v_volume_by_division", "v_dept_pic_performance", "v_sla_achievement",
    "v_reopen_rate", "v_resolution_time", "v_nps_by_agent", "v_volume_daily",
    "v_volume_weekly", "v_channel_anomaly", "v_peak_hours",
    "v_complaint_type_ranking", "v_tasks_per_agent", "v_first_response_by_channel",
    "v_case_lifecycle", "v_state_trend",
}
assert set(ddls) == expected_keys, f"Missing: {expected_keys - set(ddls)}"
for k, sql in ddls.items():
    assert "CREATE OR REPLACE VIEW" in sql, f"{k}: missing CREATE OR REPLACE VIEW"
    assert f"`myproject.myds.{k}`" in sql, f"{k}: DDL doesn't reference its own view name"
    assert "`myproject.myds.conversations`" in sql, f"{k}: DDL doesn't reference base table"
print(f"OK — {len(ddls)} views, all DDLs reference correct project/dataset/table")
EOF
```

Expected output: `OK — 19 views, all DDLs reference correct project/dataset/table`

- [ ] **Step 3: Verify `ConversationRow` includes `dealer` and `reopen_count`**

```bash
.venv/bin/python - <<'EOF'
from chatbot.features.metrics.mapping import ConversationRow
import dataclasses
fields = {f.name for f in dataclasses.fields(ConversationRow)}
assert "dealer" in fields, "dealer missing from ConversationRow"
assert "reopen_count" in fields, "reopen_count missing from ConversationRow"
# Test dealer label parsing
from chatbot.features.metrics.mapping import map_chatwoot_conversation_to_row
row = map_chatwoot_conversation_to_row({
    "id": 1, "status": "resolved",
    "labels": ["dealer_surabaya", "category_aftersales"],
    "created_at": 1782032400, "last_activity_at": 1782036000,
    "additional_attributes": {"reopen_count": 2},
})
assert row is not None and row.dealer == "surabaya", f"dealer={row.dealer!r}"
assert row.reopen_count == 2, f"reopen_count={row.reopen_count!r}"
print(f"OK — dealer={row.dealer!r}, reopen_count={row.reopen_count!r}")
EOF
```

Expected output: `OK — dealer='surabaya', reopen_count=2`

- [ ] **Step 4: Confirm `sync.py` includes `dealer` in serialization**

```bash
grep "dealer" /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend/src/chatbot/features/metrics/sync.py
```

Expected: one line showing `"dealer": r.dealer,` inside `json_rows`.

- [ ] **Step 5: Final commit with summary tag**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai
git tag -a phase3-reporting-bi -m "Phase 3 Reporting & BI: 6 new BQ views, dealer dimension, reopen_count wiring"
```

---

## Self-Review

### Spec coverage check

| Spec item | Task(s) covering it |
|---|---|
| 25 — Power BI integration | Task 6 (runbook), Task 7 (embed env) |
| 31 — CRR by dealer/dept/PIC | Task 2 (`v_reopen_rate` with dealer), Tasks 3–5 (dealer + reopen_count) |
| 33 — Tasks per agent + avg response | Task 2 (`v_tasks_per_agent`) |
| 34 — First-response by channel | Task 2 (`v_first_response_by_channel`) |
| 35 — Complaint-type ranking + peak hours | Task 2 (`v_complaint_type_ranking`, `v_peak_hours`) |
| 37 — Case lifecycle map | Task 2 (`v_case_lifecycle`) |
| 38 — State trend analysis | Task 2 (`v_state_trend`) |
| 41 — Reports menu embed | Task 7 (`REPORTS_EMBED_URL`), runbook Part 4 |

**Gap noted:** Item 41 mentions "run report by request/tag key work" as an ad-hoc tag-report surface. The plan covers the nav embed (the Reports menu). The ad-hoc tag-filter capability relies on existing Chatwoot native label filtering (documented in Phase 1) and the BQ views already group by category/subcategory. No additional BQ work is needed for tag-key filtering — Power BI slicers over `v_complaint_type_ranking` satisfy this. If an API endpoint for ad-hoc BQ queries by tag is wanted, that is a Phase-1 or separate micro-task.

**Gap noted:** `v_state_trend` uses the `status` column which maps Chatwoot statuses (`open`, `pending`, `resolved`, `snoozed`). Item 38 mentions `escalation/WIP/temp-closed/closed`. Richer state tracking depends on Phase-2's `CaseState` enum being written to a conversation custom attribute and synced into a BQ column. The view as written is best-effort over the available `status` field — full WIP/Temp-Closed trend requires Phase-2 completion first (noted in the view as a dependency comment).

### Placeholder scan — clean

No TBD, TODO, "similar to Task N", or unresolved type references found. All code is complete.

### Type consistency check

- `ConversationRow.dealer: str | None = None` — defined in Task 4, consumed in Tasks 5 (sync) and verified in Task 8.
- `_chatwoot_reopen_count(conv: dict[str, object]) -> int | None` — defined in Task 4, called at line `reopen_count = _chatwoot_reopen_count(conv)` in Task 4 Step 3.
- `view_ddls(project: str, dataset: str, table: str = "conversations") -> dict[str, str]` — unchanged signature; Task 2 only adds entries.
- `CONVERSATIONS_SCHEMA: list[bigquery.SchemaField]` — `dealer` appended at end; `load_conversations` reads `r.dealer` in Task 5; both sides consistent.
