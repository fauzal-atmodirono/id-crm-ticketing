from __future__ import annotations

import base64
import hashlib
import hmac

from chatbot.features.chat.twilio_signature import verify_twilio_signature

_TOKEN = "test_auth_token"
_URL = "https://example.com/webhooks/twilio-whatsapp"
_PARAMS = {"From": "whatsapp:+60123456789", "Body": "Hello", "MessageSid": "SM1"}


def _expected_signature(token: str, url: str, params: dict[str, str]) -> str:
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mac = hmac.new(token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode("utf-8")


def test_accepts_valid_signature() -> None:
    sig = _expected_signature(_TOKEN, _URL, _PARAMS)
    assert verify_twilio_signature(_TOKEN, _URL, _PARAMS, sig) is True


def test_rejects_tampered_body() -> None:
    sig = _expected_signature(_TOKEN, _URL, _PARAMS)
    tampered = {**_PARAMS, "Body": "Goodbye"}
    assert verify_twilio_signature(_TOKEN, _URL, tampered, sig) is False


def test_rejects_missing_signature() -> None:
    assert verify_twilio_signature(_TOKEN, _URL, _PARAMS, None) is False


def test_rejects_wrong_token() -> None:
    sig = _expected_signature(_TOKEN, _URL, _PARAMS)
    assert verify_twilio_signature("other_token", _URL, _PARAMS, sig) is False
