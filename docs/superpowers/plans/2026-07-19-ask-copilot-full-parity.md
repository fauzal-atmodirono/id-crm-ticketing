# Ask Copilot (Full Parity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current one-shot `/assist/ask` with a real, multi-turn **Ask Copilot** — an agent-facing chat panel in Chatwoot's conversation sidebar, backed by a tool-calling Gemini agent that can read the conversation (including private notes), the contact's details/custom-attributes, the contact's past conversations, and the knowledge base, and whose answers can be inserted into the reply box.

**Architecture:** Two codebases. In **`proton-conversational-ai`** we add a stateless multi-turn `/assist/copilot` endpoint: the frontend owns the chat thread and sends it each turn; the backend runs a bounded Gemini **function-calling loop** whose tools are read-only Chatwoot lookups (new `ChatwootContextClient`, reusing the existing `chatwoot_*` settings) plus the existing `KnowledgePort.search_kb`. In **`id-crm-ticketing`** we add frontend patch `0005` that reuses Chatwoot's presentational `Copilot.vue` thread panel wrapped in our own `ProtonCopilotContainer.vue` (our data layer, not Captain's), gated by a new `copilot` Proton feature flag, opened from the reply-editor ✨ "Ask Copilot" item, with an "Insert into reply" action.

**Tech Stack:** FastAPI + Pydantic v2, `google-genai` (Gemini 2.5 Flash) function-calling, `httpx.AsyncClient`, pytest-asyncio + respx, Vue 3 (Options + `<script setup>`), Chatwoot v4.15.1 patch-set (Docker `git apply` pipeline), SSE for streaming.

## Global Constraints

- Backend Python ≥ 3.12, FastAPI ≥ 0.115, Pydantic ≥ 2.8, `google-genai ≥ 0.1.1`. **No new Python dependencies** — everything needed (`google-genai`, `httpx`, `google-cloud-firestore`) is already in `pyproject.toml`.
- `pytest` root: `proton-conversational-ai/apps/backend/`; `asyncio_mode = "auto"`; tests are hermetic — `conftest.py` disables `.env` loading, construct `Settings(...)` in-test, mock `genai_client.aio.models.generate_content` with `AsyncMock`, stub Chatwoot HTTP with `respx`.
- All new backend endpoints are guarded by `x-api-key` via constant-time `hmac.compare_digest` against `settings.proton_backend_key`; empty key → **503** (matches the existing `/assist/*` guard in `features/assist/router.py`).
- Reuse existing settings verbatim — `chatwoot_api_url`, `chatwoot_api_token`, `chatwoot_account_id`, `chatwoot_enabled`, `proton_backend_key`, `assist_gemini_model`. Any new setting must be added to `src/chatbot/platform/config.py` **and** `apps/backend/.env.example`, names matching env vars verbatim.
- Chatwoot fork constraints (from `docs/superpowers/plans/2026-07-18-phase0-fork-foundation.md`): upstream pin **v4.15.1**; **MIT-community only** (never touch `/enterprise` or Captain stores/APIs); patches are exported `git diff` files under `deploy/chatwoot-fork/patches/` and applied by the Dockerfile's `for p in *.patch` glob; build off-VM; frontend imports use the `dashboard/composables` alias (there is no `composables` alias).
- Feature gating: the panel is active only when `useProtonConfig().hasFeature('copilot')` is true (i.e. `PROTON_FEATURES` includes `copilot`). With the flag off, nothing renders and no requests fire.
- The backend is **stateless per request**: the client sends the full copilot `thread`; the server persists nothing. (Server-side thread persistence is explicitly out of scope — see Non-Goals.)

### Non-Goals (explicitly deferred)

- Server-side thread persistence (Firestore). Threads live in the browser for now.
- Multi-assistant selection / per-inbox assistants (a Captain concept). One implicit Proton assistant.
- Writing to Chatwoot from Copilot (creating notes/labels). Copilot is read-only + "insert into reply" only.

---

## File Structure

### Repo: `proton-conversational-ai/apps/backend`

