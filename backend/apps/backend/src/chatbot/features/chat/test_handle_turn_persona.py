"""Tests for _register_chat_persona and _resolve_chat_assistant helpers.

Stubs _resolve_chat_assistant so tests don't need live Firestore.

The service is built via __new__, so every attribute _register_chat_persona
touches has to be seeded by hand: _instruction_by_session (the persona-only
composed string) and, since P7 task 11a, _assistant_by_session (the resolved
persona object the per-request instruction composer reads tone_* /
media_diagnosis_instruction overrides from).
"""

from __future__ import annotations

import pytest

from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig
from chatbot.features.chat.prompts import AGENT_INSTRUCTION
from chatbot.features.chat.service import OrchestratorService


def _svc_with_persona(assistant):
    svc = OrchestratorService.__new__(OrchestratorService)
    svc._instruction_by_session = {}
    svc._assistant_by_session = {}
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
    assert (
        "Prefer Bahasa Melayu when the customer's language is unclear, but "
        "always match the language the customer writes in." in reg
    )
    # The resolved object itself is parked for the turn, not just the string:
    # tone/media overrides are read off it mid-run.
    assert svc._assistant_by_session["crm-1"] is a


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
