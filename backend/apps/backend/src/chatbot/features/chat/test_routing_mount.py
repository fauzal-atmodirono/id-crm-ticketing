from __future__ import annotations

"""Test that the routing router is mounted on the main FastAPI app.

Uses app.openapi() to check the OpenAPI schema — network-free and deterministic.
Mirrors test_chatwoot_wiring.py: calls bootstrap_application() directly (the
module-level `app = bootstrap_application()` requires live credentials at import
time, so we can't do `from chatbot.main import app` in isolation).
"""


def test_routing_routes_registered(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The /routing/agents and /routing/priorities endpoints appear in the OpenAPI schema."""
    # Provide minimal genai creds so bootstrap_application() doesn't raise at
    # OrchestratorService construction (same pattern as test_chatwoot_wiring.py,
    # which also relies on a Vertex-capable environment).
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    from chatbot.main import bootstrap_application  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    app = bootstrap_application()
    paths = app.openapi()["paths"]
    assert "/routing/agents" in paths
    assert "/routing/priorities" in paths
    # Parametrized PUT/DELETE path should also be present.
    assert "/routing/priorities/{agent_id}" in paths
    get_settings.cache_clear()
