#!/usr/bin/env python3
"""Register the Taxonomy Manager as a Chatwoot Dashboard App for one tenant.

Usage:
    python register_taxonomy_app.py \
        --tenant default \
        [--app-url http://agent.1-2-3-4.nip.io/apps/taxonomy-manager] \
        [--dry-run]

The Chatwoot Application API endpoint for dashboard apps is:
    GET  /api/v1/accounts/{id}/dashboard_apps
    POST /api/v1/accounts/{id}/dashboard_apps
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

# Re-use the env-file parser from provision_labels
sys.path.insert(0, str(Path(__file__).parent))
from provision_labels import _load_env_file, ChatwootClient, ChatwootError


@dataclass
class DashboardAppConfig:
    title: str
    url: str


def register_dashboard_app(
    base_url: str,
    api_token: str,
    account_id: int,
    cfg: DashboardAppConfig,
    dry_run: bool = False,
) -> str:
    """Return 'created' or 'unchanged'."""
    with ChatwootClient(base_url, api_token, account_id) as client:
        r = client._client.get(client._url("dashboard_apps"))
        client._raise_for_status(r)
        existing = r.json() if isinstance(r.json(), list) else r.json().get("payload", [])
        existing_titles = {app["title"] for app in existing}

        if cfg.title in existing_titles:
            print(f"  [SKIP] Dashboard app '{cfg.title}' already registered.")
            return "unchanged"

        print(f"  [CREATE] Dashboard app '{cfg.title}' -> {cfg.url.split('?')[0]}")
        if not dry_run:
            create_r = client._client.post(
                client._url("dashboard_apps"),
                json={"title": cfg.title, "content": [{"url": cfg.url, "type": "iframe"}]},
            )
            client._raise_for_status(create_r)
        return "created"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Register Taxonomy Manager as a Chatwoot dashboard app.")
    p.add_argument("--tenant", help="Tenant name")
    p.add_argument("--env-file", help="Path to tenant .env file")
    p.add_argument("--chatwoot-url")
    p.add_argument("--api-token")
    p.add_argument("--account-id", type=int)
    p.add_argument("--app-url", required=True,
                   help="Full URL to the taxonomy-manager app (e.g. http://agent.<ip>.nip.io/apps/taxonomy-manager)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    env: dict[str, str] = {}
    env_file_path: Path | None = None
    if args.env_file:
        env_file_path = Path(args.env_file)
    elif args.tenant:
        env_file_path = Path(__file__).parent.parent / "deploy" / "tenants" / f"{args.tenant}.env"

    if env_file_path:
        if not env_file_path.exists():
            print(f"ERROR: env file not found: {env_file_path}", file=sys.stderr)
            return 1
        env = _load_env_file(env_file_path)

    chatwoot_url = args.chatwoot_url or env.get("CHATWOOT_URL") or os.environ.get("CHATWOOT_URL", "")
    api_token = args.api_token or env.get("CHATWOOT_API_TOKEN") or os.environ.get("CHATWOOT_API_TOKEN", "")
    account_id = args.account_id or int(env.get("CHATWOOT_ACCOUNT_ID") or os.environ.get("CHATWOOT_ACCOUNT_ID", "1"))

    if not chatwoot_url or not api_token:
        print("ERROR: chatwoot-url and api-token required.", file=sys.stderr)
        return 1

    app_url = (
        f"{args.app_url}?apiToken={api_token}&accountId={account_id}&chatwootUrl={chatwoot_url}"
    )
    cfg = DashboardAppConfig(title="Taxonomy Manager", url=app_url)

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Registering Taxonomy Manager for '{args.tenant or chatwoot_url}'")
    register_dashboard_app(chatwoot_url, api_token, account_id, cfg, dry_run=args.dry_run)
    print(f"{prefix}Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
