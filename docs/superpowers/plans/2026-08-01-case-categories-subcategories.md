# Case Categories & Subcategories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat, hierarchy-less `category_*`/`subcat_*` Chatwoot label convention with one enforced main category + dependent subcategory per conversation, driven by a tenant-configurable taxonomy, stored as Chatwoot custom attributes so Chatwoot's own native UI enforces single-select exclusivity.

**Architecture:** A `CASE_TAXONOMY_JSON` env value (mirroring `PIC_MAP_JSON`'s pattern) is parsed once per service into a `CaseTaxonomy` object. `backend/`'s `classify_ticket_tool` validates against it and the Chatwoot adapter writes `case_category`/`case_subcategory` as custom attributes (not labels) at the same points it already writes `sla_minutes`. `agent/`'s resolution-time `categorize.py` becomes a fallback that only fires if a conversation reaches resolution with no category set.

**Tech Stack:** Python 3, pydantic-settings, pytest, structlog, httpx (existing stack — no new dependencies).

## Global Constraints

- Fail-open: malformed/empty `CASE_TAXONOMY_JSON` → empty taxonomy → classify_ticket_tool falls back to accepting any text (today's behavior), never breaks the AI turn.
- Default-preserving: `CASE_TAXONOMY_JSON` ships with a working default (see spec) so the system works out of the box with no tenant configuration required.
- No backfill of existing `category_*`/`subcat_*` labels — left in place, untouched.
- `division`/`department`/`sla` stay as labels — only `category`/`subcategory` move to custom attributes.
- No new frontend component — Chatwoot's native custom-attribute sidebar handles display/edit.

---

### Task 1: `CaseTaxonomy` loader + Settings field (backend)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/case_taxonomy.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_case_taxonomy.py`
- Modify: `backend/apps/backend/src/chatbot/platform/config.py` (near `pic_map_json`, currently at line 329, comment block lines 323-328)

**Interfaces:**
- Produces: `CategoryEntry(label: str, subcategories: list[str])`, `CaseTaxonomy` with `.main_categories() -> list[str]`, `.label_for(slug: str) -> str | None`, `.subcategories_for(slug: str) -> list[str]`, `.is_valid_category(slug: str) -> bool`, `.is_valid_subcategory(slug: str, subcategory: str) -> bool`, `.flattened_subcategory_options() -> list[str]`, `.is_empty() -> bool`; `build_case_taxonomy(settings: Settings) -> CaseTaxonomy`. Used by Task 3 (classify tool) and Task 7 (provisioning script logic reference — that script has its own standalone copy, see Task 7 notes).

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/backend/src/chatbot/features/chat/test_case_taxonomy.py
from chatbot.features.chat.case_taxonomy import build_case_taxonomy
from chatbot.platform.config import Settings


def _settings(taxonomy_json: str) -> Settings:
    return Settings(_env_file=None, case_taxonomy_json=taxonomy_json)


VALID = """
{
  "sales": {"label": "Sales", "subcategories": ["Test Drive Booking", "Pricing Inquiry"]},
  "apps": {"label": "Apps", "subcategories": ["Login Issue"]}
}
"""


def test_valid_taxonomy_lookups():
    tax = build_case_taxonomy(_settings(VALID))
    assert tax.main_categories() == ["sales", "apps"]
    assert tax.label_for("sales") == "Sales"
    assert tax.label_for("SALES") == "Sales"  # case-insensitive
    assert tax.subcategories_for("sales") == ["Test Drive Booking", "Pricing Inquiry"]
    assert tax.is_valid_category("sales") is True
    assert tax.is_valid_category("unknown") is False
    assert tax.is_valid_subcategory("sales", "Pricing Inquiry") is True
    assert tax.is_valid_subcategory("sales", "Not A Real Sub") is False
    assert tax.is_empty() is False


def test_flattened_subcategory_options():
    tax = build_case_taxonomy(_settings(VALID))
    assert tax.flattened_subcategory_options() == [
        "Sales: Test Drive Booking",
        "Sales: Pricing Inquiry",
        "Apps: Login Issue",
    ]


def test_empty_json_yields_empty_taxonomy():
    tax = build_case_taxonomy(_settings(""))
    assert tax.is_empty() is True
    assert tax.main_categories() == []
    assert tax.is_valid_category("sales") is False


def test_malformed_json_yields_empty_taxonomy_not_crash():
    tax = build_case_taxonomy(_settings("{not valid json"))
    assert tax.is_empty() is True


def test_non_dict_json_yields_empty_taxonomy():
    tax = build_case_taxonomy(_settings("[1, 2, 3]"))
    assert tax.is_empty() is True


def test_entry_missing_label_is_skipped_not_crash():
    tax = build_case_taxonomy(_settings('{"sales": {"subcategories": ["x"]}}'))
    assert tax.is_empty() is True


def test_subcategories_default_to_empty_list_when_absent():
    tax = build_case_taxonomy(_settings('{"sales": {"label": "Sales"}}'))
    assert tax.subcategories_for("sales") == []


def test_subcategories_wrong_type_ignored_not_crash():
    tax = build_case_taxonomy(_settings('{"sales": {"label": "Sales", "subcategories": "not-a-list"}}'))
    assert tax.subcategories_for("sales") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_case_taxonomy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chatbot.features.chat.case_taxonomy'` (and `case_taxonomy_json` not a valid `Settings` field yet).

- [ ] **Step 3: Add the `case_taxonomy_json` Settings field**

In `backend/apps/backend/src/chatbot/platform/config.py`, immediately after the `pic_map_json` field (line 329), add:

```python
    # Case category/subcategory taxonomy — JSON object keyed by main-category
    # slug: {"label": str, "subcategories": [str, ...]}. Same fail-open pattern
    # as PIC_MAP_JSON: malformed/empty -> empty taxonomy, classify_ticket_tool
    # falls back to accepting free text (pre-taxonomy behavior). Ships with a
    # working default so the system functions out of the box; override per
    # tenant once the client finalizes their scheme — no code change needed.
    case_taxonomy_json: str = (
        '{"sales":{"label":"Sales","subcategories":["Test Drive Booking",'
        '"Pricing Inquiry","Vehicle Availability","Trade-In","Financing"]},'
        '"aftersales":{"label":"Aftersales","subcategories":["Service Booking",'
        '"Warranty Claim","Spare Parts","Recall"]},'
        '"apps":{"label":"Apps","subcategories":["Login Issue","App Crash",'
        '"Feature Request","Account Sync"]},'
        '"charging":{"label":"Charging","subcategories":["Charger Fault",'
        '"Charging Station Locator","Billing"]},'
        '"roadside_assistance":{"label":"Roadside Assistance","subcategories":'
        '["Breakdown","Accident","Towing"]},'
        '"general_enquiry":{"label":"General Enquiry","subcategories":'
        '["Product Info","Dealer Locator","Other"]},'
        '"complaint":{"label":"Complaint","subcategories":["Service Quality",'
        '"Product Defect","Staff Conduct","Other"]}}'
    )
```

- [ ] **Step 4: Implement `case_taxonomy.py`**

```python
# backend/apps/backend/src/chatbot/features/chat/case_taxonomy.py
"""Case category/subcategory taxonomy — loaded once from CASE_TAXONOMY_JSON.

Mirrors pic_registry.py's fail-open JSON-parsing pattern: malformed/absent
config never crashes the app, it just yields an empty taxonomy (classification
falls back to accepting free text, matching pre-taxonomy behavior).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CategoryEntry:
    label: str
    subcategories: list[str] = field(default_factory=list)


class CaseTaxonomy:
    """slug -> CategoryEntry table, loaded once from JSON config."""

    def __init__(self, table: dict[str, CategoryEntry]) -> None:
        self._table = table

    def main_categories(self) -> list[str]:
        """Slugs, in the order they appeared in the source JSON."""
        return list(self._table.keys())

    def label_for(self, slug: str) -> str | None:
        entry = self._table.get(slug.lower())
        return entry.label if entry else None

    def subcategories_for(self, slug: str) -> list[str]:
        entry = self._table.get(slug.lower())
        return list(entry.subcategories) if entry else []

    def is_valid_category(self, slug: str) -> bool:
        return slug.lower() in self._table

    def is_valid_subcategory(self, slug: str, subcategory: str) -> bool:
        return subcategory in self.subcategories_for(slug)

    def flattened_subcategory_options(self) -> list[str]:
        """'<Label>: <Subcategory>' for every category — the case_subcategory
        custom attribute definition's option list."""
        options: list[str] = []
        for entry in self._table.values():
            options.extend(f"{entry.label}: {sub}" for sub in entry.subcategories)
        return options

    def is_empty(self) -> bool:
        return not self._table


def build_case_taxonomy(settings: Settings) -> CaseTaxonomy:
    """Parse CASE_TAXONOMY_JSON and return a CaseTaxonomy.

    Returns an empty taxonomy (all validation calls return False/[]) when the
    JSON is absent, empty, or malformed — so a misconfigured taxonomy never
    crashes the app.
    """
    raw = (settings.case_taxonomy_json or "").strip()
    if not raw:
        return CaseTaxonomy({})
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("case_taxonomy_json_parse_failed", error=str(exc))
        return CaseTaxonomy({})
    if not isinstance(data, dict):
        _log.warning("case_taxonomy_json_not_a_dict", got=type(data).__name__)
        return CaseTaxonomy({})
    table: dict[str, CategoryEntry] = {}
    for slug, val in data.items():
        if not isinstance(val, dict):
            continue
        try:
            subs_raw = val.get("subcategories", [])
            subcategories = [str(s) for s in subs_raw] if isinstance(subs_raw, list) else []
            table[slug.lower()] = CategoryEntry(label=str(val["label"]), subcategories=subcategories)
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning("case_taxonomy_entry_invalid", slug=slug, error=str(exc))
    return CaseTaxonomy(table)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_case_taxonomy.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/case_taxonomy.py src/chatbot/features/chat/test_case_taxonomy.py src/chatbot/platform/config.py
git commit -m "feat(chat): add CASE_TAXONOMY_JSON-driven CaseTaxonomy loader"
```

---

### Task 2: Mirror taxonomy loader + Settings field (agent/)

**Files:**
- Create: `agent/app/services/case_taxonomy.py`
- Create: `agent/tests/test_case_taxonomy.py`
- Modify: `agent/app/config.py` (near `lifecycle_category_labels`, currently lines 98-103)

**Interfaces:**
- Produces: same `CaseTaxonomy`/`build_case_taxonomy` shape as Task 1, in `agent/`'s own package (services are intentionally decoupled — no shared library between `backend/` and `agent/` per this repo's architecture). Used by Task 6.

- [ ] **Step 1: Write the failing test**

```python
# agent/tests/test_case_taxonomy.py
from app.config import get_settings
from app.services.case_taxonomy import build_case_taxonomy


def _settings(taxonomy_json: str):
    s = get_settings()
    object.__setattr__(s, "case_taxonomy_json", taxonomy_json) if False else None
    return s.model_copy(update={"case_taxonomy_json": taxonomy_json})


VALID = '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking"]}}'


def test_valid_taxonomy_lookups():
    tax = build_case_taxonomy(_settings(VALID))
    assert tax.main_categories() == ["sales"]
    assert tax.label_for("sales") == "Sales"
    assert tax.subcategories_for("sales") == ["Test Drive Booking"]
    assert tax.is_valid_category("sales") is True


def test_empty_json_yields_empty_taxonomy():
    tax = build_case_taxonomy(_settings(""))
    assert tax.is_empty() is True


def test_malformed_json_yields_empty_taxonomy_not_crash():
    tax = build_case_taxonomy(_settings("{broken"))
    assert tax.is_empty() is True
```

Note: check `agent/tests/conftest.py` for the actual `Settings` construction helper already used by sibling tests (e.g. `test_sop_config.py`, `test_pic_registry.py`-equivalent if one exists under `agent/tests/`) and use that helper instead of `model_copy` if one already exists — match the established convention in this file, don't introduce a second pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_case_taxonomy.py -v`
Expected: FAIL — `ModuleNotFoundError` and missing `case_taxonomy_json` field.

- [ ] **Step 3: Add the `case_taxonomy_json` Settings field**

In `agent/app/config.py`, immediately after `lifecycle_category_labels` (line 103), add:

```python
    # Case category/subcategory taxonomy — set to the SAME value as backend/'s
    # CASE_TAXONOMY_JSON (both services parse it independently; there is no
    # shared library between them). Used by services/categorize.py as the
    # resolution-time fallback classifier's candidate list. Empty -> the
    # fallback classifier no-ops (same as today's lifecycle_category_labels
    # behavior when empty).
    case_taxonomy_json: str = ""
```

Leave `lifecycle_category_labels` defined but note in its existing comment (line 100-102) that it's superseded by `case_taxonomy_json` and kept only so an old deployment that still sets it doesn't hard-fail on startup (unused by the new code path — see Task 6).

- [ ] **Step 4: Implement `case_taxonomy.py`**

```python
# agent/app/services/case_taxonomy.py
"""Case category/subcategory taxonomy for the resolution-time fallback
classifier (services/categorize.py). Mirrors backend/'s case_taxonomy.py —
duplicated, not shared, per this repo's agent/backend decoupling."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CategoryEntry:
    label: str
    subcategories: list[str] = field(default_factory=list)


class CaseTaxonomy:
    def __init__(self, table: dict[str, CategoryEntry]) -> None:
        self._table = table

    def main_categories(self) -> list[str]:
        return list(self._table.keys())

    def label_for(self, slug: str) -> str | None:
        entry = self._table.get(slug.lower())
        return entry.label if entry else None

    def subcategories_for(self, slug: str) -> list[str]:
        entry = self._table.get(slug.lower())
        return list(entry.subcategories) if entry else []

    def is_valid_category(self, slug: str) -> bool:
        return slug.lower() in self._table

    def is_valid_subcategory(self, slug: str, subcategory: str) -> bool:
        return subcategory in self.subcategories_for(slug)

    def is_empty(self) -> bool:
        return not self._table


def build_case_taxonomy(settings: Settings) -> CaseTaxonomy:
    raw = (settings.case_taxonomy_json or "").strip()
    if not raw:
        return CaseTaxonomy({})
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("case_taxonomy_json_parse_failed: %s", exc)
        return CaseTaxonomy({})
    if not isinstance(data, dict):
        _log.warning("case_taxonomy_json_not_a_dict: got %s", type(data).__name__)
        return CaseTaxonomy({})
    table: dict[str, CategoryEntry] = {}
    for slug, val in data.items():
        if not isinstance(val, dict):
            continue
        try:
            subs_raw = val.get("subcategories", [])
            subcategories = [str(s) for s in subs_raw] if isinstance(subs_raw, list) else []
            table[slug.lower()] = CategoryEntry(label=str(val["label"]), subcategories=subcategories)
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning("case_taxonomy_entry_invalid slug=%s: %s", slug, exc)
    return CaseTaxonomy(table)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd agent && pytest tests/test_case_taxonomy.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
cd agent
git add app/services/case_taxonomy.py tests/test_case_taxonomy.py app/config.py
git commit -m "feat: mirror CASE_TAXONOMY_JSON loader in agent/ for resolution-time fallback"
```

---

### Task 3: Validate `classify_ticket_tool` against the taxonomy

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/agents.py` (`build_ai_agent`, lines 17-30 signature/docstring area; `classify_ticket_tool`, lines 53-78)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_classify_ticket_tool.py` (new — no existing dedicated test file for this tool was found; sibling pattern is `test_flag_for_ticket_tool.py`/`test_show_models_tool.py`, both call `build_ai_agent(get_settings(), InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter())` directly)

**Interfaces:**
- Consumes: `CaseTaxonomy` from Task 1 (`build_case_taxonomy`, `is_valid_category`, `is_valid_subcategory`, `main_categories`, `label_for`).
- Produces: `classify_ticket_tool` now rejects an invalid category/subcategory pair (state unchanged) instead of accepting any string.

- [ ] **Step 1: Write the failing test**

```python
# backend/apps/backend/src/chatbot/features/chat/test_classify_ticket_tool.py
import pytest
from google.adk.tools.tool_context import ToolContext

from chatbot.features.chat.agents import build_ai_agent
from chatbot.platform.config import get_settings


class _InMemoryTicketing:
    async def create_ticket(self, **kwargs):
        return "T-1"


class _InMemoryKnowledge:
    async def search_kb(self, query, limit=2):
        return []


def _classify_tool(taxonomy_json: str):
    settings = get_settings()
    settings = settings.model_copy(update={"case_taxonomy_json": taxonomy_json})
    agent = build_ai_agent(settings, _InMemoryTicketing(), _InMemoryKnowledge())
    for tool in agent.tools:
        if getattr(tool, "__name__", "") == "classify_ticket_tool" or (
            hasattr(tool, "func") and tool.func.__name__ == "classify_ticket_tool"
        ):
            return tool
    pytest.fail("classify_ticket_tool not found in agent.tools")


class _FakeToolContext:
    def __init__(self):
        self.state = {}


VALID = '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking"]}}'


@pytest.mark.asyncio
async def test_valid_category_and_subcategory_written():
    tool = _classify_tool(VALID)
    ctx = _FakeToolContext()
    await tool(ctx, category="sales", subcategory="Test Drive Booking", priority="HIGH", sla_minutes=60)
    assert ctx.state["category"] == "sales"
    assert ctx.state["subcategory"] == "Test Drive Booking"
    assert ctx.state["priority"] == "HIGH"
    assert ctx.state["sla_minutes"] == 60


@pytest.mark.asyncio
async def test_invalid_category_not_written_but_priority_still_is():
    tool = _classify_tool(VALID)
    ctx = _FakeToolContext()
    await tool(ctx, category="not_a_real_category", subcategory="x", priority="LOW", sla_minutes=30)
    assert "category" not in ctx.state
    assert "subcategory" not in ctx.state
    assert ctx.state["priority"] == "LOW"
    assert ctx.state["sla_minutes"] == 30


@pytest.mark.asyncio
async def test_valid_category_invalid_subcategory_neither_written():
    tool = _classify_tool(VALID)
    ctx = _FakeToolContext()
    await tool(ctx, category="sales", subcategory="Not A Real Sub", priority="LOW", sla_minutes=30)
    assert "category" not in ctx.state
    assert "subcategory" not in ctx.state


@pytest.mark.asyncio
async def test_empty_taxonomy_falls_back_to_accepting_free_text():
    tool = _classify_tool("")
    ctx = _FakeToolContext()
    await tool(ctx, category="Anything", subcategory="Whatever", priority="LOW", sla_minutes=30)
    assert ctx.state["category"] == "Anything"
    assert ctx.state["subcategory"] == "Whatever"
```

If `agent.tools` isn't directly iterable/introspectable this way in this ADK version, check how `test_flag_for_ticket_tool.py` locates and calls a tool from `build_ai_agent`'s return value — mirror that exact lookup mechanism instead (it already solves this problem for a sibling tool).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_classify_ticket_tool.py -v`
Expected: FAIL on `test_invalid_category_not_written_but_priority_still_is` and `test_valid_category_invalid_subcategory_neither_written` (today's tool writes any string unconditionally).

- [ ] **Step 3: Implement validation in `classify_ticket_tool`**

In `backend/apps/backend/src/chatbot/features/chat/agents.py`:

1. Add the import (top of file, alongside existing `TYPE_CHECKING` imports at lines 12-14):

```python
from chatbot.features.chat.case_taxonomy import build_case_taxonomy
```

2. Inside `build_ai_agent`, right after the docstring (before `search_kb_tool`'s definition, i.e. before line 33), build the taxonomy once:

```python
    case_taxonomy = build_case_taxonomy(settings)
```

3. Replace `classify_ticket_tool` (lines 53-78) with:

```python
    async def classify_ticket_tool(
        tool_context: ToolContext,
        category: str,
        subcategory: str,
        priority: str,
        sla_minutes: int,
    ) -> str:
        """Classify the current ticket details.

        Args:
            tool_context: Context injected by the ADK runner.
            category: General category of the problem.
            subcategory: Precise subcategory matching the chosen category.
            priority: Priority tier (LOW, MEDIUM, HIGH, URGENT).
            sla_minutes: Targeted SLA duration in minutes.
        """
        tool_context.state["priority"] = priority
        tool_context.state["sla_minutes"] = sla_minutes

        if case_taxonomy.is_empty():
            # No taxonomy configured — pre-feature fallback: accept free text.
            tool_context.state["category"] = category
            tool_context.state["subcategory"] = subcategory
        elif case_taxonomy.is_valid_category(category) and case_taxonomy.is_valid_subcategory(
            category, subcategory
        ):
            tool_context.state["category"] = category
            tool_context.state["subcategory"] = subcategory
        else:
            _log.warning(
                "classify_ticket_tool_invalid_category",
                category=category,
                subcategory=subcategory,
            )

        return (
            f"[internal] ticket classified as {category} -> {subcategory} "
            f"({priority}, SLA {sla_minutes}m)."
        )

    if not case_taxonomy.is_empty():
        classify_ticket_tool.__doc__ = (
            "Classify the current ticket details.\n\n"
            "Args:\n"
            "    tool_context: Context injected by the ADK runner.\n"
            f"    category: MUST be exactly one of: {', '.join(case_taxonomy.main_categories())}.\n"
            "    subcategory: MUST match one of the subcategories for the chosen category:\n"
            + "\n".join(
                f"        {slug} -> {', '.join(case_taxonomy.subcategories_for(slug))}"
                for slug in case_taxonomy.main_categories()
            )
            + "\n    priority: Priority tier (LOW, MEDIUM, HIGH, URGENT).\n"
            "    sla_minutes: Targeted SLA duration in minutes."
        )
```

4. Add `_log = structlog.get_logger(__name__)` near the top of `agents.py` if it isn't already defined there (check first — `structlog` may already be imported/used elsewhere in this file; if so reuse the existing logger instance instead of creating a second one).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_classify_ticket_tool.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full chat test suite to check for regressions**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/ -v -k "classify or agent"`
Expected: PASS, no regressions in `test_flag_for_ticket_tool.py`/`test_show_models_tool.py` (they don't touch classify_ticket_tool but share `build_ai_agent`).

- [ ] **Step 6: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/agents.py src/chatbot/features/chat/test_classify_ticket_tool.py
git commit -m "feat(chat): validate classify_ticket_tool category/subcategory against taxonomy"
```

---

### Task 4: Write `case_category`/`case_subcategory` as custom attributes, stop labeling them

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py` (`_dimension_labels` at lines 293-325; `create_ticket` at lines 480-547; `open_handoff` around lines 695-763)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_chatwoot_ticketing.py` (existing — extend it; check its current assertions on `_dimension_labels`/labels payload first so you update rather than duplicate them)

**Interfaces:**
- Consumes: nothing new (category/subcategory are already parameters on `create_ticket`/`open_handoff`).
- Produces: `_dimension_labels` signature drops `category`/`subcategory` params (now `_dimension_labels(division, department, sla_minutes)`); both call sites POST `case_category`/`case_subcategory` in the same `custom_attributes` call that already sets `sla_minutes`.

- [ ] **Step 1: Read the existing test file to find what to update**

Run: `grep -n "_dimension_labels\|category_\|subcat_" backend/apps/backend/src/chatbot/features/chat/test_chatwoot_ticketing.py`

Update every assertion that expects `category_*`/`subcat_*` in the labels payload — those should move to asserting a `custom_attributes` call containing `case_category`/`case_subcategory` instead. Add new test cases per the steps below if the existing file doesn't already parametrize category/subcategory.

- [ ] **Step 2: Write the failing test**

```python
# addition to test_chatwoot_ticketing.py — adjust class/fixture names to match
# whatever this file's existing ChatwootAdapter test setup already uses.
@pytest.mark.asyncio
async def test_create_ticket_writes_case_category_as_custom_attribute(chatwoot_adapter, respx_mock):
    respx_mock.post(f"{BASE}/conversations/{CONV_ID}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )
    # ... existing setup for the other mocked calls create_ticket makes ...

    await chatwoot_adapter.create_ticket(
        session_id="s1",
        title="t",
        body="b",
        urgency="high",
        category="sales",
        subcategory="Test Drive Booking",
        division="Sales",
        department="dept_sales",
        sla_minutes=60,
    )

    custom_attrs_calls = [
        c for c in respx_mock.calls if c.request.url.path.endswith("/custom_attributes")
    ]
    assert len(custom_attrs_calls) == 1  # ONE call, not two — merged with sla_minutes
    body = json.loads(custom_attrs_calls[0].request.content)
    assert body["custom_attributes"]["case_category"] == "sales"
    assert body["custom_attributes"]["case_subcategory"] == "Test Drive Booking"
    assert body["custom_attributes"]["sla_minutes"] == 60

    labels_calls = [c for c in respx_mock.calls if c.request.url.path.endswith("/labels")]
    labels_body = json.loads(labels_calls[0].request.content)
    assert not any(lbl.startswith("category_") for lbl in labels_body["labels"])
    assert not any(lbl.startswith("subcat_") for lbl in labels_body["labels"])
    assert any(lbl.startswith("division_") for lbl in labels_body["labels"])  # unaffected
```

Match this test's fixture names (`chatwoot_adapter`, `respx_mock`, `BASE`, `CONV_ID`) to whatever the existing file in this repo actually calls them — read the file first (Step 1) and use its real names, not these placeholders.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_chatwoot_ticketing.py -k case_category -v`
Expected: FAIL — today's code puts category/subcategory in labels, not custom_attributes, and fires two separate custom_attributes-shaped assertions incorrectly (only sla_minutes is written there today).

- [ ] **Step 4: Implement the write-path change**

In `backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`:

1. Change `_dimension_labels`'s signature (lines 293-300) to drop `category`/`subcategory`:

```python
    @staticmethod
    def _dimension_labels(
        division: str | None,
        department: str | None,
        sla_minutes: int | None,
    ) -> list[str]:
        """Encode the AI classification as Chatwoot conversation labels.

        category/subcategory moved to custom attributes (case_category/
        case_subcategory) — see the custom_attributes block at each call site.
        Uses the SAME tag-name convention the Zendesk metrics ``mapping.py``
        already parses (``division_*``, ``dept_*``, ``sla_<int>``) so the batch
        sync can read the dimensions straight back off the conversation.
        """

        def _norm(v: str) -> str:
            return v.strip().lower().replace(" ", "_")

        labels: list[str] = []
        if division:
            labels.append(f"division_{_norm(division)}")
        if department:
            labels.append(f"dept_{_norm(department)}")
        if sla_minutes is not None:
            labels.append(f"sla_{sla_minutes}")
        return labels
```

2. In `create_ticket` (around lines 514-531), replace the `sla_minutes`-only custom_attributes block and the `_dimension_labels` call:

```python
        # case_category/case_subcategory + sla_minutes as custom attributes —
        # case_category/subcategory are List-type Chatwoot attribute
        # definitions (see chatwoot-config/provision_case_taxonomy.py), so
        # Chatwoot's own native sidebar enforces single-select exclusivity.
        custom_attrs: dict[str, Any] = {}
        if sla_minutes is not None:
            custom_attrs["sla_minutes"] = sla_minutes
        if category:
            custom_attrs["case_category"] = category
        if subcategory:
            custom_attrs["case_subcategory"] = subcategory
        if custom_attrs:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/custom_attributes",
                {"custom_attributes": custom_attrs},
            )
        # Apply the escalation labels LAST: a downstream sync escalates on a
        # conversation_updated carrying the escalate label, so nothing must update
        # the conversation after this or each update re-triggers a duplicate ticket.
        dimension_labels = self._dimension_labels(division, department, sla_minutes)
```

(the rest of `create_ticket` — the `pic_lbl`/labels POST — is unchanged, `dimension_labels` is just built with fewer args now.)

3. Apply the identical change in `open_handoff` (around lines 729-747): merge `payload.category`/`payload.subcategory` into the same custom_attributes dict as `payload.sla_minutes`, then call `self._dimension_labels(payload.division, payload.department, payload.sla_minutes)`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/test_chatwoot_ticketing.py -v`
Expected: PASS, including any pre-existing tests you updated in Step 1.

- [ ] **Step 6: Run the full adapter test file plus escalation tests for regressions**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/ -k "chatwoot or escalation" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/adapters/chatwoot.py src/chatbot/features/chat/test_chatwoot_ticketing.py
git commit -m "feat(chat): write case_category/case_subcategory as custom attributes, not labels"
```

---

### Task 5: Update the metrics/reports mapping to read the new custom attributes

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/metrics/mapping.py` (`map_chatwoot_conversation_to_row`, lines 290-314 region)
- Test: `backend/apps/backend/src/chatbot/features/metrics/test_mapping.py` (check exact filename via `ls backend/apps/backend/src/chatbot/features/metrics/test_*.py` first)

**Interfaces:**
- Consumes: nothing new — reads `conv["custom_attributes"]` instead of label regexes.
- Produces: `map_chatwoot_conversation_to_row` still returns the same `ConversationRow` shape; `category`/`subcategory` fields now sourced from custom attributes. This is the fix for the reports table column (`0020-reports-native-merge.patch`) too — that Vue table reads whatever this function/its downstream serialization produces, it does not call Chatwoot directly.

- [ ] **Step 1: Write the failing test**

```python
def test_map_chatwoot_conversation_reads_case_category_from_custom_attributes():
    conv = {
        "id": 42,
        "status": "resolved",
        "created_at": 1700000000,
        "last_activity_at": 1700003600,
        "labels": ["division_sales", "sla_60"],
        "custom_attributes": {
            "case_category": "Sales",
            "case_subcategory": "Sales: Test Drive Booking",
        },
        "meta": {"sender": {"id": 1, "phone_number": "+60123456789"}},
    }
    row = map_chatwoot_conversation_to_row(conv)
    assert row is not None
    assert row.category == "Sales"
    assert row.subcategory == "Sales: Test Drive Booking"
    assert row.division == "Sales"  # explicit division_ label still wins


def test_map_chatwoot_conversation_missing_custom_attributes_yields_none_category():
    conv = {
        "id": 43,
        "status": "open",
        "created_at": 1700000000,
        "labels": ["division_apps"],
        "meta": {"sender": {"id": 1}},
    }
    row = map_chatwoot_conversation_to_row(conv)
    assert row is not None
    assert row.category is None
    assert row.subcategory is None
```

Check the existing test file's exact fixture/conversation-dict shape (sender id, meta structure, etc.) first and match it — these snippets show the fields that matter for this test, fill in whatever other required fields the existing `ConversationRow`/test helpers need to not fail on unrelated missing data.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -k case_category -v`
Expected: FAIL — `row.category` is `None` in the first test because today's code reads `category_*` labels, and this conversation has no such label.

- [ ] **Step 3: Implement the change**

In `map_chatwoot_conversation_to_row` (`mapping.py`), replace lines 308-309:

```python
    category = _first_tag(labels, _CATEGORY_TAG)
    subcategory = _first_tag(labels, _SUBCAT_TAG)
```

with:

```python
    custom_attrs = conv.get("custom_attributes")
    custom_attrs = custom_attrs if isinstance(custom_attrs, dict) else {}
    category = custom_attrs.get("case_category")
    subcategory = custom_attrs.get("case_subcategory")
```

Leave `_CATEGORY_TAG`/`_SUBCAT_TAG` regex constants (lines 36-37) defined — `map_ticket_to_row` (the Zendesk path, line ~167) still uses them and is out of scope for this change.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/apps/backend && pytest src/chatbot/features/metrics/ -v`
Expected: PASS, no regressions in `map_ticket_to_row` tests (untouched code path).

- [ ] **Step 5: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/metrics/mapping.py src/chatbot/features/metrics/test_mapping.py
git commit -m "fix(metrics): read case_category/case_subcategory from custom attributes"
```

---

### Task 6: Convert `agent/`'s resolution-time classifier to a taxonomy-aware fallback

**Files:**
- Modify: `agent/app/services/categorize.py` (full file, 89 lines — `maybe_categorize` at lines 64-89, `_candidate_slugs` at lines 29-31)
- Test: `agent/tests/test_categorize.py` (check exact filename via `ls agent/tests/test_categoriz*`)

**Interfaces:**
- Consumes: `CaseTaxonomy` from Task 2; `agent/app/clients/chatwoot.py::ChatwootClient.get_conversation(conversation_id)` (existing, currently unused by this file) and `.set_custom_attributes(conversation_id, attributes: dict)` (existing, already used elsewhere in this file).
- Produces: `maybe_categorize` no longer classifies when `case_category` is already set on the conversation; writes `case_category`/`case_subcategory` as custom attributes instead of an `add_labels` call.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_maybe_categorize_skips_when_already_classified(monkeypatch, chatwoot_client_stub):
    chatwoot_client_stub.conversations[1] = {
        "id": 1,
        "custom_attributes": {"case_category": "sales"},
    }
    settings = get_settings().model_copy(update={
        "lifecycle_auto_categorize": True,
        "case_taxonomy_json": '{"sales": {"label": "Sales", "subcategories": []}}',
    })
    await maybe_categorize(1, settings=settings, chatwoot=chatwoot_client_stub)
    assert chatwoot_client_stub.set_custom_attributes_calls == []  # never called — already set


@pytest.mark.asyncio
async def test_maybe_categorize_classifies_when_empty(monkeypatch, chatwoot_client_stub):
    chatwoot_client_stub.conversations[2] = {"id": 2, "custom_attributes": {}}
    chatwoot_client_stub.messages[2] = [{"content": "I want to book a test drive", "private": False, "sender": {"type": "contact"}}]
    settings = get_settings().model_copy(update={
        "lifecycle_auto_categorize": True,
        "case_taxonomy_json": '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking"]}}',
    })
    monkeypatch.setattr("app.services.categorize.classify_category", lambda transcript, candidates: "sales")
    await maybe_categorize(2, settings=settings, chatwoot=chatwoot_client_stub)
    assert chatwoot_client_stub.set_custom_attributes_calls == [(2, {"case_category": "sales"})]
```

Check `agent/tests/conftest.py` for the actual stub/fixture name used for a fake `ChatwootClient` in existing `categorize.py` tests (there should be one already, since `test_categorize.py` presumably exists) — mirror its exact interface (`conversations`, `messages`, `set_custom_attributes_calls` attribute names shown above are illustrative; use whatever the real stub already exposes, extending it with a `conversations` dict + `get_conversation` method if the existing stub doesn't have one yet).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_categorize.py -k maybe_categorize -v`
Expected: FAIL — today's `maybe_categorize` doesn't call `get_conversation` at all and writes via `add_labels`, not `set_custom_attributes`.

- [ ] **Step 3: Implement the change**

In `agent/app/services/categorize.py`:

1. Replace `_candidate_slugs` (lines 29-31) — it now takes a `CaseTaxonomy` instead of parsing `lifecycle_category_labels`:

```python
def _candidate_slugs(taxonomy: CaseTaxonomy) -> list[str]:
    return taxonomy.main_categories()
```

2. Add the import at the top of the file:

```python
from app.services.case_taxonomy import CaseTaxonomy, build_case_taxonomy
```

3. Replace `maybe_categorize` (lines 64-89):

```python
async def maybe_categorize(conversation_id: int, *, settings=None, chatwoot=None) -> None:
    settings = settings or get_settings()
    chatwoot = chatwoot or get_chatwoot_client()

    if not settings.lifecycle_auto_categorize:
        return

    taxonomy = build_case_taxonomy(settings)
    if taxonomy.is_empty():
        return

    conversation = await chatwoot.get_conversation(conversation_id)
    existing = (conversation or {}).get("custom_attributes") or {}
    if existing.get("case_category"):
        return  # already classified mid-conversation — never overwrite

    candidates = _candidate_slugs(taxonomy)
    messages = await chatwoot.get_messages(conversation_id)
    transcript = _transcript_from_messages(messages)
    if not transcript:
        return

    category = classify_category(transcript, candidates)
    if category is None:
        return

    attrs = {"case_category": category}
    sub_transcript_hint = transcript
    subcategory_candidates = taxonomy.subcategories_for(category)
    if subcategory_candidates:
        subcategory = classify_category(sub_transcript_hint, subcategory_candidates)
        if subcategory is not None:
            attrs["case_subcategory"] = subcategory

    await chatwoot.set_custom_attributes(conversation_id, attrs)
```

(`classify_category`, `_transcript_from_messages`, `_SYSTEM_PROMPT`, `get_chatwoot_client`, `get_settings` imports stay as-is — only `_candidate_slugs` and `maybe_categorize` change. `classify_category`'s existing "accept only if in candidates" fail-open behavior is reused unmodified for the subcategory classification too, since `subcategory_candidates` is just another candidate list.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_categorize.py -v`
Expected: PASS, including any pre-existing tests in this file (update any that asserted the old `add_labels`/`lifecycle_category_labels` behavior).

- [ ] **Step 5: Commit**

```bash
cd agent
git add app/services/categorize.py tests/test_categorize.py
git commit -m "feat: agent/categorize.py becomes a taxonomy-aware fallback-only classifier"
```

---

### Task 7: Provisioning script for the Chatwoot custom attribute definitions

**Files:**
- Create: `chatwoot-config/provision_case_taxonomy.py`

**Interfaces:**
- Consumes: `CASE_TAXONOMY_JSON` env value (read directly by this script — standalone, not importing from `backend/` or `agent/` packages, matching `provision_features.py`'s existing standalone-script convention); Chatwoot's REST API (`POST/PUT /api/v1/accounts/{account_id}/custom_attribute_definitions`).
- Produces: two Chatwoot custom attribute definitions, `case_category` and `case_subcategory`, both `attribute_model: "conversation_attribute"`, `attribute_type: "list"`.

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Provision the case_category / case_subcategory Chatwoot custom attribute
definitions from CASE_TAXONOMY_JSON, so Chatwoot's native conversation
sidebar renders them as single-select dropdowns (List type) — the mechanism
that enforces "one main category" without any custom frontend code.

Unlike provision_features.py (account *features*, not in Chatwoot's public
REST API — requires `rails runner`), custom attribute definitions ARE public
REST API resources, so this script talks directly to the HTTP API via httpx.

Idempotent: looks up existing definitions by attribute_key first, PATCHes if
found (updating the option list), POSTs if not. Safe to re-run any time the
taxonomy changes — e.g. once Proton finalizes their scheme.

Usage:
    CASE_TAXONOMY_JSON='...' python3 provision_case_taxonomy.py \\
        --chatwoot-url https://crm.example.com --account-id 1 --api-token <token>
    python3 provision_case_taxonomy.py --dry-run ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def _load_taxonomy(raw: str) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("CASE_TAXONOMY_JSON must be a JSON object")
    return data


def _category_options(taxonomy: dict) -> list[str]:
    return [str(v["label"]) for v in taxonomy.values() if isinstance(v, dict) and "label" in v]


def _subcategory_options(taxonomy: dict) -> list[str]:
    options: list[str] = []
    for v in taxonomy.values():
        if not isinstance(v, dict) or "label" not in v:
            continue
        label = str(v["label"])
        for sub in v.get("subcategories", []) or []:
            options.append(f"{label}: {sub}")
    return options


def _find_existing(client: httpx.Client, base: str, key: str) -> dict | None:
    res = client.get(f"{base}/custom_attribute_definitions")
    res.raise_for_status()
    for defn in res.json():
        if defn.get("attribute_key") == key:
            return defn
    return None


def _upsert(client: httpx.Client, base: str, key: str, name: str, options: list[str], dry_run: bool) -> None:
    payload = {
        "attribute_display_name": name,
        "attribute_display_type": "list",
        "attribute_key": key,
        "attribute_model": "conversation_attribute",
        "attribute_values": options,
    }
    existing = _find_existing(client, base, key)
    if dry_run:
        action = "UPDATE" if existing else "CREATE"
        print(f"[dry-run] {action} {key}: {len(options)} options")
        return
    if existing:
        res = client.patch(f"{base}/custom_attribute_definitions/{existing['id']}", json=payload)
    else:
        res = client.post(f"{base}/custom_attribute_definitions", json=payload)
    res.raise_for_status()
    print(f"{'Updated' if existing else 'Created'} {key} ({len(options)} options)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chatwoot-url", required=True)
    parser.add_argument("--account-id", required=True, type=int)
    parser.add_argument("--api-token", default=os.environ.get("CHATWOOT_API_TOKEN"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.api_token:
        print("error: --api-token or CHATWOOT_API_TOKEN required", file=sys.stderr)
        return 1

    raw = os.environ.get("CASE_TAXONOMY_JSON", "").strip()
    if not raw:
        print("error: CASE_TAXONOMY_JSON is not set", file=sys.stderr)
        return 1
    taxonomy = _load_taxonomy(raw)

    base = f"{args.chatwoot_url.rstrip('/')}/api/v1/accounts/{args.account_id}"
    headers = {"api_access_token": args.api_token, "Api-Access-Token": args.api_token}
    with httpx.Client(headers=headers, timeout=15.0) as client:
        _upsert(client, base, "case_category", "Case Category", _category_options(taxonomy), args.dry_run)
        _upsert(client, base, "case_subcategory", "Case Subcategory", _subcategory_options(taxonomy), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write a unit test for the pure functions (no live Chatwoot needed)**

```python
# chatwoot-config/test_provision_case_taxonomy.py
from provision_case_taxonomy import _category_options, _load_taxonomy, _subcategory_options

TAXONOMY_JSON = '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking", "Pricing Inquiry"]}}'


def test_load_taxonomy_parses_dict():
    assert _load_taxonomy(TAXONOMY_JSON) == {
        "sales": {"label": "Sales", "subcategories": ["Test Drive Booking", "Pricing Inquiry"]}
    }


def test_load_taxonomy_rejects_non_dict():
    import pytest

    with pytest.raises(ValueError):
        _load_taxonomy("[1, 2, 3]")


def test_category_options():
    assert _category_options(_load_taxonomy(TAXONOMY_JSON)) == ["Sales"]


def test_subcategory_options_are_flattened_with_label_prefix():
    assert _subcategory_options(_load_taxonomy(TAXONOMY_JSON)) == [
        "Sales: Test Drive Booking",
        "Sales: Pricing Inquiry",
    ]
```

- [ ] **Step 3: Run the test**

Run: `cd chatwoot-config && python3 -m pytest test_provision_case_taxonomy.py -v`
Expected: PASS (4 tests). Check `chatwoot-config/` for an existing `requirements.txt`/`pyproject.toml` and add `httpx`/`pytest` there if not already present (check first — `provision_features.py` likely already needs some HTTP or subprocess deps; reuse whatever dependency file already exists in that directory rather than creating a new one).

- [ ] **Step 4: Manual dry-run smoke test against local dev Chatwoot**

Run:
```bash
CASE_TAXONOMY_JSON="$(grep -A100 'case_taxonomy_json' backend/apps/backend/src/chatbot/platform/config.py | head -1)" \
  python3 chatwoot-config/provision_case_taxonomy.py \
  --chatwoot-url http://crm.localhost --account-id 1 --api-token <local admin token> --dry-run
```
Expected output: `[dry-run] CREATE case_category: 7 options` and `[dry-run] CREATE case_subcategory: 23 options` (against the default taxonomy). This step can't be scripted into the automated suite (needs a live Chatwoot + admin token) — record the actual output in the task's commit message or a comment for the reviewer.

- [ ] **Step 5: Commit**

```bash
git add chatwoot-config/provision_case_taxonomy.py chatwoot-config/test_provision_case_taxonomy.py
git commit -m "feat(chatwoot-config): provision case_category/case_subcategory custom attribute definitions"
```

---

### Task 8: Document `CASE_TAXONOMY_JSON` in tenant env templates

**Files:**
- Modify: `deploy/tenants/example.env`
- Modify: `backend/apps/backend/.env.example`
- Modify: `agent/.env.example` (check exact filename — may be `deploy/agent.env.example` or similar; grep for where `PIC_MAP_JSON`/`EMAIL_AUTOACK_ENABLED` are documented today and add next to them, per CLAUDE.md's convention: "anything new must be added to both app/config.py and deploy/tenants/example.env")

**Interfaces:** None — documentation only, no code.

- [ ] **Step 1: Add to `deploy/tenants/example.env`**

Find the section documenting `PIC_MAP_JSON` (grep `PIC_MAP_JSON` in this file) and add immediately after:

```bash
# Case category/subcategory taxonomy — JSON object keyed by main-category slug:
# {"slug": {"label": "Display Name", "subcategories": ["Sub A", "Sub B"]}}.
# Set the SAME value in both the backend and agent tenant env sections below —
# each service parses it independently (no shared config store between them).
# Empty -> backend/'s classify_ticket_tool falls back to free text, agent/'s
# resolution-time fallback classifier no-ops. Ships with a working default in
# code (see backend config.py) if left unset here.
CASE_TAXONOMY_JSON=
```

- [ ] **Step 2: Add the same field documentation to `backend/apps/backend/.env.example`**

Find where `PIC_MAP_JSON` is documented (mirroring `config.py`'s comment) and add `CASE_TAXONOMY_JSON=` with an equivalent one-line comment.

- [ ] **Step 3: Add to agent/'s env example file**

Find where `EMAIL_AUTOACK_ENABLED`/`LIFECYCLE_*` vars are documented and add `CASE_TAXONOMY_JSON=` there too, noting it supersedes `LIFECYCLE_CATEGORY_LABELS` for the categorization use case (that var stays defined but unused by the new code path — see Task 6).

- [ ] **Step 4: Commit**

```bash
git add deploy/tenants/example.env backend/apps/backend/.env.example
# plus whatever the correct agent env-example path turned out to be
git commit -m "docs: document CASE_TAXONOMY_JSON in tenant env templates"
```

---

## Plan Self-Review Notes

- **Spec coverage:** all 6 numbered items in the spec's "Design" section map 1:1 to Task 1-2 (taxonomy loaders), Task 3 (classify tool), Task 4 (write path), Task 7 (provisioning), Task 6 (agent fallback). Task 5 was added beyond the spec's explicit "Design" list — the case-categories research fork discovered the spec's Design section missed `features/metrics/mapping.py` as a third label-reading site (used by the BigQuery/reports pipeline); without Task 5 the reports column and metrics export would silently stop populating category/subcategory the moment Task 4 lands. This is a plan-level addition to close a gap the spec didn't anticipate, not a deviation from its intent.
- **No backfill task** — intentional, matches the spec's explicit non-goal.
- **No cascading-subcategory-UI task** — intentional, matches the spec's explicit non-goal (flattened list, no custom frontend).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-01-case-categories-subcategories.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