| Path | Action | Responsibility |
|---|---|---|
| `src/chatbot/features/assist/chatwoot_context.py` | Create | `ChatwootContextClient` — read-only Chatwoot lookups (transcript incl. private notes, contact + custom attributes, contact's other conversations) over `httpx` |
| `src/chatbot/features/assist/test_chatwoot_context.py` | Create | respx tests for each lookup + the `chatwoot_enabled=False` short-circuit |
| `src/chatbot/features/assist/copilot_tools.py` | Create | Gemini tool schemas + a `ToolExecutor` mapping tool calls → `ChatwootContextClient` / `KnowledgePort` |
| `src/chatbot/features/assist/test_copilot_tools.py` | Create | Tests: schema shape, executor dispatch, unknown-tool handling |
| `src/chatbot/features/assist/copilot_router.py` | Create | `build_copilot_router(...)` — `POST /assist/copilot` (and `/assist/copilot/stream`), the bounded function-calling loop |
| `src/chatbot/features/assist/test_copilot_router.py` | Create | Tests: auth guard, single-turn answer, tool-call→final loop, iteration cap, 503 |
| `src/chatbot/platform/config.py` | Modify | Add `copilot_gemini_model`, `copilot_max_tool_iterations` |
| `src/chatbot/platform/test_config_copilot.py` | Create | Defaults + env-override tests for the two new fields |
| `src/chatbot/main.py` | Modify | `_wire_copilot(app, knowledge_port, settings)` + call it in `bootstrap_application()` |
| `apps/backend/.env.example` | Modify | Document the two new vars |

### Repo: `id-crm-ticketing`

| Path | Action | Responsibility |
|---|---|---|
| `deploy/chatwoot-fork/patches/0005-ask-copilot-panel.patch` | Create (engineer-authored `git diff`) | Adds `protonCopilot.js` API client, `ProtonCopilotContainer.vue`, panel mount + launcher in `ConversationView.vue`, `is_proton_copilot_open` uiSetting toggle, repurposes the ✨ `ask_copilot` menu item to open the panel, i18n strings |
| `deploy/tenants/example.env` | Modify | Note `copilot` as a `PROTON_FEATURES` value |
| `deploy/chatwoot-fork/README.md` | Modify | Add patch 0005 to the patch list + upgrade notes |

---

## Tasks

### Task 1: Backend config — copilot settings

**Files:**
- Modify: `src/chatbot/platform/config.py`
- Create: `src/chatbot/platform/test_config_copilot.py`

**Interfaces:**
- Consumes: existing `Settings(BaseSettings)` (fields `proton_backend_key`, `assist_gemini_model`, `chatwoot_*` already present)
- Produces: `Settings.copilot_gemini_model: str` (default `"gemini-2.5-flash"`), `Settings.copilot_max_tool_iterations: int` (default `5`)

- [ ] **Step 1: Write the failing test**

Create `src/chatbot/platform/test_config_copilot.py`:

```python
import pytest
from chatbot.platform.config import Settings


def test_copilot_settings_defaults() -> None:
    s = Settings()
    assert s.copilot_gemini_model == "gemini-2.5-flash"
    assert s.copilot_max_tool_iterations == 5


def test_copilot_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPILOT_GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("COPILOT_MAX_TOOL_ITERATIONS", "8")
    s = Settings()
    assert s.copilot_gemini_model == "gemini-2.5-pro"
    assert s.copilot_max_tool_iterations == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend && python -m pytest src/chatbot/platform/test_config_copilot.py -v`
Expected: FAIL — `Settings` has no `copilot_gemini_model`.

- [ ] **Step 3: Add the two fields**

In `src/chatbot/platform/config.py`, immediately after the `assist_cors_origins` field (end of the "Proton assist endpoints" block):

```python
    # --- Ask Copilot (multi-turn agent) ------------------------------------
    # Model for the copilot tool-calling loop; defaults to the assist model.
    copilot_gemini_model: str = "gemini-2.5-flash"
    # Hard cap on tool-call rounds per copilot turn, so a misbehaving model
    # cannot loop forever. Each round is one Gemini call.
    copilot_max_tool_iterations: int = 5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest src/chatbot/platform/test_config_copilot.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Document env vars**

In `apps/backend/.env.example`, under the assist section, add:

```dotenv
# --- Ask Copilot -------------------------------------------------------------
COPILOT_GEMINI_MODEL=gemini-2.5-flash
COPILOT_MAX_TOOL_ITERATIONS=5
```

- [ ] **Step 6: Commit**

```bash
git -C /Users/yudaadipratama/Archive/proton-conversational-ai add \
  apps/backend/src/chatbot/platform/config.py \
  apps/backend/src/chatbot/platform/test_config_copilot.py \
  apps/backend/.env.example
git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
  -m "feat(config): add copilot_gemini_model + copilot_max_tool_iterations"
```

---

### Task 2: Backend — `ChatwootContextClient` (read-only lookups)

**Files:**
- Create: `src/chatbot/features/assist/chatwoot_context.py`
- Create: `src/chatbot/features/assist/test_chatwoot_context.py`

**Interfaces:**
- Consumes: `Settings` (`chatwoot_api_url`, `chatwoot_api_token`, `chatwoot_account_id`, `chatwoot_enabled`)
- Produces:
  - `class ChatwootContextClient` with:
    - `async def get_transcript(conversation_id: str) -> list[dict]` → `[{"sender": "customer"|"agent", "private": bool, "content": str}]` (oldest → newest; includes private notes)
    - `async def get_contact(conversation_id: str) -> dict` → `{"id": int, "name": str, "email": str, "phone": str, "custom_attributes": dict}`
    - `async def list_contact_conversations(conversation_id: str) -> list[dict]` → `[{"id": int, "status": str, "last_activity_at": int, "labels": list[str]}]` (excludes the current conversation)
  - Every method returns `[]`/`{}` when `chatwoot_enabled` is False or the HTTP call fails (never raises — mirrors `ChatwootAdapter._request`).

> **Chatwoot API reference** (base = `{chatwoot_api_url}/api/v1/accounts/{chatwoot_account_id}`; header `api_access_token: <token>`):
> - `GET /conversations/{id}` → `messages[]` each has `content`, `message_type` (0=incoming/customer, 1=outgoing/agent, 2=activity), `private` (bool); and `meta.sender` (the contact) with `id`.
> - `GET /contacts/{contact_id}` → `payload` with `name`, `email`, `phone_number`, `custom_attributes`.
> - `GET /contacts/{contact_id}/conversations` → `payload[]` of conversations with `id`, `status`, `last_activity_at`, `labels`.

- [ ] **Step 1: Write the failing tests**

Create `src/chatbot/features/assist/test_chatwoot_context.py`:

```python
from __future__ import annotations

import httpx
import respx

from chatbot.features.assist.chatwoot_context import ChatwootContextClient
from chatbot.platform.config import Settings


def _settings() -> Settings:
    return Settings(
        chatwoot_api_url="http://cw.local",
        chatwoot_api_token="tok",
        chatwoot_account_id=1,
        chatwoot_enabled=True,
    )


def _base() -> str:
    return "http://cw.local/api/v1/accounts/1"


@respx.mock
async def test_get_transcript_includes_private_notes() -> None:
    respx.get(f"{_base()}/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "meta": {"sender": {"id": 7}},
                "messages": [
                    {"content": "my battery died", "message_type": 0, "private": False},
                    {"content": "internal: check warranty", "message_type": 1, "private": True},
                    {"content": "we will help", "message_type": 1, "private": False},
                    {"content": "conversation opened", "message_type": 2, "private": False},
                ],
            },
        )
    )
    client = ChatwootContextClient(_settings())
    turns = await client.get_transcript("42")
    # activity messages (type 2) are dropped; private notes kept and flagged
    assert turns == [
        {"sender": "customer", "private": False, "content": "my battery died"},
        {"sender": "agent", "private": True, "content": "internal: check warranty"},
        {"sender": "agent", "private": False, "content": "we will help"},
    ]


