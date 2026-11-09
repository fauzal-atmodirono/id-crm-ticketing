#!/usr/bin/env python3
"""Post a correctly-signed Chatwoot agent-bot delivery at an arbitrary URL.

Built for the AEON360 WhatsApp integration, where the only way to exercise the
bot endpoint used to be sending a real WhatsApp message from a real handset.
The Twilio sandbox number is retired, so "just try it" now means testing on the
production number -- this script is the substitute.

It reproduces exactly what Chatwoot's `AgentBots::WebhookJob` sends: the same
payload shape (captured live 2026-08-20, see the endpoint-conversion guide
section 2), the same `X-Chatwoot-Signature` HMAC over "{timestamp}.{raw_body}",
and the same `X-Chatwoot-Delivery` idempotency header.

What it is for:

* **Diagnosing a silent bot.** Chatwoot logs that it delivered, never what came
  back. This prints the status code, which is the missing half.
* **Acceptance tests 10 and 11** without a handset: `--replay` reuses a delivery
  id (exactly one reply expected), `--bad-signature` must yield 401.
* **The section 5.1.1 fail-safe**, by pointing `--url` at a dead port so the
  delivery fails the way a real outage would.

Safety properties, in the order they matter:

* **`--conversation-id` defaults to 999999**, a display_id that does not exist.
  If the endpoint under test decides to reply, its POST back to Chatwoot 404s
  instead of messaging a real customer. Pass a real id only when you mean to.
* **`--print-only` is available and sends nothing** -- use it to eyeball the
  payload, or to hand someone a curl-equivalent.
* **The secret is never printed.** It is read from the environment and only its
  fingerprint is shown, so this is safe to run with output pasted into a ticket.

Usage:

    export CHATWOOT_BOT_SECRET=...      # the agent bot's secret, not its token

    # Is the endpoint alive and does it accept our signature?
    python3 deploy/scripts/aeon360-bot-probe.py \
        --url https://innovation.dev.aeon360.net/aeon360-customer-waba/chatwoot/bot

    # Acceptance test 11 -- must be 401
    python3 deploy/scripts/aeon360-bot-probe.py --url ... --bad-signature

    # Acceptance test 10 -- send twice with one delivery id, expect one reply
    python3 deploy/scripts/aeon360-bot-probe.py --url ... --delivery-id dup-1
    python3 deploy/scripts/aeon360-bot-probe.py --url ... --delivery-id dup-1

Exit code is 0 when the observed status matches `--expect`, 1 otherwise, so this
can gate a deploy.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

# Chatwoot's own skew window (lib/webhooks/trigger.rb). A receiver that does not
# reject outside it will happily accept a captured request forever.
MAX_SKEW_SECONDS = 300

SAFE_CONVERSATION_ID = 999999


def _ssl_context() -> ssl.SSLContext:
    """Prefer certifi's CA bundle when it is installed.

    A python.org build on macOS ships without a usable CA store until someone
    runs `Install Certificates.command`, so the default context fails every
    HTTPS request with CERTIFICATE_VERIFY_FAILED -- which reads exactly like
    the endpoint being down, and sent us chasing the wrong thing once already.
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def build_payload(args: argparse.Namespace) -> dict:
    """Mirror the live 2026-08-20 capture, including its two traps.

    `sender.type` is deliberately absent: Chatwoot's `Contact#webhook_data`
    emits no type for a customer, so a receiver keying on `sender.type ==
    "contact"` is always false and goes silent. And `conversation.id` is the
    display_id -- the value every REST URL wants -- not the row id.
    """
    phone = args.phone
    sender = {
        "id": 1,
        "name": "Probe Contact",
        "phone_number": phone,
        "email": None,
        "identifier": None,
        "thumbnail": "",
        "blocked": False,
        "additional_attributes": {},
        "custom_attributes": {},
    }
    if args.message_type == "outgoing":
        # A human agent's message does carry a type, and that is the signal the
        # receiver is supposed to treat as an interrupt.
        sender["type"] = "user"

    message = {
        "id": args.message_id,
        "content": args.content,
        "message_type": args.message_type,
        "content_type": "text",
        "content_attributes": {},
        "private": args.private,
        "source_id": f"SMprobe{args.message_id}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "sender": sender,
    }

    return {
        "event": args.event,
        **message,
        "inbox": {"id": args.inbox_id, "name": "AEON360 Whatsapp"},
        "account": {"id": args.account_id, "name": "AEON360"},
        "conversation": {
            "id": args.conversation_id,
            "inbox_id": args.inbox_id,
            "status": args.status,
            "channel": "Channel::TwilioSms",
            "can_reply": True,
            "labels": [],
            "custom_attributes": {},
            "unread_count": 1,
            "waiting_since": int(time.time()),
            "first_reply_created_at": None,
            "priority": None,
            "snoozed_until": None,
            "contact_inbox": {"source_id": f"whatsapp:{phone}"},
            "meta": {
                "sender": {**sender, "type": sender.get("type", "contact")},
                "assignee": None,
                "assignee_type": None,
                "team": None,
                "hmac_verified": False,
            },
            "messages": [message],
        },
    }


