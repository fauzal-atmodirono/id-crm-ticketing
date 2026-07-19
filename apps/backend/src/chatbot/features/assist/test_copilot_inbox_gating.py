"""Tests for inbox-level mode gating in the copilot router.

Covers:
- inbox_id with mode 'off' → short-circuit, no genai call.
- inbox_id with mode 'suggest' or 'auto' → normal flow.
- inbox_id None → no gating (behaviour-preserving).
- assignment_store=None → no gating (behaviour-preserving).
- inbox assigned to assistant X + request with no assistant_id → copilot uses X's persona.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.copilot_router import build_copilot_router
from chatbot.features.chat.adapters.assistants_store import (
    Assistant,
    AssistantConfig,
    InMemoryAssistantsStore,
    _new_id,
    _now,
)
from chatbot.features.chat.adapters.inbox_assignment_store import InMemoryInboxAssignmentStore
from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
from chatbot.platform.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KEY = "k"


class _FakeKb:
    async def search_kb(self, query: str, limit: int = 3):
        return []


def _text_response(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.function_calls = []
    return r


def _build(
    genai_responses: list | None = None,
    *,
    assignment_store: InMemoryInboxAssignmentStore | None = None,
    assistants_store: InMemoryAssistantsStore | None = None,
    tenant_store: InMemoryTenantSettingsStore | None = None,
) -> tuple[TestClient, MagicMock]:
    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(
        side_effect=genai_responses or [_text_response("ok")]
    )
    app = FastAPI()
    app.include_router(
        build_copilot_router(
            settings=Settings(proton_backend_key=_KEY, chatwoot_enabled=False),
            knowledge_port=_FakeKb(),
            genai_client=genai,
            assistants_store=assistants_store or InMemoryAssistantsStore(),
            tenant_settings_store=tenant_store or InMemoryTenantSettingsStore(),
            assignment_store=assignment_store,
        )
    )
    return TestClient(app, raise_server_exceptions=False), genai


def _req(inbox_id: int | None = None) -> dict:
    body: dict = {
        "conversation_id": "conv-1",
        "thread": [{"role": "user", "content": "hi"}],
    }
    if inbox_id is not None:
        body["inbox_id"] = inbox_id
    return body


# ---------------------------------------------------------------------------
# Gating: mode = off
# ---------------------------------------------------------------------------


async def test_off_mode_returns_short_circuit() -> None:
    """When inbox mode is 'off', the copilot returns the gating message immediately."""
    store = InMemoryInboxAssignmentStore()
    await store.set(10, "asst_x", "off")

    c, genai = _build(assignment_store=store)
    r = c.post("/assist/copilot", json=_req(inbox_id=10), headers={"x-api-key": _KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "AI assist is turned off for this inbox."
    assert body["tool_calls"] == []
    assert body["sources"] == []
    # genai must NOT have been called.
    genai.aio.models.generate_content.assert_not_called()


async def test_off_mode_no_genai_call() -> None:
    """Verifies the genai mock is never invoked when mode is 'off'."""
    store = InMemoryInboxAssignmentStore()
    await store.set(99, "asst_any", "off")

    c, genai = _build(
        genai_responses=[_text_response("should not be seen")],
        assignment_store=store,
    )
    r = c.post("/assist/copilot", json=_req(inbox_id=99), headers={"x-api-key": _KEY})
    assert r.status_code == 200
    genai.aio.models.generate_content.assert_not_called()


# ---------------------------------------------------------------------------
# Gating: mode = suggest / auto → normal flow
# ---------------------------------------------------------------------------


async def test_suggest_mode_normal_flow() -> None:
    """When inbox mode is 'suggest', normal Gemini flow proceeds."""
    store = InMemoryInboxAssignmentStore()
    await store.set(10, "asst_x", "suggest")

    c, genai = _build(
        genai_responses=[_text_response("Here is the answer.")],
        assignment_store=store,
    )
    r = c.post("/assist/copilot", json=_req(inbox_id=10), headers={"x-api-key": _KEY})
    assert r.status_code == 200
    assert r.json()["answer"] == "Here is the answer."
    genai.aio.models.generate_content.assert_called_once()


async def test_auto_mode_normal_flow() -> None:
    """When inbox mode is 'auto', normal Gemini flow proceeds."""
    store = InMemoryInboxAssignmentStore()
    await store.set(10, "asst_x", "auto")

    c, genai = _build(
        genai_responses=[_text_response("Auto answer.")],
        assignment_store=store,
    )
    r = c.post("/assist/copilot", json=_req(inbox_id=10), headers={"x-api-key": _KEY})
    assert r.status_code == 200
    assert r.json()["answer"] == "Auto answer."
    genai.aio.models.generate_content.assert_called_once()


# ---------------------------------------------------------------------------
# Behaviour-preservation: inbox_id None → no gating
# ---------------------------------------------------------------------------


async def test_no_inbox_id_skips_gating() -> None:
    """When inbox_id is not provided, no gating occurs even if store has 'off' entries."""
    store = InMemoryInboxAssignmentStore()
    # Store has every inbox as 'off'; shouldn't matter because no inbox_id in req.
    await store.set(10, "asst_x", "off")

    c, genai = _build(
        genai_responses=[_text_response("Normal answer.")],
        assignment_store=store,
    )
    r = c.post(
        "/assist/copilot",
        json=_req(inbox_id=None),  # no inbox_id field
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    assert r.json()["answer"] == "Normal answer."
    genai.aio.models.generate_content.assert_called_once()


def test_no_assignment_store_skips_gating() -> None:
    """When assignment_store=None, gating is completely bypassed."""
    c, genai = _build(
        genai_responses=[_text_response("Normal answer.")],
        assignment_store=None,  # explicitly None
    )
    r = c.post("/assist/copilot", json=_req(inbox_id=10), headers={"x-api-key": _KEY})
    assert r.status_code == 200
    assert r.json()["answer"] == "Normal answer."
    genai.aio.models.generate_content.assert_called_once()


def test_inbox_not_in_store_uses_default_mode() -> None:
    """When inbox_id is set but not in the assignment store, tenant default mode applies.

    The default mode is 'suggest', which is not 'off', so the normal flow runs.
    """
    store = InMemoryInboxAssignmentStore()
    # inbox 10 has no stored assignment → default mode 'suggest' → no gating

    c, genai = _build(
        genai_responses=[_text_response("Default flow answer.")],
        assignment_store=store,
    )
    r = c.post("/assist/copilot", json=_req(inbox_id=10), headers={"x-api-key": _KEY})
    assert r.status_code == 200
    assert r.json()["answer"] == "Default flow answer."
    genai.aio.models.generate_content.assert_called_once()


# ---------------------------------------------------------------------------
# Fix 2: inbox-assigned assistant persona used when req.assistant_id is None
# ---------------------------------------------------------------------------


async def test_inbox_assigned_assistant_persona_used_when_no_req_assistant_id() -> None:
    """When inbox is assigned to assistant X and request has no assistant_id,
    the copilot resolves and uses X's system_instruction in the generate_content call."""
    assistants_store = InMemoryAssistantsStore()
    assignment_store = InMemoryInboxAssignmentStore()

    # Create a custom assistant with a distinctive persona.
    custom_instructions = "You are the SalesBot persona — only answer sales questions."
    asst = Assistant(
        id=_new_id(),
        name="SalesBot",
        description="",
        product_name="PROTON",
        config=AssistantConfig(instructions=custom_instructions),
        enabled=True,
        is_default=False,
        created_at=_now(),
    )
    await assistants_store.create(asst)
    # Assign the custom assistant to inbox 42.
    await assignment_store.set(42, asst.id, "suggest")

    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(return_value=_text_response("Sales answer."))

    app = FastAPI()
    app.include_router(
        build_copilot_router(
            settings=Settings(proton_backend_key=_KEY, chatwoot_enabled=False),
            knowledge_port=_FakeKb(),
            genai_client=genai,
            assistants_store=assistants_store,
            tenant_settings_store=InMemoryTenantSettingsStore(),
            assignment_store=assignment_store,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    # Request has inbox_id=42 but NO assistant_id.
    r = client.post(
        "/assist/copilot",
        json={
            "conversation_id": "conv-99",
            "thread": [{"role": "user", "content": "help me buy"}],
            "inbox_id": 42,
            # assistant_id intentionally absent
        },
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    # The generate_content call must carry the SalesBot persona in system_instruction.
    genai.aio.models.generate_content.assert_called_once()
    call_kwargs = genai.aio.models.generate_content.call_args
    # system_instruction should contain the custom instructions.
    sys_instr = (call_kwargs.kwargs.get("config") or {}).get("system_instruction", "")
    assert custom_instructions in sys_instr, (
        f"Expected SalesBot persona in system_instruction but got: {sys_instr!r}"
    )
