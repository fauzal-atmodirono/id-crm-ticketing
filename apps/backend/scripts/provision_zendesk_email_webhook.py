"""Create the Zendesk webhook target + trigger that delivers inbound customer
emails to POST /webhooks/zendesk-email.

The trigger fires on ticket create AND update (via_id 4 = email channel) so
follow-up customer replies and CSAT number replies reach the webhook, not only
the initial email. Runtime guards (_is_no_reply, paused no-op, state machine)
correctly scope processing; the AI's own API replies are not via_id 4, so there
is no reply loop.

The webhook body sends ONLY the numeric ticket id ({"ticket_id":"<id>"}), which
is always valid JSON regardless of email content. The backend fetches the
customer's comment text and requester details from the Zendesk API — this
prevents arbitrary email content (quotes, backslashes, newlines) from corrupting
the JSON payload and causing 422 errors.

Usage (from apps/backend/):
    PUBLIC_BASE_URL=https://<tunnel>  .venv/bin/python scripts/provision_zendesk_email_webhook.py

Reads Zendesk creds + ZENDESK_SUPPORT_WEBHOOK_SECRET from settings/.env.
Never prints the secret.
"""

from __future__ import annotations

import base64
import os
import sys

import httpx

from chatbot.platform.config import get_settings


def _auth_header(settings) -> dict[str, str]:
    raw = f"{settings.zendesk_email}/token:{settings.zendesk_api_token}"
    enc = base64.b64encode(raw.encode()).decode()
    return {"Authorization": f"Basic {enc}", "Content-Type": "application/json"}


def main() -> int:
    settings = get_settings()
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        print("ERROR: set PUBLIC_BASE_URL to your public backend URL")
        return 1
    secret = settings.zendesk_support_webhook_secret
    if not secret:
        print("ERROR: ZENDESK_SUPPORT_WEBHOOK_SECRET is empty")
        return 1

    sub = settings.zendesk_subdomain
    api = f"https://{sub}.zendesk.com/api/v2"
    headers = _auth_header(settings)

    webhook_body = {
        "webhook": {
            "name": "Proton AI — inbound email",
            "endpoint": f"{base}/webhooks/zendesk-email",
            "http_method": "POST",
            "request_format": "json",
            "status": "active",
            "subscriptions": ["conditional_ticket_event"],
            "authentication": {
                "type": "api_key",
                "data": {"name": "X-Proton-Webhook-Secret", "value": secret},
                "add_position": "header",
            },
        }
    }
    with httpx.Client(timeout=15.0) as client:
        res = client.post(f"{api}/webhooks", json=webhook_body, headers=headers)
        res.raise_for_status()
        webhook_id = res.json()["webhook"]["id"]
        print(f"webhook created: id={webhook_id}")

        trigger_body = {
            "trigger": {
                "title": "AI: customer email -> webhook",
                "conditions": {
                    "all": [
                        {"field": "comment_is_public", "operator": "is", "value": "true"},
                        {"field": "current_via_id", "operator": "is", "value": "4"},
                    ]
                },
                "actions": [
                    {
                        "field": "notification_webhook",
                        "value": [
                            webhook_id,
                            '{"ticket_id":"{{ticket.id}}"}',
                        ],
                    }
                ],
            }
        }
        res = client.post(f"{api}/triggers", json=trigger_body, headers=headers)
        res.raise_for_status()
        print(f"trigger created: id={res.json()['trigger']['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
