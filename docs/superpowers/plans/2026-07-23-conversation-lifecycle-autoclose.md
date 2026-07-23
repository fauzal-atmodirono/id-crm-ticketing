# Conversation Lifecycle & Auto-Close Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the conversation-lifecycle state machine (business-hours gating, idle→warn→auto-close, resolution YES/NO confirmation, AI/agent rating surveys, AI disclaimer/welcome) to the `agent/` service, driving the Proton process-flow SOP.

**Architecture:** A new async scanner in the `agent/` service ticks over open/pending Chatwoot conversations and drives a per-conversation lifecycle state (`ACTIVE → IDLE_WARNED → AWAITING_RESOLUTION → AWAITING_SURVEY → CLOSED`) stored in a new `conversation_lifecycle` table and mirrored to a Chatwoot custom attribute. The orchestrator gains a pre-check that routes survey/confirmation replies to a lifecycle parser instead of Gemini. Business hours are read from Chatwoot's native per-inbox `working_hours` (no custom hours config). Everything is additive and fail-open, gated behind `LIFECYCLE_ENABLED` (default off → byte-identical to today).

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 async (sqlite/aiosqlite in tests, postgres/psycopg in prod), httpx, pytest (`asyncio_mode=auto`), respx for HTTP stubs. `zoneinfo` from the stdlib for timezone math.

## Global Constraints

- All work is in `agent/`. Run everything from `agent/` (`cd agent`).
- New env vars go in **both** `agent/app/config.py` (as `Settings` fields) **and** `deploy/tenants/example.env`, and (if required at import time) `agent/tests/conftest.py`. Names must match verbatim, case-insensitively.
- Background tasks and scanner ticks **never raise for expected "nothing to do" cases** (missing fields, unknown ids, downstream HTTP failures) — log and skip. Raising out of a background task only produces an unretrieved-exception log.
- Fail-open: when `LIFECYCLE_ENABLED` is false, or the proton backend / Chatwoot is unreachable, behavior must be identical to today.
- Match existing style: module docstrings explain the *why* and the concurrency/idempotency reasoning.
- No new third-party dependencies (async scanner uses `asyncio`, not APScheduler).
- Tests never hit postgres, real Chatwoot/Zammad, or Gemini. Use the sqlite fixture + respx.
- SOP message templates are hardcoded defaults, overridable via proton persona messages where those exist.
- Commit after each task with a `feat:`/`test:` message.

**Lifecycle state constants** (defined once in Task 5, `app/services/lifecycle.py`, referenced everywhere):
`ACTIVE = "active"`, `IDLE_WARNED = "idle_warned"`, `AWAITING_RESOLUTION = "awaiting_resolution"`, `AWAITING_SURVEY = "awaiting_survey"`, `CLOSED = "closed"`.

---

### Task 1: Config & env vars

**Files:**
- Modify: `agent/app/config.py` (add fields to `Settings`, after `auto_resolve` on line 47)
- Modify: `deploy/tenants/example.env` (after line 39, the agent section)
- Test: `agent/tests/test_lifecycle_config.py` (create)

**Interfaces:**
- Produces: `Settings.lifecycle_enabled: bool`, `.lifecycle_scan_interval_seconds: int`, `.lifecycle_idle_warn_minutes: int`, `.lifecycle_idle_close_grace_minutes: int`, `.lifecycle_idle_close_out_of_hours_grace_minutes: int`, `.lifecycle_confirm_grace_minutes: int`, `.lifecycle_survey_enabled: bool`, `.lifecycle_disclaimer_enabled: bool`, `.lifecycle_auto_categorize: bool`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_lifecycle_config.py`:

```python
"""Lifecycle settings default to the feature being off / no-op."""

from app.config import Settings


def _settings() -> Settings:
    # conftest.py has set all required env vars at import time.
    return Settings()


def test_lifecycle_defaults_are_off_and_safe():
    s = _settings()
    assert s.lifecycle_enabled is False
    assert s.lifecycle_scan_interval_seconds == 60
    assert s.lifecycle_idle_warn_minutes == 10
    assert s.lifecycle_idle_close_grace_minutes == 5
    assert s.lifecycle_idle_close_out_of_hours_grace_minutes == 0
    assert s.lifecycle_confirm_grace_minutes == 10
    assert s.lifecycle_survey_enabled is True
    assert s.lifecycle_disclaimer_enabled is True
    assert s.lifecycle_auto_categorize is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_lifecycle_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'lifecycle_enabled'`.

- [ ] **Step 3: Add the fields**

In `agent/app/config.py`, immediately after `auto_resolve: bool = False` (line 47):

```python

    # Conversation lifecycle & auto-close (feature is a no-op when disabled).
    # Drives the Proton process-flow SOP: idle warn/close, resolution
    # confirmation, rating surveys, AI disclaimer. See
    # docs/superpowers/specs/2026-07-23-conversation-lifecycle-autoclose-design.md
    lifecycle_enabled: bool = False
    lifecycle_scan_interval_seconds: int = 60
    lifecycle_idle_warn_minutes: int = 10
    lifecycle_idle_close_grace_minutes: int = 5
    lifecycle_idle_close_out_of_hours_grace_minutes: int = 0
    lifecycle_confirm_grace_minutes: int = 10
    lifecycle_survey_enabled: bool = True
    lifecycle_disclaimer_enabled: bool = True
    lifecycle_auto_categorize: bool = False
```

- [ ] **Step 4: Document the env vars**

In `deploy/tenants/example.env`, after line 39 (`AUTO_RESOLVE=false`):

```bash

# --- Conversation lifecycle & auto-close (agent service) ---
# Master switch. When false the whole feature is a no-op (default).
LIFECYCLE_ENABLED=false
# How often the scanner ticks over open/pending conversations (seconds).
LIFECYCLE_SCAN_INTERVAL_SECONDS=60
# Minutes of customer inactivity before the idle warning is posted.
LIFECYCLE_IDLE_WARN_MINUTES=10
# Grace after the warning before auto-close, IN business hours (10+5=15m total).
LIFECYCLE_IDLE_CLOSE_GRACE_MINUTES=5
# Grace after the warning before auto-close, OUTSIDE business hours (10+0=10m total).
LIFECYCLE_IDLE_CLOSE_OUT_OF_HOURS_GRACE_MINUTES=0
# Minutes to wait for a YES/NO (or rating) reply before treating as resolved.
LIFECYCLE_CONFIRM_GRACE_MINUTES=10
# Post the rating survey after resolution.
LIFECYCLE_SURVEY_ENABLED=true
# Post the AI disclaimer/welcome on conversation-created.
LIFECYCLE_DISCLAIMER_ENABLED=true
# Optionally apply a case-category label on bot auto-close (off by default).
LIFECYCLE_AUTO_CATEGORIZE=false
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd agent && pytest tests/test_lifecycle_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/app/config.py agent/tests/test_lifecycle_config.py deploy/tenants/example.env
git commit -m "feat(agent): add conversation-lifecycle settings (default off)"
```

---

### Task 2: `ConversationLifecycle` model + store helpers

**Files:**
- Modify: `agent/app/db/models.py` (add the model at the end)
- Create: `agent/app/services/lifecycle_store.py`
- Test: `agent/tests/test_lifecycle_store.py`

**Interfaces:**
- Produces (model): `ConversationLifecycle` with columns `conversation_id` (BigInteger PK), `state` (Text), `channel` (Text|None), `survey_variant` (Text|None), `warned_at` (DateTime|None), `state_changed_at` (DateTime), `created_at` (DateTime).
- Produces (store, all open their own session, all fail-open-safe to import):
  - `async def get_row(conversation_id: int) -> ConversationLifecycle | None`
  - `async def get_state(conversation_id: int) -> str | None`
  - `async def seed_active(conversation_id: int, channel: str | None) -> None` — insert `ACTIVE` if no row exists; no-op if a row already exists.
  - `async def transition(conversation_id: int, new_state: str, *, warned_at: datetime | None = None, survey_variant: str | None = None) -> None` — upsert the row's `state` (and optional `warned_at`/`survey_variant`), stamping `state_changed_at = now`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_lifecycle_store.py`:

