# SOP Completion (Categorization, Email Auto-Ack, Channel Ack-SLAs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the remaining Proton SOP items — (B) bot auto-categorization on bot-resolution, (C1) email once-per-thread auto-ack, (C2) per-channel agent-ack SLAs — all off by default and fail-open.

**Architecture:** B and C1 live in the `agent/` service (B: a Gemini classifier + a merge-safe Chatwoot `add_labels`, applied at the three bot-resolution points; C1: a channel branch in `on_conversation_created` guarded by structural per-conversation dedup). C2 extends the backend `sla.py` first-response breach check with a per-channel ack-threshold override map (minutes, to support sub-hour thresholds), reusing the existing scan/dedup/alert machinery.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 async, httpx, google-genai (agent), APScheduler (backend), pytest (`asyncio_mode=auto`, sqlite/aiosqlite + respx in agent; backend pytest needs `GOOGLE_API_KEY` set at collection).

## Global Constraints

- B + C1 code in `agent/` (run `cd agent && pytest`). C2 code in `backend/apps/backend` (run `cd backend/apps/backend && GOOGLE_API_KEY=dummy .venv/bin/pytest src/chatbot/features/chat/ -q` — the key must be set at collection, a known upstream quirk).
- Every sub-feature is OFF by default and byte-identical when off: `LIFECYCLE_AUTO_CATEGORIZE=false` (already exists) with empty `LIFECYCLE_CATEGORY_LABELS`; `EMAIL_AUTOACK_ENABLED=false`; empty `sla_ack_minutes_by_channel_json`.
- Fail-open: classify/label/auto-ack/channel-resolution errors are logged and skipped — they never break the close, the webhook, or the SLA scan. Never raise out of a background task / scan tick.
- New agent env → `agent/app/config.py` + `deploy/tenants/example.env`. New backend env → the backend `Settings` (`backend/apps/backend/src/chatbot/platform/config.py`) + `deploy/tenants/example.env`.
- TDD: failing test first, watch it fail, implement, watch it pass, commit. Frequent commits.
- Branch `dev-yuda` (do NOT branch or merge to main).
- Categorization fires only at genuine bot-resolution points (where the bot toggles the conversation `resolved`): lifecycle survey-complete, lifecycle YES-with-survey-disabled, and scanner `resolve_timeout`. NOT at the idle `close` step (that only moves to `AWAITING_RESOLUTION`).

---

### Task 1: Agent config (categorize taxonomy + email auto-ack)

**Files:**
- Modify: `agent/app/config.py` (add fields after the lifecycle block, near `lifecycle_auto_categorize`)
- Modify: `deploy/tenants/example.env` (after the lifecycle block)
- Test: `agent/tests/test_sop_config.py` (create)

**Interfaces:**
- Produces: `Settings.lifecycle_category_labels: str = ""`, `Settings.email_autoack_enabled: bool = False`, `Settings.email_autoack_template: str = <SOP default>`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_sop_config.py`:

```python
from app.config import Settings


def test_sop_completion_defaults():
    s = Settings()
    assert s.lifecycle_category_labels == ""
    assert s.email_autoack_enabled is False
    # Template default is the SOP email acknowledgement; check a stable anchor.
    assert "acknowledge receipt of your enquiry" in s.email_autoack_template
    assert "1300 888 877" in s.email_autoack_template
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_sop_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'lifecycle_category_labels'`.

- [ ] **Step 3: Add the fields**

In `agent/app/config.py`, immediately after `lifecycle_auto_categorize: bool = False`:

```python

    # SOP completion (B: categorization taxonomy; C1: email auto-ack).
    # Comma-separated category slugs the bot may assign on resolution (must
    # match the tenant's deployed taxonomy). Empty → auto-categorize no-ops.
    lifecycle_category_labels: str = ""
    # C1: email once-per-thread auto-acknowledgement.
    email_autoack_enabled: bool = False
    email_autoack_template: str = (
        "Dear Customer,\n"
        "Thank you for your email. This message serves to acknowledge receipt "
        "of your enquiry.\n"
        "We will respond within one (1) business day during our operating hours.\n"
        "For urgent matters, please contact our Call Centre at 1300 888 877.\n"
        "Operating Hours:\n"
        "Monday–Friday: 8:30 AM – 5:30 PM\n"
        "Saturday, Sunday & Public Holidays: 9:00 AM – 5:00 PM\n"
        "Thank you for your patience and understanding.\n\n"
        "Warm regards,\n"
        "Proton e.MAS Centre"
    )