@respx.mock
async def test_get_contact_returns_attributes() -> None:
    respx.get(f"{_base()}/conversations/42").mock(
        return_value=httpx.Response(200, json={"meta": {"sender": {"id": 7}}, "messages": []})
    )
    respx.get(f"{_base()}/contacts/7").mock(
        return_value=httpx.Response(
            200,
            json={"payload": {
                "id": 7, "name": "Aiman", "email": "a@x.my", "phone_number": "+60",
                "custom_attributes": {"plan": "premium"},
            }},
        )
    )
    client = ChatwootContextClient(_settings())
    contact = await client.get_contact("42")
    assert contact["name"] == "Aiman"
    assert contact["custom_attributes"] == {"plan": "premium"}


@respx.mock
async def test_list_contact_conversations_excludes_current() -> None:
    respx.get(f"{_base()}/conversations/42").mock(
        return_value=httpx.Response(200, json={"meta": {"sender": {"id": 7}}, "messages": []})
    )
    respx.get(f"{_base()}/contacts/7/conversations").mock(
        return_value=httpx.Response(
            200,
            json={"payload": [
                {"id": 42, "status": "open", "last_activity_at": 100, "labels": []},
                {"id": 30, "status": "resolved", "last_activity_at": 90, "labels": ["billing"]},
            ]},
        )
    )
    client = ChatwootContextClient(_settings())
    convs = await client.list_contact_conversations("42")
    assert [c["id"] for c in convs] == [30]


async def test_disabled_short_circuits() -> None:
    s = Settings(chatwoot_enabled=False)
    client = ChatwootContextClient(s)
    assert await client.get_transcript("42") == []
    assert await client.get_contact("42") == {}
    assert await client.list_contact_conversations("42") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/chatbot/features/assist/test_chatwoot_context.py -v`
Expected: FAIL — `ModuleNotFoundError: chatbot.features.assist.chatwoot_context`.

- [ ] **Step 3: Implement `chatwoot_context.py`**

Create `src/chatbot/features/assist/chatwoot_context.py`:

```python
"""Read-only Chatwoot lookups used as Copilot tools.

Deliberately separate from ChatwootAdapter (which owns write/escalation flows):
this client only reads, never raises, and returns empty structures on failure or
when Chatwoot is disabled — so a tool call can never crash a Copilot turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_MESSAGE_TYPE_CUSTOMER = 0
_MESSAGE_TYPE_AGENT = 1
_MESSAGE_TYPE_ACTIVITY = 2


class ChatwootContextClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        s = self._settings
        return f"{s.chatwoot_api_url}/api/v1/accounts/{s.chatwoot_account_id}"

    async def _get(self, path: str) -> Any:
        if not self._settings.chatwoot_enabled:
            return None
        token = self._settings.chatwoot_api_token
        headers = {"api_access_token": token, "Api-Access-Token": token}
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    f"{self._base()}{path}", headers=headers, timeout=10.0
                )
                res.raise_for_status()
                return res.json() if res.content else None
        except Exception as e:  # noqa: BLE001 — never raise into a tool call
            _log.error("chatwoot_context_get_failed", path=path, error=str(e))
            return None

    async def _sender_id(self, conversation_id: str) -> int | None:
        data = await self._get(f"/conversations/{conversation_id}")
        if not data:
            return None
        return (data.get("meta") or {}).get("sender", {}).get("id")

    async def get_transcript(self, conversation_id: str) -> list[dict[str, Any]]:
        data = await self._get(f"/conversations/{conversation_id}")
        if not data:
            return []
        turns: list[dict[str, Any]] = []
        for m in data.get("messages", []):
            mtype = m.get("message_type")
            if mtype == _MESSAGE_TYPE_ACTIVITY:
                continue
            content = (m.get("content") or "").strip()
            if not content:
                continue
            turns.append(
                {
                    "sender": "customer" if mtype == _MESSAGE_TYPE_CUSTOMER else "agent",
                    "private": bool(m.get("private")),
                    "content": content,
                }
            )
        return turns

    async def get_contact(self, conversation_id: str) -> dict[str, Any]:
        sender_id = await self._sender_id(conversation_id)
        if sender_id is None:
            return {}
        data = await self._get(f"/contacts/{sender_id}")
        p = (data or {}).get("payload") or {}
        if not p:
            return {}
        return {
            "id": p.get("id"),
            "name": p.get("name"),
            "email": p.get("email"),
            "phone": p.get("phone_number"),
            "custom_attributes": p.get("custom_attributes") or {},
        }

    async def list_contact_conversations(
        self, conversation_id: str
    ) -> list[dict[str, Any]]:
        sender_id = await self._sender_id(conversation_id)
        if sender_id is None:
            return []
        data = await self._get(f"/contacts/{sender_id}/conversations")
        payload = (data or {}).get("payload") or []
        current = str(conversation_id)
        out: list[dict[str, Any]] = []
        for c in payload:
            if str(c.get("id")) == current:
                continue
            out.append(
                {
                    "id": c.get("id"),
                    "status": c.get("status"),
                    "last_activity_at": c.get("last_activity_at"),
                    "labels": c.get("labels") or [],
                }
            )
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/chatbot/features/assist/test_chatwoot_context.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git -C /Users/yudaadipratama/Archive/proton-conversational-ai add \
  apps/backend/src/chatbot/features/assist/chatwoot_context.py \
  apps/backend/src/chatbot/features/assist/test_chatwoot_context.py
git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
  -m "feat(copilot): read-only ChatwootContextClient for copilot tools"
```

---

### Task 3: Backend — copilot tool schemas + executor

**Files:**
- Create: `src/chatbot/features/assist/copilot_tools.py`
- Create: `src/chatbot/features/assist/test_copilot_tools.py`

**Interfaces:**
- Consumes: `ChatwootContextClient` (Task 2), `KnowledgePort.search_kb(query, limit)` (existing, `features/chat/ports.py`)
- Produces:
  - `COPILOT_TOOLS: list[dict]` — google-genai function declarations for: `get_conversation_transcript`, `get_contact_details`, `list_past_conversations`, `search_knowledge_base`
  - `class ToolExecutor` with `async def run(name: str, args: dict) -> dict` — dispatches by tool name; returns a JSON-serializable `dict`; unknown tool → `{"error": "unknown tool: <name>"}`

- [ ] **Step 1: Write the failing tests**

Create `src/chatbot/features/assist/test_copilot_tools.py`:

```python
from __future__ import annotations

from chatbot.features.assist.copilot_tools import COPILOT_TOOLS, ToolExecutor
from chatbot.features.chat.models import KbArticle


class _FakeCtx:
    async def get_transcript(self, conversation_id: str):
        return [{"sender": "customer", "private": False, "content": "hi"}]

    async def get_contact(self, conversation_id: str):
        return {"name": "Aiman", "custom_attributes": {"plan": "premium"}}

    async def list_contact_conversations(self, conversation_id: str):
        return [{"id": 30, "status": "resolved", "labels": ["billing"]}]


class _FakeKb:
    async def search_kb(self, query: str, limit: int = 3):
        return [KbArticle(title="Warranty", content="12 months", url="http://faq/1")]


def test_tool_schemas_have_four_named_functions() -> None:
    names = {t["name"] for t in COPILOT_TOOLS}
    assert names == {
        "get_conversation_transcript",
        "get_contact_details",
        "list_past_conversations",
        "search_knowledge_base",
    }
    for t in COPILOT_TOOLS:
        assert "description" in t and "parameters" in t


async def test_executor_dispatches_transcript() -> None:
    ex = ToolExecutor(_FakeCtx(), _FakeKb(), conversation_id="42")
    out = await ex.run("get_conversation_transcript", {})
    assert out["turns"][0]["content"] == "hi"


async def test_executor_dispatches_kb_search() -> None:
    ex = ToolExecutor(_FakeCtx(), _FakeKb(), conversation_id="42")
    out = await ex.run("search_knowledge_base", {"query": "warranty"})
    assert out["articles"][0]["title"] == "Warranty"


async def test_executor_unknown_tool() -> None:
    ex = ToolExecutor(_FakeCtx(), _FakeKb(), conversation_id="42")
    out = await ex.run("nope", {})
    assert "error" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/chatbot/features/assist/test_copilot_tools.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `copilot_tools.py`**

Create `src/chatbot/features/assist/copilot_tools.py`:

```python
"""Copilot tool declarations (for Gemini function calling) + their executor.