```python
from app.services import lifecycle_store


async def test_seed_then_get_state():
    assert await lifecycle_store.get_state(101) is None
    await lifecycle_store.seed_active(101, channel="Channel::Whatsapp")
    assert await lifecycle_store.get_state(101) == "active"


async def test_seed_is_idempotent_and_does_not_clobber():
    await lifecycle_store.seed_active(102, channel="Channel::Api")
    await lifecycle_store.transition(102, "idle_warned")
    # Seeding again must NOT reset an existing row back to active.
    await lifecycle_store.seed_active(102, channel="Channel::Api")
    assert await lifecycle_store.get_state(102) == "idle_warned"


async def test_transition_updates_state_and_fields():
    await lifecycle_store.seed_active(103, channel="Channel::Api")
    await lifecycle_store.transition(103, "awaiting_survey", survey_variant="ai")
    row = await lifecycle_store.get_row(103)
    assert row.state == "awaiting_survey"
    assert row.survey_variant == "ai"


async def test_transition_on_missing_row_creates_it():
    await lifecycle_store.transition(104, "closed")
    assert await lifecycle_store.get_state(104) == "closed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_lifecycle_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.lifecycle_store'`.

- [ ] **Step 3: Add the model**

In `agent/app/db/models.py`, append at the end of the file:

```python


class ConversationLifecycle(Base):
    """Per-conversation lifecycle state for the auto-close / survey flow.

    Layered on top of Chatwoot's own pending/open/resolved status. The unique
    `conversation_id` primary key is the concurrency guard: the scanner and the
    orchestrator both drive transitions, and a row can only be in one state, so
    a duplicate scan tick can't double-fire a transition. `state_changed_at`
    dates the current state (used for the confirmation/survey timeout);
    `warned_at` dates when the idle warning was posted.
    """

    __tablename__ = "conversation_lifecycle"

    conversation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str | None] = mapped_column(Text)
    survey_variant: Mapped[str | None] = mapped_column(Text)
    warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

(`BigInteger`, `DateTime`, `Text`, `func`, `Mapped`, `mapped_column`, `datetime` are already imported at the top of the file.)

- [ ] **Step 4: Write the store module**

Create `agent/app/services/lifecycle_store.py`:

```python
"""DB access for conversation lifecycle state.

Thin CRUD over the `conversation_lifecycle` table. Each function owns its own
session (callers are background tasks / scanner ticks with no request-scoped
session). Uses select-then-insert/update rather than a DB-specific upsert so the
same code runs on sqlite (tests) and postgres (prod); the unique
`conversation_id` PK is the belt-and-suspenders guard against a racing double
insert.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import ConversationLifecycle
from app.db.session import async_session_maker


async def get_row(conversation_id: int) -> ConversationLifecycle | None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLifecycle).where(
                ConversationLifecycle.conversation_id == conversation_id
            )
        )
        return result.scalar_one_or_none()


async def get_state(conversation_id: int) -> str | None:
    row = await get_row(conversation_id)
    return row.state if row is not None else None


async def seed_active(conversation_id: int, channel: str | None) -> None:
    """Insert an ACTIVE row if none exists. No-op when a row already exists —
    never clobbers a conversation already mid-lifecycle."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLifecycle).where(
                ConversationLifecycle.conversation_id == conversation_id
            )
        )
        if result.scalar_one_or_none() is not None:
            return
        session.add(
            ConversationLifecycle(
                conversation_id=conversation_id, state="active", channel=channel
            )
        )
        await session.commit()


