# Reporting & Metrics Extensions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between the existing Phase-3 BI infrastructure and Proton's real monthly/weekly PeC ops reports — new `case_type`/`vehicle_model` conversation dimensions, business-hours-aware SLA bucketing, dealer-escalation/turnaround and WIP/aging report views, a standalone RSA incident-log module, and native report-tab UI + CSV export.

**Architecture:** Every new dimension follows the exact `case_category`/`case_subcategory` mechanism already shipped (`backend/apps/backend/src/chatbot/features/chat/case_taxonomy.py`): tenant-configurable JSON env config, parsed fail-open, written to Chatwoot as List-type custom attributes, synced into BigQuery. New BigQuery views extend `bigquery_schema.py`'s `view_ddls()` and are read through the existing `MetricsQueryPort` → `BigQueryMetricsQuery` → `insights_router.py` pipeline. RSA is intentionally NOT a Chatwoot conversation — it's a new isolated `backend/apps/backend/src/chatbot/features/rsa/` slice with its own Postgres table, following the pgvector-KB module precedent (`kb_db.py`/`kb_repository.py`/`kb_knowledge_router.py`), gated the same way RBAC and the KB are (`rsa_enabled` + `rsa_database_url`, lazy-imported in `main.py`).

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2.0 async (Postgres), google-cloud-bigquery, pytest, structlog/logging, httpx (existing stack — no new dependencies except the new CSV export uses Python's stdlib `csv` module).

## Global Constraints

- Fail-open: every new config loader (`VEHICLE_MODELS_JSON`, `CASE_TYPE_OPTIONS_JSON`, `RESOLUTION_SLA_TARGETS_JSON`) degrades to an empty/default option set on malformed JSON — logged warning, never crashes startup or an AI turn.
- `classify_ticket_tool` rejects (logs, does not write) any `case_type`/`vehicle_model` value outside the configured options rather than clobbering an existing value with garbage — same behavior as the existing `category`/`subcategory` validation.
- A per-inbox business-hours fetch failure degrades that ONE conversation row to calendar-time, never a sync-wide failure.
- No BigQuery view is created for call-centre metrics (no underlying instrumentation exists) — it ships as a static report-UI placeholder panel only.
- No role-scoped visibility in this plan — every new view/tab is visible to whoever can already see Reports today, matching every existing report tab. **Do not start executing this plan until RBAC (roadmap item #2) is confirmed done** — that check happens outside this document, before Task 1 begins.
- No RSA↔dispatch-system integration, no PPTX/PDF/PowerBy export — out of scope per the spec's Non-goals.
- `backend/` and `agent/` share no code — every loader/helper that both services need is duplicated independently in each, per this repo's existing HTTP-only service boundary.

---

### Task 1: Shared `OptionList` loader (`case_type`/`vehicle_model` configs) — backend/

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/option_lists.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_option_lists.py`
- Modify: `backend/apps/backend/src/chatbot/platform/config.py` (add two fields immediately after `case_taxonomy_json`, currently ending at line 367)

**Interfaces:**
- Produces: `OptionList` with `.options() -> list[str]`, `.is_valid(value: str) -> bool`, `.is_empty() -> bool`; `build_option_list(raw_json: str) -> OptionList`. Used by Task 3 (classify tool) and Task 7 (provisioning).

`case_type` and `vehicle_model` are both flat option lists (unlike `case_category`'s nested category→subcategory shape), so they share ONE loader instead of two near-duplicate files.

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/backend/src/chatbot/features/chat/test_option_lists.py
from chatbot.features.chat.option_lists import build_option_list


def test_valid_options_json():
    opts = build_option_list('{"options": ["Inquiry", "Complaint", "Feedback"]}')
    assert opts.options() == ["Inquiry", "Complaint", "Feedback"]
    assert opts.is_valid("Inquiry") is True
    assert opts.is_valid("inquiry") is True  # case-insensitive match
    assert opts.is_valid("Not A Real Type") is False
    assert opts.is_empty() is False


def test_empty_json_yields_empty_list():
    opts = build_option_list("")
    assert opts.is_empty() is True
    assert opts.options() == []
    assert opts.is_valid("anything") is False


def test_malformed_json_yields_empty_list_not_crash():
    opts = build_option_list("{not valid json")
    assert opts.is_empty() is True


def test_non_dict_json_yields_empty_list():
    opts = build_option_list("[1, 2, 3]")
    assert opts.is_empty() is True


def test_missing_options_key_yields_empty_list():
    opts = build_option_list("{}")
    assert opts.is_empty() is True


def test_options_wrong_type_yields_empty_list():
    opts = build_option_list('{"options": "not-a-list"}')
    assert opts.is_empty() is True


def test_non_string_options_are_stringified():
    opts = build_option_list('{"options": [1, "two"]}')
    assert opts.options() == ["1", "two"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_option_lists.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chatbot.features.chat.option_lists'`.

- [ ] **Step 3: Implement `option_lists.py`**

```python
# backend/apps/backend/src/chatbot/features/chat/option_lists.py
"""Flat tenant-configurable option lists — CASE_TYPE_OPTIONS_JSON and
VEHICLE_MODELS_JSON share this loader since both are the same shape: a bare
list of display strings (unlike case_taxonomy.py's nested category ->
subcategories structure). Mirrors case_taxonomy.py's fail-open JSON parsing:
malformed/absent config never crashes the app, it just yields an empty list
(classify_ticket_tool then no-ops for that dimension).
"""

from __future__ import annotations

import json

import structlog

_log = structlog.get_logger(__name__)


class OptionList:
    def __init__(self, options: list[str]) -> None:
        self._options = options
        self._lower = {o.lower() for o in options}

    def options(self) -> list[str]:
        return list(self._options)

    def is_valid(self, value: str) -> bool:
        return value.lower() in self._lower

    def is_empty(self) -> bool:
        return not self._options


def build_option_list(raw_json: str) -> OptionList:
    """Parse a `{"options": [...]}` JSON blob into an OptionList.

    Returns an empty OptionList (is_valid always False) when the JSON is
    absent, malformed, not an object, or "options" isn't a list.
    """
    raw = (raw_json or "").strip()
    if not raw:
        return OptionList([])
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("option_list_json_parse_failed", error=str(exc))
        return OptionList([])
    if not isinstance(data, dict):
        _log.warning("option_list_json_not_a_dict", got=type(data).__name__)
        return OptionList([])
    raw_options = data.get("options")
    if not isinstance(raw_options, list):
        _log.warning("option_list_options_key_missing_or_wrong_type")
        return OptionList([])
    return OptionList([str(o) for o in raw_options])
```

- [ ] **Step 4: Add the two Settings fields**

In `backend/apps/backend/src/chatbot/platform/config.py`, immediately after `case_taxonomy_json`'s closing `)` (currently line 367), add:

```python

    # Vehicle-model / product-line dimension — JSON object {"options": [str, ...]}.
    # Same fail-open pattern as CASE_TAXONOMY_JSON. Empty -> the vehicle_model
    # custom attribute is never offered/written (byte-identical to today) —
    # tenants with no product-line concept simply leave this unset.
    vehicle_models_json: str = '{"options": ["e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "Not Applicable"]}'

    # Case-type dimension (Inquiry/Complaint/Feedback) — JSON object
    # {"options": [str, ...]}. Same fail-open pattern. Ships with a working
    # default since this concept is fairly universal to support work, but
    # stays configurable/overridable per tenant like every other dimension here.
    case_type_options_json: str = '{"options": ["Inquiry", "Complaint", "Feedback"]}'
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_option_lists.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/option_lists.py src/chatbot/features/chat/test_option_lists.py src/chatbot/platform/config.py
git commit -m "feat(chat): add OptionList loader for CASE_TYPE_OPTIONS_JSON/VEHICLE_MODELS_JSON"
```

---

### Task 2: Mirror `OptionList` loader — agent/

**Files:**
- Create: `agent/app/services/option_lists.py`
- Create: `agent/tests/test_option_lists.py`
- Modify: `agent/app/config.py` (add two fields immediately after `case_taxonomy_json`, currently ending at line 137)

**Interfaces:**
- Produces: same `OptionList`/`build_option_list` shape as Task 1, in `agent/`'s own package. Used by Task 6.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_option_lists.py
from app.services.option_lists import build_option_list


def test_valid_options_json():
    opts = build_option_list('{"options": ["Inquiry", "Complaint", "Feedback"]}')
    assert opts.options() == ["Inquiry", "Complaint", "Feedback"]
    assert opts.is_valid("Complaint") is True
    assert opts.is_valid("Nope") is False


def test_empty_json_yields_empty_list():
    opts = build_option_list("")
    assert opts.is_empty() is True


def test_malformed_json_yields_empty_list_not_crash():
    opts = build_option_list("{broken")
    assert opts.is_empty() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_option_lists.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `option_lists.py`**

```python
# agent/app/services/option_lists.py
"""Flat tenant-configurable option lists (case_type/vehicle_model). Mirrors
backend/'s option_lists.py — duplicated, not shared, per this repo's
agent/backend decoupling."""

from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)


class OptionList:
    def __init__(self, options: list[str]) -> None:
        self._options = options
        self._lower = {o.lower() for o in options}

    def options(self) -> list[str]:
        return list(self._options)

    def is_valid(self, value: str) -> bool:
        return value.lower() in self._lower

    def is_empty(self) -> bool:
        return not self._options


def build_option_list(raw_json: str) -> OptionList:
    raw = (raw_json or "").strip()
    if not raw:
        return OptionList([])
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("option_list_json_parse_failed: %s", exc)
        return OptionList([])
    if not isinstance(data, dict):
        _log.warning("option_list_json_not_a_dict: got %s", type(data).__name__)
        return OptionList([])
    raw_options = data.get("options")
    if not isinstance(raw_options, list):
        _log.warning("option_list_options_key_missing_or_wrong_type")
        return OptionList([])
    return OptionList([str(o) for o in raw_options])
```

- [ ] **Step 4: Add the two Settings fields**

In `agent/app/config.py`, immediately after `case_taxonomy_json`'s closing `)` (currently line 137), add:

```python

    # Vehicle-model / case-type dimensions — SAME values as backend/'s
    # VEHICLE_MODELS_JSON / CASE_TYPE_OPTIONS_JSON (each service parses
    # independently). Used by services/categorize.py's fallback classifier.
    vehicle_models_json: str = '{"options": ["e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "Not Applicable"]}'
    case_type_options_json: str = '{"options": ["Inquiry", "Complaint", "Feedback"]}'
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd agent && pytest tests/test_option_lists.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
cd agent
git add app/services/option_lists.py tests/test_option_lists.py app/config.py
git commit -m "feat: mirror OptionList loader in agent/ for case_type/vehicle_model fallback"
```

---

### Task 3: Extend `classify_ticket_tool` for `case_type`/`vehicle_model`

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/agents.py` (`build_ai_agent`, `classify_ticket_tool` at lines 58-124)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_classify_ticket_tool.py` (new — check first whether it already exists from the case-categories work; if so, extend it instead of recreating)

**Interfaces:**
- Consumes: `OptionList`/`build_option_list` from Task 1.
- Produces: `classify_ticket_tool` gains two new parameters, `case_type: str` and `vehicle_model: str`; each is written to `tool_context.state` only if valid against its configured `OptionList` (or unconditionally when that list is empty — same fallback semantics as `case_category`).

- [ ] **Step 1: Check for an existing test file**

Run: `ls backend/apps/backend/src/chatbot/features/chat/test_classify_ticket_tool.py 2>/dev/null || echo "not found"`. If found, read it first and extend its existing tool-lookup helper rather than duplicating one.

- [ ] **Step 2: Write the failing test**

```python
# backend/apps/backend/src/chatbot/features/chat/test_classify_ticket_tool.py
import pytest

from chatbot.features.chat.agents import build_ai_agent
from chatbot.platform.config import get_settings


class _InMemoryTicketing:
    async def create_ticket(self, **kwargs):
        return "T-1"


class _InMemoryKnowledge:
    async def search_kb(self, query, limit=2):
        return []


def _classify_tool(**settings_overrides):
    settings = get_settings().model_copy(update=settings_overrides)
    agent = build_ai_agent(settings, _InMemoryTicketing(), _InMemoryKnowledge())
    for tool in agent.tools:
        name = getattr(tool, "__name__", "") or getattr(getattr(tool, "func", None), "__name__", "")
        if name == "classify_ticket_tool":
            return tool
    pytest.fail("classify_ticket_tool not found in agent.tools")


class _FakeToolContext:
    def __init__(self):
        self.state = {}


TAXONOMY = '{"sales": {"label": "Sales", "subcategories": []}}'
CASE_TYPES = '{"options": ["Inquiry", "Complaint"]}'
MODELS = '{"options": ["e.MAS 5", "e.MAS 7"]}'


@pytest.mark.asyncio
async def test_valid_case_type_and_vehicle_model_written():
    tool = _classify_tool(
        case_taxonomy_json=TAXONOMY, case_type_options_json=CASE_TYPES, vehicle_models_json=MODELS
    )
    ctx = _FakeToolContext()
    await tool(
        ctx, category="sales", subcategory="", priority="HIGH", sla_minutes=60,
        case_type="Inquiry", vehicle_model="e.MAS 7",
    )
    assert ctx.state["case_type"] == "Inquiry"
    assert ctx.state["vehicle_model"] == "e.MAS 7"


@pytest.mark.asyncio
async def test_invalid_case_type_and_vehicle_model_not_written():
    tool = _classify_tool(
        case_taxonomy_json=TAXONOMY, case_type_options_json=CASE_TYPES, vehicle_models_json=MODELS
    )
    ctx = _FakeToolContext()
    await tool(
        ctx, category="sales", subcategory="", priority="LOW", sla_minutes=30,
        case_type="Not A Real Type", vehicle_model="Not A Real Model",
    )
    assert "case_type" not in ctx.state
    assert "vehicle_model" not in ctx.state


@pytest.mark.asyncio
async def test_empty_option_lists_fall_back_to_accepting_free_text():
    tool = _classify_tool(
        case_taxonomy_json=TAXONOMY, case_type_options_json="", vehicle_models_json=""
    )
    ctx = _FakeToolContext()
    await tool(
        ctx, category="sales", subcategory="", priority="LOW", sla_minutes=30,
        case_type="Anything", vehicle_model="Whatever",
    )
    assert ctx.state["case_type"] == "Anything"
    assert ctx.state["vehicle_model"] == "Whatever"
```

If `agent.tools` isn't iterable/introspectable this way in the installed ADK version, mirror whatever lookup mechanism `test_classify_ticket_tool.py` (if it already exists from the case-categories work) or `test_flag_for_ticket_tool.py` already uses.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_classify_ticket_tool.py -v`
Expected: FAIL — `classify_ticket_tool` doesn't accept `case_type`/`vehicle_model` params yet.

- [ ] **Step 4: Implement the extension**

In `backend/apps/backend/src/chatbot/features/chat/agents.py`:

1. Add the import alongside the existing `case_taxonomy` import (line 10):

```python
from chatbot.features.chat.option_lists import build_option_list
```

2. Inside `build_ai_agent`, right after `case_taxonomy = build_case_taxonomy(settings)` (line 35), add:

```python
    case_type_options = build_option_list(settings.case_type_options_json)
    vehicle_model_options = build_option_list(settings.vehicle_models_json)
```

3. Change `classify_ticket_tool`'s signature (line 58) to add the two new parameters, and extend its docstring `Args:` block:

```python
    async def classify_ticket_tool(
        tool_context: ToolContext,
        category: str,
        subcategory: str,
        priority: str,
        sla_minutes: int,
        case_type: str,
        vehicle_model: str,
    ) -> str:
        """Classify the current ticket details.

        Args:
            tool_context: Context injected by the ADK runner.
            category: General category of the problem.
            subcategory: Precise subcategory matching the chosen category.
            priority: Priority tier (LOW, MEDIUM, HIGH, URGENT).
            sla_minutes: Targeted SLA duration in minutes.
            case_type: Whether this is an Inquiry, Complaint, or Feedback.
            vehicle_model: The customer's vehicle model, if mentioned.
        """
```

4. Immediately after the existing `if written: return ...` / `return ...` block that ends the category/subcategory handling (the two-branch `if written / return` logic already present, lines ~101-109), insert the case_type/vehicle_model handling BEFORE that final `return` (so the return statement still reports on everything). Concretely, replace the existing tail of the function (from `if written:` through its closing `return (...)` line) with:

```python
        if case_type_options.is_empty() or case_type_options.is_valid(case_type):
            tool_context.state["case_type"] = case_type
        else:
            _log.warning("classify_ticket_tool_invalid_case_type", case_type=case_type)

        if vehicle_model_options.is_empty() or vehicle_model_options.is_valid(vehicle_model):
            tool_context.state["vehicle_model"] = vehicle_model
        else:
            _log.warning("classify_ticket_tool_invalid_vehicle_model", vehicle_model=vehicle_model)

        if written:
            return (
                f"[internal] ticket classified as {category} -> {subcategory} "
                f"({priority}, SLA {sla_minutes}m, type={case_type}, model={vehicle_model})."
            )
        return (
            f"[internal] category '{category}' / subcategory '{subcategory}' is not a "
            f"valid taxonomy entry; not recorded ({priority}, SLA {sla_minutes}m)."
        )
```

5. In the `if not case_taxonomy.is_empty(): classify_ticket_tool.__doc__ = ...` block (lines 111-124), append two more lines to the docstring template so the model knows the valid case_type/vehicle_model options when they're configured:

```python
            + (
                f"\n    case_type: MUST be exactly one of: {', '.join(case_type_options.options())}."
                if not case_type_options.is_empty() else ""
            )
            + (
                f"\n    vehicle_model: MUST be exactly one of: {', '.join(vehicle_model_options.options())}."
                if not vehicle_model_options.is_empty() else ""
            )
        )
```

(this appends onto the existing string-concatenation docstring assignment — the final `)` that already closes it moves down to after these two new appended lines).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_classify_ticket_tool.py -v`
Expected: PASS (3 tests, plus any pre-existing category/subcategory tests in this file still passing).

- [ ] **Step 6: Run the full chat suite for regressions**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/ -k "classify or agent" -v`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/agents.py src/chatbot/features/chat/test_classify_ticket_tool.py
git commit -m "feat(chat): classify_ticket_tool emits case_type/vehicle_model, validated against configured options"
```

---

### Task 4: Write `case_type`/`vehicle_model` as custom attributes + extend provisioning

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py` (`create_ticket` custom_attrs block ~lines 510-526; `open_handoff` custom_attrs block ~lines 731-746; both call `TicketingPort`/`HandoffOpenPayload` — check `ports.py`/`schemas.py` for where `category`/`subcategory` params are declared and add the two new params alongside them)
- Modify: `chatwoot-config/provision_case_taxonomy.py` (extend to also provision `case_type`/`vehicle_model`)
- Modify: `chatwoot-config/test_provision_case_taxonomy.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_chatwoot_ticketing.py` (extend existing custom_attributes assertions)

**Interfaces:**
- Consumes: nothing new at the adapter layer beyond two more `str | None` parameters threaded through the same call chain `category`/`subcategory` already use.
- Produces: `custom_attrs` dict at both call sites gains `case_type`/`vehicle_model` keys when present.

- [ ] **Step 1: Locate where `category`/`subcategory` are declared as ticketing parameters**

Run: `grep -n "category\|subcategory" backend/apps/backend/src/chatbot/features/chat/ports.py backend/apps/backend/src/chatbot/features/chat/schemas.py`

Add `case_type: str | None = None` and `vehicle_model: str | None = None` next to `category`/`subcategory` in both: the `TicketingPort.create_ticket` protocol signature (`ports.py`) and the `HandoffOpenPayload` dataclass/model (`schemas.py`) — mirror whatever exact parameter ordering/typing convention `category`/`subcategory` already use there (Optional with `= None` default, keyword-only if the existing ones are).

Also check `backend/apps/backend/src/chatbot/features/chat/service.py`/`agents.py`'s `tool_context.state` → ticket-creation call site (wherever `state["category"]`/`state["subcategory"]` get read back out and passed into `create_ticket`/`open_handoff`) and add `case_type=state.get("case_type")`, `vehicle_model=state.get("vehicle_model")` there too — grep `state.get("category")` or `state\["category"\]` to find this call site.

- [ ] **Step 2: Write the failing test**

```python
# addition to test_chatwoot_ticketing.py — match this file's real fixture names
@pytest.mark.asyncio
async def test_create_ticket_writes_case_type_and_vehicle_model_as_custom_attributes(
    chatwoot_adapter, respx_mock
):
    respx_mock.post(f"{BASE}/conversations/{CONV_ID}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )
    # ... existing setup for the other mocked calls create_ticket makes ...

    await chatwoot_adapter.create_ticket(
        session_id="s1", title="t", body="b", urgency="high",
        category="sales", subcategory="Test Drive Booking",
        division="Sales", department="dept_sales", sla_minutes=60,
        case_type="Inquiry", vehicle_model="e.MAS 7",
    )

    custom_attrs_calls = [
        c for c in respx_mock.calls if c.request.url.path.endswith("/custom_attributes")
    ]
    assert len(custom_attrs_calls) == 1
    body = json.loads(custom_attrs_calls[0].request.content)
    assert body["custom_attributes"]["case_type"] == "Inquiry"
    assert body["custom_attributes"]["vehicle_model"] == "e.MAS 7"
```

Match fixture names to this file's actual conventions (read it first, per Task 1's Step 1 lookup).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_chatwoot_ticketing.py -k case_type -v`
Expected: FAIL — `create_ticket` doesn't accept `case_type`/`vehicle_model` yet.

- [ ] **Step 4: Implement the write-path change**

In `backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`, `create_ticket` (around lines 476-526): add `case_type: str | None = None, vehicle_model: str | None = None` to the method signature (alongside `category`/`subcategory`/`sla_minutes`), and extend the `custom_attrs` dict block:

```python
        custom_attrs: dict[str, Any] = {}
        if sla_minutes is not None:
            custom_attrs["sla_minutes"] = sla_minutes
        if category:
            custom_attrs["case_category"] = category
        if subcategory:
            custom_attrs["case_subcategory"] = subcategory
        if case_type:
            custom_attrs["case_type"] = case_type
        if vehicle_model:
            custom_attrs["vehicle_model"] = vehicle_model
```

Apply the identical two-line addition to `open_handoff`'s equivalent `custom_attrs` block (around line 736-741), reading `payload.case_type`/`payload.vehicle_model`.

- [ ] **Step 5: Extend the provisioning script**

In `chatwoot-config/provision_case_taxonomy.py`, add two new functions and two new `_upsert` calls. Rename the module's purpose slightly in its docstring (first line) to "case_category/case_subcategory/case_type/vehicle_model", then:

```python
def _flat_options(raw_json: str) -> list[str]:
    """Parse a `{"options": [...]}` blob (same shape as backend/'s
    OptionList config) into a plain list, defaulting to [] on any error —
    this script has no logger, so a bad env var just yields no options
    provisioned for that attribute (upsert with an empty list clears it)."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    options = data.get("options")
    return [str(o) for o in options] if isinstance(options, list) else []
```

In `main()`, after the existing two `_upsert` calls (lines 104-105), add:

```python
    case_types_raw = os.environ.get("CASE_TYPE_OPTIONS_JSON", "").strip()
    vehicle_models_raw = os.environ.get("VEHICLE_MODELS_JSON", "").strip()
    if case_types_raw:
        _upsert(client, base, "case_type", "Case Type", _flat_options(case_types_raw), args.dry_run)
    if vehicle_models_raw:
        _upsert(client, base, "vehicle_model", "Vehicle Model", _flat_options(vehicle_models_raw), args.dry_run)
```

- [ ] **Step 6: Extend the provisioning script's test**

```python
# addition to chatwoot-config/test_provision_case_taxonomy.py
from provision_case_taxonomy import _flat_options


def test_flat_options_parses_options_list():
    assert _flat_options('{"options": ["Inquiry", "Complaint"]}') == ["Inquiry", "Complaint"]


def test_flat_options_malformed_json_yields_empty_list():
    assert _flat_options("{not json") == []


def test_flat_options_missing_key_yields_empty_list():
    assert _flat_options("{}") == []
```

- [ ] **Step 7: Run all the tests**

Run:
```bash
cd backend/apps/backend && pytest src/chatbot/features/chat/test_chatwoot_ticketing.py -v
cd ../../../chatwoot-config && python3 -m pytest test_provision_case_taxonomy.py -v
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/adapters/chatwoot.py src/chatbot/features/chat/ports.py src/chatbot/features/chat/schemas.py src/chatbot/features/chat/test_chatwoot_ticketing.py src/chatbot/features/chat/agents.py
git commit -m "feat(chat): write case_type/vehicle_model as custom attributes"
cd ../../../../..
git add chatwoot-config/provision_case_taxonomy.py chatwoot-config/test_provision_case_taxonomy.py
git commit -m "feat(chatwoot-config): provision case_type/vehicle_model custom attribute definitions"
```

---

### Task 5: `agent/categorize.py` fallback for `case_type`/`vehicle_model`

**Files:**
- Modify: `agent/app/services/categorize.py` (`maybe_categorize`, lines 70-121)
- Test: `agent/tests/test_categorize.py`

**Interfaces:**
- Consumes: `OptionList`/`build_option_list` from Task 2.
- Produces: `maybe_categorize` additionally best-effort-classifies `case_type` and `vehicle_model` when they're empty on the conversation, using the SAME Gemini `classify_category` call already used for `case_category` (candidates = the configured option list instead of taxonomy slugs).

- [ ] **Step 1: Write the failing test**

```python
# addition to agent/tests/test_categorize.py — match this file's existing
# ChatwootClient stub/fixture names (read the file first).
@pytest.mark.asyncio
async def test_maybe_categorize_also_classifies_case_type_and_vehicle_model(monkeypatch, chatwoot_client_stub):
    chatwoot_client_stub.conversations[3] = {"id": 3, "custom_attributes": {}}
    chatwoot_client_stub.messages[3] = [
        {"content": "I want to book a test drive for the e.MAS 7", "private": False, "sender": {"type": "contact"}}
    ]
    settings = get_settings().model_copy(update={
        "lifecycle_auto_categorize": True,
        "case_taxonomy_json": '{"sales": {"label": "Sales", "subcategories": []}}',
        "case_type_options_json": '{"options": ["Inquiry", "Complaint"]}',
        "vehicle_models_json": '{"options": ["e.MAS 5", "e.MAS 7"]}',
    })
    monkeypatch.setattr(
        "app.services.categorize.classify_category",
        lambda transcript, candidates: (
            "sales" if candidates == ["sales"]
            else "Inquiry" if "Inquiry" in candidates
            else "e.MAS 7" if "e.MAS 7" in candidates
            else None
        ),
    )
    await maybe_categorize(3, settings=settings, chatwoot=chatwoot_client_stub)
    written = chatwoot_client_stub.set_custom_attributes_calls[-1][1]
    assert written["case_category"] == "Sales"
    assert written["case_type"] == "Inquiry"
    assert written["vehicle_model"] == "e.MAS 7"


@pytest.mark.asyncio
async def test_maybe_categorize_skips_case_type_when_already_set(monkeypatch, chatwoot_client_stub):
    chatwoot_client_stub.conversations[4] = {
        "id": 4, "custom_attributes": {"case_type": "Complaint"},
    }
    chatwoot_client_stub.messages[4] = [{"content": "hello", "private": False, "sender": {"type": "contact"}}]
    settings = get_settings().model_copy(update={
        "lifecycle_auto_categorize": True,
        "case_taxonomy_json": '{"sales": {"label": "Sales", "subcategories": []}}',
        "case_type_options_json": '{"options": ["Inquiry", "Complaint"]}',
    })
    monkeypatch.setattr("app.services.categorize.classify_category", lambda transcript, candidates: "sales")
    await maybe_categorize(4, settings=settings, chatwoot=chatwoot_client_stub)
    written = chatwoot_client_stub.set_custom_attributes_calls[-1][1]
    assert "case_type" not in written
```

Note: `classify_category`'s real signature is `async def classify_category(transcript, candidates)` — `monkeypatch.setattr` with a plain (non-async) lambda works with `unittest.mock`-style patching only if the test file already does this elsewhere for this function (check the existing `test_categorize.py` pattern from Task 6 of the case-categories plan and mirror it exactly, including whether it needs an `AsyncMock` instead of a lambda).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_categorize.py -k "case_type or vehicle_model" -v`
Expected: FAIL — `maybe_categorize` doesn't touch `case_type`/`vehicle_model` yet.

- [ ] **Step 3: Implement the extension**

In `agent/app/services/categorize.py`:

1. Add the import:

```python
from app.services.option_lists import build_option_list
```

2. Inside `maybe_categorize`, after the taxonomy-empty early-return (`if taxonomy.is_empty(): return`) but before building `attrs` (i.e. right after the `transcript = _transcript_from_messages(...)` / `if not transcript: return` block, before `category = await classify_category(...)`), no change needed there — insert the new classification AFTER the existing `attrs = {"case_category": label}` block and its subcategory branch (i.e. append at the end of the function body, before `await chatwoot.set_custom_attributes(conversation_id, attrs)`):

```python
        case_type_options = build_option_list(settings.case_type_options_json)
        if case_type_options.options() and not existing.get("case_type"):
            case_type = await classify_category(transcript, case_type_options.options())
            if case_type is not None:
                attrs["case_type"] = case_type

        vehicle_model_options = build_option_list(settings.vehicle_models_json)
        if vehicle_model_options.options() and not existing.get("vehicle_model"):
            vehicle_model = await classify_category(transcript, vehicle_model_options.options())
            if vehicle_model is not None:
                attrs["vehicle_model"] = vehicle_model
```

(place this block immediately before the existing `await chatwoot.set_custom_attributes(conversation_id, attrs)` line at the end of `maybe_categorize`). `classify_category`'s existing "accept only if the answer is in `candidates`" fail-open behavior is reused unmodified — `case_type_options.options()`/`vehicle_model_options.options()` are just another candidate list.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_categorize.py -v`
Expected: PASS, including all pre-existing tests in this file.

- [ ] **Step 5: Commit**

```bash
cd agent
git add app/services/categorize.py tests/test_categorize.py
git commit -m "feat: agent/categorize.py fallback also classifies case_type/vehicle_model"
```

---

### Task 6: `case_type`/`vehicle_model` in the metrics pipeline (schema + mapping + sync)

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py` (`CONVERSATIONS_SCHEMA`, lines 9-31)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/mapping.py` (`ConversationRow` dataclass lines 47-66; `map_chatwoot_conversation_to_row` lines 279-330)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/sync.py` (`load_conversations`, lines 100-131)
- Test: `backend/apps/backend/src/chatbot/features/metrics/test_mapping.py`, `test_bigquery_schema.py`, `test_sync.py`

**Interfaces:**
- Produces: `ConversationRow` gains `case_type: str | None = None`, `vehicle_model: str | None = None`; `CONVERSATIONS_SCHEMA` gains matching nullable `STRING` fields; `load_conversations`'s `json_rows` dict includes both.

- [ ] **Step 1: Write the failing tests**

```python
# addition to test_mapping.py
def test_map_chatwoot_conversation_reads_case_type_and_vehicle_model():
    conv = {
        "id": 50,
        "status": "resolved",
        "created_at": 1700000000,
        "last_activity_at": 1700003600,
        "labels": ["division_sales"],
        "custom_attributes": {"case_type": "Inquiry", "vehicle_model": "e.MAS 7"},
        "meta": {"sender": {"id": 1, "phone_number": "+60123456789"}},
    }
    row = map_chatwoot_conversation_to_row(conv)
    assert row is not None
    assert row.case_type == "Inquiry"
    assert row.vehicle_model == "e.MAS 7"


def test_map_chatwoot_conversation_missing_case_type_yields_none():
    conv = {
        "id": 51, "status": "open", "created_at": 1700000000,
        "labels": ["division_apps"], "meta": {"sender": {"id": 1}},
    }
    row = map_chatwoot_conversation_to_row(conv)
    assert row is not None
    assert row.case_type is None
    assert row.vehicle_model is None
```

```python
# addition to test_bigquery_schema.py — extend the existing field-set assertion
def test_schema_has_case_type_and_vehicle_model_fields() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    assert "case_type" in names
    assert "vehicle_model" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -k "case_type or vehicle_model" -v`
Expected: FAIL — neither field exists yet.

- [ ] **Step 3: Implement the schema change**

In `bigquery_schema.py`'s `CONVERSATIONS_SCHEMA` list, after the `dealer` field (line 30), add:

```python
    bigquery.SchemaField("case_type", "STRING"),
    bigquery.SchemaField("vehicle_model", "STRING"),
```

- [ ] **Step 4: Implement the mapping change**

In `mapping.py`'s `ConversationRow` dataclass, after `dealer: str | None = None` (line 66), add:

```python
    case_type: str | None = None
    vehicle_model: str | None = None
```

In `map_chatwoot_conversation_to_row`, after the existing `category = custom_attrs.get("case_category")` / `subcategory = custom_attrs.get("case_subcategory")` lines (~308-309), add:

```python
    case_type = custom_attrs.get("case_type")
    vehicle_model = custom_attrs.get("vehicle_model")
```

Then add `case_type=case_type, vehicle_model=vehicle_model,` to the `ConversationRow(...)` construction at the end of the function (alongside the existing `dealer=dealer,` line).

- [ ] **Step 5: Implement the sync-load change**

In `sync.py`'s `load_conversations`, in the `json_rows` list-comprehension dict, after `"dealer": r.dealer,  # Phase-3` add:

```python
            "case_type": r.case_type,
            "vehicle_model": r.vehicle_model,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -v`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/sync.py src/chatbot/features/metrics/test_mapping.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): sync case_type/vehicle_model into the conversations BQ table"
```

---

### Task 7: Business-hours-aware duration calculation (pure function)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/metrics/business_hours.py`
- Create: `backend/apps/backend/src/chatbot/features/metrics/test_business_hours.py`

**Interfaces:**
- Produces: `working_minutes_between(start: datetime, end: datetime, inbox: dict) -> int`. Used by Task 8.

`agent/app/services/business_hours.py::is_within_business_hours` only answers "is this ONE instant inside business hours" — it can't sum working minutes across a date range. This is a NEW capability (not a literal port), implemented independently in `backend/` per this repo's service-decoupling convention, reusing the identical `working_hours` row shape (`day_of_week` 0=Sunday..6=Saturday, `open_hour`/`open_minutes`/`close_hour`/`close_minutes`/`open_all_day`/`closed_all_day`) that Chatwoot's `GET /inboxes/{id}` already returns and that `agent/`'s version already parses.

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/backend/src/chatbot/features/metrics/test_business_hours.py
from datetime import UTC, datetime

from chatbot.features.metrics.business_hours import working_minutes_between

# Monday-Friday 09:00-18:00 UTC (day_of_week: Sunday=0..Saturday=6)
INBOX_9_TO_6 = {
    "working_hours_enabled": True,
    "timezone": "UTC",
    "working_hours": [
        {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 18, "close_minutes": 0,
         "open_all_day": False, "closed_all_day": False}
        for d in (1, 2, 3, 4, 5)  # Mon..Fri
    ] + [
        {"day_of_week": d, "closed_all_day": True} for d in (0, 6)  # Sun, Sat
    ],
}

NO_HOURS_CONFIGURED = {"working_hours_enabled": False}


def test_same_day_within_hours():
    start = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)  # Monday
    end = datetime(2026, 7, 6, 12, 30, tzinfo=UTC)
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 150


def test_spans_a_weekend_excludes_it():
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)  # Friday 17:00 (1h left in the day)
    end = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)     # Monday 10:00 (1h into the day)
    # Fri 17:00-18:00 = 60min, Sat/Sun = 0, Mon 09:00-10:00 = 60min
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 120


def test_starts_before_hours_clips_to_open():
    start = datetime(2026, 7, 6, 6, 0, tzinfo=UTC)   # Monday 06:00, before 09:00 open
    end = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 60


def test_ends_after_hours_clips_to_close():
    start = datetime(2026, 7, 6, 17, 0, tzinfo=UTC)
    end = datetime(2026, 7, 6, 23, 0, tzinfo=UTC)  # well past 18:00 close
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 60


def test_no_hours_configured_falls_back_to_calendar_minutes():
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)
    end = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    total_calendar_minutes = int((end - start).total_seconds() // 60)
    assert working_minutes_between(start, end, NO_HOURS_CONFIGURED) == total_calendar_minutes


def test_end_before_start_returns_zero():
    start = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    end = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
    assert working_minutes_between(start, end, INBOX_9_TO_6) == 0


def test_unknown_timezone_falls_back_to_utc_not_crash():
    inbox = dict(INBOX_9_TO_6, timezone="Not/AZone")
    start = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    end = datetime(2026, 7, 6, 11, 0, tzinfo=UTC)
    assert working_minutes_between(start, end, inbox) == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_business_hours.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `business_hours.py`**

```python
# backend/apps/backend/src/chatbot/features/metrics/business_hours.py
"""Sum the working minutes between two timestamps, per a Chatwoot inbox's
native business-hours config (GET /inboxes/{id}).

NOT a copy of agent/app/services/business_hours.py's is_within_business_hours
(a point-in-time boolean) — this computes a DURATION across a date range, a
capability that module doesn't have. Independently implemented in backend/
per this repo's agent/backend service-decoupling convention; both read the
identical `working_hours` row shape Chatwoot returns (day_of_week 0=Sunday..
6=Saturday, open/close hour+minutes, open_all_day/closed_all_day).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)


