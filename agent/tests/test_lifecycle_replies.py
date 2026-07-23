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
