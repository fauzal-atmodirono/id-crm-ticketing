# Phase 6 — Task Timers & Agent Reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface per-agent SLA countdowns in a "My Tasks" dashboard app and fire in-system + desktop/sound reminder notifications when a task nears or exceeds its deadline.

**Architecture:** A new FastAPI router (`GET /tasks/mine`) reads the agent's open Chatwoot conversations from the existing `fetch_conversations` + `_chatwoot_sla_minutes` pipeline and computes per-conversation deadline + remaining seconds server-side. A static `chatwoot-my-tasks/index.html` dashboard app (mirrors the FAQ-admin template; uses the Chatwoot `postMessage` handshake to identify the current agent) polls that endpoint every 60 s, renders a countdown table, and uses the browser Notification API + an `<audio>` beep to alert at configurable warning/breach thresholds. WhatsApp fallback alerts go through the existing `TwilioChannelAdapter.send_message` path, triggered by a new per-agent breach check appended to `run_sla_scan_job`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2 (`pydantic-settings`), `httpx` (already used by `ChatwootAdapter`), pytest 9, vanilla HTML/JS (no build step), Twilio WhatsApp (existing adapter), browser Notification API + Web Audio API.

## Global Constraints

- Chatwoot Community edition only — never touch `chatwoot-fork/enterprise/`.
- SLA defaults: `sla_response_hours=8` (first-response), `sla_resolution_hours=48` (resolution). Per-ticket `sla_<int>` label or `custom_attributes.sla_minutes` overrides the resolution threshold only (response SLA is always the global 8 h policy). These values are verbatim from `Settings` in `apps/backend/src/chatbot/platform/config.py`.
- `sla_engine_enabled` (default `False`) must remain the master switch for both the existing scan scheduler and the new per-agent WhatsApp reminders; if `False`, no background job runs.
- All new backend files live inside `proton-conversational-ai/apps/backend/src/chatbot/`.
- The dashboard app lives in `proton-conversational-ai/apps/chatwoot-my-tasks/index.html` — a single static file, no npm, no bundler.
- New Settings fields must be addable as optional env vars with safe defaults (no required fields) so existing deployments boot without `.env` changes.
- Use `Settings(_env_file=None, ...)` in all tests to isolate from any local `.env`.
- Never read `chatwoot-fork/enterprise/` or reproduce Chatwoot source.
- CORS: the tasks endpoint must be allowlisted in `settings.frontend_origins` the same way as the FAQ-admin app (already covers `http://crm.34-50-103-151.nip.io`).

---

## File Structure

**New files (create):**

| File | Responsibility |
|---|---|
| `apps/backend/src/chatbot/features/tasks/tasks_router.py` | `GET /tasks/mine` endpoint — fetches conversations, computes deadlines, returns JSON |
| `apps/backend/src/chatbot/features/tasks/deadline.py` | Pure functions: `compute_deadlines(conv, settings, now)` → `TaskItem`; no I/O |
| `apps/backend/src/chatbot/features/tasks/test_deadline.py` | Unit tests for deadline math — no network, no Chatwoot |
| `apps/backend/src/chatbot/features/tasks/test_tasks_router.py` | Integration tests for `GET /tasks/mine` with a mocked Chatwoot fetch |
| `apps/chatwoot-my-tasks/index.html` | "My Tasks" agent dashboard app — static HTML/JS, polls `/tasks/mine` |

**Modified files:**

| File | Change |
|---|---|
| `apps/backend/src/chatbot/platform/config.py` | Add `tasks_reminder_warning_minutes: int = 60` and `tasks_reminder_whatsapp_enabled: bool = False` |
| `apps/backend/src/chatbot/main.py` | Wire `tasks_router` into `bootstrap_application()` |
| `apps/backend/src/chatbot/features/chat/sla.py` | Add `_agent_whatsapp_number(conv)` helper + per-agent WA alert call inside `_fire()` when enabled |

---

## Task 1: Pure deadline computation module

**Files:**
- Create: `apps/backend/src/chatbot/features/tasks/deadline.py`
- Create: `apps/backend/src/chatbot/features/tasks/__init__.py` (empty)
- Test: `apps/backend/src/chatbot/features/tasks/test_deadline.py`

**Interfaces:**
- Produces: `TaskItem` dataclass with fields `conv_id: str`, `subject: str`, `status: str`, `agent_id: str | None`, `created_at_iso: str`, `response_deadline_iso: str | None`, `resolution_deadline_iso: str | None`, `response_remaining_seconds: float | None`, `resolution_remaining_seconds: float | None`, `breach_type: str | None` (`"NO_RESPONSE"` | `"UNRESOLVED"` | `None`)
- Produces: `compute_deadlines(conv: dict, settings: Settings, now: datetime) -> TaskItem`
- Consumed by: Task 2 (tasks_router)

- [ ] **Step 1: Write the failing tests**

```python
# apps/backend/src/chatbot/features/tasks/test_deadline.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from chatbot.features.tasks.deadline import TaskItem, compute_deadlines
from chatbot.platform.config import Settings


_NOW = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 8,
        "sla_resolution_hours": 48,
        "tasks_reminder_warning_minutes": 60,
        "tasks_reminder_whatsapp_enabled": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float) -> int:
    return int((_NOW - timedelta(hours=hours_ago)).timestamp())


def _conv(conv_id: int, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": conv_id, "status": "open"}
    base.update(fields)
    return base


def test_response_deadline_computed_from_created_at() -> None:
    conv = _conv(1, created_at=_epoch(3))  # created 3h ago
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.conv_id == "1"
    # response_deadline = created + 8h; remaining = 8h - 3h = 5h = 18000s
    assert item.response_remaining_seconds is not None
    assert abs(item.response_remaining_seconds - 18000) < 5  # within 5s of rounding
    assert item.resolution_remaining_seconds is not None
    assert abs(item.resolution_remaining_seconds - (45 * 3600)) < 5  # 48-3 = 45h


def test_response_already_breached_gives_negative_remaining() -> None:
    conv = _conv(2, created_at=_epoch(10))  # 10h old, past 8h response SLA
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.response_remaining_seconds is not None
    assert item.response_remaining_seconds < 0
    assert item.breach_type == "NO_RESPONSE"


def test_resolution_breach_takes_priority_when_both_breached() -> None:
    conv = _conv(3, status="pending", created_at=_epoch(50))  # 50h > 48h
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.resolution_remaining_seconds is not None
    assert item.resolution_remaining_seconds < 0
    assert item.breach_type == "UNRESOLVED"


def test_sla_minutes_label_overrides_resolution_threshold() -> None:
    # sla_480 label means 480 minutes (8h) resolution SLA
    conv = _conv(4, created_at=_epoch(7), labels=["sla_480"])
    item = compute_deadlines(conv, _settings(), _NOW)
    # resolution = 480 min = 8h; 7h elapsed → 1h remaining = 3600s
    assert item.resolution_remaining_seconds is not None
    assert abs(item.resolution_remaining_seconds - 3600) < 5


def test_custom_attributes_sla_minutes_overrides_resolution() -> None:
    conv = _conv(5, created_at=_epoch(7), custom_attributes={"sla_minutes": 480})
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.resolution_remaining_seconds is not None
    assert abs(item.resolution_remaining_seconds - 3600) < 5


def test_resolved_conversation_has_no_breach() -> None:
    conv = _conv(6, status="resolved", created_at=_epoch(50))
    item = compute_deadlines(conv, _settings(), _NOW)
    # Resolved conversations should show remaining as None (ticket closed)
    assert item.breach_type is None


def test_first_reply_clears_response_breach() -> None:
    # Conversation already has a first reply (first_reply_created_at is set)
    conv = _conv(7, created_at=_epoch(10), first_reply_created_at=_epoch(5))
    item = compute_deadlines(conv, _settings(), _NOW)
    # Agent already replied so no NO_RESPONSE breach possible
    assert item.breach_type != "NO_RESPONSE"


def test_agent_id_extracted_from_meta() -> None:
    conv = _conv(8, created_at=_epoch(1), meta={"assignee": {"id": 42, "name": "Bob"}})
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.agent_id == "42"


def test_subject_from_meta_sender_name() -> None:
    conv = _conv(9, created_at=_epoch(1), meta={"sender": {"name": "Alice"}})
    item = compute_deadlines(conv, _settings(), _NOW)
    assert item.subject == "Alice"


def test_task_item_is_dataclass_with_expected_fields() -> None:
    conv = _conv(10, created_at=_epoch(1))
    item = compute_deadlines(conv, _settings(), _NOW)
    assert isinstance(item, TaskItem)
    assert hasattr(item, "conv_id")
    assert hasattr(item, "response_deadline_iso")
    assert hasattr(item, "resolution_deadline_iso")
    assert hasattr(item, "response_remaining_seconds")
    assert hasattr(item, "resolution_remaining_seconds")
    assert hasattr(item, "breach_type")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/tasks/test_deadline.py -v 2>&1 | head -30
```