async def transition(
    conversation_id: int,
    new_state: str,
    *,
    warned_at: datetime | None = None,
    survey_variant: str | None = None,
) -> None:
    """Set the row's state (creating the row if absent), stamping
    state_changed_at. Optional warned_at / survey_variant are set only when
    provided (None leaves the existing value untouched)."""
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLifecycle).where(
                ConversationLifecycle.conversation_id == conversation_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ConversationLifecycle(conversation_id=conversation_id, state=new_state)
            session.add(row)
        else:
            row.state = new_state
        row.state_changed_at = now
        if warned_at is not None:
            row.warned_at = warned_at
        if survey_variant is not None:
            row.survey_variant = survey_variant
        await session.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_lifecycle_store.py -v`
Expected: PASS (4 tests). The autouse `_reset_agent_db` fixture creates the new table via `init_db` and wipes rows between tests.

- [ ] **Step 6: Commit**

```bash
git add agent/app/db/models.py agent/app/services/lifecycle_store.py agent/tests/test_lifecycle_store.py
git commit -m "feat(agent): conversation_lifecycle table + store helpers"
```

---

### Task 3: Chatwoot client additions

**Files:**
- Modify: `agent/app/clients/chatwoot.py` (add methods to `ChatwootClient`, after `set_agent_bot` on line 107)
- Test: `agent/tests/test_chatwoot_client_lifecycle.py`

**Interfaces:**
- Produces on `ChatwootClient`:
  - `async def list_conversations(self, status: str | None = None, assignee_type: str | None = None) -> Any` — GET the account conversations list; returns the raw JSON (callers normalize `data.payload`).
  - `async def get_inbox(self, inbox_id: int) -> Any` — GET one inbox (for `working_hours` / `channel_type`).
  - `async def set_custom_attributes(self, conversation_id: int, attributes: dict) -> Any` — POST conversation custom attributes.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_chatwoot_client_lifecycle.py`:

```python
import httpx
import respx

from app.clients.chatwoot import ChatwootClient

BASE = "http://chatwoot-rails:3000"


def _client() -> ChatwootClient:
    return ChatwootClient(base_url=BASE, api_access_token="t", account_id=1)


@respx.mock
async def test_list_conversations_passes_params_and_returns_json():
    route = respx.get(f"{BASE}/api/v1/accounts/1/conversations").mock(
        return_value=httpx.Response(200, json={"data": {"payload": [{"id": 7}]}})
    )
    client = _client()
    data = await client.list_conversations(status="open", assignee_type="all")
    assert data["data"]["payload"][0]["id"] == 7
    assert route.calls.last.request.url.params["status"] == "open"
    assert route.calls.last.request.url.params["assignee_type"] == "all"
    await client.aclose()


@respx.mock
async def test_get_inbox_returns_json():
    respx.get(f"{BASE}/api/v1/accounts/1/inboxes/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "channel_type": "Channel::Email"})
    )
    client = _client()
    inbox = await client.get_inbox(5)
    assert inbox["channel_type"] == "Channel::Email"
    await client.aclose()


@respx.mock
async def test_set_custom_attributes_posts_body():
    route = respx.post(
        f"{BASE}/api/v1/accounts/1/conversations/9/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={"ok": True}))
    client = _client()
    await client.set_custom_attributes(9, {"lifecycle_state": "idle_warned"})
    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body == {"custom_attributes": {"lifecycle_state": "idle_warned"}}
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_chatwoot_client_lifecycle.py -v`
Expected: FAIL with `AttributeError: 'ChatwootClient' object has no attribute 'list_conversations'`.

- [ ] **Step 3: Add the methods**

In `agent/app/clients/chatwoot.py`, after `set_agent_bot` (line 107, inside `ChatwootClient`):

```python

    async def list_conversations(
        self, status: str | None = None, assignee_type: str | None = None
    ) -> Any:
        """List account conversations. `status` is one of
        open/pending/resolved/snoozed; `assignee_type` one of me/unassigned/all.
        Returns the raw JSON — the payload list is at `data.payload`."""
        params: dict[str, str] = {}
        if status is not None:
            params["status"] = status
        if assignee_type is not None:
            params["assignee_type"] = assignee_type
        response = await self._client.get(
            f"/api/v1/accounts/{self.account_id}/conversations", params=params
        )
        response.raise_for_status()
        return response.json()

    async def get_inbox(self, inbox_id: int) -> Any:
        """Fetch one inbox, including native `working_hours`, `timezone`,
        `working_hours_enabled`, and `channel_type`."""
        response = await self._client.get(
            f"/api/v1/accounts/{self.account_id}/inboxes/{inbox_id}"
        )
        response.raise_for_status()
        return response.json()

    async def set_custom_attributes(
        self, conversation_id: int, attributes: dict
    ) -> Any:
        """Merge-set conversation custom attributes (used to mirror
        lifecycle_state into the Chatwoot right-panel for agent visibility)."""
        response = await self._client.post(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/custom_attributes",
            json={"custom_attributes": attributes},
        )
        response.raise_for_status()
        return response.json() if response.content else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_chatwoot_client_lifecycle.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/app/clients/chatwoot.py agent/tests/test_chatwoot_client_lifecycle.py
git commit -m "feat(agent): Chatwoot client list_conversations/get_inbox/set_custom_attributes"
```

---

### Task 4: Business-hours helper

**Files:**
- Create: `agent/app/services/business_hours.py`
- Test: `agent/tests/test_business_hours.py`

**Interfaces:**
- Produces: `def is_within_business_hours(inbox: dict, now: datetime | None = None) -> bool` — evaluates Chatwoot's native `working_hours` array against `now` (default: current time in the inbox timezone). Returns `True` when `working_hours_enabled` is falsy (treat as always-open).

**Chatwoot `working_hours` shape** (each element): `{"day_of_week": 0..6 (0=Sunday), "open_all_day": bool, "closed_all_day": bool, "open_hour": int, "open_minutes": int, "close_hour": int, "close_minutes": int}`. Inbox also has `"timezone": "Asia/Kuala_Lumpur"` and `"working_hours_enabled": bool`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_business_hours.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.business_hours import is_within_business_hours

# Monday 09:00–17:00 open; Sunday closed all day.
INBOX = {
    "working_hours_enabled": True,
    "timezone": "Asia/Kuala_Lumpur",
    "working_hours": [
        {"day_of_week": 1, "closed_all_day": False, "open_all_day": False,
         "open_hour": 9, "open_minutes": 0, "close_hour": 17, "close_minutes": 0},
        {"day_of_week": 0, "closed_all_day": True, "open_all_day": False,
         "open_hour": 0, "open_minutes": 0, "close_hour": 0, "close_minutes": 0},
    ],
}

TZ = ZoneInfo("Asia/Kuala_Lumpur")


def test_within_hours_on_monday_midday():
    assert is_within_business_hours(INBOX, datetime(2026, 7, 20, 12, 0, tzinfo=TZ)) is True


def test_before_open_on_monday():
    assert is_within_business_hours(INBOX, datetime(2026, 7, 20, 8, 0, tzinfo=TZ)) is False


def test_closed_all_day_sunday():
    assert is_within_business_hours(INBOX, datetime(2026, 7, 19, 12, 0, tzinfo=TZ)) is False


def test_day_with_no_row_is_closed():
    # Tuesday has no working_hours row → treated as closed.
    assert is_within_business_hours(INBOX, datetime(2026, 7, 21, 12, 0, tzinfo=TZ)) is False


def test_disabled_working_hours_is_always_open():
    assert is_within_business_hours({"working_hours_enabled": False}) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_business_hours.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.business_hours'`.

- [ ] **Step 3: Write the helper**

Create `agent/app/services/business_hours.py`:

```python
"""Read-only evaluation of Chatwoot's native per-inbox business hours.

We deliberately do NOT own a business-hours config: the tenant configures
working hours + timezone + the out-of-office auto-reply natively per inbox in
the Chatwoot UI, and Chatwoot posts the out-of-office reply itself. This helper
only *reads* whether "now" is within those hours, so the lifecycle scanner can
pick the right auto-close grace and tag out-of-hours cases.

Chatwoot's `working_hours` uses day_of_week 0=Sunday..6=Saturday. Python's
isoweekday() is Monday=1..Sunday=7, so `isoweekday() % 7` maps Sunday→0.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def is_within_business_hours(inbox: dict, now: datetime | None = None) -> bool:
    if not inbox.get("working_hours_enabled"):
        # No native hours configured → treat as always open (in-hours grace).
        return True

    tz_name = inbox.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        logger.debug("business_hours: unknown timezone %r, using UTC", tz_name)
        tz = timezone.utc

    now = now.astimezone(tz) if now is not None else datetime.now(tz)
    dow = now.isoweekday() % 7  # Sunday=0..Saturday=6, matching Chatwoot

    rows = inbox.get("working_hours") or []
    row = next((r for r in rows if r.get("day_of_week") == dow), None)
    if row is None or row.get("closed_all_day"):
        return False
    if row.get("open_all_day"):
        return True

    open_minute = int(row.get("open_hour", 0)) * 60 + int(row.get("open_minutes", 0))
    close_minute = int(row.get("close_hour", 0)) * 60 + int(row.get("close_minutes", 0))
    now_minute = now.hour * 60 + now.minute
    return open_minute <= now_minute < close_minute
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_business_hours.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/business_hours.py agent/tests/test_business_hours.py
git commit -m "feat(agent): native Chatwoot business-hours reader"
```

---

### Task 5: Lifecycle service — templates, state constants, disclaimer/welcome

**Files:**
- Create: `agent/app/services/lifecycle.py`
- Test: `agent/tests/test_lifecycle_disclaimer.py`

**Interfaces:**
- Consumes: `lifecycle_store.seed_active` (Task 2); `get_chatwoot_client`, `get_proton_config_client` (existing `app.clients.deps`); `ProtonConfigClient.get_assistant_messages` (existing).
- Produces (state constants): `ACTIVE`, `IDLE_WARNED`, `AWAITING_RESOLUTION`, `AWAITING_SURVEY`, `CLOSED`.
- Produces (templates): `DISCLAIMER_DEFAULT`, `IDLE_WARNING_DEFAULT`, `IDLE_CLOSE_DEFAULT`, `RESOLUTION_PROMPT_DEFAULT`, `SURVEY_AI_DEFAULT`, `SURVEY_AGENT_DEFAULT`, `THANKS_DEFAULT` (all `str`).
- Produces: `async def on_conversation_created(payload: dict) -> None` — seed the lifecycle row `ACTIVE` and, when `lifecycle_disclaimer_enabled`, post the disclaimer/welcome once.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_lifecycle_disclaimer.py`:

```python
from unittest.mock import AsyncMock

import pytest

from app.services import lifecycle, lifecycle_store


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    # No proton backend configured → disclaimer falls back to the default.
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: None)
    return client


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "lifecycle_disclaimer_enabled", True, raising=False)


async def test_on_conversation_created_seeds_and_posts_disclaimer(chatwoot):
    payload = {"id": 55, "inbox_id": 3, "channel": "Channel::Whatsapp"}
    await lifecycle.on_conversation_created(payload)

    assert await lifecycle_store.get_state(55) == "active"
    chatwoot.create_message.assert_awaited_once()
    args, kwargs = chatwoot.create_message.await_args
    assert args[0] == 55
    assert kwargs.get("private") is False


async def test_on_conversation_created_missing_id_is_noop(chatwoot):
    await lifecycle.on_conversation_created({"inbox_id": 3})
    chatwoot.create_message.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_lifecycle_disclaimer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.lifecycle'`.

- [ ] **Step 3: Write the module (constants, templates, disclaimer)**

Create `agent/app/services/lifecycle.py`:

