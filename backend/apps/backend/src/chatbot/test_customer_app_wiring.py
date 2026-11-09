"""The demo customer app reaches the real app (main.py wiring).

The router's own tests mount it by hand, so they would pass with main.py
never including it. Booting bootstrap_application() is the only proof.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean_settings() -> Any:
    from chatbot.platform.config import get_settings  # noqa: PLC0415

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
        # No Chatwoot token / inbox: a mounted route answers 503 and never
        # reaches out.
        "CHATWOOT_API_TOKEN": "",
        "CHATWOOT_INBOX_ID": "0",
    }
    for name, value in {**base, **env}.items():
        monkeypatch.setenv(name, value)
    from chatbot.main import bootstrap_application  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    return bootstrap_application()


def _get(app: Any) -> int:
    res = TestClient(app).get("/customer-app/messages", params={"persona": "ahmad"})
    return res.status_code


def test_flag_on_mounts_the_customer_app(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _get(_boot(monkeypatch, DEMO_CUSTOMER_APP_ENABLED="true")) == 503


def test_flag_off_does_not_mount_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(monkeypatch, DEMO_CUSTOMER_APP_ENABLED="false")
    assert _get(app) == 404
    assert not any(getattr(r, "path", "").startswith("/customer-app") for r in app.routes)


def test_chat_caller_context_flag_on_mounts_the_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(
        monkeypatch,
        CHAT_CALLER_CONTEXT_ENABLED="true",
        CRM_PROVIDER="chatwoot",
        CHATWOOT_API_TOKEN="t",
        CHATWOOT_INBOX_ID="1",
    )
    res = TestClient(app).post("/agent/contact-context", json={"phone": "+60000000101"})
    assert res.status_code == 401  # mounted: wants the backend key


def test_chat_caller_context_flag_off_is_not_mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(monkeypatch, CHAT_CALLER_CONTEXT_ENABLED="false", CRM_PROVIDER="chatwoot")
    res = TestClient(app).post("/agent/contact-context", json={"phone": "+60000000101"})
    assert res.status_code == 404
