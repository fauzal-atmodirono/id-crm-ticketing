from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from chatbot.main import bootstrap_application
from chatbot.platform.config import get_settings


def test_kb_suggest_and_faq_feedback_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot the full app and verify agent-assist FAQ routes are registered.

    GET /kb/suggest?q=battery → 200 with a ``suggestions`` key (uses the in-memory
    KnowledgeAdapter, which returns an empty list; the key must still be present).
    POST /kb/feedback with no x-api-key → 401 (auth guard is active).

    Forces HANDOFF_STORE=memory so no real Firestore is touched. Follows the same
    hermetic pattern as ``test_sla_audit_wiring_end_to_end``.
    """
    monkeypatch.setenv("HANDOFF_STORE", "memory")
    get_settings.cache_clear()
    try:
        app = bootstrap_application()
        client = TestClient(app)

        # GET /kb/suggest — open endpoint; must return 200 with suggestions key
        suggest_res = client.get("/kb/suggest?q=battery")
        assert suggest_res.status_code == 200
        assert "suggestions" in suggest_res.json()

        # POST /kb/feedback — no api key → 401
        feedback_res = client.post(
            "/kb/feedback",
            json={
                "article_id": "test-article",
                "session_id": "",
                "helpful": True,
                "score": 5,
            },
        )
        assert feedback_res.status_code == 401
    finally:
        get_settings.cache_clear()


def test_twilio_whatsapp_route_is_registered() -> None:
    app = bootstrap_application()
    # FastAPI nests included routers rather than flattening app.routes, so assert
    # against the OpenAPI path table, which walks all effective routes.
    assert "/webhooks/twilio-whatsapp" in app.openapi()["paths"]


def test_sla_audit_wiring_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot the full app and verify the audit log is wired into the chat router.

    With no sla_webhook_secret configured (default ""), the endpoint accepts
    requests without a secret header. POST the escalation webhook, expect
    {"status": "recorded"}, then GET the audit trail and confirm the entry
    exists with to_state "WIP".

    Forces the in-memory audit store (HANDOFF_STORE=memory) so the test never
    touches real Firestore — hermetic and CI-safe regardless of the local .env.
    get_settings is @cache'd, so clear it before (to pick up the override) and
    after (so later tests re-read the real .env).
    """
    monkeypatch.setenv("HANDOFF_STORE", "memory")
    get_settings.cache_clear()
    try:
        app = bootstrap_application()
        client = TestClient(app)

        ticket_id = f"T-boot-{uuid.uuid4().hex[:8]}"

        # POST escalation — no secret header needed (sla_webhook_secret defaults to "")
        post_res = client.post(
            "/webhooks/zendesk-sla-escalation",
            json={"ticket_id": ticket_id, "level": "escalate"},
        )
        assert post_res.status_code == 200
        assert post_res.json() == {"status": "recorded"}

        # GET audit trail — should contain exactly one entry with to_state "WIP"
        get_res = client.get(f"/cases/{ticket_id}/audit")
        assert get_res.status_code == 200
        data = get_res.json()
        assert data["ticket_id"] == ticket_id
        assert len(data["audit"]) == 1
        assert data["audit"][0]["to_state"] == "WIP"
    finally:
        get_settings.cache_clear()