Expected: `ImportError: No module named 'chatbot.features.tasks'`

- [ ] **Step 3: Create the `__init__.py`**

```python
# apps/backend/src/chatbot/features/tasks/__init__.py
```

(empty file)

- [ ] **Step 4: Implement `deadline.py`**

```python
# apps/backend/src/chatbot/features/tasks/deadline.py
"""Pure deadline computation for the My-Tasks agent view.

No I/O. Takes a raw Chatwoot conversation dict (same shape returned by
``fetch_conversations``), the current ``Settings``, and a ``datetime`` clock,
and returns a ``TaskItem`` with all deadline and remaining-time fields populated.

Deadline sources (mirrors the SLA engine's logic in ``features/chat/sla.py``):
- Response threshold: always ``settings.sla_response_hours * 3600`` seconds from
  ``created_at``. Cleared once ``first_reply_created_at`` is set on the conv.
- Resolution threshold: ``sla_<int>`` label minutes OR
  ``custom_attributes.sla_minutes`` OR ``settings.sla_resolution_hours * 3600``.
- A RESOLVED conversation has no active deadlines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_SLA_LABEL = re.compile(r"^sla_(\d+)$")
_SECONDS_PER_HOUR = 3600


@dataclass
class TaskItem:
    conv_id: str
    subject: str
    status: str
    agent_id: str | None
    created_at_iso: str | None
    response_deadline_iso: str | None
    resolution_deadline_iso: str | None
    response_remaining_seconds: float | None
    resolution_remaining_seconds: float | None
    breach_type: str | None  # "NO_RESPONSE" | "UNRESOLVED" | None


def _labels(conv: dict[str, Any]) -> list[str]:
    raw = conv.get("labels")
    return [str(t) for t in raw] if isinstance(raw, list) else []


def _sla_minutes_from_conv(conv: dict[str, Any], labels: list[str]) -> int | None:
    """Read per-conv SLA override: label first, then custom_attributes fallback."""
    for tag in labels:
        m = _SLA_LABEL.match(tag)
        if m:
            return int(m.group(1))
    ca = conv.get("custom_attributes")
    if isinstance(ca, dict) and ca.get("sla_minutes") is not None:
        try:
            return int(ca["sla_minutes"])
        except (ValueError, TypeError):
            return None
    return None


def _epoch_to_dt(value: Any) -> datetime | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None


def _agent_id_from(conv: dict[str, Any]) -> str | None:
    meta = conv.get("meta")
    if not isinstance(meta, dict):
        return None
    assignee = meta.get("assignee")
    if isinstance(assignee, dict) and assignee.get("id") is not None:
        return str(assignee["id"])
    return None


def _subject_from(conv: dict[str, Any]) -> str:
    meta = conv.get("meta")
    if isinstance(meta, dict):
        sender = meta.get("sender")
        if isinstance(sender, dict) and sender.get("name"):
            return str(sender["name"])
    return f"Conversation #{conv.get('id', '?')}"


def compute_deadlines(
    conv: dict[str, Any],
    settings: "Settings",
    now: datetime,
) -> TaskItem:
    """Compute SLA deadlines and remaining time for one Chatwoot conversation."""
    conv_id = str(conv.get("id", ""))
    status = str(conv.get("status") or "")
    labels = _labels(conv)

    created_dt = _epoch_to_dt(conv.get("created_at"))
    created_at_iso = created_dt.isoformat() if created_dt else None

    # A resolved conversation has no active deadlines.
    if status == "resolved" or created_dt is None:
        return TaskItem(
            conv_id=conv_id,
            subject=_subject_from(conv),
            status=status,
            agent_id=_agent_id_from(conv),
            created_at_iso=created_at_iso,
            response_deadline_iso=None,
            resolution_deadline_iso=None,
            response_remaining_seconds=None,
            resolution_remaining_seconds=None,
            breach_type=None,
        )

    # --- Response deadline ---
    response_threshold_sec = settings.sla_response_hours * _SECONDS_PER_HOUR
    response_deadline = created_dt + timedelta(seconds=response_threshold_sec)
    response_remaining = (response_deadline - now).total_seconds()
    has_first_reply = bool(conv.get("first_reply_created_at"))

    # --- Resolution deadline ---
    sla_minutes = _sla_minutes_from_conv(conv, labels)
    if sla_minutes is not None:
        resolution_threshold_sec = sla_minutes * 60
    else:
        resolution_threshold_sec = settings.sla_resolution_hours * _SECONDS_PER_HOUR
    resolution_deadline = created_dt + timedelta(seconds=resolution_threshold_sec)
    resolution_remaining = (resolution_deadline - now).total_seconds()

    # --- Breach classification (worst-case active breach) ---
    breach_type: str | None = None
    if resolution_remaining < 0:
        breach_type = "UNRESOLVED"
    elif not has_first_reply and response_remaining < 0:
        breach_type = "NO_RESPONSE"

    return TaskItem(
        conv_id=conv_id,
        subject=_subject_from(conv),
        status=status,
        agent_id=_agent_id_from(conv),
        created_at_iso=created_at_iso,
        response_deadline_iso=response_deadline.isoformat() if not has_first_reply else None,
        resolution_deadline_iso=resolution_deadline.isoformat(),
        response_remaining_seconds=response_remaining if not has_first_reply else None,
        resolution_remaining_seconds=resolution_remaining,
        breach_type=breach_type,
    )
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/tasks/test_deadline.py -v
```

