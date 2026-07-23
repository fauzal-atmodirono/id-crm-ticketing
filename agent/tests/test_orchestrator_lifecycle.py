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
