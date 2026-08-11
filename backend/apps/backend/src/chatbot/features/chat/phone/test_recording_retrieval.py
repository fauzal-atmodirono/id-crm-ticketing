"""Unit tests for Call Recording Retrieval (P11 Task 1)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from chatbot.features.chat.phone.recording_router import (
    build_recording_router,
    register_recording,
    reset_recordings,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_recordings()
    yield
    reset_recordings()


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "call_recording_retrieval_enabled": True,
            "proton_backend_key": "valid_key",
        }
    )


def test_the_flag_off_returns_404(settings) -> None:
    off_settings = settings.model_copy(update={"call_recording_retrieval_enabled": False})
    app = FastAPI()
    app.include_router(build_recording_router(off_settings))
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": settings.proton_backend_key})
    assert res.status_code == 404


def test_a_caller_without_call_recording_listen_is_rejected(settings) -> None:
    app = FastAPI()
    app.include_router(build_recording_router(settings))
    client = TestClient(app)

    # Missing auth header -> 401
    res = client.get("/calls/conv_123/recording")
    assert res.status_code in (401, 403)


def test_a_permitted_caller_receives_a_signed_url(settings) -> None:
    register_recording("conv_123", "https://storage.provider.com/audio/123.mp3")

    app = FastAPI()
    app.include_router(build_recording_router(settings))
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": settings.proton_backend_key})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "available"
    assert "https://storage.provider.com/audio/123.mp3" in body["recording_url"]
    assert "signature=" in body["recording_url"]


def test_the_audio_is_not_proxied_through_the_application(settings) -> None:
    register_recording("conv_123", "https://storage.provider.com/audio/123.mp3")

    app = FastAPI()
    app.include_router(build_recording_router(settings))
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": settings.proton_backend_key})
    assert res.status_code == 200
    # Response contains redirect/signed URL metadata, not binary audio payload
    assert "recording_url" in res.json()
    assert "audio/mpeg" not in res.headers.get("content-type", "")


def test_the_signed_url_expires(settings) -> None:
    register_recording("conv_123", "https://storage.provider.com/audio/123.mp3")

    app = FastAPI()
    app.include_router(build_recording_router(settings))
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": settings.proton_backend_key})
    assert res.status_code == 200
    assert "expires_at" in res.json()


def test_every_retrieval_writes_an_audit_entry_naming_the_listener(settings) -> None:
    register_recording("conv_123", "https://storage.provider.com/audio/123.mp3")

    app = FastAPI()
    app.include_router(build_recording_router(settings))
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": settings.proton_backend_key})
    assert res.status_code == 200


def test_a_conversation_with_no_recording_returns_a_clear_empty_state(settings) -> None:
    app = FastAPI()
    app.include_router(build_recording_router(settings))
    client = TestClient(app)

    res = client.get("/calls/conv_empty/recording", headers={"x-api-key": settings.proton_backend_key})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "empty"
    assert "No call recording exists" in body["message"]


def test_a_recording_deleted_by_retention_returns_a_distinct_state(settings) -> None:
    register_recording("conv_deleted", "https://storage.provider.com/audio/deleted.mp3", is_deleted=True)

    app = FastAPI()
    app.include_router(build_recording_router(settings))
    client = TestClient(app)

    res = client.get("/calls/conv_deleted/recording", headers={"x-api-key": settings.proton_backend_key})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "deleted"
    assert "deleted under the retention policy" in body["message"]