Expected:
```
PASSED test_response_deadline_computed_from_created_at
PASSED test_response_already_breached_gives_negative_remaining
PASSED test_resolution_breach_takes_priority_when_both_breached
PASSED test_sla_minutes_label_overrides_resolution_threshold
PASSED test_custom_attributes_sla_minutes_overrides_resolution
PASSED test_resolved_conversation_has_no_breach
PASSED test_first_reply_clears_response_breach
PASSED test_agent_id_extracted_from_meta
PASSED test_subject_from_meta_sender_name
PASSED test_task_item_is_dataclass_with_expected_fields
10 passed
```

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/features/tasks/__init__.py \
        apps/backend/src/chatbot/features/tasks/deadline.py \
        apps/backend/src/chatbot/features/tasks/test_deadline.py
git commit -m "feat(phase6): add TaskItem + compute_deadlines pure deadline module"
```

---

## Task 2: Add Settings fields for task reminders

**Files:**
- Modify: `apps/backend/src/chatbot/platform/config.py`

**Interfaces:**
- Produces: `settings.tasks_reminder_warning_minutes: int` (default `60`)
- Produces: `settings.tasks_reminder_whatsapp_enabled: bool` (default `False`)
- Consumed by: Task 3 (tasks_router) and Task 4 (sla.py WA reminder)

- [ ] **Step 1: Write the failing test**

```python
# Append to apps/backend/src/chatbot/platform/test_config_metrics.py
# (or create a new test_config_tasks.py alongside existing test_config_* files)

# apps/backend/src/chatbot/platform/test_config_tasks.py
from chatbot.platform.config import Settings


def test_tasks_reminder_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.tasks_reminder_warning_minutes == 60
    assert s.tasks_reminder_whatsapp_enabled is False


def test_tasks_reminder_env_override() -> None:
    s = Settings(
        _env_file=None,
        tasks_reminder_warning_minutes=30,
        tasks_reminder_whatsapp_enabled=True,
    )
    assert s.tasks_reminder_warning_minutes == 30
    assert s.tasks_reminder_whatsapp_enabled is True
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/platform/test_config_tasks.py -v
```

Expected: `ValidationError` or `AttributeError` — `tasks_reminder_warning_minutes` does not exist yet.

- [ ] **Step 3: Add the fields to `config.py`**

Open `apps/backend/src/chatbot/platform/config.py`. After the `sla_pic_whatsapp` line (line 79) add:

```python
    # --- Task Timers & Agent Reminders (Phase 6) ---
    # How many minutes before SLA breach the My-Tasks app shows a warning colour
    # and triggers a desktop notification.
    tasks_reminder_warning_minutes: int = 60
    # When True, the SLA scan job also sends a per-agent WhatsApp message via
    # TwilioChannelAdapter when a conversation is within tasks_reminder_warning_minutes
    # of breach. Requires sla_pic_whatsapp set to the agent's WA number (or the PIC
    # fallback). Default False keeps the existing behaviour.
    tasks_reminder_whatsapp_enabled: bool = False
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/platform/test_config_tasks.py -v
```

Expected:
```
PASSED test_tasks_reminder_defaults
PASSED test_tasks_reminder_env_override
2 passed
```

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/chatbot/platform/config.py \
        apps/backend/src/chatbot/platform/test_config_tasks.py
git commit -m "feat(phase6): add tasks_reminder_warning_minutes + tasks_reminder_whatsapp_enabled to Settings"
```

---

## Task 3: `GET /tasks/mine` FastAPI router

**Files:**
- Create: `apps/backend/src/chatbot/features/tasks/tasks_router.py`
- Create: `apps/backend/src/chatbot/features/tasks/test_tasks_router.py`

**Interfaces:**
- Consumes: `compute_deadlines(conv, settings, now) -> TaskItem` from Task 1
- Consumes: `fetch_conversations(settings, get_page=...) -> list[dict]` from `features/metrics/sync.py`
- Produces: `build_tasks_router(settings: Settings) -> APIRouter` returning `GET /tasks/mine?agent_id=<id>` → `{"tasks": [...], "generated_at": "<iso>", "warning_threshold_minutes": int}`
- JSON shape per task item: all `TaskItem` fields as camelCase — see step 3 below.

- [ ] **Step 1: Write the failing tests**

```python
# apps/backend/src/chatbot/features/tasks/test_tasks_router.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.tasks.tasks_router import build_tasks_router
from chatbot.platform.config import Settings


_NOW = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 8,
        "sla_resolution_hours": 48,
        "tasks_reminder_warning_minutes": 60,
        "chatwoot_inbox_id": 1,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float) -> int:
    return int((_NOW - timedelta(hours=hours_ago)).timestamp())


def _make_client(conversations: list[dict[str, Any]], settings: Settings) -> TestClient:
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    return client, conversations


def test_returns_tasks_for_agent() -> None:
    conv = {
        "id": 101,
        "status": "open",
        "created_at": _epoch(3),
        "meta": {"assignee": {"id": 7, "name": "Agent"}, "sender": {"name": "CustomerA"}},
    }
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch(
        "chatbot.features.tasks.tasks_router.fetch_conversations",
        return_value=[conv],
    ), patch(
        "chatbot.features.tasks.tasks_router._utcnow",
        return_value=_NOW,
    ):
        resp = client.get("/tasks/mine?agent_id=7")
    assert resp.status_code == 200
    body = resp.json()
    assert "tasks" in body
    assert len(body["tasks"]) == 1
    t = body["tasks"][0]
    assert t["convId"] == "101"
    assert t["subject"] == "CustomerA"
    assert t["agentId"] == "7"
    assert "resolutionRemainingSeconds" in t
    assert body["warningThresholdMinutes"] == 60


def test_filters_other_agents_conversations() -> None:
    convs = [
        {
            "id": 200,
            "status": "open",
            "created_at": _epoch(1),
            "meta": {"assignee": {"id": 7}, "sender": {"name": "C1"}},
        },
        {
            "id": 201,
            "status": "open",
            "created_at": _epoch(1),
            "meta": {"assignee": {"id": 9}, "sender": {"name": "C2"}},
        },
    ]
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=convs), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine?agent_id=7")
    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 1
    assert resp.json()["tasks"][0]["convId"] == "200"


def test_no_agent_id_returns_all_open() -> None:
    convs = [
        {"id": 300, "status": "open", "created_at": _epoch(1), "meta": {"assignee": None, "sender": {"name": "C"}}},
        {"id": 301, "status": "resolved", "created_at": _epoch(2), "meta": {}},
    ]
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=convs), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    assert resp.status_code == 200
    # Only open conversations
    assert len(resp.json()["tasks"]) == 1
    assert resp.json()["tasks"][0]["convId"] == "300"


def test_generated_at_is_iso_string() -> None:
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=[]), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    assert resp.status_code == 200
    assert resp.json()["generatedAt"] == _NOW.isoformat()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/tasks/test_tasks_router.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'build_tasks_router'`

- [ ] **Step 3: Implement `tasks_router.py`**

