#!/usr/bin/env python3
"""Project the BigQuery warehouse onto Chatwoot contacts, making BQ the source
of truth and the CRM a view of it.

Why this exists
---------------
Before this, the warehouse and the CRM held the same nasabah because both were
generated from the same code -- twins, not a pipeline. Nothing flowed. That is
fine for a screenshot and useless as an argument, because the obvious question
("so where does the CRM actually get this from?") has no answer.

With this, there is one answer: BigQuery. Change a row in `v_nasabah_profile`,
run this, ask the bot the same question, and its reply changes. That is the
personalization thesis demonstrated rather than asserted, and it is the seam
Phase 1 slots into -- swap the view's source from our synthetic table to
Bahana's real back-office feed and this script does not change at all.

Safety
------
This writes to a live CRM, so it is deliberately hard to misuse:

- **Dry-run by default.** `--apply` is required to write anything. A dry run
  prints the exact field-level diff it would apply.
- **It never creates contacts.** Seeding is `seed_demo_data`'s job. A warehouse
  row with no matching contact is reported, not inserted -- because creating
  one here would silently diverge from the seeder's identity rules (the +999
  non-routable convention, the [DEMO] prefix, batch markers).
- **It refuses to touch a contact without a `demo_seed` marker.** That marker
  is what distinguishes a seeded demo contact from a real one. Without this
  guard, a phone-number collision between the synthetic population and a real
  nasabah would let a demo profile overwrite a live customer record. Override
  only with `--allow-unmarked`, which exists so the refusal is a decision
  rather than a wall.
- **It writes only the nine profile attributes**, merged over whatever else the
  contact carries. Chatwoot's contact update replaces `custom_attributes`
  wholesale, so this reads the current object first and merges -- otherwise
  every unrelated attribute on that contact is silently deleted.

Usage
-----
    export CW_TOKEN=...        # CHATWOOT_API_TOKEN from tenants/bahana.env
    python3 bahana_bq_to_crm_sync.py                # dry run, shows the diff
    python3 bahana_bq_to_crm_sync.py --apply        # actually writes
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import httpx

# The contract with two consumers that never import this module: the seeder
# (deploy/scripts/seed_demo_data/client.py::build_nasabah_custom_attributes)
# and the agent (agent/app/services/customer_context.py::_PROFILE_FIELDS).
# `demo_seed` is written but is a purge marker, not a profile field.
PROFILE_KEYS = (
    "risk_profile",
    "aum_band",
    "rdn_balance",
    "holdings",
    "days_since_last_transaction",
    "product_gaps",
    "next_best_offer",
    "offer_rationale",
)


def fetch_warehouse(project: str, dataset: str, location: str) -> dict[str, dict]:
    """Read v_nasabah_profile, keyed by phone.

    Uses the bq CLI rather than the Python client so this has no dependency
    beyond what the operator already has authenticated for everything else.
    """
    sql = (
        f"SELECT phone, name, {', '.join(PROFILE_KEYS)}, demo_seed "
        f"FROM `{project}.{dataset}.v_nasabah_profile`"
    )
    r = subprocess.run(
        [
            "bq", f"--project_id={project}", f"--location={location}",
            "query", "--use_legacy_sql=false", "--format=json", sql,
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        sys.exit(f"BigQuery read failed:\n{r.stderr[:800]}")
    rows = json.loads(r.stdout or "[]")
    return {row["phone"]: row for row in rows}


def fetch_contacts(base: str, account: int, token: str) -> dict[str, dict]:
    """Every contact in the account, keyed by phone. Paginates until dry."""
    out: dict[str, dict] = {}
    page = 1
    while True:
        r = httpx.get(
            f"{base}/api/v1/accounts/{account}/contacts",
            params={"page": page},
            headers={"api_access_token": token},
            timeout=60,
        )
        r.raise_for_status()
        payload = r.json().get("payload") or []
        if not payload:
            break
        for c in payload:
            phone = (c.get("phone_number") or "").strip()
            if phone:
                out[phone] = c
        page += 1
        if page > 50:  # backstop; 50 pages is far past any demo tenant
            break
    return out


def diff_for(contact: dict, row: dict) -> dict[str, tuple[str, str]]:
    """Field-level changes this sync would make to one contact."""
    current = contact.get("custom_attributes") or {}
    changes: dict[str, tuple[str, str]] = {}
    for key in PROFILE_KEYS:
        new = str(row.get(key) or "").strip()
        old = str(current.get(key) or "").strip()
        if new and new != old:
            changes[key] = (old, new)
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Project the BQ warehouse onto Chatwoot contacts."
    )
    ap.add_argument("--project", default="lv-playground-genai")
    ap.add_argument("--dataset", default="bahana_demo")
    ap.add_argument("--location", default="asia-southeast2")
    ap.add_argument("--chatwoot-url", default="https://bahana.crm.34-50-103-151.nip.io")
    ap.add_argument("--account-id", type=int, default=1)
    ap.add_argument("--apply", action="store_true", help="Actually write. Default is a dry run.")
    ap.add_argument(
        "--allow-unmarked",
        action="store_true",
        help="Permit writing to contacts that carry no demo_seed marker.",
    )
    args = ap.parse_args()

    token = os.environ.get("CW_TOKEN", "").strip()
    if not token:
        sys.exit("CW_TOKEN is not set (CHATWOOT_API_TOKEN from tenants/bahana.env).")

    warehouse = fetch_warehouse(args.project, args.dataset, args.location)
    contacts = fetch_contacts(args.chatwoot_url, args.account_id, token)

    print(f"warehouse rows : {len(warehouse)}")
    print(f"CRM contacts   : {len(contacts)}")
    print(f"mode           : {'APPLY' if args.apply else 'dry run'}\n")

    planned: list[tuple[str, dict, dict, dict]] = []
    unmatched, unmarked = [], []

    for phone, row in sorted(warehouse.items()):
        contact = contacts.get(phone)
        if contact is None:
            unmatched.append((phone, row.get("name")))
            continue
        attrs = contact.get("custom_attributes") or {}
        if not str(attrs.get("demo_seed") or "").strip() and not args.allow_unmarked:
            unmarked.append((phone, contact.get("name")))
            continue
        changes = diff_for(contact, row)
        if changes:
            planned.append((phone, contact, row, changes))

    for phone, contact, row, changes in planned:
        print(f"{contact.get('name')}  {phone}")
        for key, (old, new) in sorted(changes.items()):
            print(f"    {key:28} {old or '(empty)'!r}  ->  {new!r}")

    print(f"\ncontacts needing changes : {len(planned)}")
    print(f"warehouse rows unmatched : {len(unmatched)}")
    print(f"contacts skipped (unmarked, would need --allow-unmarked): {len(unmarked)}")
    for phone, name in unmatched:
        print(f"    unmatched: {name} {phone}")
    for phone, name in unmarked:
        print(f"    unmarked : {name} {phone}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write these.")
        return 0

    written = 0
    for phone, contact, row, changes in planned:
        # Chatwoot REPLACES custom_attributes on update, so merge over the
        # existing object rather than sending only the profile keys -- sending
        # a subset silently deletes everything else on the contact.
        merged = dict(contact.get("custom_attributes") or {})
        for key in PROFILE_KEYS:
            value = str(row.get(key) or "").strip()
            if value:
                merged[key] = value
        body: dict = {"custom_attributes": merged}
        name = str(row.get("name") or "").strip()
        if name and name != contact.get("name"):
            body["name"] = name
        r = httpx.put(
            f"{args.chatwoot_url}/api/v1/accounts/{args.account_id}/contacts/{contact['id']}",
            headers={"api_access_token": token, "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        r.raise_for_status()
        written += 1

    print(f"\napplied to {written} contacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
