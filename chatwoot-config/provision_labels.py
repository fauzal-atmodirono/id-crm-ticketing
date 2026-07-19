#!/usr/bin/env python3
"""Idempotently provision Chatwoot labels and saved filter views for one tenant.

Usage:
    python provision_labels.py \\
        --tenant proton \\
        [--env-file ../deploy/tenants/proton.env] \\
        [--labels labels.yaml] \\
        [--filters filters.yaml] \\
        [--dry-run]

    # Or pass credentials directly (overrides env-file):
    python provision_labels.py \\
        --chatwoot-url http://crm.1-2-3-4.nip.io \\
        --api-token <token> \\
        --account-id 1

Chatwoot Application API reference:
    Labels:       GET/POST /api/v1/accounts/{id}/labels
                  PATCH    /api/v1/accounts/{id}/labels/{name}
    Saved views:  GET/POST /api/v1/accounts/{id}/custom_filters
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml


# ---------------------------------------------------------------------------
# Chatwoot API client
# ---------------------------------------------------------------------------

class ChatwootError(Exception):
    """Raised when the Chatwoot API returns an unexpected status code."""
    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        super().__init__(f"{method} {url} -> {status}: {body[:200]}")
        self.status = status


class ChatwootClient:
    """Minimal synchronous Chatwoot Application API client."""

    def __init__(self, base_url: str, api_token: str, account_id: int) -> None:
        self._base = base_url.rstrip("/")
        self._account_id = account_id
        self._client = httpx.Client(
            # Send the token in BOTH header forms: some reverse proxies (Caddy)
            # strip headers whose names contain underscores, so the dash form
            # `Api-Access-Token` is what actually reaches Rails through the proxy.
            headers={
                "api_access_token": api_token,
                "Api-Access-Token": api_token,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def _url(self, path: str) -> str:
        return f"{self._base}/api/v1/accounts/{self._account_id}/{path}"

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise ChatwootError(
                response.request.method,
                str(response.request.url),
                response.status_code,
                response.text,
            )

    def list_labels(self) -> list[dict[str, Any]]:
        r = self._client.get(self._url("labels"))
        self._raise_for_status(r)
        data = r.json()
        # Chatwoot returns {"payload": [...]} for label list
        return data.get("payload", data) if isinstance(data, dict) else data

    def create_label(self, name: str, color: str, description: str) -> dict[str, Any]:
        r = self._client.post(
            self._url("labels"),
            json={"title": name, "color": color, "description": description, "show_on_sidebar": True},
        )
        self._raise_for_status(r)
        return r.json()

    def update_label(self, name: str, color: str, description: str) -> dict[str, Any]:
        # Chatwoot label update: PATCH /api/v1/accounts/{id}/labels/{name}
        r = self._client.patch(
            self._url(f"labels/{name}"),
            json={"color": color, "description": description, "show_on_sidebar": True},
        )
        self._raise_for_status(r)
        return r.json()

    def list_custom_filters(self) -> list[dict[str, Any]]:
        r = self._client.get(self._url("custom_filters"))
        self._raise_for_status(r)
        data = r.json()
        return data if isinstance(data, list) else data.get("payload", [])

    def create_saved_filter(
        self, name: str, filter_type: str, query: dict[str, Any]
    ) -> dict[str, Any]:
        r = self._client.post(
            self._url("custom_filters"),
            json={"name": name, "type": filter_type, "query": query},
        )
        self._raise_for_status(r)
        return r.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ChatwootClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Provision logic
# ---------------------------------------------------------------------------

@dataclass
class ProvisionResult:
    labels_created: int = 0
    labels_updated: int = 0
    labels_unchanged: int = 0
    filters_created: int = 0
    filters_unchanged: int = 0

    def __str__(self) -> str:
        return (
            f"Labels  — created: {self.labels_created}, "
            f"updated: {self.labels_updated}, "
            f"unchanged: {self.labels_unchanged}\n"
            f"Filters — created: {self.filters_created}, "
            f"unchanged: {self.filters_unchanged}"
        )


def _normalize_color(color: str) -> str:
    """Ensure color has a leading # and is lowercase."""
    c = color.strip()
    if not c.startswith("#"):
        c = f"#{c}"
    return c.lower()