```

- [ ] **Step 4: Document env vars**

In `deploy/tenants/example.env`, after the lifecycle block:

```bash

# --- SOP completion (agent service) ---
# Category slugs the bot may assign on resolution (comma-separated); must match
# the tenant's taxonomy. Empty disables auto-categorization even if
# LIFECYCLE_AUTO_CATEGORIZE=true.
LIFECYCLE_CATEGORY_LABELS=
# Email once-per-thread auto-acknowledgement.
EMAIL_AUTOACK_ENABLED=false
# EMAIL_AUTOACK_TEMPLATE has a built-in SOP default; override only to customize.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd agent && pytest tests/test_sop_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/app/config.py agent/tests/test_sop_config.py deploy/tenants/example.env
git commit -m "feat(agent): config for bot categorization taxonomy + email auto-ack (off)"
```

---

### Task 2: Category classifier (`categorize.py`)

**Files:**
- Create: `agent/app/services/categorize.py`
- Test: `agent/tests/test_categorize.py`

**Interfaces:**
- Consumes: `app.ai.gemini.generate` (existing, `async def generate(system_prompt, context, client=None) -> str`, raises on failure); `app.config.get_settings`.
- Produces:
  - `def _candidate_slugs(settings) -> list[str]` — parse `lifecycle_category_labels` CSV → cleaned slug list.
  - `async def classify_category(transcript: str, candidates: list[str]) -> str | None` — one Gemini call; returns a slug from `candidates` or `None` (fail-open on any error or non-matching output).

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_categorize.py`:

```python
from unittest.mock import AsyncMock

import pytest

from app.services import categorize


async def test_classify_returns_matching_slug(monkeypatch):
    monkeypatch.setattr(categorize.gemini, "generate", AsyncMock(return_value="  battery  "))
    result = await categorize.classify_category("car won't charge", ["battery", "sales"])
    assert result == "battery"


async def test_classify_rejects_non_candidate(monkeypatch):
    monkeypatch.setattr(categorize.gemini, "generate", AsyncMock(return_value="weather"))
    result = await categorize.classify_category("hello", ["battery", "sales"])
    assert result is None


async def test_classify_failopen_on_error(monkeypatch):
    monkeypatch.setattr(categorize.gemini, "generate", AsyncMock(side_effect=RuntimeError("boom")))
    result = await categorize.classify_category("hello", ["battery"])
    assert result is None


async def test_classify_empty_candidates_is_none():
    assert await categorize.classify_category("hello", []) is None


def test_candidate_slugs_parses_csv():
    from app.config import Settings

    s = Settings(lifecycle_category_labels="battery, sales ,,aftersales")
    assert categorize._candidate_slugs(s) == ["battery", "sales", "aftersales"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_categorize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.categorize'`.

- [ ] **Step 3: Write the module**

Create `agent/app/services/categorize.py`:

```python
"""Bot case-categorization via a single Gemini classify call.

When the bot resolves a conversation, the SOP requires it to assign the
appropriate case category. This picks one slug from the tenant's configured
taxonomy (``lifecycle_category_labels``) for the conversation transcript, using
the plain-text Gemini entry point. Fail-open: any error or an answer that is not
one of the candidates yields ``None`` (no label applied), never an exception —
categorization must never block the resolution it rides on.
"""

from __future__ import annotations

import logging

from app.ai import gemini
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a support ticket classifier. Given a customer conversation "
    "transcript and a list of allowed category slugs, reply with EXACTLY ONE "
    "slug from the list that best fits the conversation — no punctuation, no "
    "explanation, just the slug. If none fit, reply with the single word NONE."
)


def _candidate_slugs(settings: Settings) -> list[str]:
    raw = settings.lifecycle_category_labels or ""
    return [slug.strip() for slug in raw.split(",") if slug.strip()]


async def classify_category(transcript: str, candidates: list[str]) -> str | None:
    if not candidates:
        return None
    context = (
        f"Allowed category slugs: {', '.join(candidates)}\n\n"
        f"Transcript:\n{transcript}"
    )
    try:
        answer = await gemini.generate(_SYSTEM_PROMPT, context)
    except Exception:
        logger.exception("categorize: gemini classify failed; skipping")
        return None
    slug = (answer or "").strip()
    return slug if slug in candidates else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_categorize.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/categorize.py agent/tests/test_categorize.py
git commit -m "feat(agent): Gemini case-category classifier (fail-open)"
```