def working_minutes_between(start: datetime, end: datetime, inbox: dict) -> int:
    """Minutes between start and end that fall within inbox's working hours.

    Both start/end must be timezone-aware. Falls back to plain calendar
    minutes when the inbox has no working hours configured (mirrors
    is_within_business_hours' "always open" fallback). end <= start -> 0.
    """
    if end <= start:
        return 0
    if not inbox.get("working_hours_enabled"):
        return int((end - start).total_seconds() // 60)

    tz_name = inbox.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        _log.debug("working_minutes_between: unknown timezone %r, using UTC", tz_name)
        tz = timezone.utc

    start_local = start.astimezone(tz)
    end_local = end.astimezone(tz)
    rows_by_dow = {r.get("day_of_week"): r for r in (inbox.get("working_hours") or [])}

    total_minutes = 0
    cursor_date: date = start_local.date()
    while cursor_date <= end_local.date():
        dow = (cursor_date.isoweekday()) % 7  # Python Mon=1..Sun=7 -> Chatwoot Sun=0..Sat=6
        row = rows_by_dow.get(dow)
        day_start = datetime.combine(cursor_date, time.min, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        window_start = max(start_local, day_start)
        window_end = min(end_local, day_end)

        if row and not row.get("closed_all_day"):
            if row.get("open_all_day"):
                open_dt, close_dt = day_start, day_end
            else:
                open_dt = day_start + timedelta(
                    hours=int(row.get("open_hour", 0)), minutes=int(row.get("open_minutes", 0))
                )
                close_dt = day_start + timedelta(
                    hours=int(row.get("close_hour", 0)), minutes=int(row.get("close_minutes", 0))
                )
            overlap_start = max(window_start, open_dt)
            overlap_end = min(window_end, close_dt)
            if overlap_end > overlap_start:
                total_minutes += int((overlap_end - overlap_start).total_seconds() // 60)

        cursor_date += timedelta(days=1)

    return total_minutes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_business_hours.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/business_hours.py src/chatbot/features/metrics/test_business_hours.py
git commit -m "feat(metrics): add working_minutes_between for business-hours-aware SLA calc"
```

---

### Task 8: Wire business-hours timing into the sync pipeline

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py` (`CONVERSATIONS_SCHEMA`)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/mapping.py` (`ConversationRow`; new pure function `apply_working_hours`)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/sync.py` (`run_sync`, `load_conversations`; new `fetch_inbox_hours`)
- Test: `test_mapping.py`, `test_sync.py`, `test_bigquery_schema.py`

**Interfaces:**
- Consumes: `working_minutes_between` from Task 7.
- Produces: `ConversationRow` gains `first_response_working_minutes: int | None`, `resolution_working_minutes: int | None`; `run_sync` fetches each unique `inbox_id`'s hours once per sync run (cached in a plain dict) and augments each row; a per-inbox fetch failure leaves that row's two new fields as calendar-time minutes (never None, never sync-wide failure).

- [ ] **Step 1: Write the failing tests**

```python
# addition to test_mapping.py
from datetime import UTC, datetime

from chatbot.features.metrics.mapping import ConversationRow, apply_working_hours

INBOX = {
    "working_hours_enabled": True,
    "timezone": "UTC",
    "working_hours": [
        {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 18, "close_minutes": 0,
         "open_all_day": False, "closed_all_day": False}
        for d in (1, 2, 3, 4, 5)
    ] + [{"day_of_week": d, "closed_all_day": True} for d in (0, 6)],
}


def _row(**overrides):
    base = dict(
        conversation_id="1", channel="Web", created_at="2026-07-06T10:00:00+00:00",
        updated_at="2026-07-06T12:30:00+00:00", status="resolved", resolved_by="agent",
        csat_score=None, nps_score=None,
        first_response_at="2026-07-06T10:30:00+00:00",
        resolved_at="2026-07-06T12:30:00+00:00",
    )
    base.update(overrides)
    return ConversationRow(**base)


def test_apply_working_hours_computes_both_durations():
    row = apply_working_hours(_row(), INBOX)
    assert row.first_response_working_minutes == 30
    assert row.resolution_working_minutes == 150


def test_apply_working_hours_none_inbox_falls_back_to_calendar_minutes():
    row = apply_working_hours(_row(), None)
    assert row.first_response_working_minutes == 30  # same as calendar here (all within one day)
    assert row.resolution_working_minutes == 150


def test_apply_working_hours_missing_timestamps_yields_none():
    row = apply_working_hours(_row(first_response_at=None, resolved_at=None), INBOX)
    assert row.first_response_working_minutes is None
    assert row.resolution_working_minutes is None
```

```python
# addition to test_bigquery_schema.py
def test_schema_has_working_minutes_fields() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    assert "first_response_working_minutes" in names
    assert "resolution_working_minutes" in names
```

```python
# addition to test_sync.py — match this file's existing run_sync test fixtures
def test_run_sync_augments_rows_with_working_minutes(monkeypatch):
    conv = {
        "id": 99, "inbox_id": 7, "status": "resolved",
        "created_at": 1751792400,  # 2026-07-06T09:00:00Z
        "last_activity_at": 1751801400,  # 2026-07-06T11:30:00Z
        "labels": [], "custom_attributes": {},
        "meta": {"sender": {"id": 1}},
        "first_reply_created_at": 1751794200,  # 2026-07-06T09:30:00Z
    }
    inbox_hours = {
        "working_hours_enabled": True, "timezone": "UTC",
        "working_hours": [{"day_of_week": 1, "open_hour": 9, "open_minutes": 0,
                            "close_hour": 18, "close_minutes": 0,
                            "open_all_day": False, "closed_all_day": False}],
    }
    loaded_rows = []
    result = run_sync(
        get_settings(),
        fetch=lambda settings: [conv],
        fetch_inbox=lambda settings, inbox_id: inbox_hours,
        load=lambda settings, rows: loaded_rows.extend(rows),
    )
    assert result["rows"] == 1
    assert loaded_rows[0].resolution_working_minutes == 150
```

Check `test_sync.py`'s existing `run_sync` call signature first — if `run_sync` doesn't yet accept a `fetch_inbox` injectable, this test also verifies the new parameter Step 3 below adds.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -k "working_minutes or working_hours" -v`
Expected: FAIL — none of this exists yet.

- [ ] **Step 3: Implement the schema + mapping changes**

In `bigquery_schema.py`'s `CONVERSATIONS_SCHEMA`, after the `vehicle_model` field added in Task 6, add:

```python
    bigquery.SchemaField("first_response_working_minutes", "INT64"),
    bigquery.SchemaField("resolution_working_minutes", "INT64"),
```

In `mapping.py`'s `ConversationRow`, after `vehicle_model: str | None = None` (added in Task 6), add:

```python
    first_response_working_minutes: int | None = None
    resolution_working_minutes: int | None = None
```

Add the new pure function at the end of `mapping.py`:

```python
def apply_working_hours(row: ConversationRow, inbox: dict | None) -> ConversationRow:
    """Return a copy of row with first_response_working_minutes/
    resolution_working_minutes computed. inbox=None (hours fetch failed or
    inbox has no hours configured) -> plain calendar-time minutes, per this
    plan's fallback rule (never leaves these fields silently None when the
    underlying timestamps exist)."""
    from dataclasses import replace

    def _minutes(start_iso: str | None, end_iso: str | None) -> int | None:
        if not start_iso or not end_iso:
            return None
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        if inbox is None:
            return max(0, int((end - start).total_seconds() // 60))
        return working_minutes_between(start, end, inbox)

    return replace(
        row,
        first_response_working_minutes=_minutes(row.created_at, row.first_response_at),
        resolution_working_minutes=_minutes(row.created_at, row.resolved_at),
    )
```

Add the import at the top of `mapping.py`:

```python
from chatbot.features.metrics.business_hours import working_minutes_between
```

- [ ] **Step 4: Implement the sync wiring**

In `sync.py`, add a new function (after `fetch_conversations`):

```python
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
```

Replace `run_sync` with a version that augments rows with working-hours timing, keeping `fetch`/`load` injectable exactly as before and adding a new injectable `fetch_inbox`:

```python
def run_sync(
    settings: Settings,
    *,
    fetch: Callable[[Settings], list[dict[str, Any]]] | None = None,
    fetch_inbox: Callable[[Settings, int], dict[str, Any] | None] | None = None,
    load: Callable[[Settings, list[ConversationRow]], None] | None = None,
) -> dict[str, int]:
    """Fetch conversations, map to rows, augment with business-hours timing,
    load. Returns counts. Injectable for tests."""
    conversations = (fetch or fetch_conversations)(settings)
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
                inbox_cache[inbox_id] = fetch_inbox_fn(settings, inbox_id)
            inbox = inbox_cache[inbox_id]
        rows.append(apply_working_hours(row, inbox))

    (load or load_conversations)(settings, rows)
    _log.info("metrics_sync_done", conversations=len(conversations), rows=len(rows))
    return {"conversations": len(conversations), "rows": len(rows)}
```

Add the import: `from chatbot.features.metrics.mapping import ConversationRow, apply_working_hours, map_chatwoot_conversation_to_row` (replacing the existing narrower import line).

In `load_conversations`'s `json_rows` dict, after the `vehicle_model` fields added in Task 6, add:

```python
            "first_response_working_minutes": r.first_response_working_minutes,
            "resolution_working_minutes": r.resolution_working_minutes,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/sync.py src/chatbot/features/metrics/test_mapping.py src/chatbot/features/metrics/test_sync.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): compute business-hours-aware first-response/resolution durations at sync time"
```

---

### Task 9: Stamp `dealer_escalated_at` when a dealer label first appears (agent/)

**IMPORTANT DEVIATION FROM THE SPEC:** the spec assumed `dealer_escalated_at` would be "stamped when a `dealer_<slug>` label is first applied — wherever that assignment currently happens." Research for this plan found **no code anywhere in `backend/` or `agent/` currently writes a `dealer_<slug>` label** — `mapping.py`'s `_DEALER_TAG` regex only *reads* it. Dealer labeling today is manual (an agent applies it via Chatwoot's native label picker), which is consistent with roadmap item #6 ("Dealer-forward explicit agent action") being a still-unbuilt future feature. Also, `sync.py`'s `load_conversations` does a `WRITE_TRUNCATE` full-table reload every run — there is no previous-sync state to diff against, so "first time this label appears" cannot be detected inside the BigQuery sync path at all. This task adds the smallest correct fix: a Chatwoot-side webhook handler in `agent/` (which already receives `conversation_updated` events) that stamps a `dealer_escalated_at` custom attribute the first time it sees a `dealer_*` label on a conversation — idempotent, fail-open, follows this repo's existing webhook-handler pattern exactly.

**Files:**
- Modify: `agent/app/services/sync.py` (add `maybe_stamp_dealer_escalation`)
- Modify: `agent/app/routers/chatwoot.py` (dispatch the new function alongside the existing `maybe_escalate` call on `conversation_updated`, line 56)
- Test: `agent/tests/test_sync_escalation.py` (or wherever `maybe_escalate`'s tests live — extend it) and `agent/tests/test_chatwoot_router.py`

**Interfaces:**
- Consumes: `ChatwootClient.get_conversation`/`.set_custom_attributes` (both already exist, `agent/app/clients/chatwoot.py` lines 41 and 139).
- Produces: `maybe_stamp_dealer_escalation(payload: dict) -> None`, dispatched as a `BackgroundTasks` task exactly like `maybe_escalate`.

- [ ] **Step 1: Write the failing test**

```python
# addition to agent/tests/test_sync_escalation.py — match this file's existing
# ChatwootClient stub/fixture conventions
import pytest

from app.services.sync import maybe_stamp_dealer_escalation


@pytest.mark.asyncio
async def test_stamps_dealer_escalated_at_on_first_dealer_label(chatwoot_client_stub):
    chatwoot_client_stub.conversations[10] = {"id": 10, "custom_attributes": {}}
    payload = {"id": 10, "labels": ["division_sales", "dealer_kl_glenmarie"]}

    await maybe_stamp_dealer_escalation(payload)

    calls = chatwoot_client_stub.set_custom_attributes_calls
    assert len(calls) == 1
    assert calls[0][0] == 10
    assert "dealer_escalated_at" in calls[0][1]


@pytest.mark.asyncio
async def test_no_dealer_label_no_op(chatwoot_client_stub):
    payload = {"id": 11, "labels": ["division_sales"]}
    await maybe_stamp_dealer_escalation(payload)
    assert chatwoot_client_stub.set_custom_attributes_calls == []


@pytest.mark.asyncio
async def test_already_stamped_never_overwritten(chatwoot_client_stub):
    chatwoot_client_stub.conversations[12] = {
        "id": 12, "custom_attributes": {"dealer_escalated_at": "2026-07-01T00:00:00+00:00"},
    }
    payload = {"id": 12, "labels": ["dealer_kl_glenmarie"]}
    await maybe_stamp_dealer_escalation(payload)
    assert chatwoot_client_stub.set_custom_attributes_calls == []


@pytest.mark.asyncio
async def test_missing_conversation_id_no_op(chatwoot_client_stub):
    await maybe_stamp_dealer_escalation({"labels": ["dealer_kl_glenmarie"]})
    assert chatwoot_client_stub.set_custom_attributes_calls == []
```

Use whatever fixture/stub name this repo's existing `test_sync_escalation.py` already provides for a fake `ChatwootClient` (it must already exist, since `maybe_escalate` has tests) — do not introduce a second stub convention.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_sync_escalation.py -k dealer -v`
Expected: FAIL — `ImportError: cannot import name 'maybe_stamp_dealer_escalation'`.

- [ ] **Step 3: Implement `maybe_stamp_dealer_escalation`**

In `agent/app/services/sync.py`, add near the top (alongside the other `re` pattern constants, or inline if this file has none yet — check the file's imports first):

```python
import re
from datetime import UTC, datetime

_DEALER_LABEL = re.compile(r"^dealer_(.+)$")
```

Add the function after `maybe_escalate`:

```python
async def maybe_stamp_dealer_escalation(payload: dict) -> None:
    """Handle a Chatwoot `conversation_updated` event: stamp a
    `dealer_escalated_at` custom attribute the first time a `dealer_<slug>`
    label appears on the conversation, so the BI turnaround-time view has a
    real escalation timestamp to diff against `resolved_at`. Idempotent
    (never overwrites an existing stamp) and fail-open — a Chatwoot API
    error here must never affect the rest of the webhook dispatch."""
    conversation_id = payload.get("id")
    labels = payload.get("labels") or []
    if conversation_id is None or not any(_DEALER_LABEL.match(lbl) for lbl in labels):
        return

    try:
        chatwoot = get_chatwoot_client()
        conversation = await chatwoot.get_conversation(conversation_id)
        existing = (conversation or {}).get("custom_attributes") or {}
        if existing.get("dealer_escalated_at"):
            return  # already stamped — never overwrite

        await chatwoot.set_custom_attributes(
            conversation_id, {"dealer_escalated_at": datetime.now(UTC).isoformat()}
        )
    except Exception:
        logger.exception(
            "maybe_stamp_dealer_escalation: failed for conversation %s", conversation_id
        )
```

Check whether `sync.py` already imports `get_chatwoot_client` and `logger` (it almost certainly does, since `maybe_escalate` and sibling functions use them) — reuse those, don't re-import.

- [ ] **Step 4: Dispatch it from the webhook router**

In `agent/app/routers/chatwoot.py`, change line 56 from:

```python
    elif event == "conversation_updated":
        background_tasks.add_task(sync.maybe_escalate, payload)
```

to:

```python
    elif event == "conversation_updated":
        background_tasks.add_task(sync.maybe_escalate, payload)
        background_tasks.add_task(sync.maybe_stamp_dealer_escalation, payload)
```

- [ ] **Step 5: Add a router-level test**

```python
# addition to agent/tests/test_chatwoot_router.py
def test_conversation_updated_dispatches_both_escalate_and_dealer_stamp(monkeypatch, client, valid_signature_headers):
    calls = []
    monkeypatch.setattr("app.services.sync.maybe_escalate", lambda payload: calls.append(("escalate", payload)))
    monkeypatch.setattr("app.services.sync.maybe_stamp_dealer_escalation", lambda payload: calls.append(("dealer", payload)))
    response = client.post(
        "/webhooks/chatwoot",
        json={"event": "conversation_updated", "id": 1, "labels": ["dealer_x"]},
        headers=valid_signature_headers,
    )
    assert response.status_code == 200
    # BackgroundTasks run after the response in FastAPI's TestClient context manager usage —
    # follow whatever pattern this file's existing conversation_updated test already uses to
    # assert a background task was scheduled (e.g. inspecting response or a fixture that flushes
    # background tasks), rather than asserting `calls` directly if that pattern doesn't apply here.
```

Read this file's existing `conversation_updated` test (for `maybe_escalate`) first and mirror its exact assertion mechanism rather than the illustrative one above.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_sync_escalation.py tests/test_chatwoot_router.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd agent
git add app/services/sync.py app/routers/chatwoot.py tests/test_sync_escalation.py tests/test_chatwoot_router.py
git commit -m "feat: stamp dealer_escalated_at on first dealer_* label, for BI turnaround reporting"
```

---

### Task 10: `dealer_escalated_at` in the metrics pipeline

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py` (`CONVERSATIONS_SCHEMA`)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/mapping.py` (`ConversationRow`; `map_chatwoot_conversation_to_row`)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/sync.py` (`load_conversations`)
- Test: `test_mapping.py`, `test_bigquery_schema.py`

**Interfaces:**
- Produces: `ConversationRow.dealer_escalated_at: str | None`; read from `custom_attributes["dealer_escalated_at"]` (the field Task 9 writes).

- [ ] **Step 1: Write the failing test**

```python
# addition to test_mapping.py
def test_map_chatwoot_conversation_reads_dealer_escalated_at():
    conv = {
        "id": 60, "status": "resolved", "created_at": 1700000000,
        "labels": ["dealer_kl_glenmarie"],
        "custom_attributes": {"dealer_escalated_at": "2026-07-01T00:00:00+00:00"},
        "meta": {"sender": {"id": 1}},
    }
    row = map_chatwoot_conversation_to_row(conv)
    assert row is not None
    assert row.dealer_escalated_at == "2026-07-01T00:00:00+00:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_mapping.py -k dealer_escalated -v`
Expected: FAIL.

- [ ] **Step 3: Implement the change**

`bigquery_schema.py`: add `bigquery.SchemaField("dealer_escalated_at", "TIMESTAMP"),` to `CONVERSATIONS_SCHEMA`.

`mapping.py`: add `dealer_escalated_at: str | None = None` to `ConversationRow`. In `map_chatwoot_conversation_to_row`, after the `dealer = _first_tag(labels, _DEALER_TAG)` line, add `dealer_escalated_at = custom_attrs.get("dealer_escalated_at")`, and add `dealer_escalated_at=dealer_escalated_at,` to the `ConversationRow(...)` construction.

`sync.py`: add `"dealer_escalated_at": r.dealer_escalated_at,` to `load_conversations`'s `json_rows` dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/sync.py src/chatbot/features/metrics/test_mapping.py
git commit -m "feat(metrics): sync dealer_escalated_at into the conversations BQ table"
```

---

### Task 11: `RESOLUTION_SLA_TARGETS_JSON` config + `v_resolution_sla_buckets` view

**Files:**
- Modify: `backend/apps/backend/src/chatbot/platform/config.py` (add `resolution_sla_targets_json`)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py` (`view_ddls` signature + new view)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/sync.py` (`ensure_views` call site)
- Test: `test_bigquery_schema.py`

**Interfaces:**
- Produces: `view_ddls(project, dataset, table, sla_targets_json)` (signature change — one new required parameter; both call sites updated) creates `v_resolution_sla_buckets` with bucket edges baked into the generated SQL from the parsed config.

`view_ddls` currently takes only `(project, dataset, table)` — since BigQuery views are static SQL text, the per-`case_type` bucket edges from `RESOLUTION_SLA_TARGETS_JSON` must be interpolated into the generated DDL string at creation time, not read dynamically at query time.

- [ ] **Step 1: Add the Settings field**

In `config.py`, after `case_type_options_json` (added in Task 1), add:

```python

    # SOP resolution-time targets, in working hours, per case_type. JSON:
    # {"<case_type lowercased>": {"buckets_wh": [int, ...], "labels": [str, ...]}}.
    # buckets_wh are the upper edges (exclusive) of every bucket except the
    # last, which is open-ended; labels must have exactly one more entry than
    # buckets_wh. Malformed/missing entries fall back to being excluded from
    # v_resolution_sla_buckets (that case_type's rows simply won't bucket).
    resolution_sla_targets_json: str = (
        '{"inquiry": {"buckets_wh": [8], "labels": ["Within 8wh", ">8wh"]},'
        '"complaint": {"buckets_wh": [24, 48, 72], '
        '"labels": ["<24wh", "24-48wh", "48-72wh", ">72wh"]},'
        '"feedback": {"buckets_wh": [48], "labels": ["Within 48h", ">48h"]}}'
    )
```

- [ ] **Step 2: Write the failing test**

```python
# addition to test_bigquery_schema.py
def test_view_ddls_requires_sla_targets_and_creates_bucket_view() -> None:
    targets = '{"complaint": {"buckets_wh": [24, 48, 72], "labels": ["<24wh", "24-48wh", "48-72wh", ">72wh"]}}'
    ddls = view_ddls("proj", "ds", "conversations", targets)
    assert "v_resolution_sla_buckets" in ddls
    ddl = ddls["v_resolution_sla_buckets"]
    assert "resolution_working_minutes" in ddl
    assert "1440" in ddl  # 24wh * 60 minutes
    assert "case_type" in ddl


def test_view_ddls_malformed_sla_targets_yields_view_with_no_case_types() -> None:
    ddls = view_ddls("proj", "ds", "conversations", "{not valid json")
    assert "v_resolution_sla_buckets" in ddls  # view still created, just matches nothing
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_bigquery_schema.py -k sla_buckets -v`
Expected: FAIL — `view_ddls()` doesn't accept a 4th argument yet.

- [ ] **Step 4: Implement the view**

In `bigquery_schema.py`, change `view_ddls`'s signature and add the bucket-SQL builder:

```python
def _sla_bucket_case_sql(sla_targets_json: str) -> str:
    """Build a SQL CASE expression bucketing resolution_working_minutes per
    case_type, from RESOLUTION_SLA_TARGETS_JSON. Malformed JSON -> a CASE
    that matches nothing (ELSE NULL), so the view still creates cleanly and
    just returns zero rows until the config is fixed."""
    import json as _json

    try:
        targets = _json.loads(sla_targets_json or "{}")
    except (ValueError, TypeError):
        targets = {}
    if not isinstance(targets, dict):
        targets = {}

    branches: list[str] = []
    for case_type, spec in targets.items():
        if not isinstance(spec, dict):
            continue
        edges = spec.get("buckets_wh")
        labels = spec.get("labels")
        if not isinstance(edges, list) or not isinstance(labels, list) or len(labels) != len(edges) + 1:
            continue
        prev_minutes = 0
        for edge_wh, label in zip(edges, labels[:-1], strict=True):
            edge_minutes = int(edge_wh) * 60
            branches.append(
                f"WHEN LOWER(case_type) = '{case_type.lower()}' "
                f"AND resolution_working_minutes >= {prev_minutes} "
                f"AND resolution_working_minutes < {edge_minutes} THEN '{label}'"
            )
            prev_minutes = edge_minutes
        branches.append(
            f"WHEN LOWER(case_type) = '{case_type.lower()}' "
            f"AND resolution_working_minutes >= {prev_minutes} THEN '{labels[-1]}'"
        )
    if not branches:
        return "NULL"
    return "CASE " + " ".join(branches) + " ELSE NULL END"


def view_ddls(project: str, dataset: str, table: str, sla_targets_json: str = "{}") -> dict[str, str]:
    """The CREATE OR REPLACE VIEW statements for the Looker tiles."""
    fq = f"`{project}.{dataset}.{table}`"
    bucket_case = _sla_bucket_case_sql(sla_targets_json)
    return {
        # ... all existing entries UNCHANGED ...
        "v_resolution_sla_buckets": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_resolution_sla_buckets` AS "
            f"SELECT COALESCE(case_type, 'Unknown') AS case_type, "
            f"{bucket_case} AS bucket_label, "
            f"COUNT(*) AS cases "
            f"FROM {fq} WHERE resolution_working_minutes IS NOT NULL "
            f"GROUP BY case_type, bucket_label"
        ),
    }
```

(the `# ... all existing entries UNCHANGED ...` comment marks where every current dict entry stays exactly as-is — only the function signature and the new `v_resolution_sla_buckets` entry are additions; `_sla_bucket_case_sql` is a new top-level function above `view_ddls`.)

- [ ] **Step 5: Update the `ensure_views` call site**

In `sync.py`'s `ensure_views`, change:

```python
    for ddl in view_ddls(
        settings.bigquery_project_id,
        settings.bigquery_dataset,
        settings.bigquery_conversations_table,
    ).values():
```

to:

```python
    for ddl in view_ddls(
        settings.bigquery_project_id,
        settings.bigquery_dataset,
        settings.bigquery_conversations_table,
        settings.resolution_sla_targets_json,
    ).values():
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -v`
Expected: PASS (update any pre-existing `view_ddls("proj", "ds", "conversations")` calls in other tests to pass a 4th arg, or give it a default — `sla_targets_json: str = "{}"` already defaults to an empty-but-valid JSON object so old 3-arg call sites in tests keep working, only the specific new bucket tests need the 4th arg).

- [ ] **Step 7: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/platform/config.py src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/sync.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): add v_resolution_sla_buckets, configurable per-case_type working-hour targets"
```

---

### Task 12: `v_dealer_escalation` and `v_case_aging` views

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py` (`view_ddls`)
- Test: `test_bigquery_schema.py`

**Interfaces:**
- Produces: two new entries in the `view_ddls()` return dict.

- [ ] **Step 1: Write the failing test**

```python
# addition to test_bigquery_schema.py
def test_view_ddls_includes_dealer_escalation_and_case_aging() -> None:
    ddls = view_ddls("proj", "ds", "conversations", "{}")
    assert "v_dealer_escalation" in ddls
    assert "dealer_escalated_at" in ddls["v_dealer_escalation"]
    assert "v_case_aging" in ddls
    assert "bucket_label" in ddls["v_case_aging"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_bigquery_schema.py -k "dealer_escalation or case_aging" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the views**

Add both entries to `view_ddls`'s return dict (alongside `v_resolution_sla_buckets` from Task 11):

```python
        "v_dealer_escalation": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_dealer_escalation` AS "
            f"SELECT COALESCE(dealer, 'Unknown') AS dealer, "
            f"COUNT(*) AS cases_escalated, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR)) / 24.0 AS avg_turnaround_days, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR), 100)[OFFSET(50)] "
            f"/ 24.0 AS p50_turnaround_days, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR), 100)[OFFSET(90)] "
            f"/ 24.0 AS p90_turnaround_days "
            f"FROM {fq} WHERE dealer_escalated_at IS NOT NULL GROUP BY dealer"
        ),
        "v_dealer_escalation_slowest_cases": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_dealer_escalation_slowest_cases` AS "
            f"SELECT conversation_id, COALESCE(dealer, 'Unknown') AS dealer, "
            f"TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR) / 24.0 AS turnaround_days "
            f"FROM {fq} WHERE dealer_escalated_at IS NOT NULL AND resolved_at IS NOT NULL "
            f"ORDER BY turnaround_days DESC LIMIT 50"
        ),
        "v_case_aging": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_case_aging` AS "
            f"SELECT conversation_id, COALESCE(case_type, 'Unknown') AS case_type, "
            f"COALESCE(division, 'Unknown') AS division, COALESCE(dealer, 'Unknown') AS dealer, "
            f"COALESCE(pic, 'Unassigned') AS pic, status, created_at, "
            f"TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, HOUR) / 24.0 AS age_days, "
            f"CASE "
            f"WHEN TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, DAY) <= 3 THEN '1-3 days' "
            f"WHEN TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, DAY) <= 6 THEN '4-6 days' "
            f"ELSE '7+ days' END AS bucket_label "
            f"FROM {fq} WHERE status IN ('open', 'pending') AND created_at IS NOT NULL "
            f"ORDER BY age_days DESC"
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_bigquery_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): add v_dealer_escalation and v_case_aging views"
```

---

### Task 13: `v_volume_by_type_division` and `v_category_by_vehicle_model` views

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/bigquery_schema.py` (`view_ddls`)
- Test: `test_bigquery_schema.py`

**Interfaces:**
- Produces: two new entries in `view_ddls()`, extending the existing `v_volume_by_division`/`v_complaint_type_ranking` shapes with `case_type`/`vehicle_model`.

- [ ] **Step 1: Write the failing test**

```python
# addition to test_bigquery_schema.py
def test_view_ddls_includes_volume_and_category_cross_tabs() -> None:
    ddls = view_ddls("proj", "ds", "conversations", "{}")
    assert "v_volume_by_type_division" in ddls
    assert "case_type" in ddls["v_volume_by_type_division"]
    assert "v_category_by_vehicle_model" in ddls
    assert "vehicle_model" in ddls["v_category_by_vehicle_model"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_bigquery_schema.py -k "volume_by_type or category_by_vehicle" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the views**

```python
        "v_volume_by_type_division": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_type_division` AS "
            f"SELECT FORMAT_DATE('%Y-%m', DATE(created_at)) AS month, channel, "
            f"COALESCE(case_type, 'Unknown') AS case_type, "
            f"COALESCE(division, 'Unknown') AS division, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY month, channel, case_type, division"
        ),
        "v_category_by_vehicle_model": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_category_by_vehicle_model` AS "
            f"SELECT COALESCE(category, 'Unknown') AS category, "
            f"COALESCE(subcategory, 'Unknown') AS subcategory, "
            f"COALESCE(vehicle_model, 'Unknown') AS vehicle_model, "
            f"COALESCE(case_type, 'Unknown') AS case_type, COUNT(*) AS cases "
            f"FROM {fq} GROUP BY category, subcategory, vehicle_model, case_type "
            f"ORDER BY cases DESC"
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_bigquery_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/bigquery_schema.py src/chatbot/features/metrics/test_bigquery_schema.py
git commit -m "feat(metrics): add v_volume_by_type_division and v_category_by_vehicle_model views"
```

---

### Task 14: Read-side dataclasses + port + mock for the 7 new views

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/query_port.py`
- Test: none new — `query_port.py` has no dedicated test file (verified: it's pure data shape + a Protocol + a Mock); its correctness is exercised by Task 15's router tests and Task 16's adapter tests.

**Interfaces:**
- Produces: `DealerEscalationRow`, `DealerSlowCaseRow`, `DealerEscalationMetrics`, `SlaBucketRow`, `SlaBucketMetrics`, `CaseAgingRow`, `CaseAgingMetrics`, `VolumeByTypeDivisionRow`, `CategoryByVehicleModelRow`; `DepartmentsMetrics` gains a new `category_by_vehicle_model: list[CategoryByVehicleModelRow]` field (per the spec: "Category/vehicle-model cross-tab added into the existing Departments & PIC tab"); `MetricsQueryPort` Protocol gains `fetch_dealer_escalation`, `fetch_sla_buckets`, `fetch_case_aging`, `fetch_volume_by_type_division`; `MockMetricsQuery` implements all of them.

- [ ] **Step 1: Add the new dataclasses**

At the end of `query_port.py`, before the `MetricsQueryPort` Protocol class, add:

```python
@dataclass(frozen=True)
class DealerEscalationRow:
    dealer: str
    cases_escalated: int
    avg_turnaround_days: float | None
    p50_turnaround_days: float | None
    p90_turnaround_days: float | None


@dataclass(frozen=True)
class DealerSlowCaseRow:
    conversation_id: str
    dealer: str
    turnaround_days: float | None


@dataclass(frozen=True)
class DealerEscalationMetrics:
    by_dealer: list[DealerEscalationRow]
    slowest_cases: list[DealerSlowCaseRow]


@dataclass(frozen=True)
class SlaBucketRow:
    case_type: str
    bucket_label: str | None
    cases: int


@dataclass(frozen=True)
class SlaBucketMetrics:
    buckets: list[SlaBucketRow]


@dataclass(frozen=True)
class CaseAgingRow:
    conversation_id: str
    case_type: str
    division: str
    dealer: str
    pic: str
    status: str
    created_at: datetime | None
    age_days: float | None
    bucket_label: str


@dataclass(frozen=True)
class CaseAgingMetrics:
    cases: list[CaseAgingRow]


@dataclass(frozen=True)
class VolumeByTypeDivisionRow:
    month: str
    channel: str
    case_type: str
    division: str
    volume: int


@dataclass(frozen=True)
class VolumeByTypeDivisionMetrics:
    volume: list[VolumeByTypeDivisionRow]


@dataclass(frozen=True)
class CategoryByVehicleModelRow:
    category: str
    subcategory: str
    vehicle_model: str
    case_type: str
    cases: int
```

- [ ] **Step 2: Extend `DepartmentsMetrics`**

Change:

```python
@dataclass(frozen=True)
class DepartmentsMetrics:
    dept_pic: list[DeptPicRow]
    reopen: list[ReopenRow]
```

to:

```python
@dataclass(frozen=True)
class DepartmentsMetrics:
    dept_pic: list[DeptPicRow]
    reopen: list[ReopenRow]
    category_by_vehicle_model: list[CategoryByVehicleModelRow]
```

(this dataclass must be declared AFTER `CategoryByVehicleModelRow` now — move `DepartmentsMetrics`'s definition below the new dataclasses added in Step 1, or move `CategoryByVehicleModelRow` above `DepartmentsMetrics`'s current position; either works, just keep Python's "must be defined before use" ordering valid.)

- [ ] **Step 3: Extend the `MetricsQueryPort` Protocol**

```python
class MetricsQueryPort(Protocol):
    async def fetch_dashboard(self) -> DashboardMetrics: ...
    async def fetch_anomalies(self) -> list[AnomalyRow]: ...
    async def fetch_departments(self) -> DepartmentsMetrics: ...
    async def fetch_callcenter(self) -> CallCentreMetrics: ...
    async def fetch_lifecycle(self) -> LifecycleMetrics: ...
    async def fetch_dealer_escalation(self) -> DealerEscalationMetrics: ...
    async def fetch_sla_buckets(self) -> SlaBucketMetrics: ...
    async def fetch_case_aging(self) -> CaseAgingMetrics: ...
    async def fetch_volume_by_type_division(self) -> VolumeByTypeDivisionMetrics: ...
```

- [ ] **Step 4: Extend `MockMetricsQuery`**

Change `fetch_departments`'s mock to include the new field, and add four new mock methods:

```python
    async def fetch_departments(self) -> DepartmentsMetrics:
        return DepartmentsMetrics(
            dept_pic=[DeptPicRow("Aftersales", "Ali", 40, 12.0, 240.0, 0.9)],
            reopen=[ReopenRow("Dealer KL", "Aftersales", "Ali", 40, 4, 0.1)],
            category_by_vehicle_model=[
                CategoryByVehicleModelRow("Charging", "Home Charging", "e.MAS 5", "Complaint", 12)
            ],
        )

    async def fetch_dealer_escalation(self) -> DealerEscalationMetrics:
        return DealerEscalationMetrics(
            by_dealer=[DealerEscalationRow("Dealer KL", 12, 3.5, 3.0, 6.0)],
            slowest_cases=[DealerSlowCaseRow("CONV042", "Dealer KL", 12.0)],
        )

    async def fetch_sla_buckets(self) -> SlaBucketMetrics:
        return SlaBucketMetrics(
            buckets=[
                SlaBucketRow("Inquiry", "Within 8wh", 887),
                SlaBucketRow("Inquiry", ">8wh", 137),
                SlaBucketRow("Complaint", "<24wh", 378),
                SlaBucketRow("Complaint", ">72wh", 290),
            ]
        )

    async def fetch_case_aging(self) -> CaseAgingMetrics:
        return CaseAgingMetrics(
            cases=[
                CaseAgingRow(
                    "CONV099", "Complaint", "Sales", "Dealer KL", "Ali", "open",
                    created_at=None, age_days=4.0, bucket_label="4-6 days",
                )
            ]
        )

    async def fetch_volume_by_type_division(self) -> VolumeByTypeDivisionMetrics:
        return VolumeByTypeDivisionMetrics(
            volume=[VolumeByTypeDivisionRow("2026-06", "WhatsApp", "Inquiry", "Sales", 682)]
        )
```

- [ ] **Step 5: Run the full metrics suite for import/regression errors**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -v`
Expected: PASS (this task adds no new test file, but must not break `test_dashboard_router.py`/`test_insights_router.py`, which construct `MockMetricsQuery` and would fail to import if the Protocol/dataclasses have a typo).

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/query_port.py
git commit -m "feat(metrics): add read-side dataclasses/port/mock for the 4 new report views"
```

---

### Task 15: BigQuery adapter wiring for the 4 new views

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/query_adapter.py`
- Test: none new (no existing `test_query_adapter.py`-level unit tests wrap individual `_fetch_X_sync` methods with a fake BQ client per the current file's pattern — verified via `test_query_adapter.py`'s existing structure; extend it if it does cover per-method fetch, otherwise this task is verified via Task 17's router-level tests using `MockMetricsQuery`, since `BigQueryMetricsQuery` itself needs a live/faked BQ client to unit test and this repo's existing convention is to NOT unit-test the live adapter directly — check `test_query_adapter.py` first and follow its actual convention, adding a test in its style if one exists for e.g. `fetch_departments`)

**Interfaces:**
- Produces: `BigQueryMetricsQuery.fetch_dealer_escalation`, `.fetch_sla_buckets`, `.fetch_case_aging`, `.fetch_volume_by_type_division`; `fetch_departments`'s `_block` call for `category_by_vehicle_model` added.

- [ ] **Step 1: Check the existing adapter test file's convention**

Run: `cat backend/apps/backend/src/chatbot/features/metrics/test_query_adapter.py | head -60`. If it fakes a BQ client and asserts `_block`-shaped calls, write an equivalent test for one of the new fetch methods (e.g. `fetch_dealer_escalation`) following that exact pattern before Step 2. If it doesn't unit-test per-view fetches this way, skip straight to Step 2's implementation and rely on Task 17's router test for coverage.

- [ ] **Step 2: Implement the adapter methods**

Add the new imports to `query_adapter.py`'s import block from `query_port`:

```python
    CaseAgingMetrics,
    CaseAgingRow,
    CategoryByVehicleModelRow,
    DealerEscalationMetrics,
    DealerEscalationRow,
    DealerSlowCaseRow,
    SlaBucketMetrics,
    SlaBucketRow,
    VolumeByTypeDivisionMetrics,
    VolumeByTypeDivisionRow,
```

(merge alphabetically into the existing `from chatbot.features.metrics.query_port import (...)` block rather than adding a second import statement.)

Change `_fetch_departments_sync` to include the cross-tab:

```python
    def _fetch_departments_sync(self) -> DepartmentsMetrics:
        return DepartmentsMetrics(
            dept_pic=self._block("v_dept_pic_performance", DeptPicRow),
            reopen=self._block("v_reopen_rate", ReopenRow),
            category_by_vehicle_model=self._block(
                "v_category_by_vehicle_model", CategoryByVehicleModelRow
            ),
        )
```

Add four new method pairs (mirroring `fetch_anomalies`'s single-block shape and `fetch_lifecycle`'s multi-block shape):

```python
    def _fetch_dealer_escalation_sync(self) -> DealerEscalationMetrics:
        return DealerEscalationMetrics(
            by_dealer=self._block("v_dealer_escalation", DealerEscalationRow),
            slowest_cases=self._block("v_dealer_escalation_slowest_cases", DealerSlowCaseRow),
        )

    async def fetch_dealer_escalation(self) -> DealerEscalationMetrics:
        return await asyncio.to_thread(self._fetch_dealer_escalation_sync)

    def _fetch_sla_buckets_sync(self) -> SlaBucketMetrics:
        return SlaBucketMetrics(buckets=self._block("v_resolution_sla_buckets", SlaBucketRow))

    async def fetch_sla_buckets(self) -> SlaBucketMetrics:
        return await asyncio.to_thread(self._fetch_sla_buckets_sync)

    def _fetch_case_aging_sync(self) -> CaseAgingMetrics:
        return CaseAgingMetrics(cases=self._block("v_case_aging", CaseAgingRow))

    async def fetch_case_aging(self) -> CaseAgingMetrics:
        return await asyncio.to_thread(self._fetch_case_aging_sync)

    def _fetch_volume_by_type_division_sync(self) -> VolumeByTypeDivisionMetrics:
        return VolumeByTypeDivisionMetrics(
            volume=self._block("v_volume_by_type_division", VolumeByTypeDivisionRow)
        )

    async def fetch_volume_by_type_division(self) -> VolumeByTypeDivisionMetrics:
        return await asyncio.to_thread(self._fetch_volume_by_type_division_sync)
```

- [ ] **Step 3: Run tests**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/query_adapter.py
git commit -m "feat(metrics): wire the 4 new views into BigQueryMetricsQuery"
```

---

### Task 16: New `insights_router.py` routes + `main.py` wiring

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/insights_router.py`
- Modify: `backend/apps/backend/src/chatbot/main.py` (no change needed — `build_metrics_insights_router` is already called in `_wire_metrics_features`; the new routes ride the same router instance)
- Test: `backend/apps/backend/src/chatbot/features/metrics/test_insights_router.py`

**Interfaces:**
- Produces: `GET /metrics/dealer-escalation`, `GET /metrics/sla-buckets`, `GET /metrics/case-aging`, `GET /metrics/volume-by-type` — all `x-api-key` gated identically to the 3 existing routes.

- [ ] **Step 1: Write the failing test**

```python
# addition to test_insights_router.py — match this file's existing test client/fixture setup
def test_dealer_escalation_requires_api_key(client):
    response = client.get("/metrics/dealer-escalation")
    assert response.status_code == 401


def test_dealer_escalation_returns_mock_data(client, api_key_header):
    response = client.get("/metrics/dealer-escalation", headers=api_key_header)
    assert response.status_code == 200
    assert "by_dealer" in response.json()


def test_sla_buckets_returns_mock_data(client, api_key_header):
    response = client.get("/metrics/sla-buckets", headers=api_key_header)
    assert response.status_code == 200
    assert "buckets" in response.json()


def test_case_aging_returns_mock_data(client, api_key_header):
    response = client.get("/metrics/case-aging", headers=api_key_header)
    assert response.status_code == 200
    assert "cases" in response.json()


def test_volume_by_type_returns_mock_data(client, api_key_header):
    response = client.get("/metrics/volume-by-type", headers=api_key_header)
    assert response.status_code == 200
    assert "volume" in response.json()
```

Match `client`/`api_key_header` to whatever this file's existing tests (for `/metrics/departments` etc.) actually call their fixtures.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_insights_router.py -v`
Expected: FAIL — 404s, routes don't exist yet.

- [ ] **Step 3: Implement the routes**

In `insights_router.py`, add after the existing `lifecycle` route (before `return router`):

```python
    @router.get("/metrics/dealer-escalation")
    async def dealer_escalation(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_dealer_escalation())

    @router.get("/metrics/sla-buckets")
    async def sla_buckets(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_sla_buckets())

    @router.get("/metrics/case-aging")
    async def case_aging(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_case_aging())

    @router.get("/metrics/volume-by-type")
    async def volume_by_type(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _require_key(x_api_key)
        return asdict(await port.fetch_volume_by_type_division())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_insights_router.py -v`
Expected: PASS.

- [ ] **Step 5: Full backend suite regression check**

Run: `cd backend/apps/backend && pytest src/ -v`
Expected: PASS, no regressions anywhere (this is the point where all of Tasks 1-16's cross-cutting changes get exercised together for the first time).

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/insights_router.py src/chatbot/features/metrics/test_insights_router.py
git commit -m "feat(metrics): expose /metrics/{dealer-escalation,sla-buckets,case-aging,volume-by-type}"
```

---

### Task 17: CSV export for the new views

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/export.py` (add `render_csv`, generalized beyond `DashboardMetrics`)
- Modify: `backend/apps/backend/src/chatbot/features/metrics/export_router.py` (new `/metrics/{view}/export` routes)
- Test: `test_export.py`, `test_export_router.py`

**Interfaces:**
- Produces: `render_csv(bundle: Any) -> bytes` — works on ANY frozen dataclass whose fields are `list[<dataclass>]`, not just `DashboardMetrics` (generalizes the existing `_blocks()` reflection helper, which already only depends on `dataclasses.fields()`, so no behavior change for the existing xlsx/pdf callers).

The spec calls for "a CSV export button" per new view; this codebase currently only has xlsx/pdf renderers (no CSV at all) — this task adds CSV as a genuinely new capability, reusing the existing `_blocks()` reflection helper unmodified.

- [ ] **Step 1: Write the failing test**

```python
# addition to test_export.py
import csv
import io

from chatbot.features.metrics.export import render_csv
from chatbot.features.metrics.query_port import DealerEscalationMetrics, DealerEscalationRow, DealerSlowCaseRow


def test_render_csv_produces_one_section_per_block():
    metrics = DealerEscalationMetrics(
        by_dealer=[DealerEscalationRow("Dealer KL", 12, 3.5, 3.0, 6.0)],
        slowest_cases=[DealerSlowCaseRow("CONV042", "Dealer KL", 12.0)],
    )
    content = render_csv(metrics).decode("utf-8")
    reader = list(csv.reader(io.StringIO(content)))
    assert ["by_dealer"] in reader
    assert ["dealer", "cases_escalated", "avg_turnaround_days", "p50_turnaround_days", "p90_turnaround_days"] in reader
    assert ["Dealer KL", "12", "3.5", "3.0", "6.0"] in reader
    assert ["slowest_cases"] in reader


def test_render_csv_empty_block_has_no_data_marker():
    metrics = DealerEscalationMetrics(by_dealer=[], slowest_cases=[])
    content = render_csv(metrics).decode("utf-8")
    assert "(no data)" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_export.py -k csv -v`
Expected: FAIL — `ImportError: cannot import name 'render_csv'`.

- [ ] **Step 3: Implement `render_csv`**

In `export.py`, add:

```python
import csv


def render_csv(metrics: Any) -> bytes:
    """CSV export for any dataclass whose fields are list[<dataclass>] — same
    _blocks() reflection `render_xlsx`/`render_pdf` already use, so this
    works for DashboardMetrics AND every new report bundle (DealerEscalation-
    Metrics, SlaBucketMetrics, CaseAgingMetrics, VolumeByTypeDivisionMetrics,
    DepartmentsMetrics, ...) with no per-view code."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for name, rows in _blocks(metrics):
        writer.writerow([name])
        if rows:
            first_row = cast(Any, rows[0])
            writer.writerow([f.name for f in fields(first_row)])
            for row in rows:
                writer.writerow(list(astuple(cast(Any, row))))
        else:
            writer.writerow(["(no data)"])
        writer.writerow([])
    return buf.getvalue().encode("utf-8")
```

Change `_blocks`'s type hint from `DashboardMetrics` to `Any` (it already only calls `dataclasses.fields()`, which works on any dataclass instance — this is a type-hint-only change, no behavior change):

```python
def _blocks(metrics: Any) -> list[tuple[str, list[Any]]]:
    return [(f.name, getattr(metrics, f.name)) for f in fields(metrics)]
```

Remove the now-unused `if TYPE_CHECKING: from chatbot.features.metrics.query_port import DashboardMetrics` import block (it's no longer referenced now that `_blocks`, `render_xlsx`, and `render_pdf` all take `Any`) — or leave it if `render_xlsx`/`render_pdf`'s own signatures still type-hint `DashboardMetrics` specifically (check first; if they do, keep the import and just widen `_blocks`'s hint alone).

- [ ] **Step 4: Write the export-router test**

```python
# addition to test_export_router.py
def test_dealer_escalation_csv_export(client):
    response = client.get("/metrics/dealer-escalation/export?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/test_export_router.py -k dealer -v`
Expected: FAIL — 404.

- [ ] **Step 6: Implement the export routes**

In `export_router.py`, add a generic per-view CSV export helper and 4 new routes. Replace the file's `build_metrics_export_router` to add:

```python
def build_metrics_export_router(port: MetricsQueryPort) -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics/export")
    async def export(format: str = "xlsx") -> Response:
        metrics = await port.fetch_dashboard()
        if format == "xlsx":
            return Response(
                content=render_xlsx(metrics), media_type=_XLSX,
                headers={"Content-Disposition": "attachment; filename=bot-metrics.xlsx"},
            )
        if format == "pdf":
            return Response(
                content=render_pdf(metrics), media_type="application/pdf",
                headers={"Content-Disposition": "attachment; filename=bot-metrics.pdf"},
            )
        raise HTTPException(status_code=400, detail="format must be xlsx or pdf")

    def _csv_route(path: str, filename: str, fetch):
        @router.get(path, name=f"export_csv_{filename}")
        async def _export_csv() -> Response:
            metrics = await fetch()
            return Response(
                content=render_csv(metrics), media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
            )

    _csv_route("/metrics/dealer-escalation/export", "dealer-escalation", port.fetch_dealer_escalation)
    _csv_route("/metrics/sla-buckets/export", "sla-buckets", port.fetch_sla_buckets)
    _csv_route("/metrics/case-aging/export", "case-aging", port.fetch_case_aging)
    _csv_route("/metrics/volume-by-type/export", "volume-by-type", port.fetch_volume_by_type_division)
    _csv_route("/metrics/departments/export", "departments", port.fetch_departments)

    return router
```

Add `from chatbot.features.metrics.export import render_csv, render_pdf, render_xlsx` (extending the existing import line).

Note: the `format=csv` query-param on `/metrics/export` was NOT added — that endpoint is `DashboardMetrics`-specific by design (the existing behavior); the new per-view CSV routes are separate paths, matching the router-per-view pattern `insights_router.py` already established in Task 16.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/export.py src/chatbot/features/metrics/export_router.py src/chatbot/features/metrics/test_export.py src/chatbot/features/metrics/test_export_router.py
git commit -m "feat(metrics): add CSV export, one route per new report view"
```

---

### Task 18: RSA module — Postgres model + engine (`backend/`)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/rsa/__init__.py` (empty)
- Create: `backend/apps/backend/src/chatbot/features/rsa/rsa_db.py`
- Create: `backend/apps/backend/src/chatbot/features/rsa/test_rsa_db.py`
- Modify: `backend/apps/backend/src/chatbot/platform/config.py` (add `rsa_enabled`, `rsa_database_url`)

**Interfaces:**
- Produces: `RsaIncident` SQLAlchemy model, `build_engine(url)`, `build_session_maker(engine)`, `init_rsa_db(engine)` — identical shape to `kb_db.py` minus the pgvector-specific bits (no embeddings needed).

- [ ] **Step 1: Add the Settings fields**

In `config.py`, near `rbac_database_url` (the most recent own-Postgres-module precedent), add:

```python

    # RSA (roadside assistance) incident log — own Postgres table, gated the
    # same way the pgvector KB and RBAC are: default-off, needs BOTH flags to
    # activate. Manual staff data entry only, no dispatch-system integration.
    rsa_enabled: bool = False
    rsa_database_url: str = ""
```

- [ ] **Step 2: Write the failing test**

```python
# backend/apps/backend/src/chatbot/features/rsa/test_rsa_db.py
import pytest
from sqlalchemy import select

from chatbot.features.rsa.rsa_db import RsaIncident, build_engine, build_session_maker, init_rsa_db


@pytest.mark.asyncio
async def test_init_and_insert_incident(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path}/rsa.db")
    await init_rsa_db(engine)
    session_maker = build_session_maker(engine)

    async with session_maker() as session:
        incident = RsaIncident(
            id="rsa-1", incident_date="2026-07-01", vehicle_no="VPP8636",
            vehicle_model="e.MAS 7", cause="Flat Tyre",
            purchased_from="Proton e.MAS - Wheelcorp EV (Setia Alam - SVC)",
            breakdown_location="Highway PLUS", arrived_location="Wheelcorp EV Setia Alam",
            customer_called_in_time=None, towing_assigned_time=None,
            time_arrived_breakdown_area=None, time_arrived_outlet=None,
            total_km=8, late_reason=None, remarks="Water leaking", created_by="agent-1",
        )
        session.add(incident)
        await session.commit()

    async with session_maker() as session:
        result = await session.execute(select(RsaIncident).where(RsaIncident.id == "rsa-1"))
        row = result.scalar_one()
        assert row.vehicle_no == "VPP8636"
        assert row.cause == "Flat Tyre"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/rsa/test_rsa_db.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement `rsa_db.py`**

```python
# backend/apps/backend/src/chatbot/features/rsa/rsa_db.py
"""Async SQLAlchemy layer for the RSA (roadside assistance) incident log.

Own Postgres table, NOT synced through BigQuery and NOT a Chatwoot
conversation — staff-entered operational data with no message thread,
structurally unlike everything else in this codebase's metrics pipeline.
Patterned on kb_db.py (same _to_async_url upgrade, same lazy-engine +
init-on-startup shape) minus the pgvector-specific bits — RSA has no
embeddings, it's a plain CRUD table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RsaIncident(Base):
    __tablename__ = "rsa_incidents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    incident_date: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_no: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_model: Mapped[str | None] = mapped_column(Text)
    cause: Mapped[str] = mapped_column(Text, nullable=False)
    purchased_from: Mapped[str | None] = mapped_column(Text)
    breakdown_location: Mapped[str | None] = mapped_column(Text)
    arrived_location: Mapped[str | None] = mapped_column(Text)
    customer_called_in_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    towing_assigned_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_arrived_breakdown_area: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_arrived_outlet: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_km: Mapped[int | None] = mapped_column(Integer)
    late_reason: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def build_engine(url: str) -> AsyncEngine:
    return create_async_engine(_to_async_url(url))


def build_session_maker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_rsa_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/rsa/test_rsa_db.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/rsa/ src/chatbot/platform/config.py
git commit -m "feat(rsa): add RsaIncident Postgres model + engine helpers"
```

---

### Task 19: RSA repository (Port + InMemory + Postgres)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/rsa/rsa_repository.py`
- Create: `backend/apps/backend/src/chatbot/features/rsa/test_rsa_repository.py`

**Interfaces:**
- Produces: `RsaRepositoryPort` Protocol (`create_incident`, `list_incidents`, `get_incident`, `update_incident`, `delete_incident`, `aggregate`), `InMemoryRsaRepository`, `PgRsaRepository`.

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/backend/src/chatbot/features/rsa/test_rsa_repository.py
import pytest

from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository


@pytest.mark.asyncio
async def test_create_list_get_update_delete():
    repo = InMemoryRsaRepository()
    incident_id = await repo.create_incident(
        incident_date="2026-07-01", vehicle_no="VPP8636", vehicle_model="e.MAS 7",
        cause="Flat Tyre", purchased_from="Wheelcorp EV", breakdown_location="Highway PLUS",
        arrived_location="Wheelcorp EV Setia Alam", total_km=8, remarks="Water leaking",
        created_by="agent-1",
    )
    rows = await repo.list_incidents()
    assert len(rows) == 1
    assert rows[0].id == incident_id

    row = await repo.get_incident(incident_id)
    assert row is not None
    assert row.vehicle_no == "VPP8636"

    updated = await repo.update_incident(incident_id, remarks="Water leaking, resolved on-site")
    assert updated is True
    row = await repo.get_incident(incident_id)
    assert row.remarks == "Water leaking, resolved on-site"

    deleted = await repo.delete_incident(incident_id)
    assert deleted is True
    assert await repo.get_incident(incident_id) is None


@pytest.mark.asyncio
async def test_update_delete_nonexistent_returns_false():
    repo = InMemoryRsaRepository()
    assert await repo.update_incident("nope", remarks="x") is False
    assert await repo.delete_incident("nope") is False


@pytest.mark.asyncio
async def test_aggregate_by_cause_and_dealer():
    repo = InMemoryRsaRepository()
    await repo.create_incident(
        incident_date="2026-07-01", vehicle_no="A1", vehicle_model="e.MAS 7",
        cause="Flat Tyre", purchased_from="Dealer A", breakdown_location="X",
        arrived_location="Y", total_km=1, remarks="", created_by="a",
    )
    await repo.create_incident(
        incident_date="2026-07-02", vehicle_no="A2", vehicle_model="e.MAS 5",
        cause="Flat Tyre", purchased_from="Dealer A", breakdown_location="X",
        arrived_location="Y", total_km=1, remarks="", created_by="a",
    )
    await repo.create_incident(
        incident_date="2026-07-03", vehicle_no="A3", vehicle_model="e.MAS 7",
        cause="Flat Battery", purchased_from="Dealer B", breakdown_location="X",
        arrived_location="Y", total_km=1, remarks="", created_by="a",
    )
    agg = await repo.aggregate()
    by_cause = {r.cause: r.count for r in agg.by_cause}
    assert by_cause == {"Flat Tyre": 2, "Flat Battery": 1}
    by_dealer = {r.dealer: r.count for r in agg.by_dealer}
    assert by_dealer == {"Dealer A": 2, "Dealer B": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/rsa/test_rsa_repository.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `rsa_repository.py`**

```python
# backend/apps/backend/src/chatbot/features/rsa/rsa_repository.py
"""Port + InMemory + Postgres repository for RSA incidents. Mirrors
kb_repository.py's port/adapter split (see docs/superpowers/plans/
2026-07-26-pgvector-knowledge-base.md for the precedent this follows)."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy import select

from chatbot.features.rsa.rsa_db import RsaIncident


@dataclass(frozen=True)
class CauseCount:
    cause: str
    count: int


@dataclass(frozen=True)
class DealerCount:
    dealer: str
    count: int


@dataclass(frozen=True)
class RsaAggregate:
    by_cause: list[CauseCount]
    by_dealer: list[DealerCount]


class RsaRepositoryPort(Protocol):
    async def create_incident(self, **fields) -> str: ...
    async def list_incidents(self) -> list[RsaIncident]: ...
    async def get_incident(self, incident_id: str) -> RsaIncident | None: ...
    async def update_incident(self, incident_id: str, **fields) -> bool: ...
    async def delete_incident(self, incident_id: str) -> bool: ...
    async def aggregate(self) -> RsaAggregate: ...


@dataclass
class _InMemoryRow:
    id: str
    incident_date: str
    vehicle_no: str
    vehicle_model: str | None = None
    cause: str = ""
    purchased_from: str | None = None
    breakdown_location: str | None = None
    arrived_location: str | None = None
    customer_called_in_time: datetime | None = None
    towing_assigned_time: datetime | None = None
    time_arrived_breakdown_area: datetime | None = None
    time_arrived_outlet: datetime | None = None
    total_km: int | None = None
    late_reason: str | None = None
    remarks: str | None = None
    created_by: str | None = None


class InMemoryRsaRepository:
    """Dev/test repository — no DB needed."""

    def __init__(self) -> None:
        self._rows: dict[str, _InMemoryRow] = {}

    async def create_incident(self, **fields) -> str:
        incident_id = str(uuid.uuid4())
        self._rows[incident_id] = _InMemoryRow(id=incident_id, **fields)
        return incident_id

    async def list_incidents(self) -> list[_InMemoryRow]:
        return list(self._rows.values())

    async def get_incident(self, incident_id: str) -> _InMemoryRow | None:
        return self._rows.get(incident_id)

    async def update_incident(self, incident_id: str, **fields) -> bool:
        row = self._rows.get(incident_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True

    async def delete_incident(self, incident_id: str) -> bool:
        return self._rows.pop(incident_id, None) is not None

    async def aggregate(self) -> RsaAggregate:
        cause_counter = Counter(r.cause for r in self._rows.values())
        dealer_counter = Counter(
            r.purchased_from for r in self._rows.values() if r.purchased_from
        )
        return RsaAggregate(
            by_cause=[CauseCount(cause, count) for cause, count in cause_counter.items()],
            by_dealer=[DealerCount(dealer, count) for dealer, count in dealer_counter.items()],
        )


class PgRsaRepository:
    """Postgres-backed repository, using the SQLAlchemy model from rsa_db.py."""

    def __init__(self, session_maker) -> None:
        self._session_maker = session_maker

    async def create_incident(self, **fields) -> str:
        incident_id = str(uuid.uuid4())
        async with self._session_maker() as session:
            session.add(RsaIncident(id=incident_id, **fields))
            await session.commit()
        return incident_id

    async def list_incidents(self) -> list[RsaIncident]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(RsaIncident).order_by(RsaIncident.created_at.desc())
            )
            return list(result.scalars().all())

    async def get_incident(self, incident_id: str) -> RsaIncident | None:
        async with self._session_maker() as session:
            result = await session.execute(
                select(RsaIncident).where(RsaIncident.id == incident_id)
            )
            return result.scalar_one_or_none()

    async def update_incident(self, incident_id: str, **fields) -> bool:
        async with self._session_maker() as session:
            result = await session.execute(
                select(RsaIncident).where(RsaIncident.id == incident_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            for key, value in fields.items():
                setattr(row, key, value)
            await session.commit()
            return True

    async def delete_incident(self, incident_id: str) -> bool:
        async with self._session_maker() as session:
            result = await session.execute(
                select(RsaIncident).where(RsaIncident.id == incident_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def aggregate(self) -> RsaAggregate:
        async with self._session_maker() as session:
            result = await session.execute(select(RsaIncident))
            rows = list(result.scalars().all())
        cause_counter = Counter(r.cause for r in rows)
        dealer_counter = Counter(r.purchased_from for r in rows if r.purchased_from)
        return RsaAggregate(
            by_cause=[CauseCount(c, n) for c, n in cause_counter.items()],
            by_dealer=[DealerCount(d, n) for d, n in dealer_counter.items()],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/rsa/test_rsa_repository.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/rsa/rsa_repository.py src/chatbot/features/rsa/test_rsa_repository.py
git commit -m "feat(rsa): add RsaRepositoryPort with InMemory + Postgres implementations"
```

---

### Task 20: RSA CRUD + aggregate router, `main.py` wiring, CSV export

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/rsa/rsa_router.py`
- Create: `backend/apps/backend/src/chatbot/features/rsa/test_rsa_router.py`
- Modify: `backend/apps/backend/src/chatbot/main.py` (new gated wiring block, mirroring the pgvector-KB block at lines 444-481)

**Interfaces:**
- Produces: `POST/GET/PATCH/DELETE /rsa/incidents[/{id}]`, `GET /rsa/incidents/aggregate`, `GET /rsa/incidents/export?format=csv` — all `x-api-key` gated (accepts `faq_admin_api_key` or `proton_backend_key`, mirroring `kb_knowledge_router.py`'s `_authorize`).

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/backend/src/chatbot/features/rsa/test_rsa_router.py
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository
from chatbot.features.rsa.rsa_router import build_rsa_router
from chatbot.platform.config import get_settings


@pytest.fixture
def client():
    settings = get_settings().model_copy(update={"faq_admin_api_key": "test-key"})
    repo = InMemoryRsaRepository()
    app = FastAPI()
    app.include_router(build_rsa_router(repo, settings))
    return TestClient(app)


def _headers():
    return {"x-api-key": "test-key"}


def test_create_requires_api_key(client):
    response = client.post("/rsa/incidents", json={
        "incident_date": "2026-07-01", "vehicle_no": "VPP8636", "cause": "Flat Tyre",
    })
    assert response.status_code == 401


def test_create_list_get_incident(client):
    create_res = client.post(
        "/rsa/incidents",
        json={"incident_date": "2026-07-01", "vehicle_no": "VPP8636", "cause": "Flat Tyre"},
        headers=_headers(),
    )
    assert create_res.status_code == 200
    incident_id = create_res.json()["id"]

    list_res = client.get("/rsa/incidents", headers=_headers())
    assert list_res.status_code == 200
    assert len(list_res.json()["incidents"]) == 1

    get_res = client.get(f"/rsa/incidents/{incident_id}", headers=_headers())
    assert get_res.status_code == 200
    assert get_res.json()["vehicle_no"] == "VPP8636"


def test_get_missing_incident_404(client):
    response = client.get("/rsa/incidents/does-not-exist", headers=_headers())
    assert response.status_code == 404


def test_update_and_delete_incident(client):
    incident_id = client.post(
        "/rsa/incidents",
        json={"incident_date": "2026-07-01", "vehicle_no": "VPP8636", "cause": "Flat Tyre"},
        headers=_headers(),
    ).json()["id"]

    patch_res = client.patch(
        f"/rsa/incidents/{incident_id}", json={"remarks": "resolved"}, headers=_headers()
    )
    assert patch_res.status_code == 200

    delete_res = client.delete(f"/rsa/incidents/{incident_id}", headers=_headers())
    assert delete_res.status_code == 200
    assert client.get(f"/rsa/incidents/{incident_id}", headers=_headers()).status_code == 404


def test_aggregate_endpoint(client):
    client.post(
        "/rsa/incidents",
        json={"incident_date": "2026-07-01", "vehicle_no": "A1", "cause": "Flat Tyre"},
        headers=_headers(),
    )
    response = client.get("/rsa/incidents/aggregate", headers=_headers())
    assert response.status_code == 200
    assert response.json()["by_cause"] == [{"cause": "Flat Tyre", "count": 1}]


def test_csv_export(client):
    client.post(
        "/rsa/incidents",
        json={"incident_date": "2026-07-01", "vehicle_no": "A1", "cause": "Flat Tyre"},
        headers=_headers(),
    )
    response = client.get("/rsa/incidents/export?format=csv", headers=_headers())
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/rsa/test_rsa_router.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `rsa_router.py`**

```python
# backend/apps/backend/src/chatbot/features/rsa/rsa_router.py
"""HTTP surface for RSA incidents — CRUD + aggregate + CSV export.

Auth mirrors kb_knowledge_router.py's _authorize (x-api-key vs
faq_admin_api_key / proton_backend_key). Manual staff data entry: no
background tasks, no dispatch-system integration.
"""

from __future__ import annotations

import csv
import hmac
import io
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel


class _IncidentRequest(BaseModel):
    incident_date: str
    vehicle_no: str
    cause: str
    vehicle_model: str | None = None
    purchased_from: str | None = None
    breakdown_location: str | None = None
    arrived_location: str | None = None
    customer_called_in_time: datetime | None = None
    towing_assigned_time: datetime | None = None
    time_arrived_breakdown_area: datetime | None = None
    time_arrived_outlet: datetime | None = None
    total_km: int | None = None
    late_reason: str | None = None
    remarks: str | None = None
    created_by: str | None = None


class _IncidentUpdateRequest(BaseModel):
    incident_date: str | None = None
    vehicle_no: str | None = None
    cause: str | None = None
    vehicle_model: str | None = None
    purchased_from: str | None = None
    breakdown_location: str | None = None
    arrived_location: str | None = None
    customer_called_in_time: datetime | None = None
    towing_assigned_time: datetime | None = None
    time_arrived_breakdown_area: datetime | None = None
    time_arrived_outlet: datetime | None = None
    total_km: int | None = None
    late_reason: str | None = None
    remarks: str | None = None


def build_rsa_router(repo, settings) -> APIRouter:
    router = APIRouter()

    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        supplied = x_api_key.encode("utf-8")
        for key in (settings.faq_admin_api_key, settings.proton_backend_key):
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
        raise HTTPException(status_code=401, detail="Unauthorized")

    def _incident_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row.id, "incident_date": row.incident_date, "vehicle_no": row.vehicle_no,
            "vehicle_model": row.vehicle_model, "cause": row.cause,
            "purchased_from": row.purchased_from, "breakdown_location": row.breakdown_location,
            "arrived_location": row.arrived_location,
            "customer_called_in_time": row.customer_called_in_time,
            "towing_assigned_time": row.towing_assigned_time,
            "time_arrived_breakdown_area": row.time_arrived_breakdown_area,
            "time_arrived_outlet": row.time_arrived_outlet,
            "total_km": row.total_km, "late_reason": row.late_reason,
            "remarks": row.remarks, "created_by": row.created_by,
        }

    @router.post("/rsa/incidents")
    async def create_incident(
        payload: _IncidentRequest, x_api_key: str | None = Header(default=None)
    ) -> dict[str, str]:
        _authorize(x_api_key)
        incident_id = await repo.create_incident(**payload.model_dump())
        return {"id": incident_id, "status": "created"}

    @router.get("/rsa/incidents")
    async def list_incidents(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        rows = await repo.list_incidents()
        return {"incidents": [_incident_dict(r) for r in rows]}

    @router.get("/rsa/incidents/aggregate")
    async def aggregate(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        agg = await repo.aggregate()
        return asdict(agg)

    @router.get("/rsa/incidents/export")
    async def export_csv(
        format: str = "csv", x_api_key: str | None = Header(default=None)
    ) -> Response:
        _authorize(x_api_key)
        if format != "csv":
            raise HTTPException(status_code=400, detail="format must be csv")
        rows = await repo.list_incidents()
        buf = io.StringIO()
        writer = csv.writer(buf)
        if rows:
            fieldnames = list(_incident_dict(rows[0]).keys())
            writer.writerow(fieldnames)
            for row in rows:
                writer.writerow([_incident_dict(row).get(f) for f in fieldnames])
        else:
            writer.writerow(["(no data)"])
        return Response(
            content=buf.getvalue().encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=rsa-incidents.csv"},
        )

    @router.get("/rsa/incidents/{incident_id}")
    async def get_incident(
        incident_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        row = await repo.get_incident(incident_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _incident_dict(row)

    @router.patch("/rsa/incidents/{incident_id}")
    async def update_incident(
        incident_id: str, payload: _IncidentUpdateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        fields = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not await repo.update_incident(incident_id, **fields):
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": incident_id, "status": "updated"}

    @router.delete("/rsa/incidents/{incident_id}")
    async def delete_incident(
        incident_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, str]:
        _authorize(x_api_key)
        if not await repo.delete_incident(incident_id):
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": incident_id, "status": "deleted"}

    return router
```

Note the route ORDER: `/rsa/incidents/aggregate` and `/rsa/incidents/export` are declared BEFORE `/rsa/incidents/{incident_id}` — FastAPI matches routes in declaration order, so the literal paths must come first or `{incident_id}` would greedily capture "aggregate"/"export" as an id.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/rsa/test_rsa_router.py -v`
Expected: PASS.

- [ ] **Step 5: Wire into `main.py`**

In `main.py`, immediately after the pgvector-KB block (after the `_init_kb_db` startup handler, before the `# --- RBAC` comment, i.e. after line 481), add:

```python
    # --- RSA (roadside assistance) incident log (default-off) ---
    if settings.rsa_enabled and settings.rsa_database_url:
        from chatbot.features.rsa.rsa_db import build_engine as build_rsa_engine
        from chatbot.features.rsa.rsa_db import build_session_maker as build_rsa_session_maker
        from chatbot.features.rsa.rsa_repository import PgRsaRepository
        from chatbot.features.rsa.rsa_router import build_rsa_router

        rsa_engine = build_rsa_engine(settings.rsa_database_url)
        rsa_session_maker = build_rsa_session_maker(rsa_engine)
        rsa_repo = PgRsaRepository(rsa_session_maker)
        app.include_router(build_rsa_router(rsa_repo, settings))
        app.state.rsa_engine = rsa_engine

    @app.on_event("startup")
    async def _init_rsa_db() -> None:
        engine = getattr(app.state, "rsa_engine", None)
        if engine is not None:
            from chatbot.features.rsa.rsa_db import init_rsa_db
            await init_rsa_db(engine)
```

- [ ] **Step 6: Full backend suite regression check**

Run: `cd backend/apps/backend && pytest src/ -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/rsa/rsa_router.py src/chatbot/features/rsa/test_rsa_router.py src/chatbot/main.py
git commit -m "feat(rsa): add CRUD/aggregate/CSV-export router, wire into main.py behind RSA_ENABLED"
```

---

### Task 21: Document all new env vars

**Files:**
- Modify: `deploy/tenants/example.env`
- Modify: `backend/apps/backend/.env.example`
- Modify: `agent/app/config.py`'s tenant-env doc location (grep for where `LIFECYCLE_CATEGORY_LABELS` is documented in `deploy/tenants/example.env` and add near it)

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add to `deploy/tenants/example.env`**

Immediately after the existing `CASE_TAXONOMY_JSON=` line, add:

```bash
# Vehicle-model / case-type dimensions (Inquiry/Complaint/Feedback). Same
# fail-open pattern as CASE_TAXONOMY_JSON; set the SAME value in both the
# backend and agent tenant env sections.
VEHICLE_MODELS_JSON=
CASE_TYPE_OPTIONS_JSON=

# SOP resolution-time targets in working hours, per case_type (backend only —
# only the metrics sync/BigQuery-view side needs this).
RESOLUTION_SLA_TARGETS_JSON=

# RSA (roadside assistance) incident log — own Postgres table, default-off.
# Needs BOTH flags to activate (mirrors KNOWLEDGE_PG_ENABLED/RBAC_ENABLED).
RSA_ENABLED=false
RSA_DATABASE_URL=
```

- [ ] **Step 2: Add to `backend/apps/backend/.env.example`**

Mirror the same 5 variables with the same one-line comments, matching that file's existing formatting conventions (check `KNOWLEDGE_PG_ENABLED`/`KNOWLEDGE_DATABASE_URL`'s entries there for the exact style to copy).

- [ ] **Step 3: Commit**

```bash
git add deploy/tenants/example.env backend/apps/backend/.env.example
git commit -m "docs: document VEHICLE_MODELS_JSON/CASE_TYPE_OPTIONS_JSON/RESOLUTION_SLA_TARGETS_JSON/RSA_* env vars"
```

---

### Task 24: Align illustrative `case_taxonomy_json` default with the real Proton division/concern taxonomy

**Added mid-execution (2026-08-02):** the user pointed at two real Proton ops reporting decks (`docs/client-materials/Weekly Report Proton e.MAS.pptx`, `docs/client-materials/MONTHLY REPORTING FOR  Proton e.MAS.pptx`) and asked to reconcile our taxonomy defaults against them. Research (full-deck extraction + comparison against `config.py`/`labels.yaml`) found the illustrative `case_taxonomy_json` default in both `backend/apps/backend/src/chatbot/platform/config.py` and its mirror `agent/app/config.py` has three **structural** mismatches against the real division/case_type split the reports actually use (not just missing entries — the shape is wrong):

1. `complaint` is a top-level *category* in the current default, but in the real reports "Complaint" is the **case_type** (orthogonal to every division: Sales complaints, Aftersales complaints, etc.) — this plan's own Task 1/2/3 just added a dedicated `case_type_options_json` (`Inquiry`/`Complaint`/`Feedback`) that now owns this dimension, so keeping `complaint` as a `case_taxonomy_json` category is redundant/conflicting with the new field.
2. `roadside_assistance` and `general_enquiry` are top-level categories in the current default but don't correspond to any real top-level division — in the real reports, roadside-assistance concerns are a *subcategory* under **Aftersales** (there's also a fully separate, already-planned standalone RSA incident-log module from Tasks 18-20 — that's a different thing: a detailed incident log vs. how a *conversation* about RSA gets classified; both can coexist).
3. Three real, high-volume divisions are missing entirely from the current default: **Product** (Infotainment/Telematics), **Marketing**, and **Others** (the real reports' explicit "not related to Proton e.MAS" catch-all — 32% of one month's inquiries).

`case_type_options_json` (`Inquiry`/`Complaint`/`Feedback`) and `vehicle_models_json` (`e.MAS 5`/`e.MAS 7`/`e.MAS 7 PHEV`/`Not Applicable`) were verified against the same decks and are already correct — no change needed to either. `deploy/tenants/example.env`'s `CASE_TAXONOMY_JSON=` ships empty by design (real tenant values live in the gitignored per-tenant env) — this task only fixes the illustrative default shipped as a Python fallback in the two `config.py` files, so it's a genuinely representative example rather than fighting the env-var-driven design by hardcoding Proton's full ~150-entry concern list.

`chatwoot-config/labels.yaml` (a separate, older `category_*`/`division_*` label system, unrelated to this JSON-driven `case_taxonomy.py` mechanism) is explicitly OUT OF SCOPE for this task — it needs its own separate investigation before touching, not bundled here.

**Files:**
- Modify: `backend/apps/backend/src/chatbot/platform/config.py` (`case_taxonomy_json` default, lines 352-367)
- Modify: `agent/app/config.py` (mirrored `case_taxonomy_json` default — grep for `roadside_assistance` to find it)
- Modify: `backend/apps/backend/src/chatbot/features/chat/test_case_taxonomy.py` (`test_default_settings_produce_non_empty_taxonomy`, currently asserts `main_categories() == ["sales", "aftersales", "apps", "charging", "roadside_assistance", "general_enquiry", "complaint"]` at lines 71-86 — this MUST be updated to the new category list or it will fail)
- Check for and update any equivalent default-taxonomy-asserting test in `agent/tests/` (grep `roadside_assistance` or `general_enquiry` across `agent/tests/` first — none was found during research, but re-verify since agent/'s test suite may have grown since)

**Interfaces:** None — this only changes a `Settings` field's default string value and its two mirror-location tests. No function signatures change.

- [ ] **Step 1: Confirm current defaults and dependent tests**

Run `grep -rn "roadside_assistance\|general_enquiry" backend/apps/backend/src agent/app agent/tests` to confirm exactly which files reference the current default's category slugs before changing it (re-run this — do not trust the count above blindly, code may have moved since this task was written).

- [ ] **Step 2: Replace the `case_taxonomy_json` default in `backend/apps/backend/src/chatbot/platform/config.py`**

Replace the current default value (the `case_taxonomy_json: str = (...)` block, currently lines 352-367) with:

```python
    case_taxonomy_json: str = (
        '{"sales":{"label":"Sales","subcategories":["Accessories","Booking",'
        '"Insurance","New Model","Promotion","Refund","Test Drive","Trade In",'
        '"Transfer Ownership","Vehicle Delivery","Vehicle Details",'
        '"Customer Experience"]},'
        '"aftersales":{"label":"Aftersales","subcategories":["Body",'
        '"Roadside Assistance","Service / Recall Campaign","Service Operation",'
        '"Spare Part","Warranty","User Manual","Features"]},'
        '"apps":{"label":"Apps","subcategories":["Information","Operation",'
        '"User ID","No QR Scanner","Notification","Profile","Remote Control"]},'
        '"charging":{"label":"Charging","subcategories":["Home Charging",'
        '"Public Charging"]},'
        '"product":{"label":"Product","subcategories":["Infotainment",'
        '"Telematics"]},'
        '"marketing":{"label":"Marketing","subcategories":["Event / Campaign",'
        '"Partnership / Collaboration","Proposal","Sponsorship"]},'
        '"others":{"label":"Others","subcategories":['
        '"Not Related to Proton e.MAS"]}}'
    )
```

Keep the existing docstring/comment above the field (lines 346-351) — only the string value changes. Do not touch `vehicle_models_json`/`case_type_options_json` in this same block — they're confirmed correct and out of scope for this task.

- [ ] **Step 3: Apply the identical replacement to `agent/app/config.py`**

Find the mirrored `case_taxonomy_json` field there (grep `roadside_assistance` in `agent/app/config.py` to locate it) and apply the exact SAME new JSON string — this repo's convention (documented at both fields' original definition) is that the two services' defaults must stay byte-identical, since they're read from the SAME tenant env var in production (only the Python fallback differs per-service source file).

- [ ] **Step 4: Update `test_default_settings_produce_non_empty_taxonomy`**

In `backend/apps/backend/src/chatbot/features/chat/test_case_taxonomy.py`, change the `main_categories()` assertion (lines 78-86) from the old 7-slug list to:

```python
    assert tax.main_categories() == [
        "sales",
        "aftersales",
        "apps",
        "charging",
        "product",
        "marketing",
        "others",
    ]
```

- [ ] **Step 5: Update the equivalent agent/ test if one exists**

If Step 1's grep found a default-taxonomy-asserting test in `agent/tests/`, update it the same way. If none exists, skip this step (agent/'s `case_taxonomy.py` mirror may not have an equivalent "assert the real shipped default end-to-end" test — don't add one if the plan's Task 2 didn't establish that pattern there).

- [ ] **Step 6: Run tests to verify they pass**

Run:
```bash
cd backend/apps/backend && .venv/bin/python -m pytest src/chatbot/features/chat/test_case_taxonomy.py src/chatbot/features/chat/test_classify_ticket_tool.py -v
cd ../../../agent && .venv/bin/python -m pytest tests/ -k taxonomy -v
```
Expected: PASS. Then run each service's full suite once (`backend/apps/backend`: `.venv/bin/python -m pytest src/ -v`; `agent`: `.venv/bin/python -m pytest tests/ -v`) to confirm no other test asserts on the old category slugs (e.g. via a snapshot or a hardcoded classify_ticket_tool docstring check) — if something else breaks, read it before changing it further; the old slugs may be referenced somewhere Step 1's grep didn't catch if it used different exact search terms.

- [ ] **Step 7: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/platform/config.py src/chatbot/features/chat/test_case_taxonomy.py
git commit -m "fix(chat): align default case_taxonomy_json with real Proton division/concern taxonomy"
cd ../../../agent
git add app/config.py
git commit -m "fix: mirror the real-taxonomy case_taxonomy_json default in agent/"
```

(commit each service's change separately, matching this plan's existing per-service commit convention from Tasks 1/2.)

---

### Task 22: Fork patch — native report tabs (Dealer Escalation, SLA Compliance, WIP/Aging, category×vehicle-model)

**Files:**
- Modify (in the FE clone `/Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot`, exported as a new patch file in `deploy/chatwoot-fork/patches/`): the native Reports view directory (`app/javascript/dashboard/routes/dashboard/settings/reports/` — check its exact structure first via `ls`, following the existing `Proton{Sla,Csat,Bot,Agents}Section.vue` pattern for where sections are injected into native report views)
- Create (in the FE clone): `components/proton/ProtonDealerEscalationSection.vue`, `components/proton/ProtonSlaComplianceSection.vue`, `components/proton/ProtonWipAgingSection.vue`, plus additions to the existing Departments & PIC tab component for the category×vehicle-model cross-tab
- Create: `deploy/chatwoot-fork/patches/0030-reporting-extensions.patch`

**Interfaces:**
- Consumes: `GET /metrics/{dealer-escalation,sla-buckets,case-aging}` (Task 16) via the existing `protonMetrics.js` API client (check its current exports first — extend it, don't duplicate its `kbRequest`-style HTTP helper).

This task is UI/FE work in the separate Chatwoot clone repo, not `backend/`/`agent/` — it has no pytest suite; verification is `npx eslint --fix` + a local `vite build` (or `docker build --target builder`) per this repo's established FE convention, not TDD steps. It is broken out from the earlier code tasks for that reason.

- [ ] **Step 1: Confirm the FE clone is on a fully-patched baseline**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git status
git log --oneline -5
```

If patches 0001-0029 aren't already applied on the current checkout (verify by grepping for a known recent addition, e.g. `grep -rl "protonHasPermission" app/javascript/dashboard/components-next/sidebar/Sidebar.vue`), start from a clean upstream v4.15.1 checkout and `git apply` `deploy/chatwoot-fork/patches/0001-*.patch` through `0029-*.patch` in order first, confirming each applies clean (same procedure used for patch 0029 in this session).

- [ ] **Step 2: Check `protonMetrics.js`'s current shape**

Run: `grep -n "^export\|^async function\|kbRequest\|export function" app/javascript/dashboard/api/protonMetrics.js`

Add three new exported functions following its existing pattern exactly (same base-URL/auth helper it already uses for `/metrics/departments` etc.):

```javascript
export const getDealerEscalation = () => protonMetricsRequest('/metrics/dealer-escalation');
export const getSlaBuckets = () => protonMetricsRequest('/metrics/sla-buckets');
export const getCaseAging = () => protonMetricsRequest('/metrics/case-aging');
```

(replace `protonMetricsRequest` with whatever this file's actual internal fetch helper is named — read the file first, per Step 2's grep, and use its real name and its real signature.)

- [ ] **Step 3: Add the three new report sections**

Create `components/proton/ProtonDealerEscalationSection.vue`, mirroring `ProtonSlaSection.vue`'s structure exactly (self-fetching `onMounted`, a loading/empty/error state, a `BaseTable` rendering `by_dealer` rows with columns Dealer / Cases Escalated / Avg Turnaround (days) / P50 / P90, plus a "Slowest cases" sub-table from `slowest_cases`). Read `ProtonSlaSection.vue`'s actual current source first and copy its exact template/script structure (imports, `n-*` design tokens, `useAlert` error handling) rather than inventing new conventions — only the data-fetch call and table columns differ.

Create `ProtonSlaComplianceSection.vue` similarly, rendering `SlaBucketMetrics.buckets` grouped by `case_type` as a bar/table view (bucket_label × cases), following whichever of the existing sections (`ProtonCsatSection.vue`'s chart or `ProtonSlaSection.vue`'s table) is the closer visual fit — read both first and pick the one whose existing chart/table component it already imports, reusing that import rather than adding a new charting dependency.

Create `ProtonWipAgingSection.vue` rendering `CaseAgingMetrics.cases` as a table (case_type / division / dealer / pic / age_days / bucket_label), sortable by `age_days` descending (the data already arrives pre-sorted from `v_case_aging`'s `ORDER BY age_days DESC`, so no client-side sort is required — just render in the order received).

- [ ] **Step 4: Add the category×vehicle-model cross-tab to the Departments & PIC tab**

Find the existing Departments & PIC native section component (grep `dept_pic` or `ReopenRow`-consuming `.vue` file in `app/javascript/dashboard/routes/dashboard/settings/reports/`), and add a new sub-table/section rendering `DepartmentsMetrics.category_by_vehicle_model` (category / subcategory / vehicle_model / case_type / cases), inserted the same additive way patch `80835e0`'s complaint-type ranking addition was (a `+3-4 lines` self-contained addition to the existing view, per the REPORTS-MERGE program's established convention — check that commit's actual diff shape in this file's git history within the clone for the exact insertion pattern).

- [ ] **Step 5: Add the call-centre-metrics placeholder panel**

Add a small static component (no data fetch) — e.g. `ProtonCallCentrePlaceholder.vue` — rendering a message like "Call-centre metrics (AQT, response-time SLA, abandon rate) require telephony instrumentation not yet built (Phase 7) — check back once that's implemented." Mount it wherever the Reports nav would otherwise have gained a "Call Centre" tab, so the placeholder is discoverable rather than the feature simply not existing anywhere in the UI.

- [ ] **Step 6: Wire the new sections into the native report views' route/nav**

Follow whichever exact injection pattern `Proton{Sla,Csat,Bot,Agents}Section.vue` use (each is a self-contained `<section>` added with a small diff to an existing Options/setup view, per the 2026-07-21 REPORTS-MERGE program notes) — add the 3 new sections + the placeholder the same way, as small additive diffs to their respective native report view files, not as new standalone routes.

- [ ] **Step 7: Local build verification**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
npx eslint --fix app/javascript/dashboard/components-next/proton/ProtonDealerEscalationSection.vue app/javascript/dashboard/components-next/proton/ProtonSlaComplianceSection.vue app/javascript/dashboard/components-next/proton/ProtonWipAgingSection.vue
pnpm exec vite build
```
Expected: 0 errors. Also run the local builder-stage Docker compile-check (arm64 is fine for this, per the lesson from patch 0029): `docker build --target builder deploy/chatwoot-fork/` from the `id-crm-ticketing` repo root.

- [ ] **Step 8: Export the patch**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git diff > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0030-reporting-extensions.patch
```

Verify: on a scratch clean-upstream-v4.15.1 checkout, `git apply` `0001-*.patch` through `0030-*.patch` in sequence, all succeed with zero errors (same verification procedure used for patch 0029).

- [ ] **Step 9: Commit (id-crm-ticketing repo)**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0030-reporting-extensions.patch
git commit -m "feat(chatwoot-fork): add Dealer Escalation, SLA Compliance, WIP/Aging report sections + category-by-vehicle-model cross-tab"
```

Do NOT rebuild/deploy the Chatwoot image as part of this task — that's a separate Cloud Build + VM step, done once when the whole plan is ready to go live (per this repo's established deploy convention, and per the spec's rollout note that this is held pending RBAC anyway).

---

### Task 23: Fork patch — RSA entry/report page

**Files:**
- Create (in the FE clone): `components/proton/ProtonRsaPage.vue`, route + sidebar entry
- Create: `deploy/chatwoot-fork/patches/0031-rsa-incident-log.patch`

**Interfaces:**
- Consumes: `GET/POST/PATCH/DELETE /rsa/incidents*` (Task 20) via a new `protonRsa.js` API client, following `protonAdmin.js`'s existing shape (used by patches 0025-0027).

- [ ] **Step 1: Add `protonRsa.js` API client**

Create `app/javascript/dashboard/api/protonRsa.js` mirroring `protonAdmin.js`'s auth/fetch pattern (read that file first — it already handles the x-api-key header the same way `protonMetrics.js`/`protonKnowledge.js` do):

```javascript
// exact shape depends on protonAdmin.js's real helper name — mirror it here.
export const listRsaIncidents = () => protonAdminRequest('/rsa/incidents');
export const createRsaIncident = (payload) => protonAdminRequest('/rsa/incidents', { method: 'POST', body: payload });
export const updateRsaIncident = (id, payload) => protonAdminRequest(`/rsa/incidents/${id}`, { method: 'PATCH', body: payload });
export const deleteRsaIncident = (id) => protonAdminRequest(`/rsa/incidents/${id}`, { method: 'DELETE' });
export const getRsaAggregate = () => protonAdminRequest('/rsa/incidents/aggregate');
```

- [ ] **Step 2: Build `ProtonRsaPage.vue`**

A single native page combining: (a) an aggregate summary (cases by cause, by dealer — from `getRsaAggregate()`), (b) a data-entry form (the deck's columns: incident date, vehicle no., vehicle model, cause, purchased from, breakdown location, arrived location, 4 timestamp fields, total km, late reason, remarks) submitting via `createRsaIncident`, and (c) a table listing existing incidents with inline edit (`updateRsaIncident`) and delete (`deleteRsaIncident`). Follow `ProtonSlaPoliciesPage.vue`'s exact page-shell structure (header, `n-*` tokens, `useAlert` for success/error toasts) — read that file first and copy its shell, only the form fields and table columns differ.

- [ ] **Step 3: Wire the sidebar entry + route**

In `Sidebar.vue`, add a new gated entry following patch 0025/0026/0027's exact pattern (a new `protonHasPermission('rsa.manage')` — or reuse `'sla.manage'` if a dedicated RSA permission slug isn't warranted; check `useProtonPermissions.js`'s permission-key registry first and either add `rsa.manage` there consistently with the other 3, or justify reusing an existing key) — gated menu-item block, `to: accountScopedRoute('proton_rsa_incidents')`.

Add the route in `dashboard.routes.js` following patches 0025-0027's route-registration pattern exactly (same file, same insertion style).

- [ ] **Step 4: Local build verification**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
npx eslint --fix app/javascript/dashboard/api/protonRsa.js app/javascript/dashboard/components-next/proton/ProtonRsaPage.vue
pnpm exec vite build
```
Expected: 0 errors.

- [ ] **Step 5: Export and verify the patch**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git diff > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0031-rsa-incident-log.patch
```

Verify: `0001-*.patch` through `0031-*.patch` apply in sequence on a clean upstream checkout, zero errors.

- [ ] **Step 6: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0031-rsa-incident-log.patch
git commit -m "feat(chatwoot-fork): add native RSA incident-log entry/report page"
```

---

## Plan Self-Review Notes

- **Spec coverage:** every numbered item in the spec's "Design" section maps to a task — §1 (dimensions) → Tasks 1-6; §2 (business-hours timing) → Tasks 7-8; §3 (5 new views) → Tasks 10-13 (dealer_escalated_at capture is a NEW Task 9 the spec didn't anticipate — see the deviation note below) plus Tasks 14-17 (read-side wiring + CSV export, which the spec's Design §5 called for but didn't break out as separately as this plan does); §4 (RSA module) → Tasks 18-20; §5 (report UI) → Tasks 22-23. Task 21 (env doc) covers the spec's Rollout section. No spec Non-goal was accidentally implemented (no role-scoping, no PPTX/PowerBI, no telephony instrumentation, no RSA↔dispatch integration).
- **Deviation flagged prominently (Task 9):** the spec assumed a `dealer_<slug>`-label write site already existed to hook `dealer_escalated_at` into. It doesn't — dealer labeling is manual today, and `sync.py`'s `WRITE_TRUNCATE` full-reload sync has no previous-state to diff against, so the BigQuery-sync layer literally cannot detect "first appearance" of a label. Task 9 adds the smallest correct fix instead: a Chatwoot-side webhook stamp in `agent/`, which already receives `conversation_updated` events. Without this task, `v_dealer_escalation`'s turnaround-time columns (Task 12) would be built on a column (`dealer_escalated_at`) nothing ever populates — a real gap the case-categories plan's own Task-5 precedent (documented there as a similar "spec missed a site" addition) suggested watching for.
- **Second deviation (Task 17):** the spec said "every new view gets a CSV export button, reusing the existing export.py/export_router.py pattern" — but that pattern currently only renders xlsx/pdf, not CSV, at all. Task 17 adds `render_csv` as a new (small, low-risk) capability rather than silently substituting xlsx for what the spec explicitly asked for.
- **Placeholder scan:** no TBD/TODO markers; every step has complete, runnable code or an exact `grep`/read instruction pointing at the specific real file to consult before writing test fixture names (used only where this repo's existing fixture-naming conventions genuinely can't be predicted without reading the target file first — never as a substitute for showing real code).
- **Type/signature consistency:** `OptionList`/`build_option_list` (Tasks 1-2) is consumed identically in Tasks 3 and 5 with the same method names (`.options()`, `.is_valid()`, `.is_empty()`). `ConversationRow`'s cumulative new fields across Tasks 6/8/10 (`case_type`, `vehicle_model`, `first_response_working_minutes`, `resolution_working_minutes`, `dealer_escalated_at`) are consistently threaded through `bigquery_schema.py`/`mapping.py`/`sync.py` in the same order they're introduced. `MetricsQueryPort`'s 4 new methods (Task 14) match their `BigQueryMetricsQuery` implementations (Task 15) and their router routes (Task 16) by name exactly.
- **Scope check:** 23 tasks across 5 work areas is large, but each task is independently testable/committable (per the skill's Task Right-Sizing guidance) and the areas have a real dependency order (dimensions before views that filter by them; views before the read-side/router/export wiring that serves them; RSA is fully independent and could be executed in parallel with the metrics-pipeline tasks if using subagent-driven-development's parallelizable-task detection). Recommend executing Tasks 1-17 (dimensions → views → read-side, in order) as one continuous chain, Tasks 18-20 (RSA) as an independently-startable chain, and Tasks 22-23 (FE) last since they consume the finished HTTP endpoints from both chains.
