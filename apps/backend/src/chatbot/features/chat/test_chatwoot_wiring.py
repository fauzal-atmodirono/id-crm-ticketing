from __future__ import annotations

from fastapi.testclient import TestClient

from chatbot.main import bootstrap_application
from chatbot.platform.config import get_settings


def test_health_reports_chatwoot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    monkeypatch.setenv("CRM_PROVIDER", "chatwoot")
    app = bootstrap_application()
    client = TestClient(app)
    body = client.get("/").json()
    assert body["crm_provider"] == "chatwoot"
    get_settings.cache_clear()
