from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from chatbot.features.chat.service import OrchestratorService


def _orch() -> Any:
    return OrchestratorService.__new__(OrchestratorService)


async def test_whatsapp_state_delegates_to_conversation_state() -> None:
    orch = _orch()
    orch.conversation_state = AsyncMock(return_value="paused")
    assert await orch.whatsapp_state("email-1") == "paused"
    orch.conversation_state.assert_awaited_once_with("email-1")


async def test_needs_whatsapp_handoff_delegates_to_needs_handoff() -> None:
    orch = _orch()
    orch.needs_handoff = AsyncMock(return_value=True)
    assert await orch.needs_whatsapp_handoff("whatsapp-+60") is True
    orch.needs_handoff.assert_awaited_once_with("whatsapp-+60")


async def test_begin_whatsapp_handoff_delegates_with_whatsapp_channel() -> None:
    orch = _orch()
    orch.begin_handoff = AsyncMock()
    await orch.begin_whatsapp_handoff("whatsapp-+60", "summary", customer_phone="+60")
    orch.begin_handoff.assert_awaited_once_with(
        "whatsapp-+60", "summary", channel="WhatsApp", customer_name=None, customer_phone="+60"
    )
