"""P11 task 1 -- the recording-retrieval router, through the real app.

`recording_router.py` shipped complete and unit-tested and **was never mounted**.
Its own tests build a throwaway `FastAPI()` and `include_router` it by hand, so
they passed while the endpoint 404ed against every real deployment and
`CALL_RECORDING_RETRIEVAL_ENABLED` had no consumer any operator could reach --
the tenth instance of this exact failure in this run
(`.superpowers/sdd/DISPATCH-RULES.md`, "Reachability").

These tests boot `bootstrap_application()` and assert the discriminating pair the
P6/P10 wiring tests established: **401 rather than 404** on an unauthenticated
call, which can only happen if the route exists and its permission dependency
ran. A 404 there would mean unmounted; a 200 would mean un-gated.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest

from chatbot.features.chat.phone.recording_router import register_recording, reset_recordings


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    reset_recordings()
    yield
    reset_recordings()


def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("PROTON_BACKEND_KEY", "test_key")
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "test_secret")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from chatbot.main import bootstrap_application
    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    return bootstrap_application()


def test_the_recording_endpoint_is_mounted_and_rejects_an_unauthenticated_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, CALL_RECORDING_RETRIEVAL_ENABLED="true")
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording")
    # 401, NOT 404: the route resolved and `call_recording.listen` refused it.
    assert res.status_code == 401


def test_a_permitted_caller_reaches_the_handler_through_the_real_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, CALL_RECORDING_RETRIEVAL_ENABLED="true")
    register_recording("conv_123", "https://storage.example.invalid/audio/123.mp3")
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": "test_key"})
    assert res.status_code == 200
    assert res.json()["status"] == "available"


def test_the_flag_off_404s_for_a_caller_who_would_otherwise_be_permitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguishes "flag off" from "unmounted" -- the permitted caller is the
    only way to see the flag's own 404 rather than the permission gate's 401."""
    app = _boot(monkeypatch, CALL_RECORDING_RETRIEVAL_ENABLED="false")
    register_recording("conv_123", "https://storage.example.invalid/audio/123.mp3")
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": "test_key"})
    assert res.status_code == 404
    assert "CALL_RECORDING_RETRIEVAL_ENABLED" in res.json()["detail"]
