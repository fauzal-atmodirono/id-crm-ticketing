"""2026-11-09 caller context reaches the real app (main.py wiring).

Same reachability lesson as test_p11_wiring.py: the router's own tests
inject a service by hand, so they would pass with main.py never building
one. Booting bootstrap_application() and seeing the signed caller parameter
in /voice/phone/incoming's TwiML is only possible if main.py wired it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-twilio-token"
BASE = "https://example.test"


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
        "TWILIO_AUTH_TOKEN": TOKEN,
        "TWILIO_WEBHOOK_BASE_URL": BASE,
        "PUBLIC_WSS_BASE_URL": "wss://tunnel.test",
    }
    for name, value in {**base, **env}.items():
        monkeypatch.setenv(name, value)
    from chatbot.main import bootstrap_application
    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    return bootstrap_application()


def _post(app: Any) -> str:
    form = {"CallSid": "CA1", "From": "+60000000101"}
    data = f"{BASE}/voice/phone/incoming" + "".join(f"{k}{form[k]}" for k in sorted(form))
    sig = base64.b64encode(hmac.new(TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()
    res = TestClient(app).post(
        "/voice/phone/incoming", data=form, headers={"X-Twilio-Signature": sig}
    )
    assert res.status_code == 200
    return res.text


def test_flag_on_wires_the_caller_context_service(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(monkeypatch, PHONE_CALLER_CONTEXT_ENABLED="true")
    assert '<Parameter name="caller" value="+60000000101"/>' in _post(app)


def test_flag_off_leaves_incoming_twiml_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(monkeypatch, PHONE_CALLER_CONTEXT_ENABLED="false")
    assert "<Parameter" not in _post(app)