```python
"""Conversation lifecycle state machine for the Proton process-flow SOP.

Layered on top of Chatwoot's pending/open/resolved status, this drives the
per-conversation flow: disclaimer on create → (scanner) idle warn → auto-close →
"is your case resolved? YES/NO" → rating survey → resolved. State lives in the
`conversation_lifecycle` table (app.services.lifecycle_store) and is mirrored to
a `lifecycle_state` Chatwoot custom attribute for agent visibility.

Everything here is fail-open: a downstream failure logs and returns, it never
raises out of the background task / scanner tick that called it. Message
templates default to the SOP verbatim text and are overridden by the proton
assistant persona messages where those exist.
"""

from __future__ import annotations

import logging

from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings
from app.services import lifecycle_store

logger = logging.getLogger(__name__)

# Lifecycle states (see the design doc's state machine).
ACTIVE = "active"
IDLE_WARNED = "idle_warned"
AWAITING_RESOLUTION = "awaiting_resolution"
AWAITING_SURVEY = "awaiting_survey"
CLOSED = "closed"

# SOP verbatim message defaults (overridable via proton persona messages).
DISCLAIMER_DEFAULT = (
    "Our customer support chatbot uses artificial intelligence (AI) to assist "
    "you. Responses are generated automatically based on your input. We do not "
    "guarantee the accuracy, completeness or timeliness of the information "
    "provided. You are encouraged to verify the content independently. For "
    "specific concerns or complex issues, please contact our live agent for "
    "further assistance."
)
IDLE_WARNING_DEFAULT = "Your chat will close in 5 minutes if we do not hear from you."
IDLE_CLOSE_DEFAULT = "Closed due to inactivity."
RESOLUTION_PROMPT_DEFAULT = "Is your case resolved? Please reply YES or NO."
SURVEY_AI_DEFAULT = "Thank you. Please rate our AI assistant from 1 to 5."
SURVEY_AGENT_DEFAULT = "Thank you. Please rate our support agent from 1 to 5."
THANKS_DEFAULT = "Thank you for your feedback!"


async def _mirror_state(conversation_id: int, state: str) -> None:
    """Best-effort mirror of the lifecycle state into a Chatwoot custom
    attribute for agent visibility. Never raises."""
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.set_custom_attributes(
            conversation_id, {"lifecycle_state": state}
        )
    except Exception:
        logger.debug(
            "lifecycle: could not mirror state for conversation %s", conversation_id,
            exc_info=True,
        )


async def _welcome_text(inbox_id: int | None) -> str:
    """Persona welcome_message if configured, else the SOP disclaimer."""
    proton = get_proton_config_client()
    if proton is not None and inbox_id is not None:
        try:
            msgs = await proton.get_assistant_messages(inbox_id)
            if msgs and msgs.get("welcome"):
                return msgs["welcome"]
        except Exception:
            logger.debug("lifecycle: could not fetch welcome message", exc_info=True)
    return DISCLAIMER_DEFAULT


async def on_conversation_created(payload: dict) -> None:
    """Seed the lifecycle row ACTIVE and post the AI disclaimer/welcome once.

    Called as a background task from the Chatwoot webhook `conversation_created`
    event. Fail-open: any missing field or downstream error logs and returns.
    """
    conversation_id = payload.get("id")
    if conversation_id is None:
        logger.info("lifecycle: conversation_created without id, skipping")
        return

    channel = payload.get("channel")
    inbox_id = payload.get("inbox_id")

    await lifecycle_store.seed_active(conversation_id, channel=channel)
    await _mirror_state(conversation_id, ACTIVE)

    settings = get_settings()
    if not settings.lifecycle_disclaimer_enabled:
        return

    text = await _welcome_text(inbox_id)
    if not text:
        return
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.create_message(conversation_id, text, private=False)
    except Exception:
        logger.exception(
            "lifecycle: failed to post disclaimer for conversation %s",
            conversation_id,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_lifecycle_disclaimer.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/lifecycle.py agent/tests/test_lifecycle_disclaimer.py
git commit -m "feat(agent): lifecycle service scaffold + disclaimer/welcome trigger"
```

---

### Task 6: Lifecycle service — reply parser, surveys, human-resolved trigger

**Files:**
- Modify: `agent/app/services/lifecycle.py` (add functions after `on_conversation_created`)
- Test: `agent/tests/test_lifecycle_replies.py`

**Interfaces:**
- Consumes: `lifecycle_store.transition`, `get_state` (Task 2); `get_chatwoot_client` (`create_message`, `toggle_status`); `AiAction` + `async_session_maker` (existing) for survey recording.
- Produces:
  - `def parse_yes_no(text: str) -> bool | None` — `True` for yes-tokens, `False` for no-tokens, `None` if neither.
  - `def parse_rating(text: str) -> int | None` — first integer 1–5 found, else `None`.
  - `async def handle_lifecycle_reply(conversation_id: int, text: str, state: str) -> None` — route an incoming customer reply for a conversation in `AWAITING_RESOLUTION` or `AWAITING_SURVEY`.
  - `async def on_human_resolved(payload: dict) -> None` — when a human resolves a conversation, post the agent-performance survey (unless the lifecycle already ended).
  - `async def _record_survey(conversation_id: int, variant: str, score: int | None, raw: str) -> None` — log the survey result to `ai_actions`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_lifecycle_replies.py`:

```python
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.db.models import AiAction
from app.db.session import async_session_maker
from app.services import lifecycle, lifecycle_store


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: None)
    return client


@pytest.fixture(autouse=True)
def _survey_on(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "lifecycle_survey_enabled", True, raising=False)


def test_parse_yes_no():
    assert lifecycle.parse_yes_no("YES") is True
    assert lifecycle.parse_yes_no("no thanks") is False
    assert lifecycle.parse_yes_no("maybe later") is None


def test_parse_rating():
    assert lifecycle.parse_rating("5") == 5
    assert lifecycle.parse_rating("I'd say 4 out of 5") == 4
    assert lifecycle.parse_rating("great") is None
    assert lifecycle.parse_rating("9") is None


async def test_resolution_yes_starts_ai_survey(chatwoot):
    await lifecycle_store.transition(30, lifecycle.AWAITING_RESOLUTION)
    await lifecycle.handle_lifecycle_reply(30, "yes", lifecycle.AWAITING_RESOLUTION)
    assert await lifecycle_store.get_state(30) == "awaiting_survey"
    chatwoot.create_message.assert_awaited()  # survey prompt posted


async def test_resolution_no_reopens_for_agent(chatwoot):
    await lifecycle_store.transition(31, lifecycle.AWAITING_RESOLUTION)
    await lifecycle.handle_lifecycle_reply(31, "no", lifecycle.AWAITING_RESOLUTION)
    assert await lifecycle_store.get_state(31) == "closed"  # lifecycle ends; human owns it
    chatwoot.toggle_status.assert_awaited_with(31, "open")


async def test_survey_reply_records_and_closes(chatwoot):
    await lifecycle_store.transition(32, lifecycle.AWAITING_SURVEY, survey_variant="ai")
    await lifecycle.handle_lifecycle_reply(32, "5", lifecycle.AWAITING_SURVEY)
    assert await lifecycle_store.get_state(32) == "closed"
    chatwoot.toggle_status.assert_awaited_with(32, "resolved")
    async with async_session_maker() as s:
        rows = (await s.execute(select(AiAction))).scalars().all()
    assert any(r.decision == "survey_ai" and r.output == "5" for r in rows)


async def test_human_resolved_triggers_agent_survey(chatwoot):
    await lifecycle_store.seed_active(33, channel="Channel::Api")
    await lifecycle.on_human_resolved({"id": 33, "status": "resolved"})
    assert await lifecycle_store.get_state(33) == "awaiting_survey"
    row = await lifecycle_store.get_row(33)
    assert row.survey_variant == "agent"


async def test_human_resolved_skips_when_already_closed(chatwoot):
    await lifecycle_store.transition(34, lifecycle.CLOSED)
    await lifecycle.on_human_resolved({"id": 34, "status": "resolved"})
    # Still closed, no survey prompt.
    assert await lifecycle_store.get_state(34) == "closed"
    chatwoot.create_message.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_lifecycle_replies.py -v`
Expected: FAIL with `AttributeError: module 'app.services.lifecycle' has no attribute 'parse_yes_no'`.

- [ ] **Step 3: Add the functions**

In `agent/app/services/lifecycle.py`, add these imports at the top (alongside the existing ones):

```python
import json

