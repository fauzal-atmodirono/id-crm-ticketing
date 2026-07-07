"""Webhook HMAC signature verification for Chatwoot and Zammad.

Chatwoot: header `X-Chatwoot-Signature: sha256=<hex>` where
    hex = HMAC_SHA256(secret, f"{timestamp}.".encode() + raw_body)
and `timestamp` comes from `X-Chatwoot-Timestamp` (unix seconds, as a
string). Requests are rejected if |now - timestamp| exceeds max_skew_seconds.

Zammad: header `X-Hub-Signature: sha1=<hex>` where
    hex = HMAC_SHA1(token, raw_body)
"""

import hashlib
import hmac
import time

_CHATWOOT_PREFIX = "sha256="
_ZAMMAD_PREFIX = "sha1="


def verify_chatwoot_signature(
    secret: str,
    timestamp: str | None,
    body: bytes,
    signature: str | None,
    max_skew_seconds: int = 300,
    now: int | float | None = None,
) -> bool:
    if not secret or not timestamp or not signature:
        return False

    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False

    current = int(now) if now is not None else int(time.time())
    if abs(current - ts) > max_skew_seconds:
        return False

    if not signature.startswith(_CHATWOOT_PREFIX):
        return False
    provided_hex = signature[len(_CHATWOOT_PREFIX):]

    mac = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    )
    return hmac.compare_digest(mac.hexdigest(), provided_hex)


def verify_zammad_signature(
    token: str,
    body: bytes,
    signature: str | None,
) -> bool:
    if not token or not signature:
        return False

    if not signature.startswith(_ZAMMAD_PREFIX):
        return False
    provided_hex = signature[len(_ZAMMAD_PREFIX):]

    mac = hmac.new(token.encode(), body, hashlib.sha1)
    return hmac.compare_digest(mac.hexdigest(), provided_hex)
