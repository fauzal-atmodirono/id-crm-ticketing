"""DEMO_CONSOLE_ENABLED gates the /demo/* routes in the real app (main.py)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean_settings() -> Any:
    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    base = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "test-project",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
        "PROTON_BACKEND_KEY": "test_key",
        "CHATWOOT_WEBHOOK_SECRET": "test_secret",
    }
    for name, value in {**base, **env}.items():
        monkeypatch.setenv(name, value)
    from chatbot.main import bootstrap_application
    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    return bootstrap_application()


def test_flag_off_has_no_demo_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_boot(monkeypatch))
    assert client.get("/demo/personas").status_code == 404
    assert client.post("/demo/reset", headers={"X-Demo-Console": "1"}).status_code == 404


def test_flag_on_registers_demo_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(_boot(monkeypatch, DEMO_CONSOLE_ENABLED="true"))
    # Without the console header the route answers 403 -- before any CRM call.
    for path in ("/demo/seed", "/demo/reset"):
        assert client.post(path).status_code == 403
