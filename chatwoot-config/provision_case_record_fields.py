#!/usr/bin/env python3
"""Provision P3's case-record fields as Chatwoot conversation custom attributes.

The RFP asks for columns the report decks print -- vehicle plate and chassis,
the dealer the car was bought from, why a case is delayed, and the three WIP
notes (issue / action taken / next action). All seven are things an agent types
while working the case.

**No fork patch is needed, and that is the point.** These are ordinary Chatwoot
conversation custom attributes, and Chatwoot already renders custom attributes
in the conversation sidebar as an editable panel. Defining them here gets the
entry panel for free -- no Vue changes, no image rebuild, no Cloud Build, and
nothing to re-apply when upstream Chatwoot moves. The backend's
`/cases/{id}/fields` endpoints exist for validation and normalisation (plate
canonicalisation, dealer-slug checking) and for anything that wants to write
these programmatically; they are not required for an agent to fill the panel in.

`escalated_to` is deliberately a two-option list -- `dealer` and `none`. There
is no `hq` option because what counts as an HQ escalation is client question
Q5, still unanswered, and offering the option would produce a number nobody can
defend.

`case_detail`, `case_state`, `case_category`, `case_subcategory`, `case_type`
and `vehicle_model` are NOT provisioned here -- provision_case_taxonomy.py owns
those, and defining them twice would fight over the option lists.

Idempotent: looks up each definition by attribute_key, PATCHes if present,
POSTs if not. Safe to re-run.

Usage:
    python3 provision_case_record_fields.py --dry-run \\
        --chatwoot-url https://crm.example.com --account-id 1 --api-token <token>
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

# (attribute_key, display name, display type, options)
#
# Kept in step with backend/.../features/chat/case_fields.py: that module
# validates what this one lets an agent type. A field added there and not here
# is invisible in the sidebar; added here and not there, it reaches the
# warehouse unvalidated.
FIELDS: list[tuple[str, str, str, list[str]]] = [
    ("vehicle_plate", "Vehicle Plate", "text", []),
    ("vehicle_chassis", "Vehicle Chassis No.", "text", []),
    ("purchased_from_dealer", "Purchased From (dealer)", "text", []),
    ("escalated_to", "Escalated To", "list", ["dealer", "none"]),
    ("delay_reason", "Reason for Delay", "text", []),
    ("wip_issue", "WIP: Issue", "text", []),
    ("wip_action_taken", "WIP: Action Taken", "text", []),
    ("wip_next_action", "WIP: Next Action", "text", []),
]


def _find_existing(client: httpx.Client, base: str, key: str) -> dict | None:
    res = client.get(f"{base}/custom_attribute_definitions")
    res.raise_for_status()
    for defn in res.json():
        if defn.get("attribute_key") == key:
            return defn
    return None


def _upsert(
    client: httpx.Client,
    base: str,
    key: str,
    name: str,
    display_type: str,
    options: list[str],
    dry_run: bool,
) -> None:
    payload = {
        "attribute_display_name": name,
        "attribute_display_type": display_type,
        "attribute_key": key,
        "attribute_model": "conversation_attribute",
    }
    if display_type == "list":
        payload["attribute_values"] = options

    existing = _find_existing(client, base, key)
    if dry_run:
        print(f"[dry-run] {'UPDATE' if existing else 'CREATE'} {key} ({display_type})")
        return
    if existing:
        res = client.patch(
            f"{base}/custom_attribute_definitions/{existing['id']}", json=payload
        )
    else:
        res = client.post(f"{base}/custom_attribute_definitions", json=payload)
    res.raise_for_status()
    print(f"{'Updated' if existing else 'Created'} {key}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chatwoot-url", required=True)
    parser.add_argument("--account-id", required=True, type=int)
    parser.add_argument("--api-token", default=os.environ.get("CHATWOOT_API_TOKEN"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.api_token:
        print("error: --api-token or CHATWOOT_API_TOKEN required", file=sys.stderr)
        return 1

    base = f"{args.chatwoot_url.rstrip('/')}/api/v1/accounts/{args.account_id}"
    headers = {"api_access_token": args.api_token, "Api-Access-Token": args.api_token}

    with httpx.Client(headers=headers, timeout=15.0) as client:
        for key, name, display_type, options in FIELDS:
            _upsert(client, base, key, name, display_type, options, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