from sqlalchemy.exc import SQLAlchemyError

from app.db.models import AiAction
from app.db.session import async_session_maker
```

Then append after `on_conversation_created`:

```python
_YES_TOKENS = {"yes", "y", "ya", "yeah", "yep", "resolved", "ok", "okay", "sudah", "ya."}
_NO_TOKENS = {"no", "n", "nope", "not", "unresolved", "belum", "tidak"}


def parse_yes_no(text: str) -> bool | None:
    tokens = {t.strip(".,!?") for t in (text or "").lower().split()}
    if tokens & _YES_TOKENS:
        return True
    if tokens & _NO_TOKENS:
        return False
    return None


def parse_rating(text: str) -> int | None:
    for token in (text or "").replace("/", " ").split():
        cleaned = token.strip(".,!?")
        if cleaned.isdigit():
            value = int(cleaned)
            if 1 <= value <= 5:
                return value
    return None


async def _record_survey(
    conversation_id: int, variant: str, score: int | None, raw: str
) -> None:
    """Log the survey result to ai_actions (reuses the existing audit table).
    Fail-open: a DB blip must not break the flow."""
    output = str(score) if score is not None else json.dumps({"unparsed": raw})
    try:
        async with async_session_maker() as session:
            session.add(
                AiAction(
                    conversation_ref=f"chatwoot:{conversation_id}",
                    decision=f"survey_{variant}",
                    model="lifecycle",
                    output=output,
                )
            )
            await session.commit()
    except SQLAlchemyError:
        logger.exception(
            "lifecycle: failed to record survey for conversation %s", conversation_id
        )


async def _post(conversation_id: int, text: str) -> None:
    """Post a public message, swallowing transient errors."""
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.create_message(conversation_id, text, private=False)
    except Exception:
        logger.exception("lifecycle: failed to post message to %s", conversation_id)


async def _resolve(conversation_id: int) -> None:
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.toggle_status(conversation_id, "resolved")
    except Exception:
        logger.exception("lifecycle: failed to resolve conversation %s", conversation_id)


async def handle_lifecycle_reply(conversation_id: int, text: str, state: str) -> None:
    """Route a customer reply for a conversation mid-lifecycle. Called from the
    orchestrator's pre-check (before its pending-only filter) so it fires even
    on open/resolved conversations."""
    settings = get_settings()

    if state == AWAITING_RESOLUTION:
        answer = parse_yes_no(text)
        if answer is False or answer is None:
            # Not resolved (or unclear → err toward a human): reopen for an agent.
            await _post(
                conversation_id,
                "Thank you. We will assign an agent to assist you further.",
            )
            try:
                await get_chatwoot_client().toggle_status(conversation_id, "open")
            except Exception:
                logger.exception(
                    "lifecycle: failed to reopen conversation %s", conversation_id
                )
            await lifecycle_store.transition(conversation_id, CLOSED)
            await _mirror_state(conversation_id, CLOSED)
            return
        # answer is True → resolved by the bot.
        if settings.lifecycle_survey_enabled:
            await _post(conversation_id, SURVEY_AI_DEFAULT)
            await lifecycle_store.transition(
                conversation_id, AWAITING_SURVEY, survey_variant="ai"
            )
            await _mirror_state(conversation_id, AWAITING_SURVEY)
        else:
            await lifecycle_store.transition(conversation_id, CLOSED)
            await _mirror_state(conversation_id, CLOSED)
            await _resolve(conversation_id)
        return

    if state == AWAITING_SURVEY:
        row = await lifecycle_store.get_row(conversation_id)
        variant = row.survey_variant if row is not None else "ai"
        score = parse_rating(text)
        await _record_survey(conversation_id, variant or "ai", score, text)
        await _post(conversation_id, THANKS_DEFAULT)
        # CLOSED is set BEFORE resolving so the resulting status webhook sees a
        # terminal lifecycle and on_human_resolved skips (no double survey).
        await lifecycle_store.transition(conversation_id, CLOSED)
        await _mirror_state(conversation_id, CLOSED)
        await _resolve(conversation_id)
        return


async def on_human_resolved(payload: dict) -> None:
    """When a human resolves a conversation, post the agent-performance survey.

    Skipped when the lifecycle already ended (CLOSED) or is mid-survey — this is
    what prevents the bot's own survey-complete resolve from re-triggering a
    survey. Fail-open."""
    settings = get_settings()
    if not settings.lifecycle_survey_enabled:
        return
    conversation_id = payload.get("id")
    if conversation_id is None or payload.get("status") != "resolved":
        return

    state = await lifecycle_store.get_state(conversation_id)
    if state in (CLOSED, AWAITING_SURVEY, AWAITING_RESOLUTION):
        return  # lifecycle already owns the ending

    await _post(conversation_id, SURVEY_AGENT_DEFAULT)
    await lifecycle_store.transition(
        conversation_id, AWAITING_SURVEY, survey_variant="agent"
    )
    await _mirror_state(conversation_id, AWAITING_SURVEY)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_lifecycle_replies.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/lifecycle.py agent/tests/test_lifecycle_replies.py
git commit -m "feat(agent): lifecycle reply parser, surveys, human-resolved trigger"
```

---

### Task 7: Idle scanner

**Files:**
- Create: `agent/app/services/lifecycle_scanner.py`
- Test: `agent/tests/test_lifecycle_scanner.py`

**Interfaces:**
- Consumes: `get_chatwoot_client` (`list_conversations`, `get_inbox`, `create_message`, `toggle_status`); `business_hours.is_within_business_hours` (Task 4); `lifecycle` constants + templates + `_mirror_state`; `lifecycle_store`.
- Produces:
  - `def decide_idle_action(state: str, idle_minutes: float, state_age_minutes: float, warn_after: float, close_after: float, confirm_after: float) -> str | None` — pure decision returning `"warn"`, `"close"`, `"resolve_timeout"`, or `None`.
  - `def _now() -> datetime` — wraps `datetime.now(timezone.utc)` so tests can monkeypatch the clock.
  - `async def scan_once() -> None` — one tick over pending+open conversations.
  - `async def run_scanner() -> None` — the forever loop (sleep `lifecycle_scan_interval_seconds` between ticks), started from the app lifespan.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_lifecycle_scanner.py`:

```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import lifecycle, lifecycle_scanner, lifecycle_store


def test_decide_idle_action_transitions():
    d = lifecycle_scanner.decide_idle_action
    assert d("active", 12, 0, 10, 15, 10) == "warn"
    assert d("active", 3, 0, 10, 15, 10) is None
    assert d("idle_warned", 16, 6, 10, 15, 10) == "close"
    assert d("idle_warned", 12, 2, 10, 15, 10) is None
    assert d("awaiting_resolution", 0, 11, 10, 15, 10) == "resolve_timeout"
    assert d("awaiting_survey", 0, 11, 10, 15, 10) == "resolve_timeout"
    assert d("closed", 999, 999, 10, 15, 10) is None


@pytest.fixture
def wired(monkeypatch):
    client = AsyncMock()
    # One WhatsApp conversation, no assignee, idle 12 minutes.
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    last_activity = int((now.timestamp())) - 12 * 60
    client.list_conversations.side_effect = lambda status=None, assignee_type=None: (
        {"data": {"payload": [
            {"id": 70, "inbox_id": 4, "last_activity_at": last_activity,
             "meta": {"assignee": None}},
        ]}} if status == "pending" else {"data": {"payload": []}}
    )
    client.get_inbox.return_value = {
        "channel_type": "Channel::Whatsapp", "working_hours_enabled": False,
    }
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: now)
    return client


async def test_scan_warns_idle_conversation(wired):
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "idle_warned"
    wired.create_message.assert_awaited()  # warning posted


async def test_scan_skips_email_channel(wired, monkeypatch):
    wired.get_inbox.return_value = {
        "channel_type": "Channel::Email", "working_hours_enabled": False,
    }
    await lifecycle_store.seed_active(70, channel="Channel::Email")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "active"  # untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_lifecycle_scanner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.lifecycle_scanner'`.