The tools are read-only lookups. `conversation_id` is fixed per Copilot turn and
injected by the executor, so the model never has to pass it — this prevents the
model from probing arbitrary conversations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chatbot.features.assist.chatwoot_context import ChatwootContextClient
    from chatbot.features.chat.ports import KnowledgePort

COPILOT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_conversation_transcript",
        "description": (
            "Return the full transcript of the CURRENT conversation, including "
            "internal private notes (flagged private=true). Use to see what the "
            "customer said and what the team has already discussed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_contact_details",
        "description": (
            "Return the current customer's profile and custom attributes "
            "(e.g. plan, customer type, tags)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_past_conversations",
        "description": (
            "List this customer's OTHER (past) conversations with status and "
            "labels. Use to check history before answering."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_knowledge_base",
        "description": "Search the product/FAQ knowledge base for grounding facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query"},
                "limit": {"type": "integer", "description": "max hits (1-10)"},
            },
            "required": ["query"],
        },
    },
]


class ToolExecutor:
    def __init__(
        self,
        context: ChatwootContextClient,
        knowledge: KnowledgePort,
        conversation_id: str,
    ) -> None:
        self._ctx = context
        self._kb = knowledge
        self._conv_id = conversation_id

    async def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "get_conversation_transcript":
            return {"turns": await self._ctx.get_transcript(self._conv_id)}
        if name == "get_contact_details":
            return {"contact": await self._ctx.get_contact(self._conv_id)}
        if name == "list_past_conversations":
            return {"conversations": await self._ctx.list_contact_conversations(self._conv_id)}
        if name == "search_knowledge_base":
            query = str(args.get("query", "")).strip()
            limit = min(max(int(args.get("limit", 3)), 1), 10)
            articles = await self._kb.search_kb(query, limit)
            return {
                "articles": [
                    {"title": a.title, "content": a.content[:500], "url": a.url}
                    for a in articles
                ]
            }
        return {"error": f"unknown tool: {name}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/chatbot/features/assist/test_copilot_tools.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git -C /Users/yudaadipratama/Archive/proton-conversational-ai add \
  apps/backend/src/chatbot/features/assist/copilot_tools.py \
  apps/backend/src/chatbot/features/assist/test_copilot_tools.py
git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
  -m "feat(copilot): tool schemas + executor over Chatwoot context + KB"
```

---

### Task 4: Backend — `/assist/copilot` endpoint (function-calling loop)

**Files:**
- Create: `src/chatbot/features/assist/copilot_router.py`
- Create: `src/chatbot/features/assist/test_copilot_router.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `ChatwootContextClient` (Task 2), `COPILOT_TOOLS` + `ToolExecutor` (Task 3), `KnowledgePort`, `genai_client`
- Produces:
  - `build_copilot_router(settings, knowledge_port, genai_client) -> APIRouter` (prefix `/assist`)
  - `POST /assist/copilot` body `{ "conversation_id": str, "thread": [{"role": "user"|"assistant", "content": str}] }` → `{ "answer": str, "tool_calls": [str] }` where `tool_calls` is the ordered list of tool names invoked (for the "thinking" trace).
  - Auth: `x-api-key` → 401/503 exactly like `/assist/*`.

> **Gemini function-calling loop (google-genai):** seed `contents` from the thread; call `genai_client.aio.models.generate_content(model, contents, config={"system_instruction": ..., "tools": [{"function_declarations": COPILOT_TOOLS}]})`. If the response contains function calls (`response.function_calls`), execute each via `ToolExecutor`, append the function responses to `contents`, and loop — up to `settings.copilot_max_tool_iterations`. When the model returns text (no function calls), that's the answer. If the cap is hit without a text answer, return the last text or a safe fallback string.

- [ ] **Step 1: Write the failing tests**

Create `src/chatbot/features/assist/test_copilot_router.py`:

```python
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.copilot_router import build_copilot_router
from chatbot.features.chat.models import KbArticle
from chatbot.platform.config import Settings


class _FakeKb:
    async def search_kb(self, query: str, limit: int = 3):
        return [KbArticle(title="Warranty", content="12 months", url="http://faq/1")]


def _text_response(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.function_calls = []
    return r


def _tool_call_response(name: str, args: dict) -> MagicMock:
    r = MagicMock()
    r.text = None
    r.function_calls = [SimpleNamespace(name=name, args=args)]
    return r


def _client(responses: list, key: str = "k") -> TestClient:
    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(side_effect=responses)
    app = FastAPI()
    app.include_router(
        build_copilot_router(
            settings=Settings(proton_backend_key=key, chatwoot_enabled=False),
            knowledge_port=_FakeKb(),
            genai_client=genai,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_requires_api_key() -> None:
    c = _client([_text_response("hi")])
    r = c.post("/assist/copilot", json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_single_turn_answer() -> None:
    c = _client([_text_response("The warranty is 12 months.")])
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "warranty?"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "The warranty is 12 months."
    assert body["tool_calls"] == []


def test_tool_call_then_answer() -> None:
    c = _client([
        _tool_call_response("search_knowledge_base", {"query": "warranty"}),
        _text_response("Per the KB, 12 months."),
    ])
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "warranty?"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Per the KB, 12 months."
    assert body["tool_calls"] == ["search_knowledge_base"]


def test_iteration_cap_returns_fallback() -> None:
    # Always returns a tool call; loop must stop at the cap and still 200.
    c = _client([_tool_call_response("search_knowledge_base", {"query": "x"})] * 20)
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": "k"},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["answer"], str) and r.json()["answer"]


def test_503_when_key_unconfigured() -> None:
    c = _client([_text_response("hi")], key="")
    r = c.post(
        "/assist/copilot",
        json={"conversation_id": "1", "thread": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": "anything"},
    )
    assert r.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest src/chatbot/features/assist/test_copilot_router.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `copilot_router.py`**

Create `src/chatbot/features/assist/copilot_router.py`:

```python
"""POST /assist/copilot — multi-turn, tool-using Ask Copilot.

Stateless: the frontend owns the chat thread and sends it each turn. The server
runs a bounded Gemini function-calling loop whose tools are read-only Chatwoot
lookups + KB search. Auth mirrors /assist/*.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.assist.chatwoot_context import ChatwootContextClient
from chatbot.features.assist.copilot_tools import COPILOT_TOOLS, ToolExecutor

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_SYSTEM = (
    "You are Copilot, an assistant for a Proton Holdings SUPPORT AGENT (not the "
    "customer). Help the agent by answering their questions about the current "
    "conversation and customer. Use the provided tools to read the transcript "
    "(including private notes), the contact's details and custom attributes, the "
    "customer's past conversations, and the knowledge base before answering. "
    "Be concise and factual; if the tools don't contain the answer, say so. "
    "Reply in the language the agent used."
)

_FALLBACK = "I couldn't complete that — try rephrasing your question."


class ThreadMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1)


class CopilotRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    thread: list[ThreadMessage] = Field(min_length=1)


def build_copilot_router(
    settings: Settings,
    knowledge_port: KnowledgePort,
    genai_client: Any,
) -> APIRouter:
    router = APIRouter(prefix="/assist", tags=["copilot"])

    def _authorize(x_api_key: str | None) -> None:
        key = settings.proton_backend_key
        if not key:
            raise HTTPException(status_code=503, detail="Copilot not configured")
        if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), key.encode()):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def _seed_contents(thread: list[ThreadMessage]) -> list[dict[str, Any]]:
        # google-genai content format: role "user"/"model", parts=[{"text": ...}]
        return [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in thread
        ]

    @router.post("/copilot")
    async def copilot(
        req: CopilotRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        _log.info("assist_copilot", conv_id=req.conversation_id, turns=len(req.thread))

        executor = ToolExecutor(
            ChatwootContextClient(settings), knowledge_port, req.conversation_id
        )
        contents = _seed_contents(req.thread)
        tool_calls: list[str] = []
        config = {
            "system_instruction": _SYSTEM,
            "tools": [{"function_declarations": COPILOT_TOOLS}],
        }

        last_text = ""
        for _ in range(settings.copilot_max_tool_iterations):
            response = await genai_client.aio.models.generate_content(
                model=settings.copilot_gemini_model,
                contents=contents,
                config=config,
            )
            calls = getattr(response, "function_calls", None) or []
            if not calls:
                last_text = (getattr(response, "text", None) or "").strip()
                return {"answer": last_text or _FALLBACK, "tool_calls": tool_calls}

            # Execute each requested tool and feed the results back.
            contents.append(
                {
                    "role": "model",
                    "parts": [
                        {"function_call": {"name": c.name, "args": dict(c.args or {})}}
                        for c in calls
                    ],
                }
            )
            tool_parts = []
            for c in calls:
                tool_calls.append(c.name)
                result = await executor.run(c.name, dict(c.args or {}))
                tool_parts.append(
                    {"function_response": {"name": c.name, "response": result}}
                )
            contents.append({"role": "user", "parts": tool_parts})

        # Cap reached without a final text answer.
        return {"answer": last_text or _FALLBACK, "tool_calls": tool_calls}

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest src/chatbot/features/assist/test_copilot_router.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Run the whole assist suite**

Run: `python -m pytest src/chatbot/features/assist -v`
Expected: all pass (existing assist tests + the three new files).

- [ ] **Step 6: Commit**

```bash
git -C /Users/yudaadipratama/Archive/proton-conversational-ai add \
  apps/backend/src/chatbot/features/assist/copilot_router.py \
  apps/backend/src/chatbot/features/assist/test_copilot_router.py
git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
  -m "feat(copilot): /assist/copilot tool-calling loop endpoint"
```

---

### Task 5: Backend — wire the copilot router into the app

**Files:**
- Modify: `src/chatbot/main.py`

**Interfaces:**
- Consumes: `build_copilot_router` (Task 4), existing `_build_genai_client`, `knowledge_port`, `settings`
- Produces: `/assist/copilot` mounted on the running app; its origin already covered by the assist CORS middleware.

- [ ] **Step 1: Add the import**

In `src/chatbot/main.py`, next to `from chatbot.features.assist.router import build_assist_router`:

```python
from chatbot.features.assist.copilot_router import build_copilot_router
```

- [ ] **Step 2: Add the wiring helper**

After `_wire_assist(...)` in `src/chatbot/main.py`, add:

```python
def _wire_copilot(app: FastAPI, knowledge_port: KnowledgePort, settings: Settings) -> None:
    """Wire POST /assist/copilot (Ask Copilot). Shares the assist CORS origins."""
    genai_client = _build_genai_client(settings)
    app.include_router(build_copilot_router(settings, knowledge_port, genai_client))
```

- [ ] **Step 3: Call it in `bootstrap_application()`**

Immediately after the existing `_wire_assist(app, knowledge_port, settings)` line, add:

```python
    # --- Ask Copilot (multi-turn) ---
    _wire_copilot(app, knowledge_port, settings)
```

- [ ] **Step 4: Smoke-test the app boots and the route exists**

Create a throwaway check (do not commit):

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
PROTON_BACKEND_KEY=k python -c "from chatbot.main import app; print([r.path for r in app.routes if getattr(r,'path','').startswith('/assist')])"
```
Expected: the printed list includes `/assist/copilot`.

- [ ] **Step 5: Commit**

```bash
git -C /Users/yudaadipratama/Archive/proton-conversational-ai add apps/backend/src/chatbot/main.py
git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
  -m "feat(copilot): wire /assist/copilot into bootstrap_application"
```

---

### Task 6: Backend — extend the local test runner to serve copilot

**Files:**
- Modify: `apps/backend/run_assist_local.py`

**Interfaces:**
- Consumes: `build_copilot_router` (Task 4), existing `run_assist_local.build()`
- Produces: the local test server also mounts `/assist/copilot`, so the patched Chatwoot can be tested end-to-end without booting the full app.

- [ ] **Step 1: Mount the copilot router in `build()`**

In `run_assist_local.py`, add the import at the top:

```python
from chatbot.features.assist.copilot_router import build_copilot_router
```

and after the existing `app.include_router(build_assist_router(...))` line, add:

```python
    app.include_router(
        build_copilot_router(settings, _StubKnowledge(), _build_genai_client(settings))
    )
```

- [ ] **Step 2: Manually verify (real Gemini, stub KB, Chatwoot disabled locally)**

```bash
cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
pkill -f run_assist_local; sleep 2
PROTON_BACKEND_KEY=local-test-key nohup .venv/bin/python run_assist_local.py > /tmp/assist-local.log 2>&1 &
sleep 4
curl -s -X POST http://localhost:8000/assist/copilot \
  -H 'content-type: application/json' -H 'x-api-key: local-test-key' \
  -d '{"conversation_id":"1","thread":[{"role":"user","content":"What did the customer say?"}]}'
```
Expected: HTTP 200 JSON with `answer` (string) and `tool_calls` (array). With Chatwoot disabled the transcript tool returns empty, so the answer will note it lacks context — that's fine; it proves the loop + auth work.

- [ ] **Step 3: Commit**

```bash
git -C /Users/yudaadipratama/Archive/proton-conversational-ai add apps/backend/run_assist_local.py
git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
  -m "test(copilot): serve /assist/copilot from the local runner"
```

---

### Task 7: Frontend patch 0005 — Ask Copilot panel (author + export)

**Files:**
- Create: `deploy/chatwoot-fork/patches/0005-ask-copilot-panel.patch` (engineer-authored, exported from `git diff` against the applied working tree)

**Interfaces:**
- Consumes: `useProtonConfig()` (`backendUrl`, `backendKey`, `hasFeature('copilot')`); the existing presentational panel `dashboard/components-next/copilot/Copilot.vue` (props `messages`, `conversationInboxType`, `assistants`, `activeAssistant`; emits `sendMessage`, `reset`)
- Produces: a Proton-owned copilot chat panel in the conversation sidebar, opened from the ✨ menu, feature-gated by `copilot`, with an "Insert into reply" action.

**What this patch adds (IP-safe description — author these in a clone of v4.15.1, then `git diff` to export):**

1. **`app/javascript/dashboard/api/protonCopilot.js`** (OUR file) — one function:

```javascript
import { useProtonConfig } from 'dashboard/composables/useProtonConfig';

// thread: [{ role: 'user'|'assistant', content }]
export async function askCopilot(conversationId, thread) {
  const { backendUrl, backendKey } = useProtonConfig();
  if (!backendUrl) throw new Error('PROTON_BACKEND_URL not configured');
  const res = await fetch(`${backendUrl}/assist/copilot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-api-key': backendKey },
    body: JSON.stringify({ conversation_id: String(conversationId), thread }),
  });
  if (!res.ok) throw new Error(`copilot failed: ${res.status} ${await res.text()}`);
  return res.json(); // { answer, tool_calls }
}
```

2. **`app/javascript/dashboard/components/copilot/ProtonCopilotContainer.vue`** (OUR file) — the smart wrapper. It:
   - imports `Copilot` from `dashboard/components-next/copilot/Copilot.vue` and `askCopilot` from `dashboard/api/protonCopilot`;
   - keeps a local `messages` ref in the shape `Copilot.vue` expects (`{ id, message_type: 'agent'|'assistant'|'assistant_thinking', content }`);
   - on `@sendMessage`: pushes the agent message, pushes a transient `assistant_thinking` bubble, calls `askCopilot(currentChat.id, thread)` (mapping local messages → `{role, content}` where `agent→user`, `assistant→assistant`), then replaces the thinking bubble with the `answer` (and renders each `tool_calls` name as a thinking step);
   - reads the current conversation via `useMapGetter('getSelectedChat')` for `conversationInboxType` and `id`;
   - passes `:assistants="[]"` and a static `:activeAssistant="{ name: 'Copilot' }"` (we don't use Captain's multi-assistant model);
   - renders an **"Insert into reply"** button on each assistant message that `emit('insertReply', text)`.

3. **Panel mount + launcher** in `app/javascript/dashboard/routes/dashboard/conversation/ConversationView.vue`:
   - import `ProtonCopilotContainer` and `useProtonConfig`;
   - add computed `protonCopilotEnabled = hasFeature('copilot')` and read `is_proton_copilot_open` from `uiSettings`;
   - render, next to `<ConversationSidebar>`, `<ProtonCopilotContainer v-if="protonCopilotEnabled && isProtonCopilotOpen" @insert-reply="onCopilotInsertReply" />`;
   - `onCopilotInsertReply(text)` sets the reply-box message via the existing bus the reply box already listens to (reuse the `protonAssistResult`/`message` path from patch 0002 — emit an app-level event or set the shared store the ReplyBox uses). Keep it consistent with patch 0002's insertion mechanism.

4. **Open/close toggle** stored in uiSettings as `is_proton_copilot_open` (mirrors `is_copilot_panel_open`), toggled by:
   - repurposing the ✨ reply-editor `ask_copilot` menu item (in `ReplyTopPanel.vue`, already Proton-aware from patch 0002): when `hasFeature('copilot')`, the handler for `ask_copilot` calls `updateUISettings({ is_proton_copilot_open: true })` instead of the one-shot `/assist/ask` call.

5. **i18n**: add `PROTON_COPILOT.TITLE`, `PROTON_COPILOT.PLACEHOLDER`, `PROTON_COPILOT.INSERT_REPLY` to `app/javascript/dashboard/i18n/locale/en/*.json` and `id/*.json`.

**How to generate the patch file:**

```bash
cd /tmp/proton-chatwoot-dev   # a clone of v4.15.1 with patches 0001-0004 already applied
# after authoring the files above:
git add -A
git diff --cached -- \
  app/javascript/dashboard/api/protonCopilot.js \
  app/javascript/dashboard/components/copilot/ProtonCopilotContainer.vue \
  app/javascript/dashboard/routes/dashboard/conversation/ConversationView.vue \
  app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue \
  app/javascript/dashboard/i18n/locale/en app/javascript/dashboard/i18n/locale/id \
  > /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0005-ask-copilot-panel.patch
```

- [ ] **Step 1: Author the five changes above in the clone and confirm `git apply --check` passes**

```bash
docker run --rm -v "$PWD/deploy/chatwoot-fork/patches:/patches:ro" \
  --entrypoint sh chatwoot/chatwoot:v4.15.1 -c \
  'cd /app && git init -q && git add -A && git commit -qm base && \
   for p in /patches/000*.patch; do git apply --check --whitespace=nowarn "$p" \
   && echo "CLEAN $(basename $p)" || echo "FAILS $(basename $p)"; done'
```
Expected: `CLEAN` for 0001–0005.

- [ ] **Step 2: Commit the patch file**

```bash
git -C /Users/yudaadipratama/Archive/id-crm-ticketing add deploy/chatwoot-fork/patches/0005-ask-copilot-panel.patch
git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit \
  -m "feat(patch-0005): Ask Copilot chat panel wired to /assist/copilot"
```

---

### Task 8: Build the patched image and smoke-test the panel end-to-end

**Files:**
- Read: `deploy/chatwoot-fork/patches/*.patch`, `deploy/chatwoot-fork/Dockerfile`
- Modify: `deploy/tenants/example.env` (document `copilot` flag), `deploy/chatwoot-fork/README.md` (add 0005)

**Interfaces:**
- Consumes: all patches 0001–0005; the running backend from Tasks 1–6
- Produces: `proton-chatwoot:v4.15.1-custom` with the panel; verified in the browser against the local backend.

- [ ] **Step 1: Document the flag**

In `deploy/tenants/example.env`, update the `PROTON_FEATURES` comment to list `copilot`, e.g. `Example: ai_assist,nav_menu,copilot`. In `deploy/chatwoot-fork/README.md`, add `0005-ask-copilot-panel.patch` to the patch list.

- [ ] **Step 2: Rebuild the image (patches applied by the Dockerfile glob)**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork
docker build --build-arg UPSTREAM_VERSION=$(cat UPSTREAM_VERSION) \
  -t proton-chatwoot:v4.15.1-custom --progress=plain . 2>&1 | tail -20
```
Expected: `applying 0005-ask-copilot-panel.patch`, then `✓ N modules transformed`, build exits 0.

- [ ] **Step 3: Enable the flag and restart the local tenant**

In `deploy/tenants/default.env` set `PROTON_FEATURES=ai_assist,nav_menu,copilot` (also ensure `CHATWOOT_API_TOKEN` is set to a valid user token so the copilot tools can read Chatwoot). Then:

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/deploy
docker compose -p default -f docker-compose.tenant.yml --env-file tenants/default.env \
  up -d chatwoot-rails chatwoot-sidekiq
```

- [ ] **Step 4: Browser smoke-test** (Incognito to dodge the service worker)

Open conversation #1 → click ✨ → **Ask Copilot** → the sidebar Copilot panel opens. Ask *"What has the customer already tried, and is it under warranty?"*. Expected:
- Network shows `POST http://localhost:8000/assist/copilot → 200`.
- The panel streams a "thinking" step per tool call (e.g. `get_conversation_transcript`, `search_knowledge_base`) then the answer.
- "Insert into reply" places the answer into the reply box.

- [ ] **Step 5: Commit the docs/env updates**

```bash
git -C /Users/yudaadipratama/Archive/id-crm-ticketing add \
  deploy/tenants/example.env deploy/chatwoot-fork/README.md
git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit \
  -m "docs(chatwoot-fork): document copilot feature flag + patch 0005"
```

---

### Task 9 (optional, parity polish): streaming responses via SSE

**Files:**
- Modify: `src/chatbot/features/assist/copilot_router.py` (+ its test), `patches/0005` (frontend consumes the stream)

**Interfaces:**
- Produces: `POST /assist/copilot/stream` returning `text/event-stream` — emits `event: thinking` per tool call and `event: answer` with the final text — so the panel shows live progress like Captain.

- [ ] **Step 1: Add a streaming endpoint** that runs the same loop but wraps it in a `fastapi.responses.StreamingResponse`, yielding an SSE `thinking` line as each tool is invoked and a final `answer` line. Reuse `ToolExecutor` and the loop from Task 4 (extract the loop body into a shared async generator to keep it DRY).
- [ ] **Step 2: Test** the generator yields `thinking` events before `answer` for the tool-call case (drive it with the same mocked `generate_content` side-effects as Task 4).
- [ ] **Step 3:** Update `ProtonCopilotContainer.vue` to consume the stream (EventSource/fetch-stream) and render `thinking` steps live.
- [ ] **Step 4: Commit** each of backend and frontend changes separately.

> Defer this task unless live progress is required for launch — the non-streaming panel from Tasks 1–8 is fully functional; `tool_calls[]` already lets the panel show which tools ran after the fact.

---

## Self-Review

**Spec coverage** (against the parity capabilities established with the user):
- Multi-turn chat panel → Tasks 4, 7. Conversation transcript incl. **private notes** → Task 2 `get_transcript` (keeps `private=true`). **Cross-conversation history** → Task 2 `list_contact_conversations`. **Custom attributes** → Task 2 `get_contact`. **KB grounding** → Task 3 `search_knowledge_base` over the real `KnowledgePort`. **Refinement** → inherent in the multi-turn thread (Task 4/7). **Insert into reply** → Task 7 §3. **Streaming/thinking** → Task 9 (+ `tool_calls[]` fallback in Task 4). All covered.

**Placeholder scan:** backend tasks carry complete code and exact `pytest` commands with expected pass/fail; frontend tasks use the Phase-0 house style (IP-safe description + exact files + `git diff` export + `git apply --check` verification) because `.vue` patches aren't unit-TDD'd. No `TODO`/`TBD`/"handle edge cases" left.

**Type consistency:** `build_copilot_router(settings, knowledge_port, genai_client)` matches its call in Tasks 5/6. Request shape `{conversation_id, thread:[{role, content}]}` and response `{answer, tool_calls}` are identical across Tasks 4, 6, 7. `ChatwootContextClient` methods (`get_transcript`/`get_contact`/`list_contact_conversations`) match their use in Task 3's `ToolExecutor`. Tool names in `COPILOT_TOOLS` match the executor's dispatch and the tests.

**Known risk to flag at execution:** the exact google-genai function-calling response accessors (`response.function_calls`, `c.name`, `c.args`) and the `contents` function-response format should be confirmed against the installed `google-genai` version at Task 4 Step 3; if the SDK differs, adjust the accessors (the tests mock these, so update the fakes to match the real shape). This is the one place the plan touches SDK surface that may have drifted.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-19-ask-copilot-full-parity.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints for review.

Which approach?
