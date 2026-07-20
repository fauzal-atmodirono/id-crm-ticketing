"""Smoke test: /tasks/mine route is registered and callable."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from chatbot.main import bootstrap_application


def test_tasks_mine_route_registered() -> None:
    """Smoke: /tasks/mine endpoint is mounted (not 404). Auth is checked so 401 is OK."""
    with patch(
        "chatbot.features.tasks.tasks_router.fetch_conversations",
        return_value=[],
    ):
        app = bootstrap_application()
        client = TestClient(app)
        resp = client.get("/tasks/mine")
    assert resp.status_code in (200, 401, 502)
    if resp.status_code == 200:
        assert "tasks" in resp.json()