---

### Task 3: Chatwoot client `add_labels` (merge-safe)

**Files:**
- Modify: `agent/app/clients/chatwoot.py` (add method after `set_custom_attributes`)
- Test: `agent/tests/test_chatwoot_client_labels.py`

**Interfaces:**
- Produces on `ChatwootClient`: `async def add_labels(self, conversation_id: int, labels: list[str]) -> Any` — GET current labels, POST the union (Chatwoot's labels endpoint replaces the whole set). If GET fails/returns nothing, POST just `labels`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_chatwoot_client_labels.py`:

```python
import json

import httpx
import respx

from app.clients.chatwoot import ChatwootClient

BASE = "http://chatwoot-rails:3000"


def _client() -> ChatwootClient:
    return ChatwootClient(base_url=BASE, api_access_token="t", account_id=1)


@respx.mock
async def test_add_labels_unions_with_existing():
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/5/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["existing"]})
    )
    post = respx.post(f"{BASE}/api/v1/accounts/1/conversations/5/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["existing", "category_battery"]})
    )
    client = _client()
    await client.add_labels(5, ["category_battery"])
    body = json.loads(post.calls.last.request.content)
    assert set(body["labels"]) == {"existing", "category_battery"}
    await client.aclose()


@respx.mock
async def test_add_labels_posts_new_when_get_fails():
    respx.get(f"{BASE}/api/v1/accounts/1/conversations/9/labels").mock(
        return_value=httpx.Response(500)
    )
    post = respx.post(f"{BASE}/api/v1/accounts/1/conversations/9/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["category_sales"]})
    )
    client = _client()
    await client.add_labels(9, ["category_sales"])
    body = json.loads(post.calls.last.request.content)
    assert body["labels"] == ["category_sales"]
    await client.aclose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_chatwoot_client_labels.py -v`
Expected: FAIL with `AttributeError: 'ChatwootClient' object has no attribute 'add_labels'`.

- [ ] **Step 3: Add the method**

In `agent/app/clients/chatwoot.py`, after `set_custom_attributes`:

```python

    async def add_labels(self, conversation_id: int, labels: list[str]) -> Any:
        """Add labels without clobbering existing ones. Chatwoot's labels
        endpoint REPLACES the whole set, so we GET the current labels and POST
        the union. If the GET fails we fall back to posting just `labels`
        (adding at least the new ones rather than nothing)."""
        current: list[str] = []
        try:
            resp = await self._client.get(
                f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/labels"
            )
            resp.raise_for_status()
            data = resp.json()
            payload = data.get("payload") if isinstance(data, dict) else None
            if isinstance(payload, list):
                current = [str(x) for x in payload]
        except Exception:
            current = []
        union = list(dict.fromkeys([*current, *labels]))  # preserve order, dedup
        response = await self._client.post(
            f"/api/v1/accounts/{self.account_id}/conversations/{conversation_id}/labels",
            json={"labels": union},
        )
        response.raise_for_status()
        return response.json() if response.content else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && pytest tests/test_chatwoot_client_labels.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/app/clients/chatwoot.py agent/tests/test_chatwoot_client_labels.py
git commit -m "feat(agent): merge-safe Chatwoot add_labels"
```

---

### Task 4: `maybe_categorize` + wire into bot-resolution points

**Files:**
- Modify: `agent/app/services/categorize.py` (add `maybe_categorize`)
- Modify: `agent/app/services/lifecycle.py` (call at survey-complete close + YES-with-survey-disabled close)
- Modify: `agent/app/services/lifecycle_scanner.py` (call at `resolve_timeout`)
- Test: `agent/tests/test_categorize_wiring.py`

**Interfaces:**
- Consumes: `get_chatwoot_client` (`get_messages`, `add_labels`), `classify_category`, `_candidate_slugs`, `get_settings().lifecycle_auto_categorize`.
- Produces: `async def maybe_categorize(conversation_id: int) -> None` — gated (`lifecycle_auto_categorize` + non-empty candidates); fetches the transcript, classifies, applies `category_<slug>`; fully fail-open.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_categorize_wiring.py`:

```python
from unittest.mock import AsyncMock

import pytest

from app.services import categorize


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    client.get_messages.return_value = {"payload": [
        {"content": "my battery won't charge", "message_type": 0, "sender": {"name": "Cust"}},
    ]}
    monkeypatch.setattr(categorize, "get_chatwoot_client", lambda: client)
    return client


@pytest.fixture
def enabled(monkeypatch):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", True, raising=False)
    monkeypatch.setattr(s, "lifecycle_category_labels", "battery,sales", raising=False)


async def test_maybe_categorize_applies_label(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(return_value="battery"))
    await categorize.maybe_categorize(77)
    chatwoot.add_labels.assert_awaited_once_with(77, ["category_battery"])


async def test_maybe_categorize_noop_when_disabled(chatwoot, monkeypatch):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", False, raising=False)
    await categorize.maybe_categorize(77)
    chatwoot.add_labels.assert_not_awaited()


async def test_maybe_categorize_noop_when_no_category(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(return_value=None))
    await categorize.maybe_categorize(77)
    chatwoot.add_labels.assert_not_awaited()


async def test_maybe_categorize_failopen(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(side_effect=RuntimeError("x")))
    # Must not raise.
    await categorize.maybe_categorize(77)
    chatwoot.add_labels.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_categorize_wiring.py -v`
Expected: FAIL with `AttributeError: module 'app.services.categorize' has no attribute 'maybe_categorize'`.

- [ ] **Step 3: Add `maybe_categorize`**

Add to the imports at the top of `agent/app/services/categorize.py`:

```python
from app.clients.deps import get_chatwoot_client
```

Append to `agent/app/services/categorize.py`:

```python
def _transcript_from_messages(raw: object) -> str:
    if isinstance(raw, dict):
        messages = raw.get("payload") or []
    else:
        messages = raw or []
    lines: list[str] = []
    for message in messages[-20:]:
        if message.get("private"):
            continue
        sender = (message.get("sender") or {}).get("name", "Unknown")
        lines.append(f"{sender}: {message.get('content') or ''}")
    return "\n".join(lines)


async def maybe_categorize(conversation_id: int) -> None:
    """Classify + label a just-resolved conversation, gated + fail-open. Applies
    a single ``category_<slug>`` label. Any error is logged and swallowed — this
    never blocks the resolution it rides on."""
    settings = get_settings()
    if not settings.lifecycle_auto_categorize:
        return
    candidates = _candidate_slugs(settings)
    if not candidates:
        return
    try:
        chatwoot = get_chatwoot_client()
        raw = await chatwoot.get_messages(conversation_id)
        transcript = _transcript_from_messages(raw)
        slug = await classify_category(transcript, candidates)
        if slug is None:
            return
        await chatwoot.add_labels(conversation_id, [f"category_{slug}"])
    except Exception:
        logger.exception(
            "categorize: maybe_categorize failed for conversation %s", conversation_id
        )
```

- [ ] **Step 4: Wire into the lifecycle bot-resolution points**

In `agent/app/services/lifecycle.py`, add the import near the other `from app.services import ...`:

```python
from app.services import categorize
```

In `handle_lifecycle_reply`, the `AWAITING_SURVEY` branch resolves the conversation after recording the rating. Immediately BEFORE the `await _resolve(conversation_id)` call there, add:

```python
        await categorize.maybe_categorize(conversation_id)
```

And in the `AWAITING_RESOLUTION` → YES branch's survey-DISABLED path (the `else` that transitions CLOSED and calls `_resolve`), add the same line immediately before its `await _resolve(conversation_id)`:

```python
            await categorize.maybe_categorize(conversation_id)
```

(Do not add it to the idle `close` path or the NO/unclear branch — those are not bot-resolutions.)

- [ ] **Step 5: Wire into the scanner `resolve_timeout`**

In `agent/app/services/lifecycle_scanner.py`, add the import:

```python
from app.services import categorize
```

In `_process_one`, the `resolve_timeout` branch transitions CLOSED then calls `await lifecycle._resolve(conversation_id)`. Immediately BEFORE that `_resolve` call, add:

```python
        await categorize.maybe_categorize(conversation_id)
```

- [ ] **Step 6: Run tests + regression**

Run: `cd agent && pytest tests/test_categorize_wiring.py tests/test_lifecycle_replies.py tests/test_lifecycle_scanner.py -v`
Expected: PASS (new wiring tests + the existing lifecycle/scanner tests still green — when `lifecycle_auto_categorize` is False, `maybe_categorize` returns immediately so those tests are unchanged).
Then: `cd agent && pytest`
Expected: full suite passes.

- [ ] **Step 7: Commit**

```bash
git add agent/app/services/categorize.py agent/app/services/lifecycle.py agent/app/services/lifecycle_scanner.py agent/tests/test_categorize_wiring.py
git commit -m "feat(agent): auto-categorize at bot-resolution points (gated, fail-open)"
```

---

### Task 5: Email once-per-thread auto-ack

**Files:**
- Modify: `agent/app/services/lifecycle.py` (`on_conversation_created` channel branch + a channel resolver)
- Test: `agent/tests/test_email_autoack.py`

**Interfaces:**
- Consumes: `get_chatwoot_client().get_inbox`, `get_settings().email_autoack_enabled` / `.email_autoack_template`, existing `_post`/`seed_active`/`_mirror_state`.
- Produces: `on_conversation_created` posts the email auto-ack (instead of the AI disclaimer) once for an email-channel inbox when `email_autoack_enabled`; chat inboxes keep the existing disclaimer path.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_email_autoack.py`:

```python
from unittest.mock import AsyncMock

import pytest

from app.services import lifecycle, lifecycle_store


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: None)
    return client


@pytest.fixture
def email_on(monkeypatch):
    s = lifecycle.get_settings()
    monkeypatch.setattr(s, "email_autoack_enabled", True, raising=False)
    monkeypatch.setattr(s, "lifecycle_disclaimer_enabled", True, raising=False)


async def test_email_inbox_posts_autoack(chatwoot, email_on):
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Email"}
    await lifecycle.on_conversation_created({"id": 40, "inbox_id": 2, "channel": "Channel::Email"})
    assert await lifecycle_store.get_state(40) == "active"
    # posted the email auto-ack (not the AI disclaimer)
    args, kwargs = chatwoot.create_message.await_args
    assert args[0] == 40
    assert "acknowledge receipt of your enquiry" in args[1]


async def test_chat_inbox_still_posts_disclaimer(chatwoot, email_on):
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Whatsapp"}
    await lifecycle.on_conversation_created({"id": 41, "inbox_id": 3, "channel": "Channel::Whatsapp"})
    args, kwargs = chatwoot.create_message.await_args
    assert "artificial intelligence" in args[1].lower()  # AI disclaimer


async def test_email_autoack_disabled_falls_back_to_disclaimer(chatwoot, monkeypatch):
    s = lifecycle.get_settings()
    monkeypatch.setattr(s, "email_autoack_enabled", False, raising=False)
    monkeypatch.setattr(s, "lifecycle_disclaimer_enabled", True, raising=False)
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Email"}
    await lifecycle.on_conversation_created({"id": 42, "inbox_id": 2, "channel": "Channel::Email"})
    args, kwargs = chatwoot.create_message.await_args
    assert "artificial intelligence" in args[1].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_email_autoack.py -v`
Expected: FAIL (email inbox currently posts the AI disclaimer, not the auto-ack).

- [ ] **Step 3: Add a channel resolver + branch in `on_conversation_created`**

In `agent/app/services/lifecycle.py`, add a helper near `_welcome_text`:

```python
async def _inbox_channel(inbox_id: int | None) -> str | None:
    """Resolve the inbox channel_type (e.g. 'Channel::Email'), fail-open None."""
    if inbox_id is None:
        return None
    try:
        inbox = await get_chatwoot_client().get_inbox(inbox_id)
        return inbox.get("channel_type")
    except Exception:
        logger.debug("lifecycle: could not resolve channel for inbox %s", inbox_id, exc_info=True)
        return None
```

In `on_conversation_created`, replace the disclaimer-posting tail (the part that computes `text = await _welcome_text(inbox_id)` and posts it) with a channel-aware version:

```python
    settings = get_settings()
    channel_type = await _inbox_channel(inbox_id)

    # Email inbox: post the SOP once-per-thread auto-acknowledgement instead of
    # the AI disclaimer. Dedup is structural — conversation_created fires once
    # per conversation and seed_active only acts on a new row, so thread replies
    # and agent replies never re-trigger.
    if channel_type == "Channel::Email":
        if not settings.email_autoack_enabled:
            return
        text = settings.email_autoack_template
    else:
        if not settings.lifecycle_disclaimer_enabled:
            return
        text = await _welcome_text(inbox_id)

    if not text:
        return
    try:
        await get_chatwoot_client().create_message(conversation_id, text, private=False)
    except Exception:
        logger.exception(
            "lifecycle: failed to post disclaimer/auto-ack for conversation %s",
            conversation_id,
        )
```

(Keep the earlier `seed_active` + `_mirror_state(ACTIVE)` calls at the top of the function unchanged.)

- [ ] **Step 4: Run tests + regression**

Run: `cd agent && pytest tests/test_email_autoack.py tests/test_lifecycle_disclaimer.py -v`
Expected: PASS (new email tests + the existing disclaimer tests — the existing test uses no inbox/channel so `_inbox_channel` returns None → chat path → disclaimer, unchanged; if the existing disclaimer test stubs `get_inbox`, confirm it still resolves to the chat path).
Then: `cd agent && pytest`
Expected: full suite passes.

> Note: the existing `test_lifecycle_disclaimer.py` may not stub `get_inbox`; with the AsyncMock chatwoot fixture, `get_inbox` returns a MagicMock whose `.get("channel_type")` is not `"Channel::Email"`, so the chat/disclaimer path is taken — unchanged. If that test uses a non-AsyncMock stub, add `chatwoot.get_inbox.return_value = {"channel_type": "Channel::Api"}` to keep it on the disclaimer path.

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/lifecycle.py agent/tests/test_email_autoack.py
git commit -m "feat(agent): email once-per-thread auto-ack on conversation-created (gated)"
```

---

### Task 6: Backend config — per-channel ack minutes

**Files:**
- Modify: `backend/apps/backend/src/chatbot/platform/config.py` (add a `Settings` field)
- Modify: `backend/apps/backend/.env.example` (document it)
- Modify: `deploy/tenants/example.env` (document it in the backend section)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_channel_ack_sla.py` (create; config portion)

**Interfaces:**
- Produces: `Settings.sla_ack_minutes_by_channel_json: str = ""`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/backend/src/chatbot/features/chat/test_channel_ack_sla.py`:

```python
from chatbot.platform.config import Settings


def test_ack_minutes_config_default_empty():
    # Construct with the minimum required env already provided by the test env.
    s = Settings()
    assert s.sla_ack_minutes_by_channel_json == ""
```

> Before writing, read an existing backend test that constructs `Settings()` (e.g. a `test_sla*.py` or `conftest`) to mirror how required env/config is provided in this suite — reuse that fixture/pattern so `Settings()` constructs. If the suite constructs `Settings` via a fixture, use it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy .venv/bin/pytest src/chatbot/features/chat/test_channel_ack_sla.py -v`
Expected: FAIL with `AttributeError` on `sla_ack_minutes_by_channel_json`.

- [ ] **Step 3: Add the field**

In `backend/apps/backend/src/chatbot/platform/config.py`, add near the other `sla_*` settings:

```python
    # Per-channel first-response (ack) SLA overrides, in MINUTES, as a JSON
    # object keyed by short channel name, e.g.
    # {"whatsapp": 2, "call": 0.333, "facebook": 120, "instagram": 120, "email": 240}.
    # Empty → the global sla_response_hours applies to every channel.
    sla_ack_minutes_by_channel_json: str = ""
```

- [ ] **Step 4: Document env vars**

In `backend/apps/backend/.env.example` (near other SLA vars) and in the backend section of `deploy/tenants/example.env`:

```bash
# Per-channel first-response (ack) SLA overrides in MINUTES (JSON object).
# Empty = global SLA_RESPONSE_HOURS for all channels. Example:
# {"whatsapp":2,"call":0.333,"facebook":120,"instagram":120,"email":240}
SLA_ACK_MINUTES_BY_CHANNEL_JSON=
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy .venv/bin/pytest src/chatbot/features/chat/test_channel_ack_sla.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/platform/config.py backend/apps/backend/.env.example deploy/tenants/example.env backend/apps/backend/src/chatbot/features/chat/test_channel_ack_sla.py
git commit -m "feat(backend): config for per-channel ack SLA overrides (minutes)"
```

---

### Task 7: Per-channel ack threshold in `sla.py`

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla.py` (`scan_conversations` NO_RESPONSE_BREACH block + helpers)
- Test: `backend/apps/backend/src/chatbot/features/chat/test_channel_ack_sla.py` (extend)

**Interfaces:**
- Consumes: `Settings.sla_ack_minutes_by_channel_json`; the conversation dict (channel field).
- Produces:
  - `def _parse_ack_minutes(raw: str) -> dict[str, float]` — parse the JSON map; invalid/empty → `{}`.
  - `def _conversation_channel(conv: dict) -> str | None` — normalize the Chatwoot channel to a short key (`whatsapp`/`call`/`facebook`/`instagram`/`email`), or `None`.
  - `scan_conversations` uses a per-conversation ack threshold: the channel override (minutes×60) when present, else the global `sla_response_hours×3600`.

- [ ] **Step 1: Write the failing test (extend the file)**

Append to `backend/apps/backend/src/chatbot/features/chat/test_channel_ack_sla.py`:

```python
import json

from chatbot.features.chat import sla


def test_parse_ack_minutes_valid_and_invalid():
    assert sla._parse_ack_minutes('{"whatsapp": 2, "email": 240}') == {"whatsapp": 2.0, "email": 240.0}
    assert sla._parse_ack_minutes("") == {}
    assert sla._parse_ack_minutes("not json") == {}


def test_conversation_channel_normalization():
    assert sla._conversation_channel({"meta": {"channel": "Channel::Whatsapp"}}) == "whatsapp"
    assert sla._conversation_channel({"meta": {"channel": "Channel::Email"}}) == "email"
    assert sla._conversation_channel({"meta": {"channel": "Channel::FacebookPage"}}) == "facebook"
    assert sla._conversation_channel({"meta": {"channel": "Channel::Instagram"}}) == "instagram"
    assert sla._conversation_channel({}) is None
```

Then a behavioral test that a WhatsApp conversation breaches at 2 minutes while the global response SLA is hours. Model it on the existing SLA scan tests (reuse their `audit` fake, `fetch` injection, `now` clock, and `_fire`/alert wiring — READ an existing `test_sla*.py` in this directory first and mirror its fixtures exactly):

```python
# Pseudocode shape — adapt to the existing test's fixtures/imports:
# async def test_whatsapp_acks_breach_at_2_minutes(<existing fixtures>):
#     settings = <settings with sla_response_hours=8, sla_ack_minutes_by_channel_json='{"whatsapp":2}'>
#     conv = {"id": 1, "status": "open", "created_at": <now - 3 minutes>,
#             "meta": {"channel": "Channel::Whatsapp"}}  # no first_reply_created_at
#     fired = await sla.scan_conversations(settings, audit, now=<now>, fetch=lambda s: [conv])
#     assert any(e.to_state == sla.NO_RESPONSE_BREACH for e in fired)
#     # And a control: same conv on a channel with no override does NOT breach at 3 min
#     conv2 = {**conv, "id": 2, "meta": {"channel": "Channel::Api"}}
#     fired2 = await sla.scan_conversations(settings, audit, now=<now>, fetch=lambda s: [conv2])
#     assert not any(e.to_state == sla.NO_RESPONSE_BREACH for e in fired2)
```

Write it out concretely using the real fixtures from the neighboring SLA test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy .venv/bin/pytest src/chatbot/features/chat/test_channel_ack_sla.py -v`
Expected: FAIL — `_parse_ack_minutes`/`_conversation_channel` don't exist; the behavioral test doesn't breach at 2 min because the global hours threshold is used.

- [ ] **Step 3: Implement**

In `backend/apps/backend/src/chatbot/features/chat/sla.py`, add near the top (after imports):

```python
import json as _json

_CHANNEL_MAP = {
    "Channel::Whatsapp": "whatsapp",
    "Channel::TwilioSms": "whatsapp",
    "Channel::Voice": "call",
    "Channel::Api": "call",  # voice/IVR bridged via the API channel; adjust per deployment
    "Channel::FacebookPage": "facebook",
    "Channel::Instagram": "instagram",
    "Channel::Email": "email",
}


def _parse_ack_minutes(raw: str) -> dict[str, float]:
    if not raw:
        return {}
    try:
        data = _json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in data.items():
        try:
            out[str(key)] = float(value)
        except (ValueError, TypeError):
            continue
    return out


def _conversation_channel(conv: dict[str, Any]) -> str | None:
    meta = conv.get("meta") if isinstance(conv.get("meta"), dict) else {}
    raw = meta.get("channel") or conv.get("channel")
    if not raw:
        return None
    return _CHANNEL_MAP.get(str(raw))
```

In `scan_conversations`, after `response_threshold = settings.sla_response_hours * _SECONDS_PER_HOUR` (the global default), parse the map once:

```python
    ack_minutes_by_channel = _parse_ack_minutes(settings.sla_ack_minutes_by_channel_json)
```

Inside the per-conversation loop, compute a per-conversation ack threshold before the NO_RESPONSE_BREACH check:

```python
        channel = _conversation_channel(conv)
        ack_override = ack_minutes_by_channel.get(channel) if channel else None
        conv_response_threshold = (
            ack_override * 60 if ack_override is not None else response_threshold
        )
```

Change the NO_RESPONSE_BREACH condition to use `conv_response_threshold` instead of `response_threshold`, and update its remark to reflect the effective threshold:

```python
        if (
            status == "open"
            and not _has_first_agent_response(conv, prior)
            and age > conv_response_threshold
            and NO_RESPONSE_BREACH not in prior
        ):
            entry = await _fire(
                audit,
                ticket_id=ticket_id,
                session_id=session_id,
                to_state=NO_RESPONSE_BREACH,
                remark=(
                    f"No first agent response after "
                    f"{conv_response_threshold / 60:.1f}m (age {age / 60:.1f}m)"
                ),
                clock=clock,
                alert=alert,
            )
            fired.append(entry)
```

- [ ] **Step 4: Run tests + regression**

Run: `cd backend/apps/backend && GOOGLE_API_KEY=dummy .venv/bin/pytest src/chatbot/features/chat/test_channel_ack_sla.py src/chatbot/features/chat/test_sla.py -v`
(Adjust the second path to the actual existing SLA test filename.)
Expected: PASS — new tests plus the existing SLA tests unchanged (empty override map → `conv_response_threshold == response_threshold`, byte-identical behavior).
Then the fuller chat suite: `cd backend/apps/backend && GOOGLE_API_KEY=dummy .venv/bin/pytest src/chatbot/features/chat/ -q`
Expected: PASS.

- [ ] **Step 5: Ruff + commit**

```bash
cd backend/apps/backend && .venv/bin/ruff check src/chatbot/features/chat/sla.py --fix && cd -
git add backend/apps/backend/src/chatbot/features/chat/sla.py backend/apps/backend/src/chatbot/features/chat/test_channel_ack_sla.py
git commit -m "feat(backend): per-channel first-response ack SLA overrides"
```

---

## Manual / ops steps (post-implementation)

1. Set `LIFECYCLE_AUTO_CATEGORIZE=true` + `LIFECYCLE_CATEGORY_LABELS=<tenant slugs>` to enable categorization; verify the slugs exist in the tenant's taxonomy.
2. Set `EMAIL_AUTOACK_ENABLED=true` (+ subscribe `conversation_created` if not already, per the lifecycle feature) to enable the email auto-ack.
3. Set `SLA_ACK_MINUTES_BY_CHANNEL_JSON` on the backend (e.g. `{"whatsapp":2,"call":0.333,"facebook":120,"instagram":120,"email":240}`) and confirm `Channel::Api`→`call` mapping matches how the tenant's voice/IVR channel is wired (adjust `_CHANNEL_MAP` if the deployment uses a different channel_type for calls).
4. Smoke: bot-resolve a conversation → a `category_*` label appears; email a new thread → one auto-ack, none on replies; leave a WhatsApp conversation unanswered 2 min → `SLA_BREACH_NO_RESPONSE` fires.

## Self-Review

**Spec coverage:**
- B categorizer → Task 2 (`classify_category`) + Task 4 (`maybe_categorize` + wiring). ✅
- B merge-safe labels → Task 3 (`add_labels`). ✅
- C1 email auto-ack + structural dedup → Task 5. ✅
- C2 per-channel ack thresholds + config → Tasks 6-7. ✅
- Off-by-default / fail-open → gating in every task; config defaults in Tasks 1 & 6. ✅

**Placeholder scan:** Task 7's behavioral SLA test is intentionally specified as "mirror the existing SLA test fixtures" with a concrete pseudocode shape, because the exact fixture names live in the neighboring test file the implementer must read — this is a read-then-write instruction, not a placeholder. All other steps carry complete code.

**Type consistency:** `classify_category(transcript, candidates)` and `_candidate_slugs(settings)` defined in Task 2, used in Task 4. `add_labels(conversation_id, labels)` defined in Task 3, used in Task 4. `maybe_categorize(conversation_id)` defined in Task 4, called in Tasks 4 (lifecycle + scanner). `_parse_ack_minutes`/`_conversation_channel` defined and used within Task 7. `sla_ack_minutes_by_channel_json` defined in Task 6, used in Task 7. Consistent.