- [ ] **Step 3: Write the scanner**

Create `agent/app/services/lifecycle_scanner.py`:

```python
"""Periodic scanner that drives idle → warn → auto-close transitions.

The agent service is otherwise webhook-only; this adds one asyncio loop
(started from the app lifespan when LIFECYCLE_ENABLED) that ticks every
`lifecycle_scan_interval_seconds` over pending + open conversations. For each
chat-like, bot-phase (unassigned) conversation it computes idle time from
Chatwoot's `last_activity_at` and applies the next transition. Idempotent: the
transition only fires from the matching current state, so a re-scan can't
double-post. Fail-open: any per-conversation error is logged and skipped.

Email is excluded (async by nature; the SOP email flow has no idle-close).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.clients.deps import get_chatwoot_client
from app.config import get_settings
from app.services import business_hours, lifecycle, lifecycle_store

logger = logging.getLogger(__name__)

_SKIP_CHANNELS = {"Channel::Email"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def decide_idle_action(
    state: str,
    idle_minutes: float,
    state_age_minutes: float,
    warn_after: float,
    close_after: float,
    confirm_after: float,
) -> str | None:
    if state == lifecycle.ACTIVE and idle_minutes >= warn_after:
        return "warn"
    if state == lifecycle.IDLE_WARNED and idle_minutes >= close_after:
        return "close"
    if state in (lifecycle.AWAITING_RESOLUTION, lifecycle.AWAITING_SURVEY):
        if state_age_minutes >= confirm_after:
            return "resolve_timeout"
    return None


def _payload_list(raw) -> list:
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, dict) and isinstance(data.get("payload"), list):
            return data["payload"]
        if isinstance(raw.get("payload"), list):
            return raw["payload"]
    return raw if isinstance(raw, list) else []


def _minutes_since(epoch_seconds, now: datetime) -> float | None:
    if not epoch_seconds:
        return None
    return (now.timestamp() - float(epoch_seconds)) / 60.0


async def scan_once() -> None:
    settings = get_settings()
    chatwoot = get_chatwoot_client()
    now = _now()
    inbox_cache: dict[int, dict] = {}

    conversations: list = []
    for status in ("pending", "open"):
        try:
            conversations += _payload_list(
                await chatwoot.list_conversations(status=status, assignee_type="all")
            )
        except Exception:
            logger.exception("lifecycle_scanner: failed to list %s conversations", status)

    for conv in conversations:
        try:
            await _process_one(conv, settings, chatwoot, now, inbox_cache)
        except Exception:
            logger.exception(
                "lifecycle_scanner: failed processing conversation %s",
                conv.get("id") if isinstance(conv, dict) else conv,
            )


async def _get_inbox(chatwoot, inbox_id, cache: dict) -> dict:
    if inbox_id not in cache:
        cache[inbox_id] = await chatwoot.get_inbox(inbox_id)
    return cache[inbox_id]


async def _process_one(conv, settings, chatwoot, now, inbox_cache) -> None:
    if not isinstance(conv, dict):
        return
    conversation_id = conv.get("id")
    inbox_id = conv.get("inbox_id")
    if conversation_id is None or inbox_id is None:
        return

    # Bot-phase only: once an agent is assigned, they own the conversation.
    if (conv.get("meta") or {}).get("assignee"):
        return

    inbox = await _get_inbox(chatwoot, inbox_id, inbox_cache)
    channel = inbox.get("channel_type")
    if channel in _SKIP_CHANNELS:
        return

    row = await lifecycle_store.get_row(conversation_id)
    if row is None:
        await lifecycle_store.seed_active(conversation_id, channel=channel)
        state = lifecycle.ACTIVE
        state_changed_at = now
    else:
        state = row.state
        state_changed_at = row.state_changed_at
    if state == lifecycle.CLOSED:
        return

    idle = _minutes_since(conv.get("last_activity_at"), now)
    if idle is None:
        return
    state_age = (now - state_changed_at).total_seconds() / 60.0

    in_hours = business_hours.is_within_business_hours(inbox, now)
    warn_after = settings.lifecycle_idle_warn_minutes
    grace = (
        settings.lifecycle_idle_close_grace_minutes
        if in_hours
        else settings.lifecycle_idle_close_out_of_hours_grace_minutes
    )
    close_after = warn_after + grace
    confirm_after = settings.lifecycle_confirm_grace_minutes

    action = decide_idle_action(
        state, idle, state_age, warn_after, close_after, confirm_after
    )
    if action is None:
        return

    if action == "warn":
        await lifecycle._post(conversation_id, lifecycle.IDLE_WARNING_DEFAULT)
        await lifecycle_store.transition(
            conversation_id, lifecycle.IDLE_WARNED, warned_at=now
        )
        await lifecycle._mirror_state(conversation_id, lifecycle.IDLE_WARNED)
    elif action == "close":
        await lifecycle._post(conversation_id, lifecycle.IDLE_CLOSE_DEFAULT)
        await lifecycle._post(conversation_id, lifecycle.RESOLUTION_PROMPT_DEFAULT)
        await lifecycle_store.transition(conversation_id, lifecycle.AWAITING_RESOLUTION)
        await lifecycle._mirror_state(conversation_id, lifecycle.AWAITING_RESOLUTION)
    elif action == "resolve_timeout":
        await lifecycle_store.transition(conversation_id, lifecycle.CLOSED)
        await lifecycle._mirror_state(conversation_id, lifecycle.CLOSED)
        await lifecycle._resolve(conversation_id)


async def run_scanner() -> None:
    settings = get_settings()
    logger.info("lifecycle_scanner: started (interval=%ss)", settings.lifecycle_scan_interval_seconds)
    while True:
        try:
            await scan_once()
        except Exception:
            logger.exception("lifecycle_scanner: scan tick failed")
        await asyncio.sleep(settings.lifecycle_scan_interval_seconds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_lifecycle_scanner.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/lifecycle_scanner.py agent/tests/test_lifecycle_scanner.py
git commit -m "feat(agent): idle lifecycle scanner (warn/close/timeout)"
```

---

### Task 8: Orchestrator integration (reply pre-check + language)

**Files:**
- Modify: `agent/app/services/orchestrator.py` (`SYSTEM_PROMPT` line 62-71; `handle_bot_event` line 91)
- Test: `agent/tests/test_orchestrator_lifecycle.py`

**Interfaces:**
- Consumes: `lifecycle` (constants, `handle_lifecycle_reply`), `lifecycle_store.get_state`, `lifecycle_store.transition` (Tasks 2/5/6).
- Produces: no new public symbols; `handle_bot_event` gains a lifecycle pre-check that returns early (routing survey/confirmation replies to `lifecycle.handle_lifecycle_reply`) before the existing `_is_eligible` path, and resets `IDLE_WARNED → ACTIVE` on a normal reply.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_orchestrator_lifecycle.py`:

```python
from unittest.mock import AsyncMock

import pytest

from app.services import lifecycle, lifecycle_store, orchestrator


@pytest.fixture(autouse=True)
def _enable_lifecycle(monkeypatch):
    monkeypatch.setattr(
        orchestrator.get_settings(), "lifecycle_enabled", True, raising=False
    )


def _msg(conversation_id, content, status="open"):
    return {
        "event": "message_created",
        "message_type": "incoming",
        "content": content,
        "sender": {"type": "contact"},
        "conversation": {"id": conversation_id, "status": status},
    }


async def test_awaiting_survey_reply_routes_to_lifecycle_not_gemini(monkeypatch):
    routed = AsyncMock()
    monkeypatch.setattr(lifecycle, "handle_lifecycle_reply", routed)
    gemini_spy = AsyncMock()
    monkeypatch.setattr(orchestrator.gemini, "decide", gemini_spy)

    await lifecycle_store.transition(80, lifecycle.AWAITING_SURVEY, survey_variant="ai")
    result = await orchestrator.handle_bot_event(_msg(80, "5", status="resolved"))

    assert result is None
    routed.assert_awaited_once()
    gemini_spy.assert_not_awaited()