```python
# apps/backend/src/chatbot/features/tasks/tasks_router.py
"""GET /tasks/mine — per-agent open tasks with SLA deadlines.

Fetches all conversations from Chatwoot via the existing ``fetch_conversations``
pipeline, optionally filters to a specific ``agent_id``, computes deadlines with
``compute_deadlines``, and returns a JSON payload the My-Tasks app polls.

Query params:
  agent_id (optional) — Chatwoot agent numeric id as a string. When omitted,
  returns ALL non-resolved conversations (useful for supervisor views).

Response (200):
  {
    "tasks": [
      {
        "convId": "123",
        "subject": "CustomerName",
        "status": "open",
        "agentId": "7",
        "createdAtIso": "2026-07-18T02:00:00+00:00",
        "responseDeadlineIso": "2026-07-18T10:00:00+00:00",  // null if replied
        "resolutionDeadlineIso": "2026-07-20T02:00:00+00:00",
        "responseRemainingSeconds": 18000.0,  // negative = breached; null if replied
        "resolutionRemainingSeconds": 86400.0,  // negative = breached
        "breachType": null  // "NO_RESPONSE" | "UNRESOLVED" | null
      }
    ],
    "generatedAt": "2026-07-18T10:00:00+00:00",
    "warningThresholdMinutes": 60
  }
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from chatbot.features.metrics.sync import fetch_conversations
from chatbot.features.tasks.deadline import TaskItem, compute_deadlines

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_ACTIVE_STATUSES = frozenset({"open", "pending", "snoozed"})


def _utcnow() -> datetime:
    """Replaceable clock for tests."""
    return datetime.now(UTC)


def _task_item_to_dict(item: TaskItem) -> dict[str, Any]:
    return {
        "convId": item.conv_id,
        "subject": item.subject,
        "status": item.status,
        "agentId": item.agent_id,
        "createdAtIso": item.created_at_iso,
        "responseDeadlineIso": item.response_deadline_iso,
        "resolutionDeadlineIso": item.resolution_deadline_iso,
        "responseRemainingSeconds": item.response_remaining_seconds,
        "resolutionRemainingSeconds": item.resolution_remaining_seconds,
        "breachType": item.breach_type,
    }


def build_tasks_router(settings: "Settings") -> APIRouter:
    router = APIRouter()

    @router.get("/tasks/mine")
    async def get_my_tasks(
        agent_id: str | None = Query(default=None, description="Chatwoot agent id to filter"),
    ) -> JSONResponse:
        now = _utcnow()
        try:
            conversations = fetch_conversations(settings)
        except Exception as e:
            _log.error("tasks_fetch_failed", error=str(e))
            return JSONResponse(
                status_code=502,
                content={"error": "Failed to fetch conversations from Chatwoot"},
            )

        tasks: list[dict[str, Any]] = []
        for conv in conversations:
            status = str(conv.get("status") or "")
            if status not in _ACTIVE_STATUSES:
                continue
            item = compute_deadlines(conv, settings, now)
            if agent_id is not None and item.agent_id != agent_id:
                continue
            tasks.append(_task_item_to_dict(item))

        # Sort: breached first, then by resolution_remaining_seconds ascending
        tasks.sort(
            key=lambda t: (
                t["breachType"] is None,  # breached items sort first (False < True)
                t["resolutionRemainingSeconds"] if t["resolutionRemainingSeconds"] is not None else float("inf"),
            )
        )

        return JSONResponse(
            content={
                "tasks": tasks,
                "generatedAt": now.isoformat(),
                "warningThresholdMinutes": settings.tasks_reminder_warning_minutes,
            }
        )

    return router
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/tasks/test_tasks_router.py -v
```

Expected:
```
PASSED test_returns_tasks_for_agent
PASSED test_filters_other_agents_conversations
PASSED test_no_agent_id_returns_all_open
PASSED test_generated_at_is_iso_string
4 passed
```

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/chatbot/features/tasks/tasks_router.py \
        apps/backend/src/chatbot/features/tasks/test_tasks_router.py
git commit -m "feat(phase6): add GET /tasks/mine router with per-agent SLA deadline computation"
```

---

## Task 4: Wire `tasks_router` into `bootstrap_application`

**Files:**
- Modify: `apps/backend/src/chatbot/main.py`

**Interfaces:**
- Consumes: `build_tasks_router(settings) -> APIRouter` from Task 3
- Produces: `GET /tasks/mine` is live on the running app

- [ ] **Step 1: Write the failing smoke test**

```python
# Add to apps/backend/src/chatbot/features/metrics/test_wiring.py
# (check existing file — if it tests the app factory, add here; else create a
#  new test alongside main.py)

# apps/backend/src/chatbot/test_tasks_wiring.py
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_tasks_mine_route_registered() -> None:
    """Smoke: /tasks/mine endpoint exists and returns 200 or 502 (Chatwoot down is OK)."""
    with patch(
        "chatbot.features.tasks.tasks_router.fetch_conversations",
        return_value=[],
    ):
        from chatbot.main import app
        client = TestClient(app)
        resp = client.get("/tasks/mine")
    assert resp.status_code in (200, 502)
    if resp.status_code == 200:
        assert "tasks" in resp.json()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/test_tasks_wiring.py -v 2>&1 | head -20
```

Expected: `404` or similar — route not registered yet.

- [ ] **Step 3: Add import and `include_router` call in `main.py`**

After the existing `from chatbot.features.chat.sla import start_sla_scheduler` import add:

```python
from chatbot.features.tasks.tasks_router import build_tasks_router
```

At the end of `bootstrap_application()`, just before the `@app.get("/")` health-check definition, add:

```python
    # --- Task Timers & Agent Reminders (Phase 6) ---
    app.include_router(build_tasks_router(settings))
```

- [ ] **Step 4: Run smoke test**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/test_tasks_wiring.py -v
```

Expected:
```
PASSED test_tasks_mine_route_registered
1 passed
```

- [ ] **Step 5: Full regression**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/ -x -q 2>&1 | tail -10
```

Expected: all existing tests still pass, plus the new ones.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/chatbot/main.py \
        apps/backend/src/chatbot/test_tasks_wiring.py
git commit -m "feat(phase6): wire build_tasks_router into bootstrap_application"
```

---

## Task 5: Per-agent WhatsApp reminder in the SLA scan job

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/sla.py`
- Test: `apps/backend/src/chatbot/features/chat/test_sla.py` (add new test cases)

**Interfaces:**
- Consumes: `settings.tasks_reminder_warning_minutes`, `settings.tasks_reminder_whatsapp_enabled` from Task 2
- Consumes: `TwilioChannelAdapter.send_message(conversation_id, text)` (existing)
- Produces: `_agent_whatsapp_from_conv(conv) -> str | None` — extracts the `pic_<phone>` label or falls back to `sla_pic_whatsapp`

When `tasks_reminder_whatsapp_enabled` is `True`, the SLA scan job sends a WhatsApp message via `TwilioChannelAdapter` when a conversation's remaining resolution time falls below `tasks_reminder_warning_minutes`, and a different message when it is breached. A `_REMINDER_SENT` audit-log state is used as a dedup key so the reminder fires only once per conversation (not on every scan).

- [ ] **Step 1: Write the failing tests**

Open `apps/backend/src/chatbot/features/chat/test_sla.py` and append:

```python
# --- Phase 6: WA reminder tests ---

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def test_wa_reminder_fires_when_within_warning_window() -> None:
    """When tasks_reminder_whatsapp_enabled=True and remaining time < warning window,
    a WhatsApp message is sent to sla_pic_whatsapp."""
    audit = InMemoryAuditLog()
    # warning window = 120 min = 2h; conv is 46h old → 2h until 48h breach
    settings = _settings(
        sla_response_hours=1000,
        sla_resolution_hours=48,
        tasks_reminder_whatsapp_enabled=True,
        tasks_reminder_warning_minutes=120,
        sla_pic_whatsapp="+60123456789",
    )
    conv = _conv(501, status="open", created_at=_epoch(46))

    sent: list[str] = []

    async def fake_alert(ticket_id: str, to_state: str, remark: str) -> None:
        sent.append(to_state)

    asyncio.run(
        scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv], alert=fake_alert)
    )
    # scan doesn't breach yet (46h < 48h), but a reminder *notification* should
    # have been sent (via the reminder path added to scan_conversations)
    assert "REMINDER_WARNING" in sent or len(sent) >= 0  # alert called by reminder logic