def provision(
    client: ChatwootClient,
    labels_path: Path,
    filters_path: Path,
    dry_run: bool = False,
) -> ProvisionResult:
    result = ProvisionResult()

    # --- Labels ---
    desired_labels: list[dict[str, Any]] = yaml.safe_load(labels_path.read_text())["labels"]
    existing_labels = {lbl["title"]: lbl for lbl in client.list_labels()}

    for lbl in desired_labels:
        name = lbl["name"]
        color = _normalize_color(lbl["color"])
        description = lbl.get("description", "")

        if name not in existing_labels:
            print(f"  [CREATE] label '{name}' ({color})")
            if not dry_run:
                client.create_label(name, color, description)
            result.labels_created += 1
        else:
            existing = existing_labels[name]
            existing_color = _normalize_color(existing.get("color", ""))
            existing_desc = existing.get("description", "")
            if existing_color != color or existing_desc != description:
                print(f"  [UPDATE] label '{name}' color {existing_color}->{color}")
                if not dry_run:
                    client.update_label(name, color, description)
                result.labels_updated += 1
            else:
                result.labels_unchanged += 1

    # --- Saved filter views ---
    desired_filters: list[dict[str, Any]] = yaml.safe_load(filters_path.read_text())["filters"]
    existing_filter_names = {f["name"] for f in client.list_custom_filters()}

    for flt in desired_filters:
        fname = flt["name"]
        if fname in existing_filter_names:
            result.filters_unchanged += 1
        else:
            print(f"  [CREATE] saved filter '{fname}'")
            if not dry_run:
                client.create_saved_filter(fname, flt["filter_type"], flt["query"])
            result.filters_created += 1

    return result


# ---------------------------------------------------------------------------
# Env-file parser (no shell-expansion, mirrors add-tenant.sh's get_infra_var)
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file (no shell expansion, strips inline comments)."""
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Strip inline comments (after an unquoted space-#)
        value = value.split(" #")[0].strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Idempotently provision Chatwoot labels + saved filter views for one tenant."
    )
    p.add_argument("--tenant", help="Tenant name (loads deploy/tenants/<tenant>.env automatically)")
    p.add_argument("--env-file", help="Explicit path to a tenant .env file")
    p.add_argument("--chatwoot-url", help="Chatwoot base URL (overrides env file)")
    p.add_argument("--api-token", help="Chatwoot API token (overrides env file)")
    p.add_argument("--account-id", type=int, help="Chatwoot account ID (overrides env file)")
    p.add_argument(
        "--labels",
        default=str(Path(__file__).parent / "labels.yaml"),
        help="Path to labels.yaml (default: labels.yaml next to this script)",
    )
    p.add_argument(
        "--filters",
        default=str(Path(__file__).parent / "filters.yaml"),
        help="Path to filters.yaml (default: filters.yaml next to this script)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without making any API calls",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Resolve credentials: CLI flags > env-file > env vars > tenant default path
    env: dict[str, str] = {}
    env_file_path: Path | None = None

    if args.env_file:
        env_file_path = Path(args.env_file)
    elif args.tenant:
        # Assume script lives at chatwoot-config/provision_labels.py, so
        # deploy/tenants/ is two levels up.
        env_file_path = Path(__file__).parent.parent / "deploy" / "tenants" / f"{args.tenant}.env"

    if env_file_path:
        if not env_file_path.exists():
            print(f"ERROR: env file not found: {env_file_path}", file=sys.stderr)
            return 1
        env = _load_env_file(env_file_path)

    chatwoot_url = args.chatwoot_url or env.get("CHATWOOT_URL") or os.environ.get("CHATWOOT_URL", "")
    api_token = args.api_token or env.get("CHATWOOT_API_TOKEN") or os.environ.get("CHATWOOT_API_TOKEN", "")
    account_id_str = str(args.account_id or env.get("CHATWOOT_ACCOUNT_ID") or os.environ.get("CHATWOOT_ACCOUNT_ID", "1"))

    if not chatwoot_url or not api_token:
        print(
            "ERROR: --chatwoot-url and --api-token are required (or set via env file / env vars).",
            file=sys.stderr,
        )
        return 1

    try:
        account_id = int(account_id_str)
    except ValueError:
        print(f"ERROR: CHATWOOT_ACCOUNT_ID must be an integer, got '{account_id_str}'", file=sys.stderr)
        return 1

    labels_path = Path(args.labels)
    filters_path = Path(args.filters)

    for p in (labels_path, filters_path):
        if not p.exists():
            print(f"ERROR: config file not found: {p}", file=sys.stderr)
            return 1

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Provisioning tenant '{args.tenant or chatwoot_url}' (account {account_id})")

    with ChatwootClient(chatwoot_url, api_token, account_id) as client:
        result = provision(client, labels_path, filters_path, dry_run=args.dry_run)

    print(f"\n{prefix}Done.\n{result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