async def test_idle_warned_reply_resets_to_active(monkeypatch):
    # A pending conversation past the warning: a fresh customer message should
    # reset lifecycle to ACTIVE (activity resets the idle clock) and then fall
    # through to the normal (debounced) bot path.
    monkeypatch.setattr(orchestrator, "_debounced_process", AsyncMock())
    await lifecycle_store.transition(81, lifecycle.IDLE_WARNED)
    task = await orchestrator.handle_bot_event(_msg(81, "hello", status="pending"))
    if task is not None:
        await task
    assert await lifecycle_store.get_state(81) == "active"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_orchestrator_lifecycle.py -v`
Expected: FAIL (the survey reply is not routed; `routed.assert_awaited_once()` fails / Gemini path taken).

- [ ] **Step 3: Update the orchestrator**

In `agent/app/services/orchestrator.py`, add to the imports (after line 32, `from app.services import sync`):

```python
from app.services import lifecycle, lifecycle_store
```

Extend `SYSTEM_PROMPT` (append to the closing string on line 70-71):

```python
SYSTEM_PROMPT = (
    "You are a support agent for the company, handling a live customer "
    "conversation. Decide exactly one action by calling a function: "
    "send_reply to answer the customer directly, escalate_to_ticket if this "
    "needs a human specialist or can't be resolved from the conversation "
    "alone, or handoff_to_human for anything else you're unsure about. Keep "
    "replies short, friendly, and strictly grounded in what the conversation "
    "actually says — never invent facts, prices, policies, or commitments "
    "you can't verify from it. Always reply in the same language the customer "
    "is using."
)
```

Add the lifecycle pre-check at the very top of `handle_bot_event`, before the `_is_eligible` check (line 95):

```python
async def handle_bot_event(payload: dict) -> asyncio.Task | None:
    """Filter the event, then (re)schedule the debounced processing task for
    its conversation. Returns the scheduled task (mainly so tests can await
    it deterministically) or None if the event wasn't actionable."""
    await _maybe_handle_lifecycle_reply(payload)
    if _lifecycle_consumed(payload):
        return None

    if not _is_eligible(payload):
        return None
    ...  # (rest unchanged)
```

Then add these helpers just above `handle_bot_event`:

```python
def _incoming_customer_message(payload: dict) -> int | None:
    """Return the conversation id for an incoming, non-bot customer message,
    regardless of conversation status (the lifecycle pre-check needs to see
    open/resolved conversations, not just pending ones)."""
    if payload.get("event") != "message_created":
        return None
    if payload.get("message_type") != "incoming":
        return None
    if _sender_type(payload) == "agent_bot":
        return None
    conversation = payload.get("conversation") or {}
    return conversation.get("id")


_LIFECYCLE_CONSUMED: str = "_lifecycle_consumed"


def _lifecycle_consumed(payload: dict) -> bool:
    return bool(payload.get(_LIFECYCLE_CONSUMED))


async def _maybe_handle_lifecycle_reply(payload: dict) -> None:
    """When the feature is on, route survey/confirmation replies to the
    lifecycle parser (instead of Gemini) and reset the idle clock on a normal
    reply. Marks the payload consumed so `handle_bot_event` returns early.
    Fail-open: any error just falls through to the normal bot path."""
    settings = get_settings()
    if not settings.lifecycle_enabled:
        return
    conversation_id = _incoming_customer_message(payload)
    if conversation_id is None:
        return
    try:
        state = await lifecycle_store.get_state(conversation_id)
    except Exception:
        logger.debug(
            "orchestrator: lifecycle state lookup failed for %s", conversation_id,
            exc_info=True,
        )
        return

    if state in (lifecycle.AWAITING_RESOLUTION, lifecycle.AWAITING_SURVEY):
        text = payload.get("content") or ""
        await lifecycle.handle_lifecycle_reply(conversation_id, text, state)
        payload[_LIFECYCLE_CONSUMED] = True
        return
    if state == lifecycle.IDLE_WARNED:
        # Customer came back → reset the idle clock so the scanner won't close.
        await lifecycle_store.transition(conversation_id, lifecycle.ACTIVE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_orchestrator_lifecycle.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the existing orchestrator tests (regression)**

Run: `cd agent && pytest tests/test_orchestrator.py tests/test_orchestrator_proton.py -v`
Expected: PASS (unchanged — the pre-check is a no-op when `lifecycle_enabled` is false, which is the default in conftest).

- [ ] **Step 6: Commit**

```bash
git add agent/app/services/orchestrator.py agent/tests/test_orchestrator_lifecycle.py
git commit -m "feat(agent): orchestrator lifecycle pre-check + same-language prompt"
```

---

### Task 9: Router wiring (conversation_created + human-resolved survey)

**Files:**
- Modify: `agent/app/routers/chatwoot.py` (`_STATUS_ONLY_EVENTS` handling line 56-57; add `conversation_created`)
- Test: `agent/tests/test_chatwoot_router_lifecycle.py`

**Interfaces:**
- Consumes: `lifecycle.on_conversation_created`, `lifecycle.on_human_resolved` (Tasks 5/6); `get_settings().lifecycle_enabled`.
- Produces: the generic `/webhooks/chatwoot` receiver dispatches `conversation_created` → `lifecycle.on_conversation_created`, and (when a status event resolves) additionally dispatches `lifecycle.on_human_resolved`, both gated by `lifecycle_enabled` and only as background tasks.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_chatwoot_router_lifecycle.py`:

```python
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "lifecycle_enabled", True, raising=False)
    return TestClient(create_app())


def _post(client, payload, delivery):
    settings = get_settings()
    body = json.dumps(payload).encode()
    ts = "9999999999"
    headers = {
        "X-Chatwoot-Signature": _sign(settings.chatwoot_webhook_secret, ts, body),
        "X-Chatwoot-Timestamp": ts,
        "X-Chatwoot-Delivery": delivery,
        "Content-Type": "application/json",
    }
    return client.post("/webhooks/chatwoot", content=body, headers=headers)


def test_conversation_created_dispatches_lifecycle(client, monkeypatch):
    calls = []
    from app.services import lifecycle

    async def fake(payload):
        calls.append(payload["id"])

    monkeypatch.setattr(lifecycle, "on_conversation_created", fake)
    resp = _post(client, {"event": "conversation_created", "id": 12, "inbox_id": 2}, "d1")
    assert resp.status_code == 200
    assert calls == [12]


def test_resolved_status_triggers_agent_survey(client, monkeypatch):
    calls = []
    from app.services import lifecycle

    async def fake(payload):
        calls.append(payload["id"])

    monkeypatch.setattr(lifecycle, "on_human_resolved", fake)
    resp = _post(
        client,
        {"event": "conversation_status_changed", "id": 13, "status": "resolved"},
        "d2",
    )
    assert resp.status_code == 200
    assert calls == [13]
```

(The `9999999999` timestamp is far in the future but the Chatwoot verifier's 300s skew window is checked around `now`; if the existing `verify_chatwoot_signature` rejects future timestamps, use a current epoch string via `str(int(time.time()))` instead — mirror whatever `tests/test_chatwoot_router.py` already does.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_chatwoot_router_lifecycle.py -v`
Expected: FAIL (`conversation_created` currently hits the "unhandled event" branch; no lifecycle dispatch).

- [ ] **Step 3: Wire the router**

In `agent/app/routers/chatwoot.py`:

Add the import (line 16 area):

```python
from app.services import lifecycle, orchestrator, sync
```

Replace the event-dispatch block (lines 51-59) with:

```python
    settings_flags = get_settings()
    event = payload.get("event")
    if event in _CONTACT_EVENTS:
        background_tasks.add_task(sync.upsert_contact, payload)
    elif event == "conversation_updated":
        background_tasks.add_task(sync.maybe_escalate, payload)
    elif event == "conversation_created":
        if settings_flags.lifecycle_enabled:
            background_tasks.add_task(lifecycle.on_conversation_created, payload)
    elif event in _STATUS_ONLY_EVENTS:
        background_tasks.add_task(sync.record_conversation_status, payload)
        if settings_flags.lifecycle_enabled and payload.get("status") == "resolved":
            background_tasks.add_task(lifecycle.on_human_resolved, payload)
    else:
        logger.info("chatwoot_webhook: unhandled event %r, ignoring", event)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_chatwoot_router_lifecycle.py tests/test_chatwoot_router.py -v`
Expected: PASS (new tests + existing router tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add agent/app/routers/chatwoot.py agent/tests/test_chatwoot_router_lifecycle.py
git commit -m "feat(agent): route conversation_created + resolved to lifecycle"
```

---

### Task 10: Lifespan wiring (start/stop the scanner)

**Files:**
- Modify: `agent/app/main.py` (`lifespan` line 18-25)
- Test: `agent/tests/test_lifecycle_lifespan.py`

**Interfaces:**
- Consumes: `lifecycle_scanner.run_scanner` (Task 7); `get_settings().lifecycle_enabled`.
- Produces: the app lifespan starts the scanner task when `lifecycle_enabled`, and cancels it cleanly on shutdown.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_lifecycle_lifespan.py`:

```python
from unittest.mock import AsyncMock

import pytest

from app.config import get_settings


async def test_scanner_started_when_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "lifecycle_enabled", True, raising=False)
    started = {"count": 0}

    async def fake_scanner():
        started["count"] += 1
        # Sleep forever so the lifespan has to cancel us.
        import asyncio

        await asyncio.Event().wait()

    from app.services import lifecycle_scanner

    monkeypatch.setattr(lifecycle_scanner, "run_scanner", fake_scanner)

    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        # Give the created task a chance to start.
        import asyncio

        await asyncio.sleep(0)
    assert started["count"] == 1


async def test_scanner_not_started_when_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "lifecycle_enabled", False, raising=False)
    started = {"count": 0}

    async def fake_scanner():
        started["count"] += 1

    from app.services import lifecycle_scanner

    monkeypatch.setattr(lifecycle_scanner, "run_scanner", fake_scanner)

    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        pass
    assert started["count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_lifecycle_lifespan.py -v`
Expected: FAIL (scanner never started; `started["count"] == 0` in the enabled case).

- [ ] **Step 3: Wire the lifespan**

In `agent/app/main.py`, update the imports and `lifespan`:

```python
import asyncio
import contextlib
import logging

from app.services import lifecycle_scanner

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    scanner_task: asyncio.Task | None = None
    if get_settings().lifecycle_enabled:
        scanner_task = asyncio.create_task(lifecycle_scanner.run_scanner())
    try:
        yield
    finally:
        if scanner_task is not None:
            scanner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scanner_task
        # httpx clients are lazily-constructed singletons (app.clients.deps) so
        # their lifecycle is owned end-to-end here.
        await aclose_clients()
```

(Keep the existing `from app.config import get_settings` import; it's already there.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_lifecycle_lifespan.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Full-suite regression**

Run: `cd agent && pytest`
Expected: PASS — all pre-existing tests plus the ~10 new lifecycle test files. Confirm no regressions (the feature is off by default, so existing behavior is unchanged).

- [ ] **Step 6: Commit**

```bash
git add agent/app/main.py agent/tests/test_lifecycle_lifespan.py
git commit -m "feat(agent): start/stop lifecycle scanner from app lifespan"
```

---

### Task 11 (optional, last): Bot auto-categorization on close

**Files:**
- Modify: `agent/app/clients/chatwoot.py` (add `add_labels`)
- Modify: `agent/app/services/lifecycle_scanner.py` (`_process_one`, the `close`/`resolve_timeout` branches)
- Test: `agent/tests/test_lifecycle_categorize.py`

**Interfaces:**
- Consumes: existing Gemini or the existing handoff classification (reuse `gemini.decide` or a simpler label heuristic — pick the lightest path already present).
- Produces: `ChatwootClient.add_labels(conversation_id, labels)` (GET current labels, POST the union — Chatwoot's label endpoint replaces the whole set); when `lifecycle_auto_categorize`, the scanner applies a `category_*` label at auto-close.

> **Note:** This task is gated behind `LIFECYCLE_AUTO_CATEGORIZE` (default off) and is sequenced last so it never blocks the core flow. Implement only if the classification source is confirmed; otherwise leave the config as documented-but-inert and note it in the completion summary. Because it depends on an implementation choice (which classifier), draft its TDD steps at execution time following the same pattern as Tasks 3 and 7 (write the failing client/scanner test, add the union-label method, wire the branch, verify, commit). Do not implement speculative classification logic without confirming the source.

---

## Manual / ops steps (post-implementation, need a running stack)

These are NOT code tasks — record them in the completion summary for the operator:

1. **Enable per-inbox business hours + out-of-office** in the Chatwoot UI for each inbox (WhatsApp, Social, Email): set working days/times, timezone (`Asia/Kuala_Lumpur`), and the SOP out-of-office message. Chatwoot posts that reply natively; the scanner only reads the hours.
2. **Subscribe the `conversation_created` webhook event** in the Chatwoot account webhook config (the generic `/webhooks/chatwoot` receiver) so the disclaimer/welcome fires.
3. **Set the env vars** per tenant: `LIFECYCLE_ENABLED=true` plus any threshold overrides (`deploy/tenants/<tenant>.env`).
4. **Smoke test** on `crm.localhost`: send a WhatsApp/web message, wait out the idle window, confirm warning → close → YES/NO → survey, and confirm an agent-resolved conversation triggers the agent survey.

---

## Self-Review

**Spec coverage** (each SOP-traceability row → task):
- AI disclaimer on first contact → Task 5 (`on_conversation_created`). ✅
- Bot replies in customer's language → Task 8 (`SYSTEM_PROMPT`). ✅
- Out-of-hours auto-reply → native Chatwoot (ops step 1); business-hours read → Task 4. ✅
- Idle 10-min warning → Task 7 (`warn`). ✅
- Auto-close after idle → Task 7 (`close`), threshold via Task 4. ✅
- "Is your case resolved? YES/NO" → Task 7 posts prompt; Task 6 parses reply. ✅
- Rating survey (AI) → Task 6 (`handle_lifecycle_reply`, AWAITING_RESOLUTION→AWAITING_SURVEY). ✅
- Rating survey (agent) → Task 6 (`on_human_resolved`) + Task 9 (router). ✅
- Unresolved → assign next business hour → Task 6 (NO branch reopens for agent). Business-hours "next business hour" tagging is left to the existing routing/native out-of-office; the reopen is covered. ✅
- Bot assigns case category → Task 11 (optional). ✅
- State visible to agents → `_mirror_state` custom attribute (Tasks 5-7). ✅
- Feature off = no-op → `lifecycle_enabled` gating in Tasks 8, 9, 10; defaults in Task 1. ✅

**Placeholder scan:** No TBD/TODO in Tasks 1-10. Task 11 is explicitly optional with a documented reason for deferring detailed steps (depends on a classifier choice) — not a placeholder in the core plan.

**Type consistency:** State constants (`ACTIVE`/`IDLE_WARNED`/`AWAITING_RESOLUTION`/`AWAITING_SURVEY`/`CLOSED`) defined once in Task 5 and referenced by string-equal literals in the store tests and by name in Tasks 6-10. Store signatures (`get_row`/`get_state`/`seed_active`/`transition`) defined in Task 2 and used consistently in Tasks 5-8. Client methods (`list_conversations`/`get_inbox`/`set_custom_attributes`) defined in Task 3 and used in Tasks 5-7. `decide_idle_action` signature defined and used within Task 7. Consistent.