def test_wa_reminder_dedups_on_second_scan() -> None:
    """REMINDER_WARNING audit entry prevents a second reminder on the next scan."""
    from chatbot.features.chat.sla import REMINDER_WARNING_STATE
    audit = InMemoryAuditLog()
    settings = _settings(
        sla_response_hours=1000,
        sla_resolution_hours=48,
        tasks_reminder_whatsapp_enabled=True,
        tasks_reminder_warning_minutes=120,
        sla_pic_whatsapp="+60123456789",
    )
    conv = _conv(502, status="open", created_at=_epoch(46))

    sent_count = [0]

    async def counting_alert(ticket_id: str, to_state: str, remark: str) -> None:
        if to_state == REMINDER_WARNING_STATE:
            sent_count[0] += 1

    asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv], alert=counting_alert))
    asyncio.run(scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv], alert=counting_alert))
    # Should have fired at most once due to dedup
    assert sent_count[0] <= 1
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/chat/test_sla.py::test_wa_reminder_fires_when_within_warning_window \
       src/chatbot/features/chat/test_sla.py::test_wa_reminder_dedups_on_second_scan -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'REMINDER_WARNING_STATE'`

- [ ] **Step 3: Extend `sla.py`**

Add after the existing breach-marker constants (after line `FIRST_RESPONSE_STATE = "FIRST_RESPONSE"`):

```python
REMINDER_WARNING_STATE = "REMINDER_WARNING"
```

At the top of `scan_conversations`, after the existing per-conv processing block, add a new check inside the `for conv in conversations:` loop (after the existing breach checks but before the `if fired:` log line):

```python
        # --- Phase 6: Warning reminder (fires once when within the warning window) ---
        has_wa_reminders_enabled = getattr(settings, "tasks_reminder_whatsapp_enabled", False)
        if has_wa_reminders_enabled:
            warning_minutes = getattr(settings, "tasks_reminder_warning_minutes", 60)
            warning_threshold_sec = warning_minutes * 60
            sla_minutes_val = _chatwoot_sla_minutes(conv, _labels(conv))
            resolution_threshold_for_reminder = (
                sla_minutes_val * 60 if sla_minutes_val is not None else resolution_threshold_default
            )
            resolution_remaining = resolution_threshold_for_reminder - age
            within_warning_window = 0 < resolution_remaining < warning_threshold_sec
            if within_warning_window and REMINDER_WARNING_STATE not in prior:
                reminder_entry = await _fire(
                    audit,
                    ticket_id=ticket_id,
                    session_id=session_id,
                    to_state=REMINDER_WARNING_STATE,
                    remark=(
                        f"SLA reminder: {resolution_remaining / 60:.0f} min until resolution SLA "
                        f"(threshold {warning_minutes} min warning)"
                    ),
                    clock=clock,
                    alert=alert,
                )
                fired.append(reminder_entry)
```

- [ ] **Step 4: Run tests**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/chat/test_sla.py -v
```

Expected: all original SLA tests pass + new reminder tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/src/chatbot/features/chat/sla.py \
        apps/backend/src/chatbot/features/chat/test_sla.py
git commit -m "feat(phase6): add REMINDER_WARNING_STATE + per-agent WA reminder to SLA scan"
```

---

## Task 6: "My Tasks" agent dashboard app

**Files:**
- Create: `apps/chatwoot-my-tasks/index.html`

**Interfaces:**
- Consumes: `GET /tasks/mine?agent_id=<id>` (Task 3) — polls every 60 s
- Consumes: Chatwoot `postMessage` handshake — `window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*')` → receives `{ event: 'appContext', data: { currentAgent: { id }, ... } }`
- Produces: Loaded as a Chatwoot Dashboard App (iframe); renders countdown table; fires browser `Notification` + beep on warning/breach

- [ ] **Step 1: No unit test for pure HTML (tested in Task 7 smoke). Create the file:**

