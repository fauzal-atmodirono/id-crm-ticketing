#!/usr/bin/env python3
"""Provision the case_category / case_subcategory Chatwoot custom attribute
definitions from CASE_TAXONOMY_JSON, so Chatwoot's native conversation
sidebar renders them as single-select dropdowns (List type) — the mechanism
that enforces "one main category" without any custom frontend code.

Unlike provision_features.py (account *features*, not in Chatwoot's public
REST API — requires `rails runner`), custom attribute definitions ARE public
REST API resources, so this script talks directly to the HTTP API via httpx.

Idempotent: looks up existing definitions by attribute_key first, PATCHes if
found (updating the option list), POSTs if not. Safe to re-run any time the
taxonomy changes — e.g. once Proton finalizes their scheme.

Usage:
    CASE_TAXONOMY_JSON='...' python3 provision_case_taxonomy.py \\
        --chatwoot-url https://crm.example.com --account-id 1 --api-token <token>
    python3 provision_case_taxonomy.py --dry-run ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def _load_taxonomy(raw: str) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("CASE_TAXONOMY_JSON must be a JSON object")
    return data


def _category_options(taxonomy: dict) -> list[str]:
    return [str(v["label"]) for v in taxonomy.values() if isinstance(v, dict) and "label" in v]


def _subcategory_options(taxonomy: dict) -> list[str]:
    options: list[str] = []
    for v in taxonomy.values():
        if not isinstance(v, dict) or "label" not in v:
            continue
        label = str(v["label"])
        for sub in v.get("subcategories", []) or []:
            options.append(f"{label}: {sub}")
    return options


def _find_existing(client: httpx.Client, base: str, key: str) -> dict | None:
    res = client.get(f"{base}/custom_attribute_definitions")
    res.raise_for_status()
    for defn in res.json():
        if defn.get("attribute_key") == key:
            return defn
    return None


def _upsert(client: httpx.Client, base: str, key: str, name: str, options: list[str], dry_run: bool) -> None:
    payload = {
        "attribute_display_name": name,
        "attribute_display_type": "list",
        "attribute_key": key,
        "attribute_model": "conversation_attribute",
        "attribute_values": options,
    }
    existing = _find_existing(client, base, key)
    if dry_run:
        action = "UPDATE" if existing else "CREATE"
        print(f"[dry-run] {action} {key}: {len(options)} options")
        return
    if existing:
        res = client.patch(f"{base}/custom_attribute_definitions/{existing['id']}", json=payload)
    else:
        res = client.post(f"{base}/custom_attribute_definitions", json=payload)
    res.raise_for_status()
    print(f"{'Updated' if existing else 'Created'} {key} ({len(options)} options)")


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

    raw = os.environ.get("CASE_TAXONOMY_JSON", "").strip()
    if not raw:
        print("error: CASE_TAXONOMY_JSON is not set", file=sys.stderr)
        return 1
    taxonomy = _load_taxonomy(raw)

    base = f"{args.chatwoot_url.rstrip('/')}/api/v1/accounts/{args.account_id}"
    headers = {"api_access_token": args.api_token, "Api-Access-Token": args.api_token}
    with httpx.Client(headers=headers, timeout=15.0) as client:
        _upsert(client, base, "case_category", "Case Category", _category_options(taxonomy), args.dry_run)
        _upsert(client, base, "case_subcategory", "Case Subcategory", _subcategory_options(taxonomy), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