def sign(secret: bytes, raw_body: bytes, timestamp: str) -> str:
    """HMAC-SHA256 over "{timestamp}.{raw_body}", exactly as Chatwoot builds it.

    Signed over the raw bytes on purpose: re-serialising the parsed JSON changes
    key order and whitespace, and the signature then never matches.
    """
    digest = hmac.new(secret, timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="the agent-bot endpoint under test")
    parser.add_argument("--event", default="message_created",
                        choices=["message_created", "message_updated",
                                 "conversation_status_changed", "conversation_updated"])
    parser.add_argument("--message-type", default="incoming", choices=["incoming", "outgoing"])
    parser.add_argument("--status", default="pending", choices=["pending", "open", "resolved"])
    parser.add_argument("--content", default="Probe: are you receiving Chatwoot deliveries?")
    parser.add_argument("--private", action="store_true", help="send as a private note (test 9)")
    parser.add_argument("--conversation-id", type=int, default=SAFE_CONVERSATION_ID,
                        help=f"display_id; default {SAFE_CONVERSATION_ID} does not exist, so a "
                             "reply cannot reach a real customer")
    parser.add_argument("--message-id", type=int, default=999999)
    parser.add_argument("--inbox-id", type=int, default=1)
    parser.add_argument("--account-id", type=int, default=1)
    parser.add_argument("--phone", default="+15005550006", help="Twilio's magic test number")
    parser.add_argument("--delivery-id", default=None,
                        help="X-Chatwoot-Delivery; reuse the same value to test dedupe (test 10)")
    parser.add_argument("--bad-signature", action="store_true",
                        help="corrupt the HMAC; a correct receiver answers 401 (test 11)")
    parser.add_argument("--stale-timestamp", action="store_true",
                        help=f"backdate beyond the {MAX_SKEW_SECONDS}s skew window; expect 401")
    parser.add_argument("--expect", type=int, default=None,
                        help="exit non-zero unless this status code is observed")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--print-only", action="store_true", help="print the request, send nothing")
    args = parser.parse_args()

    secret = os.environ.get("CHATWOOT_BOT_SECRET", "")
    if not secret and not args.print_only:
        print("CHATWOOT_BOT_SECRET is unset -- this is the bot's secret, not its "
              "access token; they are different values.", file=sys.stderr)
        return 2

    raw_body = json.dumps(build_payload(args)).encode()
    timestamp = str(int(time.time()) - (MAX_SKEW_SECONDS + 60 if args.stale_timestamp else 0))
    signature = sign(secret.encode(), raw_body, timestamp)
    if args.bad_signature:
        signature = signature[:-1] + ("0" if signature[-1] != "0" else "1")
    delivery_id = args.delivery_id or str(uuid.uuid4())

    fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:12] if secret else "unset"
    print(f"POST {args.url}")
    print(f"  event={args.event} message_type={args.message_type} status={args.status} "
          f"conversation={args.conversation_id}")
    print(f"  delivery={delivery_id}  secret_sha256_12={fingerprint}  body={len(raw_body)}B")
    if args.bad_signature:
        print("  signature DELIBERATELY CORRUPTED -- expecting 401")
    if args.stale_timestamp:
        print(f"  timestamp backdated {MAX_SKEW_SECONDS + 60}s -- expecting 401")

    if args.print_only:
        print("\n--- body ---")
        print(json.dumps(json.loads(raw_body), indent=2))
        return 0

    request = urllib.request.Request(
        args.url,
        data=raw_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Chatwoot-Signature": signature,
            "X-Chatwoot-Timestamp": timestamp,
            "X-Chatwoot-Delivery": delivery_id,
            "User-Agent": "aeon360-bot-probe",
        },
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout,
                                    context=_ssl_context()) as response:
            status, body = response.status, response.read(2000).decode(errors="replace")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read(2000).decode(errors="replace")
    except urllib.error.URLError as exc:
        # A dead endpoint is a legitimate outcome here -- it is how the 5.1.1
        # fail-safe is exercised -- so report it rather than raising.
        print(f"\n  UNREACHABLE after {time.monotonic() - started:.2f}s: {exc.reason}")
        print("  Chatwoot would treat this as a failed delivery and move the "
              "conversation to open (spec 5.1.1).")
        return 1
    elapsed = time.monotonic() - started

    print(f"\n  {status} in {elapsed:.2f}s")
    if body.strip():
        print(f"  body: {body.strip()[:500]}")

    print("\n  " + {
        200: "accepted. Chatwoot considers this delivered; anything that goes "
             "wrong after this is invisible from the CRM side.",
        401: "signature rejected. Either the receiver's secret differs from "
             "CHATWOOT_BOT_SECRET, or it is signing over the parsed JSON "
             "rather than the raw bytes.",
        403: "forbidden -- on this integration that usually means the URL is "
             "the Twilio route, not the Chatwoot one. Chatwoot drops 403 "
             "permanently, so real events would be lost silently.",
        404: "no such route. Check the path.",
        500: "receiver error. Chatwoot retries 500, so this one is at least "
             "not silent.",
    }.get(status, "unexpected status -- Chatwoot retries only 429 and 500; "
                  "every other code drops the event permanently."))

    if args.expect is not None and status != args.expect:
        print(f"\n  FAIL: expected {args.expect}, got {status}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
