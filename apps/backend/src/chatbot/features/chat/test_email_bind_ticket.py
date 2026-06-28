from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from chatbot.features.chat.service import OrchestratorService


async def test_bind_email_ticket_sets_conversation_ticket_id() -> None:
    orch = OrchestratorService.__new__(OrchestratorService)
    session = SimpleNamespace(state={})
    orch._adk_sessions = SimpleNamespace(get_session=AsyncMock(return_value=session))  # type: ignore[assignment]
    orch._persist_session_state = AsyncMock()  # type: ignore[method-assign]

    await orch.bind_email_ticket("email-77", "77")

    assert session.state["conversation_ticket_id"] == "77"
    orch._persist_session_state.assert_awaited_once_with(session)


async def test_bind_email_ticket_noop_when_already_bound() -> None:
    orch = OrchestratorService.__new__(OrchestratorService)
    session = SimpleNamespace(state={"conversation_ticket_id": "77"})
    orch._adk_sessions = SimpleNamespace(get_session=AsyncMock(return_value=session))  # type: ignore[assignment]
    orch._persist_session_state = AsyncMock()  # type: ignore[method-assign]

    await orch.bind_email_ticket("email-77", "77")

    orch._persist_session_state.assert_not_awaited()


async def test_bind_email_ticket_noop_when_no_session() -> None:
    orch = OrchestratorService.__new__(OrchestratorService)
    orch._adk_sessions = SimpleNamespace(get_session=AsyncMock(return_value=None))  # type: ignore[assignment]
    orch._persist_session_state = AsyncMock()  # type: ignore[method-assign]

    await orch.bind_email_ticket("email-77", "77")

    orch._persist_session_state.assert_not_awaited()
