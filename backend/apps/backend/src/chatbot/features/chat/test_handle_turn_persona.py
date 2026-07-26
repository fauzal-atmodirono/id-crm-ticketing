"""Tests for _register_chat_persona and _resolve_chat_assistant helpers.

Stubs _resolve_chat_assistant so tests don't need live Firestore.
"""
from __future__ import annotations

import pytest

from chatbot.features.chat.prompts import AGENT_INSTRUCTION
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig


def _svc_with_persona(assistant):
    svc = OrchestratorService.__new__(OrchestratorService)
    svc._instruction_by_session = {}
    # minimal stubs for the resolver helper the impl calls:
    svc._resolve_chat_assistant = None  # replaced below

    async def _resolve(inbox_id):
        return assistant

    svc._resolve_chat_assistant = _resolve
    return svc


@pytest.mark.asyncio
async def test_register_persona_when_inbox_resolves_nonempty():
    a = Assistant(
        id="asst_test",
        name="A",
        description="",
        product_name="",
        config=AssistantConfig(language="Bahasa Melayu"),
        enabled=True,
        is_default=False,
        created_at="2026-01-01T00:00:00+00:00",
    )
    svc = _svc_with_persona(a)
    await svc._register_chat_persona("crm-1", inbox_id=3)
    reg = svc._instruction_by_session.get("crm-1")
    assert reg is not None and reg.startswith(AGENT_INSTRUCTION)
    assert "Always respond in Bahasa Melayu." in reg


@pytest.mark.asyncio
async def test_no_inbox_registers_nothing():
    svc = _svc_with_persona(None)
    await svc._register_chat_persona("crm-2", inbox_id=None)
    assert "crm-2" not in svc._instruction_by_session


@pytest.mark.asyncio
async def test_empty_persona_registers_nothing():
    a = Assistant(
        id="asst_test2",
        name="A",
        description="",
        product_name="",
        config=AssistantConfig(),  # all empty
        enabled=True,
        is_default=False,
        created_at="2026-01-01T00:00:00+00:00",
    )
    svc = _svc_with_persona(a)
    await svc._register_chat_persona("crm-3", inbox_id=3)
    assert "crm-3" not in svc._instruction_by_session
