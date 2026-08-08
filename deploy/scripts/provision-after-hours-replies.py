#!/usr/bin/env python3
"""Provision Appendix B's after-hours auto-reply on each Chatwoot inbox.

The text-channel after-hours reply is Chatwoot's own per-inbox out-of-office
message -- it is not a feature this repo owns. What was missing was never code:
it is that Appendix B's exact wording had never been written to any inbox, nor
verified against the appendix. This script closes that, and
`agent/tests/test_appendix_b_after_hours_text.py` keeps the wording honest.

Safety properties, in the order they matter:

* **`--dry-run` is the default.** Nothing is written unless `--apply` is passed.
* **An inbox whose text was deliberately customised is reported, never
  overwritten.** An operator who has tuned the wording outranks this script;
  `--force` exists for when you really do mean to reset it.
* **Idempotent.** An inbox already carrying the appendix text is left untouched
  and reported as `unchanged`, so this is safe to re-run after every deploy.

Usage:

    export CHATWOOT_URL=https://proton.crm.example
    export CHATWOOT_ACCOUNT_ID=1
    export CHATWOOT_API_TOKEN=...        # an admin access token

    python3 deploy/scripts/provision-after-hours-replies.py            # dry run
    python3 deploy/scripts/provision-after-hours-replies.py --apply
    python3 deploy/scripts/provision-after-hours-replies.py --apply --force

Run it against a scratch tenant before a real one. It talks to whatever
CHATWOOT_URL points at, and it has no idea which tenant that is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TEXT_FILE = Path(__file__).with_name("appendix-b-after-hours-text.json")

# Chatwoot exposes the out-of-office message on these channel types only.
# An Email inbox's acknowledgement is sent by the agent service instead
# (EMAIL_AUTOACK_ENABLED), so it is deliberately not touched here.
TEXT_CHANNELS = {
    "Channel::Whatsapp",
    "Channel::TwilioSms",
    "Channel::FacebookPage",
    "Channel::Instagram",
    "Channel::WebWidget",
}


def _load_text() -> str:
    data = json.loads(TEXT_FILE.read_text(encoding="utf-8"))
    return str(data["after_hours_reply"]["en"])


def _request(method: str, url: str, token: str, body: dict | None = None) -> Any:
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("api_access_token", token)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310 - fixed scheme
        return json.loads(res.read() or "null")


def _inboxes(base: str, account: str, token: str) -> list[dict]:
    data = _request("GET", f"{base}/api/v1/accounts/{account}/inboxes", token)
    if isinstance(data, dict):
        return list(data.get("payload") or [])
    return list(data or [])


def _classify(current: str, wanted: str) -> str:
    """What to do with an inbox, given the text it already carries."""
    if (current or "").strip() == wanted.strip():
        return "unchanged"
    if (current or "").strip():
        return "customised"
    return "empty"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="also overwrite an inbox whose text was deliberately customised",
    )
    args = parser.parse_args()

    base = (os.environ.get("CHATWOOT_URL") or "").rstrip("/")
    account = os.environ.get("CHATWOOT_ACCOUNT_ID") or ""
    token = os.environ.get("CHATWOOT_API_TOKEN") or ""
    if not (base and account and token):
        print("CHATWOOT_URL, CHATWOOT_ACCOUNT_ID and CHATWOOT_API_TOKEN must be set")
        return 2

    wanted = _load_text()
    try:
        inboxes = _inboxes(base, account, token)
    except (urllib.error.URLError, ValueError) as exc:
        print(f"could not list inboxes: {exc}")
        return 1

    print(f"{'APPLY' if args.apply else 'DRY RUN'} against {base} account {account}")
    counts: dict[str, int] = {}
    for inbox in inboxes:
        channel = str(inbox.get("channel_type") or "")
        name = inbox.get("name")
        inbox_id = inbox.get("id")
        if channel not in TEXT_CHANNELS:
            print(f"  skip     [{inbox_id}] {name} ({channel}: no out-of-office field)")
            counts["skipped"] = counts.get("skipped", 0) + 1
            continue

        verdict = _classify(str(inbox.get("out_of_office_message") or ""), wanted)
        if verdict == "unchanged":
            print(f"  ok       [{inbox_id}] {name}: already matches Appendix B")
            counts["unchanged"] = counts.get("unchanged", 0) + 1
            continue
        if verdict == "customised" and not args.force:
            print(
                f"  WARN     [{inbox_id}] {name}: has different custom text, "
                f"left alone (use --force to overwrite)"
            )
            counts["customised"] = counts.get("customised", 0) + 1
            continue

        if not args.apply:
            print(f"  would set[{inbox_id}] {name}")
            counts["would_set"] = counts.get("would_set", 0) + 1
            continue
        try:
            _request(
                "PATCH",
                f"{base}/api/v1/accounts/{account}/inboxes/{inbox_id}",
                token,
                {"out_of_office_message": wanted, "working_hours_enabled": True},
            )
        except (urllib.error.URLError, ValueError) as exc:
            print(f"  FAILED   [{inbox_id}] {name}: {exc}")
            counts["failed"] = counts.get("failed", 0) + 1
            continue
        print(f"  set      [{inbox_id}] {name}")
        counts["set"] = counts.get("set", 0) + 1

    print("summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to do")
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
