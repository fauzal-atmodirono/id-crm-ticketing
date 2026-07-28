"""Tests for assign_agent call on handoff.

Tests verify that _handoff_to_human_via_chatwoot calls assign_agent after
reopening the conversation, and that the flow still works when proton is None.
"""

from unittest.mock import AsyncMock

from app.services import orchestrator


async def test_handoff_calls_assign_after_reopen(monkeypatch):
    chatwoot = AsyncMock()
    proton = AsyncMock()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: proton)
    await orchestrator._handoff_to_human_via_chatwoot(70, chatwoot, "")
    chatwoot.toggle_status.assert_awaited_once_with(70, "open")
    proton.assign_agent.assert_awaited_once_with(70)


async def test_handoff_reopens_even_if_no_proton(monkeypatch):
    chatwoot = AsyncMock()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: None)
    await orchestrator._handoff_to_human_via_chatwoot(70, chatwoot, "")
    chatwoot.toggle_status.assert_awaited_once_with(70, "open")
