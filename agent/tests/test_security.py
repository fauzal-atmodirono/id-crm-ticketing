"""Tests for webhook HMAC signature verification (Chatwoot).

Chatwoot scheme: header `X-Chatwoot-Signature: sha256=<hex>` where
hex = HMAC_SHA256(secret, f"{timestamp}.".encode() + raw_body), timestamp
from `X-Chatwoot-Timestamp` (unix seconds as string). Reject if
|now - ts| > max_skew (default 300s).
"""

import hashlib
import hmac

from app.security import verify_chatwoot_signature


def _chatwoot_signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


class TestVerifyChatwootSignature:
    def test_valid_signature_passes(self):
        secret = "shhh"
        body = b'{"event": "message_created"}'
        now = 1_700_000_000
        timestamp = str(now)
        signature = _chatwoot_signature(secret, timestamp, body)

        assert verify_chatwoot_signature(
            secret, timestamp, body, signature, now=now
        ) is True

    def test_wrong_secret_fails(self):
        body = b'{"event": "message_created"}'
        now = 1_700_000_000
        timestamp = str(now)
        signature = _chatwoot_signature("correct-secret", timestamp, body)

        assert verify_chatwoot_signature(
            "wrong-secret", timestamp, body, signature, now=now
        ) is False

    def test_tampered_body_fails(self):
        secret = "shhh"
        now = 1_700_000_000
        timestamp = str(now)
        signature = _chatwoot_signature(secret, timestamp, b'{"event": "a"}')

        assert verify_chatwoot_signature(
            secret, timestamp, b'{"event": "b"}', signature, now=now
        ) is False

    def test_malformed_header_missing_prefix_fails(self):
        secret = "shhh"
        body = b"{}"
        now = 1_700_000_000
        timestamp = str(now)
        mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)

        assert verify_chatwoot_signature(
            secret, timestamp, body, mac.hexdigest(), now=now
        ) is False

    def test_malformed_header_garbage_fails(self):
        secret = "shhh"
        body = b"{}"
        now = 1_700_000_000
        timestamp = str(now)

        assert verify_chatwoot_signature(
            secret, timestamp, body, "not-a-signature", now=now
        ) is False

    def test_missing_signature_fails(self):
        secret = "shhh"
        body = b"{}"
        now = 1_700_000_000
        timestamp = str(now)

        assert verify_chatwoot_signature(
            secret, timestamp, body, None, now=now
        ) is False

    def test_missing_timestamp_fails(self):
        secret = "shhh"
        body = b"{}"
        signature = _chatwoot_signature(secret, "1700000000", body)

        assert verify_chatwoot_signature(
            secret, None, body, signature, now=1_700_000_000
        ) is False

    def test_non_numeric_timestamp_fails(self):
        secret = "shhh"
        body = b"{}"
        signature = _chatwoot_signature(secret, "not-a-number", body)

        assert verify_chatwoot_signature(
            secret, "not-a-number", body, signature, now=1_700_000_000
        ) is False

    def test_stale_timestamp_beyond_max_skew_fails(self):
        secret = "shhh"
        body = b"{}"
        now = 1_700_000_000
        timestamp = str(now - 301)
        signature = _chatwoot_signature(secret, timestamp, body)

        assert verify_chatwoot_signature(
            secret, timestamp, body, signature, max_skew_seconds=300, now=now
        ) is False

    def test_timestamp_at_exact_max_skew_boundary_passes(self):
        secret = "shhh"
        body = b"{}"
        now = 1_700_000_000
        timestamp = str(now - 300)
        signature = _chatwoot_signature(secret, timestamp, body)

        assert verify_chatwoot_signature(
            secret, timestamp, body, signature, max_skew_seconds=300, now=now
        ) is True

    def test_future_timestamp_beyond_max_skew_fails(self):
        secret = "shhh"
        body = b"{}"
        now = 1_700_000_000
        timestamp = str(now + 301)
        signature = _chatwoot_signature(secret, timestamp, body)

        assert verify_chatwoot_signature(
            secret, timestamp, body, signature, max_skew_seconds=300, now=now
        ) is False

    def test_custom_max_skew_respected(self):
        secret = "shhh"
        body = b"{}"
        now = 1_700_000_000
        timestamp = str(now - 30)
        signature = _chatwoot_signature(secret, timestamp, body)

        assert verify_chatwoot_signature(
            secret, timestamp, body, signature, max_skew_seconds=10, now=now
        ) is False

    def test_defaults_now_to_current_time_when_omitted(self):
        import time

        secret = "shhh"
        body = b"{}"
        timestamp = str(int(time.time()))
        signature = _chatwoot_signature(secret, timestamp, body)

        assert verify_chatwoot_signature(secret, timestamp, body, signature) is True
