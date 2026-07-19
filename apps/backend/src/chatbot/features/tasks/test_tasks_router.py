from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.tasks.tasks_router import build_tasks_router
from chatbot.platform.config import Settings


_NOW = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 8,
        "sla_resolution_hours": 48,
        "tasks_reminder_warning_minutes": 60,
        "chatwoot_inbox_id": 1,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch(hours_ago: float) -> int:
    return int((_NOW - timedelta(hours=hours_ago)).timestamp())


def _make_client(conversations: list[dict[str, Any]], settings: Settings) -> TestClient:
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    return client, conversations


def test_returns_tasks_for_agent() -> None:
    conv = {
        "id": 101,
        "status": "open",
        "created_at": _epoch(3),
        "meta": {"assignee": {"id": 7, "name": "Agent"}, "sender": {"name": "CustomerA"}},
    }
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch(
        "chatbot.features.tasks.tasks_router.fetch_conversations",
        return_value=[conv],
    ), patch(
        "chatbot.features.tasks.tasks_router._utcnow",
        return_value=_NOW,
    ):
        resp = client.get("/tasks/mine?agent_id=7")
    assert resp.status_code == 200
    body = resp.json()
    assert "tasks" in body
    assert len(body["tasks"]) == 1
    t = body["tasks"][0]
    assert t["convId"] == "101"
    assert t["subject"] == "CustomerA"
    assert t["agentId"] == "7"
    assert "resolutionRemainingSeconds" in t
    assert body["warningThresholdMinutes"] == 60


def test_filters_other_agents_conversations() -> None:
    convs = [
        {
            "id": 200,
            "status": "open",
            "created_at": _epoch(1),
            "meta": {"assignee": {"id": 7}, "sender": {"name": "C1"}},
        },
        {
            "id": 201,
            "status": "open",
            "created_at": _epoch(1),
            "meta": {"assignee": {"id": 9}, "sender": {"name": "C2"}},
        },
    ]
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=convs), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine?agent_id=7")
    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 1
    assert resp.json()["tasks"][0]["convId"] == "200"


def test_no_agent_id_returns_all_open() -> None:
    convs = [
        {"id": 300, "status": "open", "created_at": _epoch(1), "meta": {"assignee": None, "sender": {"name": "C"}}},
        {"id": 301, "status": "resolved", "created_at": _epoch(2), "meta": {}},
    ]
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=convs), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    assert resp.status_code == 200
    # Only open conversations
    assert len(resp.json()["tasks"]) == 1
    assert resp.json()["tasks"][0]["convId"] == "300"


def test_generated_at_is_iso_string() -> None:
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=[]), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    assert resp.status_code == 200
    assert resp.json()["generatedAt"] == _NOW.isoformat()


def test_breach_conv_has_negative_remaining_and_breach_type() -> None:
    """A 50h-old open conv against 48h SLA must have negative remaining + UNRESOLVED."""
    conv = {
        "id": 999,
        "status": "open",
        "created_at": _epoch(50),  # 50h > 48h
        "meta": {"assignee": {"id": 1}, "sender": {"name": "BrokenCustomer"}},
    }
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=[conv]), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    t = tasks[0]
    assert t["resolutionRemainingSeconds"] < 0
    assert t["breachType"] == "UNRESOLVED"


def test_warning_conv_in_warning_window() -> None:
    """A conv within the warning window has positive remaining < warning threshold."""
    # 47h old, 48h SLA, 120 min warning → remaining ≈ 3600s which is < 7200s (120 min)
    # Has first_reply to avoid NO_RESPONSE breach (which requires no reply after 8h)
    conv = {
        "id": 888,
        "status": "open",
        "created_at": _epoch(47),
        "first_reply_created_at": int((_NOW - timedelta(hours=46)).timestamp()),
        "meta": {"assignee": {"id": 2}, "sender": {"name": "WarningCustomer"}},
    }
    settings = _settings(tasks_reminder_warning_minutes=120)
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=[conv]), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    t = resp.json()["tasks"][0]
    assert t["resolutionRemainingSeconds"] is not None
    assert 0 < t["resolutionRemainingSeconds"] < 120 * 60  # within warning window
    assert t["breachType"] is None  # not yet breached


def test_sla_override_label_reflected_in_response() -> None:
    """sla_480 label → 480 min resolution SLA; verify resolutionDeadlineIso is 8h after created."""
    from datetime import timezone
    conv = {
        "id": 777,
        "status": "open",
        "created_at": _epoch(0),  # just created
        "labels": ["sla_480"],
        "meta": {"assignee": {"id": 3}, "sender": {"name": "SLACustomer"}},
    }
    settings = _settings()
    app = FastAPI()
    app.include_router(build_tasks_router(settings))
    client = TestClient(app)
    with patch("chatbot.features.tasks.tasks_router.fetch_conversations", return_value=[conv]), \
         patch("chatbot.features.tasks.tasks_router._utcnow", return_value=_NOW):
        resp = client.get("/tasks/mine")
    t = resp.json()["tasks"][0]
    # 480 min = 8h; conv just created → remaining ≈ 28800s
    assert t["resolutionRemainingSeconds"] is not None
    assert abs(t["resolutionRemainingSeconds"] - 28800) < 10