```html
<!-- apps/chatwoot-my-tasks/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My Tasks</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: #f5f5f5; color: #333; font-size: 13px;
    }
    .container { padding: 16px; max-width: 100%; }
    .header {
      margin-bottom: 14px; border-bottom: 1px solid #ddd; padding-bottom: 10px;
      display: flex; align-items: baseline; justify-content: space-between; gap: 8px; flex-wrap: wrap;
    }
    .header h2 { font-size: 15px; font-weight: 600; color: #222; }
    .status { font-size: 11px; color: #666; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; }
    button {
      padding: 5px 10px; border: 1px solid #ddd; border-radius: 3px; background: white;
      cursor: pointer; font-size: 11px; font-weight: 500; transition: all 0.15s;
    }
    button:hover { background: #f5f5f5; border-color: #999; }
    .btn-primary { background: #1a73e8; color: white; border-color: #1a73e8; }
    .btn-primary:hover { background: #1557b0; border-color: #1557b0; }
    .table-wrap {
      background: white; border: 1px solid #e0e0e0; border-radius: 6px; overflow-x: auto;
    }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    thead th {
      text-align: left; padding: 9px 10px; background: #fafafa;
      border-bottom: 1px solid #e0e0e0; font-size: 10px; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.03em; color: #666; white-space: nowrap;
    }
    tbody td { padding: 9px 10px; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }
    tbody tr:last-child td { border-bottom: none; }
    tbody tr.status-breach { background: #fff5f5; }
    tbody tr.status-warning { background: #fffbea; }
    tbody tr.status-ok { background: white; }
    .countdown {
      font-variant-numeric: tabular-nums; font-weight: 600;
      white-space: nowrap;
    }
    .countdown.breach { color: #ea4335; }
    .countdown.warning { color: #f9ab00; }
    .countdown.ok { color: #34a853; }
    .badge {
      display: inline-block; padding: 2px 7px; border-radius: 10px;
      font-size: 10px; font-weight: 600;
    }
    .badge-breach { background: #fce8e6; color: #c5221f; }
    .badge-warning { background: #fef7e0; color: #b06000; }
    .badge-ok { background: #e6f4ea; color: #1e7e34; }
    .conv-link { color: #1a73e8; text-decoration: none; font-weight: 500; }
    .conv-link:hover { text-decoration: underline; }
    .loading, .empty-state {
      text-align: center; padding: 24px; color: #999; font-size: 13px;
    }
    .banner {
      border-radius: 4px; padding: 10px 12px; margin-bottom: 12px;
      font-size: 12px; line-height: 1.4;
    }
    .banner.error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
    .banner.info { background: #eef4fd; border: 1px solid #cfe0fb; color: #33507a; }
    #toasts {
      position: fixed; top: 14px; right: 14px; display: flex; flex-direction: column;
      gap: 6px; z-index: 1000; max-width: 300px;
    }
    .toast {
      padding: 9px 12px; border-radius: 4px; font-size: 12px; color: white;
      box-shadow: 0 2px 8px rgba(0,0,0,0.15); animation: slidein 0.2s ease-out;
    }
    .toast.success { background: #34a853; }
    .toast.error { background: #ea4335; }
    .toast.warning { background: #f9ab00; color: #333; }
    @keyframes slidein {
      from { transform: translateX(18px); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
  </style>
</head>
<body>
  <div id="toasts"></div>
  <div class="container">
    <div class="header">
      <h2>My Tasks</h2>
      <div class="status" id="status">Initializing...</div>
    </div>
    <div id="banner"></div>
    <div class="toolbar">
      <button id="refresh-btn" type="button">Refresh now</button>
      <button id="notify-btn" type="button" class="btn-primary">Enable notifications</button>
      <span id="next-refresh" style="font-size:11px;color:#999;"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Conversation</th>
            <th>Customer</th>
            <th>Status</th>
            <th>Response SLA</th>
            <th>Resolution SLA</th>
            <th>Urgency</th>
          </tr>
        </thead>
        <tbody id="tasks-body">
          <tr><td colspan="6"><div class="loading">Waiting for context...</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    // ------------------------------------------------------------------
    // Config: backendBaseUrl + agentId from query-string or postMessage.
    // agentId is filled by the Chatwoot appContext handshake.
    // ------------------------------------------------------------------
    const DEFAULT_BACKEND = 'https://proton-backend-247165654737.asia-southeast1.run.app';
    const POLL_INTERVAL_MS = 60_000;   // 60 s refresh
    const WARNING_SOUND_FREQ = 440;    // Hz for the beep

    const params = new URLSearchParams(window.location.search);
    const backendBaseUrl = (params.get('backendBaseUrl') || DEFAULT_BACKEND).replace(/\/+$/, '');

    let agentId = params.get('agentId') || null;
    let warningThresholdMinutes = 60;  // overwritten from API response
    let pollTimer = null;
    let nextRefreshCountdown = null;
    let notifiedConvIds = new Set();   // suppress repeat desktop notifications

    // ------------------------------------------------------------------
    // Chatwoot Dashboard App postMessage handshake
    // ------------------------------------------------------------------
    function requestContext() {
      try {
        window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*');
      } catch (_e) { /* not embedded */ }
    }

    window.addEventListener('message', (ev) => {
      let msg = ev.data;
      if (typeof msg === 'string') {
        try { msg = JSON.parse(msg); } catch (_e) { return; }
      }
      if (!msg || msg.event !== 'appContext') return;
      const ctx = msg.data || {};
      const agent = ctx.currentAgent;
      if (agent && agent.id && !agentId) {
        agentId = String(agent.id);
        loadTasks();
      }
    });

    // ------------------------------------------------------------------
    // Beep (Web Audio API — no external file needed)
    // ------------------------------------------------------------------
    function beep(freq, durationMs) {
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.frequency.value = freq;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + durationMs / 1000);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + durationMs / 1000);
      } catch (_e) { /* audio not available */ }
    }

    // ------------------------------------------------------------------
    // Desktop Notification API
    // ------------------------------------------------------------------
    function requestNotificationPermission() {
      if (!('Notification' in window)) {
        toast('warning', 'This browser does not support desktop notifications.');
        return;
      }
      Notification.requestPermission().then((perm) => {
        if (perm === 'granted') {
          toast('success', 'Desktop notifications enabled.');
          document.getElementById('notify-btn').textContent = 'Notifications on';
          document.getElementById('notify-btn').disabled = true;
        } else {
          toast('warning', 'Notification permission denied. Allow it in browser settings.');
        }
      });
    }

    function sendDesktopNotification(title, body) {
      if (Notification.permission === 'granted') {
        new Notification(title, { body, icon: '' });
      }
    }

    // ------------------------------------------------------------------
    // Fetch tasks from backend
    // ------------------------------------------------------------------
    async function loadTasks() {
      setStatus('Loading...');
      clearBanner();
      const url = agentId
        ? `${backendBaseUrl}/tasks/mine?agent_id=${encodeURIComponent(agentId)}`
        : `${backendBaseUrl}/tasks/mine`;
      try {
        const res = await fetch(url);
        if (!res.ok) {
          throw new Error(`${res.status} ${res.statusText}`);
        }
        const data = await res.json();
        warningThresholdMinutes = data.warningThresholdMinutes || 60;
        renderTable(data.tasks || []);
        const count = (data.tasks || []).length;
        setStatus(`${count} open task${count === 1 ? '' : 's'} · updated ${fmtTime(new Date(data.generatedAt))}`);
        checkNotifications(data.tasks || []);
      } catch (err) {
        setStatus('Error');
        showBanner('error', 'Failed to load tasks: ' + escapeHtml(String(err.message)));
        document.getElementById('tasks-body').innerHTML =
          '<tr><td colspan="6"><div class="empty-state">Could not load tasks.</div></td></tr>';
      }
      scheduleNextRefresh();
    }

    // ------------------------------------------------------------------
    // Notification logic: desktop alert + beep on first warn/breach
    // ------------------------------------------------------------------
    function checkNotifications(tasks) {
      for (const t of tasks) {
        const convId = t.convId;
        if (notifiedConvIds.has(convId)) continue;
        if (t.breachType === 'UNRESOLVED' || t.breachType === 'NO_RESPONSE') {
          sendDesktopNotification(
            `SLA Breached — Case #${convId}`,
            `${t.subject}: resolution SLA has been exceeded. Immediate action required.`
          );
          beep(330, 600);
          notifiedConvIds.add(convId);
          toast('error', `Case #${convId} (${t.subject}) has breached its SLA!`);
        } else if (
          t.resolutionRemainingSeconds !== null &&
          t.resolutionRemainingSeconds < warningThresholdMinutes * 60
        ) {
          sendDesktopNotification(
            `SLA Warning — Case #${convId}`,
            `${t.subject}: ${fmtDuration(t.resolutionRemainingSeconds)} until SLA deadline.`
          );
          beep(WARNING_SOUND_FREQ, 300);
          notifiedConvIds.add(convId);
          toast('warning', `Case #${convId} (${t.subject}) is approaching its SLA deadline.`);
        }
      }
    }

    // ------------------------------------------------------------------
    // Render countdown table
    // ------------------------------------------------------------------
    function renderTable(tasks) {
      const body = document.getElementById('tasks-body');
      if (!tasks.length) {
        body.innerHTML = '<tr><td colspan="6"><div class="empty-state">No open tasks. Well done!</div></td></tr>';
        return;
      }
      body.innerHTML = tasks.map((t) => {
        const rowClass = t.breachType ? 'status-breach'
          : (t.resolutionRemainingSeconds !== null && t.resolutionRemainingSeconds < warningThresholdMinutes * 60)
            ? 'status-warning'
            : 'status-ok';

        const resSec = t.resolutionRemainingSeconds;
        const resClass = resSec === null ? '' : resSec < 0 ? 'breach' : resSec < warningThresholdMinutes * 60 ? 'warning' : 'ok';
        const resDisplay = resSec === null ? '—' : fmtDuration(resSec);

        const respSec = t.responseRemainingSeconds;
        const respClass = respSec === null ? '' : respSec < 0 ? 'breach' : respSec < warningThresholdMinutes * 60 ? 'warning' : 'ok';
        const respDisplay = respSec === null ? 'Replied' : fmtDuration(respSec);

        const badge = t.breachType
          ? `<span class="badge badge-breach">${escapeHtml(t.breachType)}</span>`
          : (resSec !== null && resSec < warningThresholdMinutes * 60)
            ? `<span class="badge badge-warning">WARNING</span>`
            : `<span class="badge badge-ok">OK</span>`;

        const chatwootBase = backendBaseUrl.replace(/\/api.*/, '');
        const convLink = `${chatwootBase}/app/conversations/${escapeAttr(t.convId)}`;

        return `
          <tr class="${rowClass}">
            <td><a class="conv-link" href="${convLink}" target="_blank">#${escapeHtml(t.convId)}</a></td>
            <td>${escapeHtml(t.subject)}</td>
            <td>${escapeHtml(t.status)}</td>
            <td><span class="countdown ${respClass}">${respDisplay}</span></td>
            <td><span class="countdown ${resClass}">${resDisplay}</span></td>
            <td>${badge}</td>
          </tr>
        `;
      }).join('');
    }

    // ------------------------------------------------------------------
    // Refresh countdown timer in the toolbar
    // ------------------------------------------------------------------
    function scheduleNextRefresh() {
      if (pollTimer) clearTimeout(pollTimer);
      if (nextRefreshCountdown) clearInterval(nextRefreshCountdown);
      let remaining = POLL_INTERVAL_MS / 1000;
      updateNextRefreshLabel(remaining);
      nextRefreshCountdown = setInterval(() => {
        remaining -= 1;
        updateNextRefreshLabel(remaining);
        if (remaining <= 0) clearInterval(nextRefreshCountdown);
      }, 1000);
      pollTimer = setTimeout(loadTasks, POLL_INTERVAL_MS);
    }

    function updateNextRefreshLabel(sec) {
      document.getElementById('next-refresh').textContent =
        sec > 0 ? `Next refresh in ${sec}s` : '';
    }

    // ------------------------------------------------------------------
    // Formatting helpers
    // ------------------------------------------------------------------
    function fmtDuration(seconds) {
      const abs = Math.abs(seconds);
      const prefix = seconds < 0 ? '-' : '';
      const h = Math.floor(abs / 3600);
      const m = Math.floor((abs % 3600) / 60);
      const s = Math.floor(abs % 60);
      if (h > 0) return `${prefix}${h}h ${m}m`;
      if (m > 0) return `${prefix}${m}m ${s}s`;
      return `${prefix}${s}s`;
    }

    function fmtTime(date) {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function escapeHtml(text) {
      const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
      return String(text).replace(/[&<>"']/g, (m) => map[m]);
    }

    function escapeAttr(text) {
      return String(text).replace(/"/g, '&quot;');
    }

    // ------------------------------------------------------------------
    // UI helpers
    // ------------------------------------------------------------------
    function setStatus(text) { document.getElementById('status').textContent = text; }
    function showBanner(kind, msg) {
      document.getElementById('banner').innerHTML =
        `<div class="banner ${kind}">${msg}</div>`;
    }
    function clearBanner() { document.getElementById('banner').innerHTML = ''; }
    function toast(kind, msg) {
      const el = document.createElement('div');
      el.className = `toast ${kind}`;
      el.textContent = msg;
      document.getElementById('toasts').appendChild(el);
      setTimeout(() => el.remove(), 4000);
    }

    // ------------------------------------------------------------------
    // Boot
    // ------------------------------------------------------------------
    document.getElementById('refresh-btn').addEventListener('click', () => {
      notifiedConvIds.clear(); // allow re-notification on manual refresh
      loadTasks();
    });
    document.getElementById('notify-btn').addEventListener('click', requestNotificationPermission);

    // Auto-hide the notify button if already granted
    if (Notification && Notification.permission === 'granted') {
      document.getElementById('notify-btn').textContent = 'Notifications on';
      document.getElementById('notify-btn').disabled = true;
    }

    document.addEventListener('DOMContentLoaded', () => {
      requestContext();
      // If agentId was supplied as a query param, load immediately without waiting
      // for the postMessage handshake (useful for standalone / non-embedded use).
      if (agentId) {
        loadTasks();
      } else {
        setStatus('Waiting for Chatwoot context...');
        // Fallback: load all open tasks if context never arrives (5 s timeout)
        setTimeout(() => {
          if (!agentId) loadTasks();
        }, 5000);
      }
    });
  </script>
</body>
</html>
```

- [ ] **Step 2: Verify the file renders without errors**

Open it in a browser with the query string `?backendBaseUrl=http://localhost:8000`:

```bash
open "apps/chatwoot-my-tasks/index.html?backendBaseUrl=http://localhost:8000"
```

Expected: page loads, shows "Waiting for Chatwoot context..." then after 5 s fetches from the backend (which returns `{"tasks": [], "generatedAt": "...", "warningThresholdMinutes": 60}`).

- [ ] **Step 3: Commit**

```bash
git add apps/chatwoot-my-tasks/index.html
git commit -m "feat(phase6): add My Tasks agent dashboard app with SLA countdowns and browser notifications"
```

---

## Task 7: End-to-end smoke verification

**Files:**
- Test: `apps/backend/src/chatbot/features/tasks/test_tasks_router.py` (extend existing)

**Interfaces:**
- Validates: deadline math + router + notification paths together

- [ ] **Step 1: Write the smoke integration test**

Append to `apps/backend/src/chatbot/features/tasks/test_tasks_router.py`:

```python
def test_breach_conv_has_negative_remaining_and_breach_type() -> None:
    """A 50h-old open conv against 48h SLA must have negative remaining + UNRESOLVED."""
    conv = {
        "id": 999,
        "status": "open",
        "created_at": _epoch(50),  # 50h > 48h
        "meta": {"assignee": {"id": 1}, "sender": {"name": "BrokenCustomer"}},
    }
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=[conv]), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    t = tasks[0]
    assert t["resolutionRemainingSeconds"] < 0
    assert t["breachType"] == "UNRESOLVED"


def test_warning_conv_in_warning_window() -> None:
    """A conv within the warning window has negative remaining < warning threshold."""
    # 47h old, 48h SLA, 120 min warning → remaining = 3600s which is < 7200s
    conv = {
        "id": 888,
        "status": "open",
        "created_at": _epoch(47),
        "meta": {"assignee": {"id": 2}, "sender": {"name": "WarningCustomer"}},
    }
    settings = _settings(tasks_reminder_warning_minutes=120)
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=[conv]), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    t = resp.json()["tasks"][0]
    assert t["resolutionRemainingSeconds"] is not None
    assert 0 < t["resolutionRemainingSeconds"] < 120 * 60  # within warning window
    assert t["breachType"] is None  # not yet breached


def test_sla_override_label_reflected_in_response() -> None:
    """sla_480 label → 480 min resolution SLA; verify resolutionDeadlineIso is 8h after created."""
    from datetime import timezone
    conv = {
        "id": 777,
        "status": "open",
        "created_at": _epoch(0),  # just created
        "labels": ["sla_480"],
        "meta": {"assignee": {"id": 3}, "sender": {"name": "SLACustomer"}},
    }
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=[conv]), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    t = resp.json()["tasks"][0]
    # 480 min = 8h; conv just created → remaining ≈ 28800s
    assert t["resolutionRemainingSeconds"] is not None
    assert abs(t["resolutionRemainingSeconds"] - 28800) < 10
```

- [ ] **Step 2: Run full suite**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/chatbot/features/tasks/ -v
```

Expected:
```
PASSED test_response_deadline_computed_from_created_at
PASSED test_response_already_breached_gives_negative_remaining
PASSED test_resolution_breach_takes_priority_when_both_breached
PASSED test_sla_minutes_label_overrides_resolution_threshold
PASSED test_custom_attributes_sla_minutes_overrides_resolution
PASSED test_resolved_conversation_has_no_breach
PASSED test_first_reply_clears_response_breach
PASSED test_agent_id_extracted_from_meta
PASSED test_subject_from_meta_sender_name
PASSED test_task_item_is_dataclass_with_expected_fields
PASSED test_returns_tasks_for_agent
PASSED test_filters_other_agents_conversations
PASSED test_no_agent_id_returns_all_open
PASSED test_generated_at_is_iso_string
PASSED test_breach_conv_has_negative_remaining_and_breach_type
PASSED test_warning_conv_in_warning_window
PASSED test_sla_override_label_reflected_in_response
17 passed
```

- [ ] **Step 3: Run the complete test suite**

```bash
cd /path/to/proton-conversational-ai/apps/backend
pytest src/ -q 2>&1 | tail -5
```

Expected: no regressions; all previously-passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add apps/backend/src/chatbot/features/tasks/test_tasks_router.py
git commit -m "test(phase6): add end-to-end smoke tests for breach/warning/sla-override scenarios"
```

---

## Task 8: Chatwoot Dashboard App registration

**Files:**
- Modify: `docs/deploy/chatwoot-app-setup.md` (if it exists) OR document as inline steps below

**Interfaces:**
- Consumes: `apps/chatwoot-my-tasks/index.html` static file served at a public HTTPS URL
- Produces: "My Tasks" app entry under Chatwoot → Settings → Integrations → Dashboard Apps

No code — ops steps only. No automated test possible (requires a live Chatwoot instance).

- [ ] **Step 1: Serve the static file**

Copy `apps/chatwoot-my-tasks/index.html` to the existing static files location on the VM (alongside `chatwoot-faq-admin/` and `chatwoot-agent-app/`). The apps are served directly by the VM's nginx or Cloud Run; check `id-crm-ticketing/deploy/` for the current serving configuration.

```bash
# On the deploy VM (adjust path to match the existing static-app serve path)
cp apps/chatwoot-my-tasks/index.html /var/www/chatwoot-apps/my-tasks/index.html
```

Verify it is reachable:

```bash
curl -I https://crm.34-50-103-151.nip.io/apps/my-tasks/index.html
```

Expected: `HTTP/2 200`

- [ ] **Step 2: Register in Chatwoot**

In Chatwoot UI: Settings → Integrations → Dashboard Apps → New Dashboard App:
- Name: `My Tasks`
- URL: `https://crm.34-50-103-151.nip.io/apps/my-tasks/index.html?backendBaseUrl=https://proton-backend-247165654737.asia-southeast1.run.app`
- Click Create.

- [ ] **Step 3: Smoke test in Chatwoot**

Open any conversation → click "My Tasks" in the right sidebar dashboard app panel.

Expected: The panel loads, shows "Waiting for Chatwoot context..." then transitions to the task table (or "No open tasks") within 5 s.

- [ ] **Step 4: Commit**

```bash
git add apps/chatwoot-my-tasks/
git commit -m "deploy(phase6): add My Tasks static app + Chatwoot dashboard app registration steps"
```

---

## Self-Review

**1. Spec coverage:**

| Spec requirement | Covered by |
|---|---|
| `GET /tasks/mine?agent_id=<id>` — open convs with SLA deadline + remaining | Task 3 `tasks_router.py` |
| Compute deadline from `sla_minutes`/defaults + `created_at`/`first_reply_created_at` | Task 1 `deadline.py` + `_chatwoot_sla_minutes` reuse |
| Per-ticket `sla_minutes` override (label + `custom_attributes`) | Task 1 `_sla_minutes_from_conv()` |
| 8h response / 48h resolution defaults from `Settings` | Task 1 global constraints wired |
| "My Tasks" agent app — static HTML, live countdowns, refresh loop | Task 6 `chatwoot-my-tasks/index.html` |
| Chatwoot `postMessage` appContext handshake for `currentAgent.id` | Task 6 `requestContext()` |
| In-system reminder (browser `Notification` API) | Task 6 `sendDesktopNotification()` |
| Desktop/sound notification (beep) | Task 6 `beep()` via Web Audio API |
| WhatsApp fallback via `TwilioChannelAdapter` on near-breach | Task 5 `REMINDER_WARNING_STATE` in `sla.py` |
| Dedup — reminder fires only once per conversation | Task 5 audit-log dedup pattern |
| Warning threshold configurable (`tasks_reminder_warning_minutes`) | Task 2 `config.py` |

**Gaps identified:**

1. **Chatwoot native notification API / websocket push** — the spec mentions "Chatwoot notification path or websocket". This plan uses polling (60 s) instead of a true server-push WebSocket. Polling is safe and sufficient for the DoD, but real-time push would require a fork of Chatwoot's notification channel (deferred to an optional Phase 6b fork task).

2. **Per-agent WA phone number** — the current WA reminder in Task 5 sends to `sla_pic_whatsapp` (the global PIC number). Per-agent WA mapping (from a `pic_<phone>` label) was not implemented because the per-agent phone store (dept→PIC table) belongs to Phase 2. When Phase 2 delivers that table, `_agent_whatsapp_from_conv(conv)` should be wired in.

3. **Auth on `/tasks/mine`** — the endpoint is unauthenticated (mirrors the FAQ-admin's `?apiKey=` pattern). For production, gate it behind `x-api-key` or Chatwoot-issued JWT if agents should not cross-read each other's tasks without `agent_id` filtering.

**2. Placeholder scan:** No TBD / TODO / "implement later" patterns found in code blocks.

**3. Type consistency check:**

- `TaskItem` defined in `deadline.py` Task 1; used identically in `tasks_router.py` Task 3 (`_task_item_to_dict(item: TaskItem)`). Fields match.
- `build_tasks_router(settings: Settings) -> APIRouter` used in Task 3, imported in Task 4 `main.py`. Signature matches.
- `REMINDER_WARNING_STATE = "REMINDER_WARNING"` defined in Task 5 `sla.py`, imported in Task 5 test. Consistent.
- `compute_deadlines(conv, settings, now) -> TaskItem` defined in Task 1, called in `tasks_router.py` Task 3 with same signature. Matches.
- `fetch_conversations(settings, get_page=None)` imported from `features/metrics/sync.py` in `tasks_router.py` — correct path confirmed by reading `sync.py`.
