from __future__ import annotations

import base64
import hashlib
import hmac


def verify_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature: str | None,
) -> bool:
    """Validate Twilio's X-Twilio-Signature for a form-encoded webhook POST.

    Twilio computes base64(HMAC-SHA1(auth_token, url + concat(sorted(k+v)))).
    """
    if not signature:
        return False
    data = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    mac = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)
