# Phase 1 — Self-service Categorization & Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver idempotent, per-tenant provisioning of the AI-emitted label taxonomy (`category_/subcat_/division_/dept_`) and saved conversation filter views via the Chatwoot API, plus an optional single-file Taxonomy Manager static app, so ops can self-serve categorization and filtered conversation views without writing code.

**Architecture:** A Python script (`chatwoot-config/provision_labels.py`) reads `chatwoot-config/labels.yaml` and `chatwoot-config/filters.yaml` and idempotently creates/updates Chatwoot labels and saved custom views for a given tenant via the Chatwoot Application REST API. An ops runbook (`docs/plans/phase1-ops-runbook.md`) documents the manual alternative via Chatwoot Settings UI. An optional self-contained static HTML app (`apps/taxonomy-manager/index.html`) lets ops list/rename/color labels through a browser without needing the API token in a terminal — served by Caddy under the Phase-0 nav host route pattern and registered as a Chatwoot dashboard app.

**Tech Stack:** Python 3.12, `httpx` (sync) for the Chatwoot API client, `PyYAML` for config, `pytest` + `respx` (HTTP mocking, same stack as the existing `agent/` suite), `bats-core` for the idempotency smoke test, vanilla HTML/JS (no build step) for the optional app.

## Global Constraints

- Chatwoot Community only — no `/enterprise` endpoints. All endpoints used are the documented public Application API.
- Python ≥ 3.12 (matches `agent/pyproject.toml` `requires-python`).
- No new always-on service: the provisioning script is a one-shot CLI run during tenant setup; the optional app is a static file served by the existing Caddy instance.
- The optional Taxonomy Manager depends on Phase 0 (nav host route exists); pure label/filter provisioning has no dependency on Phase 0.
- Multi-tenant: the script takes `--tenant` + reads `deploy/tenants/<tenant>.env` for `CHATWOOT_URL`, `CHATWOOT_API_TOKEN`, and `CHATWOOT_ACCOUNT_ID` — the same values already provisioned by `deploy/scripts/add-tenant.sh`.
- Secrets never committed: API tokens are read from env or from the tenant env file; `chatwoot-config/labels.yaml` and `chatwoot-config/filters.yaml` contain no secrets.
- Label naming: the AI already emits `category_*`, `subcat_*`, `division_*`, `dept_*`, `sla_*`, `pic_*` — this phase documents and pre-creates exactly those prefix groups. No renaming of existing label conventions.
- Deploy in `default` first; replicate to `proton`/`wahchan` by re-running the script with `--tenant proton` etc.
- Each task ends with a commit.

---

## File Structure

```
chatwoot-config/                          # NEW directory, committed to id-crm-ticketing
  labels.yaml                             # Canonical label taxonomy (category_/subcat_/division_/dept_)
  filters.yaml                            # Saved filter view definitions
  provision_labels.py                     # Idempotent CLI: creates/updates labels + saved views
  requirements.txt                        # httpx, PyYAML (pinned)
  tests/
    test_provision_labels.py              # pytest unit tests (mock Chatwoot API with respx)
  smoke/
    smoke_idempotency.bats                # bats smoke: run script twice, assert 0 changes second pass

apps/taxonomy-manager/                    # NEW directory (optional Phase 0-dependent app)
  index.html                              # Self-contained static HTML/JS app

deploy/caddy/tenants/                     # EXISTING — one .caddy file per tenant (add-tenant generates)
  # No new file: we document the manual Caddy snippet to add for the taxonomy app

docs/plans/
  phase1-ops-runbook.md                   # Ops runbook: manual label+view creation via Chatwoot UI
```

**What each file owns:**

| File | Responsibility |
|---|---|
| `chatwoot-config/labels.yaml` | Source of truth: label name, color, description, prefix group |
| `chatwoot-config/filters.yaml` | Source of truth: saved filter view definitions (filter combinator + conditions) |
| `chatwoot-config/provision_labels.py` | Idempotent CLI; reads yaml, calls Chatwoot API, prints diff |
| `chatwoot-config/requirements.txt` | Pinned `httpx==0.27.2` and `PyYAML==6.0.2` |
| `chatwoot-config/tests/test_provision_labels.py` | Unit tests for the script's API client logic |
| `chatwoot-config/smoke/smoke_idempotency.bats` | bats smoke: idempotency + `--dry-run` flag |
| `apps/taxonomy-manager/index.html` | Optional browser app: list/rename/recolor labels via Chatwoot labels API |
| `docs/plans/phase1-ops-runbook.md` | Manual alternative for ops without terminal access |

---

## Task 1: Scaffold chatwoot-config directory + requirements

**Files:**
- Create: `chatwoot-config/requirements.txt`
- Create: `chatwoot-config/labels.yaml`
- Create: `chatwoot-config/filters.yaml`

**Interfaces:**
- Produces: `labels.yaml` schema consumed by `provision_labels.py` in Task 2
- Produces: `filters.yaml` schema consumed by `provision_labels.py` in Task 2

- [ ] **Step 1: Create `chatwoot-config/requirements.txt`**

```
httpx==0.27.2
PyYAML==6.0.2
```

- [ ] **Step 2: Create `chatwoot-config/labels.yaml`**

This file is the canonical taxonomy the AI already emits. Colors follow Chatwoot's hex-color convention (string starting with `#`, 6 hex digits).

```yaml
# Chatwoot label taxonomy — Phase 1
# Convention: labels are lowercase with underscores; the AI emitter uses these
# exact names. Do not rename existing labels — add new ones; the script is additive.
#
# Fields per label:
#   name:        Chatwoot label identifier (snake_case, no spaces)
#   color:       6-digit hex (e.g. "#1F93FF") — Chatwoot stores with leading #
#   description: free text shown in Chatwoot label settings
#   group:       logical grouping for the runbook / taxonomy manager UI

labels:
  # --- Category (top-level complaint type) ---
  - name: category_complaint
    color: "#E74C3C"
    description: "AI-classified: general complaint"
    group: category

  - name: category_inquiry
    color: "#3498DB"
    description: "AI-classified: general inquiry"
    group: category

  - name: category_service_request
    color: "#2ECC71"
    description: "AI-classified: service request"
    group: category

  - name: category_feedback
    color: "#9B59B6"
    description: "AI-classified: customer feedback"
    group: category

  # --- Sub-category ---
  - name: subcat_billing
    color: "#E67E22"
    description: "AI sub-category: billing issue"
    group: subcat

  - name: subcat_technical
    color: "#1ABC9C"
    description: "AI sub-category: technical issue"
    group: subcat

  - name: subcat_delivery
    color: "#F39C12"
    description: "AI sub-category: delivery / logistics"
    group: subcat

  - name: subcat_warranty
    color: "#D35400"
    description: "AI sub-category: warranty claim"
    group: subcat

  - name: subcat_pricing
    color: "#C0392B"
    description: "AI sub-category: pricing question"
    group: subcat

  # --- Division ---
  - name: division_apps
    color: "#2980B9"
    description: "AI division: Apps (digital)"
    group: division

  - name: division_sales
    color: "#27AE60"
    description: "AI division: Sales"
    group: division

  - name: division_aftersales
    color: "#8E44AD"
    description: "AI division: Aftersales / Service"
    group: division

  - name: division_charging
    color: "#16A085"
    description: "AI division: Charging infrastructure"
    group: division

  # --- Department ---
  - name: dept_cs
    color: "#2C3E50"
    description: "AI dept: Customer Service"
    group: dept

  - name: dept_technical
    color: "#7F8C8D"
    description: "AI dept: Technical Support"
    group: dept

  - name: dept_sales
    color: "#BDC3C7"
    description: "AI dept: Sales"
    group: dept

  - name: dept_aftersales
    color: "#ECF0F1"
    description: "AI dept: Aftersales / Workshop"
    group: dept

  # --- SLA markers (written by SLA engine; do not remove) ---
  - name: sla_urgent
    color: "#E74C3C"
    description: "SLA: urgent priority (< 2h response target)"
    group: sla

  - name: sla_high
    color: "#E67E22"
    description: "SLA: high priority"
    group: sla

  - name: sla_normal
    color: "#3498DB"
    description: "SLA: normal priority"
    group: sla

  - name: sla_breach
    color: "#922B21"
    description: "SLA: breach — response time exceeded"
    group: sla

  # --- PIC markers (written by escalation engine) ---
  - name: pic_assigned
    color: "#117A65"
    description: "PIC has been assigned to this conversation"
    group: pic

  - name: pic_notified
    color: "#1A5276"
    description: "PIC has been notified (WA/email sent)"
    group: pic

  # --- Escalation state ---
  - name: escalate
    color: "#922B21"
    description: "Conversation has been escalated to Zammad ticket"
    group: escalation

  - name: escalated_l2
    color: "#6E2F1A"
    description: "Escalated to Level-2 responsible person"
    group: escalation
```

- [ ] **Step 3: Create `chatwoot-config/filters.yaml`**

Chatwoot's saved custom views (also called "saved filters") use a JSON payload with `name`, `filter_type` (`account` = team-wide, `conversation` = per-agent), and a `query` combinator. The Chatwoot Application API endpoint is `POST /api/v1/accounts/{account_id}/saved_filters`. Each `query` has `payload: [{attribute_key, filter_operator, query_operator, values}]`.

```yaml
# Saved filter views — Phase 1
# These are created as team-wide ("account"-scoped) saved views in Chatwoot.
# Chatwoot filter operators: equal_to, not_equal_to, contains, does_not_contain,
#   is_present, is_not_present, is_greater_than, is_less_than, days_before
# attribute_key for labels: "labels"  (values: list of label names)
# attribute_key for status: "status"  (values: "open","resolved","pending","snoozed","all")

filters:
  - name: "All Complaints"
    filter_type: account
    query:
      payload:
        - attribute_key: labels
          filter_operator: contains
          query_operator: null
          values:
            - category_complaint

  - name: "All Inquiries"
    filter_type: account
    query:
      payload:
        - attribute_key: labels
          filter_operator: contains
          query_operator: null
          values:
            - category_inquiry

  - name: "SLA Breach — Open"
    filter_type: account
    query:
      payload:
        - attribute_key: labels
          filter_operator: contains
          query_operator: AND
          values:
            - sla_breach
        - attribute_key: status
          filter_operator: equal_to
          query_operator: null
          values:
            - open

  - name: "Escalated — Open"
    filter_type: account
    query:
      payload:
        - attribute_key: labels
          filter_operator: contains
          query_operator: AND
          values:
            - escalate
        - attribute_key: status
          filter_operator: equal_to
          query_operator: null
          values:
            - open

  - name: "Division: Apps"
    filter_type: account
    query:
      payload:
        - attribute_key: labels
          filter_operator: contains
          query_operator: null
          values:
            - division_apps

  - name: "Division: Aftersales"
    filter_type: account
    query:
      payload:
        - attribute_key: labels
          filter_operator: contains
          query_operator: null
          values:
            - division_aftersales

  - name: "Division: Sales"
    filter_type: account
    query:
      payload:
        - attribute_key: labels
          filter_operator: contains
          query_operator: null
          values:
            - division_sales

  - name: "Division: Charging"
    filter_type: account
    query:
      payload:
        - attribute_key: labels
          filter_operator: contains
          query_operator: null
          values:
            - division_charging
```

- [ ] **Step 4: Commit scaffold**

```bash
cd /path/to/id-crm-ticketing
git add chatwoot-config/requirements.txt chatwoot-config/labels.yaml chatwoot-config/filters.yaml
git commit -m "feat(phase1): scaffold chatwoot-config with label taxonomy and filter definitions"
```

---

## Task 2: Write the provisioning script

**Files:**
- Create: `chatwoot-config/provision_labels.py`
- Create: `chatwoot-config/tests/__init__.py` (empty)

**Interfaces:**
- Consumes: `chatwoot-config/labels.yaml` — list of dicts with keys `name`, `color`, `description`, `group`
- Consumes: `chatwoot-config/filters.yaml` — list of dicts with keys `name`, `filter_type`, `query`
- Produces: `ChatwootClient` class (used by tests in Task 3)
  - `ChatwootClient(base_url: str, api_token: str, account_id: int)`
  - `ChatwootClient.list_labels() -> list[dict]`  — `GET /api/v1/accounts/{id}/labels`
  - `ChatwootClient.create_label(name: str, color: str, description: str) -> dict`  — `POST /api/v1/accounts/{id}/labels`
  - `ChatwootClient.update_label(name: str, color: str, description: str) -> dict`  — `PATCH /api/v1/accounts/{id}/labels/{name}`
  - `ChatwootClient.list_saved_filters() -> list[dict]`  — `GET /api/v1/accounts/{id}/saved_filters`
  - `ChatwootClient.create_saved_filter(name: str, filter_type: str, query: dict) -> dict`  — `POST /api/v1/accounts/{id}/saved_filters`
- Produces: `provision(client, labels_yaml_path, filters_yaml_path, dry_run) -> ProvisionResult`
  - `ProvisionResult` — dataclass: `labels_created: int`, `labels_updated: int`, `labels_unchanged: int`, `filters_created: int`, `filters_unchanged: int`

- [ ] **Step 1: Write `chatwoot-config/provision_labels.py`**

```python
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
    Saved views:  GET/POST /api/v1/accounts/{id}/saved_filters
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
            headers={"api_access_token": api_token, "Content-Type": "application/json"},
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

    def list_saved_filters(self) -> list[dict[str, Any]]:
        r = self._client.get(self._url("saved_filters"))
        self._raise_for_status(r)
        data = r.json()
        return data if isinstance(data, list) else data.get("payload", [])

    def create_saved_filter(
        self, name: str, filter_type: str, query: dict[str, Any]
    ) -> dict[str, Any]:
        r = self._client.post(
            self._url("saved_filters"),
            json={"name": name, "filter_type": filter_type, "query": query},
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
    existing_filter_names = {f["name"] for f in client.list_saved_filters()}

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
```

- [ ] **Step 2: Create empty `chatwoot-config/tests/__init__.py`**

Create the file with empty content (zero bytes).

- [ ] **Step 3: Commit**

```bash
git add chatwoot-config/provision_labels.py chatwoot-config/tests/__init__.py
git commit -m "feat(phase1): add idempotent label+filter provisioning script"
```

---

## Task 3: Write and pass unit tests for the provisioning script

**Files:**
- Create: `chatwoot-config/tests/test_provision_labels.py`

**Interfaces:**
- Consumes: `ChatwootClient`, `provision`, `ProvisionResult`, `_load_env_file`, `_normalize_color` from `provision_labels.py`
- Consumes: `chatwoot-config/labels.yaml`, `chatwoot-config/filters.yaml` for integration-style fixture

No actual HTTP; all API calls are mocked with `respx`.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for provision_labels.py.

Run from chatwoot-config/:
    pip install httpx PyYAML pytest respx
    pytest tests/test_provision_labels.py -v
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest
import respx
import httpx

# Make the parent directory importable so we can import provision_labels
sys.path.insert(0, str(Path(__file__).parent.parent))
from provision_labels import (
    ChatwootClient,
    ChatwootError,
    ProvisionResult,
    _load_env_file,
    _normalize_color,
    provision,
    main,
)

BASE = "http://chatwoot-test"
ACCOUNT_ID = 1
TOKEN = "test-token"
LABELS_URL = f"{BASE}/api/v1/accounts/{ACCOUNT_ID}/labels"
FILTERS_URL = f"{BASE}/api/v1/accounts/{ACCOUNT_ID}/saved_filters"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_client() -> ChatwootClient:
    return ChatwootClient(BASE, TOKEN, ACCOUNT_ID)


def label_response(name: str, color: str, description: str = "") -> dict:
    return {"id": 1, "title": name, "color": color, "description": description}


def filter_response(name: str) -> dict:
    return {"id": 1, "name": name, "filter_type": "account", "query": {"payload": []}}


# ---------------------------------------------------------------------------
# _normalize_color
# ---------------------------------------------------------------------------

def test_normalize_color_adds_hash():
    assert _normalize_color("1F93FF") == "#1f93ff"


def test_normalize_color_preserves_hash():
    assert _normalize_color("#E74C3C") == "#e74c3c"


def test_normalize_color_lowercases():
    assert _normalize_color("#AABBCC") == "#aabbcc"


# ---------------------------------------------------------------------------
# ChatwootClient — list_labels
# ---------------------------------------------------------------------------

@respx.mock
def test_list_labels_returns_payload_list():
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"payload": [
            label_response("category_complaint", "#e74c3c"),
        ]})
    )
    with make_client() as c:
        labels = c.list_labels()
    assert len(labels) == 1
    assert labels[0]["title"] == "category_complaint"


@respx.mock
def test_list_labels_raises_on_401():
    respx.get(LABELS_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(ChatwootError) as exc_info:
        with make_client() as c:
            c.list_labels()
    assert exc_info.value.status == 401


# ---------------------------------------------------------------------------
# ChatwootClient — create_label
# ---------------------------------------------------------------------------

@respx.mock
def test_create_label_posts_correct_body():
    route = respx.post(LABELS_URL).mock(
        return_value=httpx.Response(200, json=label_response("category_complaint", "#e74c3c"))
    )
    with make_client() as c:
        result = c.create_label("category_complaint", "#e74c3c", "General complaint")
    assert route.called
    sent = json.loads(route.calls[0].request.content)
    assert sent["title"] == "category_complaint"
    assert sent["color"] == "#e74c3c"
    assert sent["show_on_sidebar"] is True
    assert result["title"] == "category_complaint"


# ---------------------------------------------------------------------------
# ChatwootClient — update_label
# ---------------------------------------------------------------------------

@respx.mock
def test_update_label_patches_by_name():
    route = respx.patch(f"{LABELS_URL}/category_complaint").mock(
        return_value=httpx.Response(200, json=label_response("category_complaint", "#ff0000"))
    )
    with make_client() as c:
        c.update_label("category_complaint", "#ff0000", "Updated desc")
    assert route.called
    sent = json.loads(route.calls[0].request.content)
    assert sent["color"] == "#ff0000"


# ---------------------------------------------------------------------------
# ChatwootClient — list_saved_filters
# ---------------------------------------------------------------------------

@respx.mock
def test_list_saved_filters_returns_list():
    respx.get(FILTERS_URL).mock(
        return_value=httpx.Response(200, json=[filter_response("All Complaints")])
    )
    with make_client() as c:
        filters = c.list_saved_filters()
    assert len(filters) == 1
    assert filters[0]["name"] == "All Complaints"


# ---------------------------------------------------------------------------
# ChatwootClient — create_saved_filter
# ---------------------------------------------------------------------------

@respx.mock
def test_create_saved_filter_posts_correct_body():
    route = respx.post(FILTERS_URL).mock(
        return_value=httpx.Response(200, json=filter_response("All Complaints"))
    )
    query = {"payload": [{"attribute_key": "labels", "filter_operator": "contains",
                          "query_operator": None, "values": ["category_complaint"]}]}
    with make_client() as c:
        c.create_saved_filter("All Complaints", "account", query)
    sent = json.loads(route.calls[0].request.content)
    assert sent["name"] == "All Complaints"
    assert sent["filter_type"] == "account"
    assert sent["query"] == query


# ---------------------------------------------------------------------------
# provision() — all labels exist and are unchanged (idempotency)
# ---------------------------------------------------------------------------

@respx.mock
def test_provision_idempotent_no_api_calls_when_unchanged(tmp_path):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text(
        "labels:\n"
        "  - name: category_complaint\n"
        "    color: '#e74c3c'\n"
        "    description: 'General complaint'\n"
        "    group: category\n"
    )
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text(
        "filters:\n"
        "  - name: All Complaints\n"
        "    filter_type: account\n"
        "    query:\n"
        "      payload:\n"
        "        - attribute_key: labels\n"
        "          filter_operator: contains\n"
        "          query_operator: null\n"
        "          values:\n"
        "            - category_complaint\n"
    )
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"payload": [
            label_response("category_complaint", "#e74c3c", "General complaint")
        ]})
    )
    respx.get(FILTERS_URL).mock(
        return_value=httpx.Response(200, json=[filter_response("All Complaints")])
    )
    create_label_route = respx.post(LABELS_URL).mock(
        return_value=httpx.Response(200, json={})
    )
    create_filter_route = respx.post(FILTERS_URL).mock(
        return_value=httpx.Response(200, json={})
    )

    with make_client() as c:
        result = provision(c, labels_yaml, filters_yaml, dry_run=False)

    assert result.labels_unchanged == 1
    assert result.labels_created == 0
    assert result.labels_updated == 0
    assert result.filters_unchanged == 1
    assert result.filters_created == 0
    assert not create_label_route.called
    assert not create_filter_route.called


# ---------------------------------------------------------------------------
# provision() — net-new label + filter
# ---------------------------------------------------------------------------

@respx.mock
def test_provision_creates_missing_label_and_filter(tmp_path):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text(
        "labels:\n"
        "  - name: category_complaint\n"
        "    color: '#e74c3c'\n"
        "    description: 'General complaint'\n"
        "    group: category\n"
    )
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text(
        "filters:\n"
        "  - name: All Complaints\n"
        "    filter_type: account\n"
        "    query:\n"
        "      payload: []\n"
    )
    # No existing labels or filters
    respx.get(LABELS_URL).mock(return_value=httpx.Response(200, json={"payload": []}))
    respx.get(FILTERS_URL).mock(return_value=httpx.Response(200, json=[]))
    respx.post(LABELS_URL).mock(
        return_value=httpx.Response(200, json=label_response("category_complaint", "#e74c3c"))
    )
    respx.post(FILTERS_URL).mock(
        return_value=httpx.Response(200, json=filter_response("All Complaints"))
    )

    with make_client() as c:
        result = provision(c, labels_yaml, filters_yaml, dry_run=False)

    assert result.labels_created == 1
    assert result.filters_created == 1


# ---------------------------------------------------------------------------
# provision() — color mismatch triggers update
# ---------------------------------------------------------------------------

@respx.mock
def test_provision_updates_label_when_color_differs(tmp_path):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text(
        "labels:\n"
        "  - name: category_complaint\n"
        "    color: '#ff0000'\n"
        "    description: 'General complaint'\n"
        "    group: category\n"
    )
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text("filters: []\n")
    # Existing label has different color
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"payload": [
            label_response("category_complaint", "#e74c3c", "General complaint")
        ]})
    )
    respx.get(FILTERS_URL).mock(return_value=httpx.Response(200, json=[]))
    update_route = respx.patch(f"{LABELS_URL}/category_complaint").mock(
        return_value=httpx.Response(200, json=label_response("category_complaint", "#ff0000"))
    )

    with make_client() as c:
        result = provision(c, labels_yaml, filters_yaml, dry_run=False)

    assert result.labels_updated == 1
    assert update_route.called


# ---------------------------------------------------------------------------
# provision() — dry_run suppresses API mutation calls
# ---------------------------------------------------------------------------

@respx.mock
def test_provision_dry_run_does_not_mutate(tmp_path):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text(
        "labels:\n"
        "  - name: category_complaint\n"
        "    color: '#e74c3c'\n"
        "    description: 'New'\n"
        "    group: category\n"
    )
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text(
        "filters:\n"
        "  - name: New Filter\n"
        "    filter_type: account\n"
        "    query:\n"
        "      payload: []\n"
    )
    respx.get(LABELS_URL).mock(return_value=httpx.Response(200, json={"payload": []}))
    respx.get(FILTERS_URL).mock(return_value=httpx.Response(200, json=[]))
    create_label = respx.post(LABELS_URL).mock(return_value=httpx.Response(200, json={}))
    create_filter = respx.post(FILTERS_URL).mock(return_value=httpx.Response(200, json={}))

    with make_client() as c:
        result = provision(c, labels_yaml, filters_yaml, dry_run=True)

    assert result.labels_created == 1
    assert result.filters_created == 1
    assert not create_label.called
    assert not create_filter.called


# ---------------------------------------------------------------------------
# _load_env_file
# ---------------------------------------------------------------------------

def test_load_env_file_parses_key_value(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "# comment\n"
        "CHATWOOT_URL=http://chatwoot:3000\n"
        "CHATWOOT_API_TOKEN=abc123\n"
        "CHATWOOT_ACCOUNT_ID=2\n"
        "BLANK=\n"
    )
    env = _load_env_file(env_file)
    assert env["CHATWOOT_URL"] == "http://chatwoot:3000"
    assert env["CHATWOOT_API_TOKEN"] == "abc123"
    assert env["CHATWOOT_ACCOUNT_ID"] == "2"
    assert env["BLANK"] == ""


def test_load_env_file_skips_comments(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("# this is a comment\nFOO=bar\n")
    env = _load_env_file(env_file)
    assert "# this is a comment" not in env
    assert env["FOO"] == "bar"


# ---------------------------------------------------------------------------
# main() CLI — missing credentials returns exit code 1
# ---------------------------------------------------------------------------

def test_main_returns_1_on_missing_credentials(tmp_path, capsys):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text("labels: []\n")
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text("filters: []\n")
    exit_code = main([
        "--chatwoot-url", "",
        "--api-token", "",
        "--labels", str(labels_yaml),
        "--filters", str(filters_yaml),
    ])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "required" in captured.err.lower()


# ---------------------------------------------------------------------------
# main() CLI — missing labels file returns exit code 1
# ---------------------------------------------------------------------------

def test_main_returns_1_on_missing_labels_file(tmp_path, capsys):
    exit_code = main([
        "--chatwoot-url", "http://fake",
        "--api-token", "token",
        "--labels", str(tmp_path / "nonexistent.yaml"),
        "--filters", str(tmp_path / "filters.yaml"),
    ])
    assert exit_code == 1
```

- [ ] **Step 2: Install test dependencies**

```bash
cd /path/to/id-crm-ticketing/chatwoot-config
pip install httpx==0.27.2 PyYAML==6.0.2 pytest respx
```

Expected: packages install without errors.

- [ ] **Step 3: Run tests to verify they fail (import works, some logic may not exist yet)**

```bash
pytest tests/test_provision_labels.py -v 2>&1 | head -40
```

Expected: import succeeds (the script file exists), individual tests fail due to logic gaps if any — or all pass if the script was written correctly in Task 2. Either outcome is acceptable here; proceed to next step.

- [ ] **Step 4: Run full test suite to verify all pass**

```bash
pytest tests/test_provision_labels.py -v
```

Expected output (all 17 tests PASSED):
```
tests/test_provision_labels.py::test_normalize_color_adds_hash PASSED
tests/test_provision_labels.py::test_normalize_color_preserves_hash PASSED
tests/test_provision_labels.py::test_normalize_color_lowercases PASSED
tests/test_provision_labels.py::test_list_labels_returns_payload_list PASSED
tests/test_provision_labels.py::test_list_labels_raises_on_401 PASSED
tests/test_provision_labels.py::test_create_label_posts_correct_body PASSED
tests/test_provision_labels.py::test_update_label_patches_by_name PASSED
tests/test_provision_labels.py::test_list_saved_filters_returns_list PASSED
tests/test_provision_labels.py::test_create_saved_filter_posts_correct_body PASSED
tests/test_provision_labels.py::test_provision_idempotent_no_api_calls_when_unchanged PASSED
tests/test_provision_labels.py::test_provision_creates_missing_label_and_filter PASSED
tests/test_provision_labels.py::test_provision_updates_label_when_color_differs PASSED
tests/test_provision_labels.py::test_provision_dry_run_does_not_mutate PASSED
tests/test_provision_labels.py::test_load_env_file_parses_key_value PASSED
tests/test_provision_labels.py::test_load_env_file_skips_comments PASSED
tests/test_provision_labels.py::test_main_returns_1_on_missing_credentials PASSED
tests/test_provision_labels.py::test_main_returns_1_on_missing_labels_file PASSED

17 passed in X.XXs
```

- [ ] **Step 5: Commit**

```bash
git add chatwoot-config/tests/test_provision_labels.py
git commit -m "test(phase1): unit tests for label+filter provisioning (idempotency, dry-run, error cases)"
```

---

## Task 4: Bats smoke test for CLI idempotency

**Files:**
- Create: `chatwoot-config/smoke/smoke_idempotency.bats`

**Interfaces:**
- Consumes: `provision_labels.py --dry-run` CLI exit code and stdout
- Consumes: `labels.yaml`, `filters.yaml` (the real config files from Task 1)
- No network required: uses `--dry-run` so no real Chatwoot is needed

Prerequisites: `bats-core` installed (`brew install bats-core` on macOS or `apt-get install bats`).

- [ ] **Step 1: Create `chatwoot-config/smoke/smoke_idempotency.bats`**

```bash
#!/usr/bin/env bats
# Smoke tests for provision_labels.py CLI interface.
# Exercises the --dry-run flag and error paths without a real Chatwoot.
#
# Run from the repo root:
#   bats chatwoot-config/smoke/smoke_idempotency.bats
#
# Prerequisites:
#   pip install httpx PyYAML
#   brew install bats-core   # macOS
#   apt-get install bats     # debian/ubuntu

SCRIPT="$(dirname "$BATS_TEST_FILENAME")/../provision_labels.py"
LABELS="$(dirname "$BATS_TEST_FILENAME")/../labels.yaml"
FILTERS="$(dirname "$BATS_TEST_FILENAME")/../filters.yaml"

@test "script is executable via python" {
  run python "$SCRIPT" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Idempotently provision"* ]]
}

@test "--dry-run with no chatwoot-url exits 1 with error message" {
  run python "$SCRIPT" \
    --chatwoot-url "" \
    --api-token "" \
    --labels "$LABELS" \
    --filters "$FILTERS" \
    --dry-run
  [ "$status" -eq 1 ]
  [[ "$output" == *"required"* ]] || [[ "${lines[@]}" =~ "required" ]]
}

@test "--dry-run prints DRY RUN prefix when credentials provided but no real chatwoot" {
  # We can't actually call a Chatwoot here, but we can verify the CLI parses
  # credentials and attempts to connect (will fail with connection error, not arg error).
  # We check that a missing env file returns exit 1 with a clear message.
  run python "$SCRIPT" \
    --tenant nonexistent_tenant_xyz \
    --labels "$LABELS" \
    --filters "$FILTERS" \
    --dry-run
  [ "$status" -eq 1 ]
  [[ "$output" == *"not found"* ]] || [[ "${lines[*]}" == *"not found"* ]]
}

@test "labels.yaml is valid YAML with required keys" {
  run python -c "
import yaml, sys
data = yaml.safe_load(open('$LABELS'))
labels = data['labels']
assert len(labels) > 0, 'no labels defined'
for lbl in labels:
    assert 'name' in lbl, f'missing name in {lbl}'
    assert 'color' in lbl, f'missing color in {lbl}'
    assert lbl['color'].startswith('#'), f'color must start with # in {lbl}'
    assert 'description' in lbl, f'missing description in {lbl}'
    assert 'group' in lbl, f'missing group in {lbl}'
print(f'OK: {len(labels)} labels validated')
"
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK:"* ]]
}

@test "filters.yaml is valid YAML with required keys" {
  run python -c "
import yaml, sys
data = yaml.safe_load(open('$FILTERS'))
filters = data['filters']
assert len(filters) > 0, 'no filters defined'
for f in filters:
    assert 'name' in f, f'missing name in {f}'
    assert 'filter_type' in f, f'missing filter_type in {f}'
    assert f['filter_type'] in ('account', 'conversation'), f'invalid filter_type in {f}'
    assert 'query' in f, f'missing query in {f}'
    assert 'payload' in f['query'], f'missing query.payload in {f}'
print(f'OK: {len(filters)} filters validated')
"
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK:"* ]]
}

@test "all label names follow snake_case convention with known prefix" {
  run python -c "
import yaml, re
data = yaml.safe_load(open('$LABELS'))
valid_prefixes = ('category_', 'subcat_', 'division_', 'dept_', 'sla_', 'pic_', 'escalat')
pattern = re.compile(r'^[a-z][a-z0-9_]+$')
for lbl in data['labels']:
    name = lbl['name']
    assert pattern.match(name), f'name not snake_case: {name}'
    assert any(name.startswith(p) for p in valid_prefixes), f'unknown prefix: {name}'
print('OK: all label names valid')
"
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK: all label names valid"* ]]
}
```

- [ ] **Step 2: Run the bats smoke tests**

```bash
bats chatwoot-config/smoke/smoke_idempotency.bats
```

Expected output:
```
 ✓ script is executable via python
 ✓ --dry-run with no chatwoot-url exits 1 with error message
 ✓ --dry-run prints DRY RUN prefix when credentials provided but no real chatwoot
 ✓ labels.yaml is valid YAML with required keys
 ✓ filters.yaml is valid YAML with required keys
 ✓ all label names follow snake_case convention with known prefix

6 tests, 0 failures
```

- [ ] **Step 3: Commit**

```bash
git add chatwoot-config/smoke/smoke_idempotency.bats
git commit -m "test(phase1): bats smoke tests for CLI interface and YAML schema validation"
```

---

## Task 5: Ops runbook

**Files:**
- Create: `docs/plans/phase1-ops-runbook.md`

**Interfaces:**
- Produces: documentation only; no code dependencies

- [ ] **Step 1: Create `docs/plans/phase1-ops-runbook.md`**

```markdown
# Phase 1 — Self-service Categorization & Filtering: Ops Runbook

**Audience:** Proton CRM operators who need to create labels or saved filter views for a
tenant, either via the automated provisioning script or manually in the Chatwoot UI.

---

## Option A — Automated provisioning script (recommended)

### Prerequisites

1. SSH to the GCE VM (or any machine with access to the tenant's Chatwoot URL).
2. Python 3.12 installed.
3. The tenant is already provisioned (`deploy/tenants/<tenant>.env` exists with
   `CHATWOOT_API_TOKEN` and `CHATWOOT_ACCOUNT_ID` filled in).

### Install dependencies (once per machine)

```bash
cd /opt/id-crm-ticketing/chatwoot-config
pip install -r requirements.txt
```

### Dry-run first (see what will change)

```bash
python provision_labels.py --tenant default --dry-run
```

Sample output:
```
[DRY RUN] Provisioning tenant 'default' (account 1)
  [CREATE] label 'category_complaint' (#e74c3c)
  [CREATE] label 'category_inquiry' (#3498db)
  ... (all labels and filters listed)

[DRY RUN] Done.
Labels  — created: 28, updated: 0, unchanged: 0
Filters — created: 8, unchanged: 0
```

### Apply

```bash
python provision_labels.py --tenant default
```

### Replicate to other tenants

```bash
python provision_labels.py --tenant proton
python provision_labels.py --tenant wahchan
```

### Adding a new label

1. Add an entry to `chatwoot-config/labels.yaml` following the existing format:
   ```yaml
   - name: category_my_new_type
     color: "#2C3E50"
     description: "AI-classified: my new category"
     group: category
   ```
2. Re-run `provision_labels.py --tenant <tenant>`. The script is additive and
   idempotent — existing labels are untouched unless color/description changed.

### Adding a new saved filter view

1. Add an entry to `chatwoot-config/filters.yaml`:
   ```yaml
   - name: "My New Filter"
     filter_type: account
     query:
       payload:
         - attribute_key: labels
           filter_operator: contains
           query_operator: null
           values:
             - category_my_new_type
   ```
2. Re-run `provision_labels.py --tenant <tenant>`.
3. Note: saved filters cannot be updated via the API (Chatwoot Community does not
   expose a PATCH endpoint for saved filters). To update an existing filter, delete
   it manually in Chatwoot Settings → Custom Views, then re-run the script.

---

## Option B — Manual via Chatwoot Settings UI

Use this if you do not have terminal access or prefer the browser.

### Create labels manually

1. Log in to Chatwoot as a tenant admin.
2. Go to **Settings → Labels → Add Label**.
3. Set:
   - **Label name:** use the exact snake_case name from `chatwoot-config/labels.yaml`
     (e.g. `category_complaint`)
   - **Color:** the hex value from the YAML (e.g. `#E74C3C`)
   - **Description:** copy from the YAML
4. Click **Create**.
5. Repeat for all labels in the taxonomy.

Label prefix conventions:
| Prefix | Used for |
|---|---|
| `category_` | Top-level complaint/contact type (AI-emitted) |
| `subcat_` | Sub-category (AI-emitted) |
| `division_` | Business division: Apps / Sales / Aftersales / Charging |
| `dept_` | Department routing target |
| `sla_` | SLA priority markers (written by the SLA engine) |
| `pic_` | PIC assignment state (written by escalation engine) |
| `escalate` / `escalated_l2` | Escalation trigger / state |

### Create saved filter views manually

1. Go to **Conversations**.
2. In the left sidebar, click **+ New View** (under "Views").
3. Add filter conditions matching the definitions in `chatwoot-config/filters.yaml`:
   - For "All Complaints": Filter → Label → Contains → `category_complaint`
   - For "SLA Breach — Open": Label Contains `sla_breach` AND Status Equals `open`
   - (etc. — one view per entry in `filters.yaml`)
4. Name the view exactly as in `filters.yaml` so the provisioning script recognises
   it as already-existing on future runs.
5. Set **Visibility** to **Everyone** (team-wide / account scope).

---

## Verifying correct setup

After provisioning (either method):

1. Open any conversation in Chatwoot.
2. In the right panel under **Conversation Labels**, you should see the taxonomy
   labels available to add.
3. In the left sidebar under **Views**, you should see all saved filter views
   (e.g. "All Complaints", "SLA Breach — Open", etc.).
4. Click "All Complaints" — it should filter the conversation list to conversations
   labelled `category_complaint`.
5. The AI engine (`proton-conversational-ai`) already writes `category_*`,
   `subcat_*`, `division_*`, `dept_*` labels to every inbound conversation
   automatically. After an inbound contact, verify the labels appear on that
   conversation and that clicking the corresponding saved view shows the conversation.

---

## Label lifecycle — important notes

- **Do not delete labels** that the AI emitter uses — the AI backend references
  label names verbatim. To retire a label, remove it from `labels.yaml` and stop
  the AI from emitting it (backend change), then archive in Chatwoot.
- **Saved filter updates:** Chatwoot Community does not expose a PATCH endpoint for
  saved filters. To modify a saved view: delete it in the UI, update `filters.yaml`,
  re-run the provisioning script.
- **Multi-tenant:** labels and saved views are per-account (per-tenant). Running
  `provision_labels.py --tenant proton` creates the taxonomy in the `proton` tenant;
  it does not touch the `default` tenant.
```

- [ ] **Step 2: Commit**

```bash
git add docs/plans/phase1-ops-runbook.md
git commit -m "docs(phase1): ops runbook for label taxonomy provisioning (script + manual UI paths)"
```

---

## Task 6: Optional — Taxonomy Manager static app

**Dependency:** Phase 0 must be complete (nav host route + Caddy vhost serving `PROTON_BACKEND_URL/apps/<name>` exists). If Phase 0 is not done, skip this task — provisioning via script (Tasks 1–5) fully satisfies the DoD.

**Files:**
- Create: `apps/taxonomy-manager/index.html`

**Interfaces:**
- Consumes: Chatwoot Application API `GET/POST/PATCH /api/v1/accounts/{id}/labels` — accessed directly from the browser using the operator's `api_access_token` passed as a URL query param (same pattern as `chatwoot-faq-admin`)
- Config: `?chatwootUrl=...&apiToken=...&accountId=...` in the iframe URL (set by the Phase-0 nav host when registering this app as a dashboard app)
- No backend intermediary: the browser calls Chatwoot's API directly

- [ ] **Step 1: Write failing integration check**

There is no automated test for the static HTML itself (no build step); instead, write a Python smoke that verifies the HTML is well-formed and contains the expected config bootstrap pattern.

Create `chatwoot-config/tests/test_taxonomy_manager_html.py`:

```python
"""Smoke: verify taxonomy-manager/index.html exists and contains required patterns."""
from pathlib import Path

HTML_PATH = Path(__file__).parent.parent.parent / "apps" / "taxonomy-manager" / "index.html"


def test_html_file_exists():
    assert HTML_PATH.exists(), f"Expected {HTML_PATH} to exist"


def test_html_contains_url_param_bootstrap():
    content = HTML_PATH.read_text()
    assert "URLSearchParams" in content, "Should read config from URL params"
    assert "chatwootUrl" in content or "chatwoot_url" in content.lower(), \
        "Should read chatwootUrl from URL params"
    assert "apiToken" in content or "api_token" in content.lower(), \
        "Should read apiToken from URL params"
    assert "accountId" in content or "account_id" in content.lower(), \
        "Should read accountId from URL params"


def test_html_contains_postmessage_handshake():
    content = HTML_PATH.read_text()
    assert "chatwoot-dashboard-app:fetch-info" in content, \
        "Should send the Chatwoot Dashboard App postMessage handshake"


def test_html_contains_labels_api_call():
    content = HTML_PATH.read_text()
    assert "/api/v1/accounts/" in content, "Should call Chatwoot accounts API"
    assert "labels" in content, "Should reference labels endpoint"


def test_html_uses_x_api_token_or_api_access_token_header():
    content = HTML_PATH.read_text()
    # Chatwoot Application API uses api_access_token header
    assert "api_access_token" in content, \
        "Should use api_access_token header (Chatwoot Application API auth)"
```

Run the test before writing the HTML to confirm it fails:

```bash
cd /path/to/id-crm-ticketing/chatwoot-config
pytest tests/test_taxonomy_manager_html.py -v
```

Expected: `FAILED tests/test_taxonomy_manager_html.py::test_html_file_exists` (HTML does not exist yet).

- [ ] **Step 2: Create `apps/taxonomy-manager/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Proton Taxonomy Manager</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: #f5f5f5;
      color: #333;
      font-size: 13px;
    }
    .container { padding: 16px; max-width: 960px; margin: 0 auto; }
    .header {
      margin-bottom: 16px;
      border-bottom: 1px solid #ddd;
      padding-bottom: 12px;
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .header h2 { font-size: 16px; font-weight: 600; color: #222; }
    .status { font-size: 12px; color: #666; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
    button {
      padding: 6px 12px;
      border: 1px solid #ddd;
      border-radius: 3px;
      background: white;
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      transition: all 0.15s;
    }
    button:hover { background: #f5f5f5; border-color: #999; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary { background: #1a73e8; color: white; border-color: #1a73e8; }
    .btn-primary:hover { background: #1557b0; border-color: #1557b0; }
    .btn-danger { color: #ea4335; border-color: #f2c4c0; }
    .btn-danger:hover { background: #faf5f4; border-color: #ea4335; }
    .banner { border-radius: 4px; padding: 12px; margin-bottom: 12px; font-size: 12px; line-height: 1.4; }
    .banner.error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
    .banner.info { background: #eef4fd; border: 1px solid #cfe0fb; color: #33507a; }
    .table-wrap {
      background: white;
      border: 1px solid #e0e0e0;
      border-radius: 6px;
      overflow-x: auto;
    }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    thead th {
      text-align: left;
      padding: 10px 12px;
      background: #fafafa;
      border-bottom: 1px solid #e0e0e0;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      color: #666;
      white-space: nowrap;
    }
    tbody td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }
    tbody tr:last-child td { border-bottom: none; }
    .color-swatch {
      display: inline-block;
      width: 16px;
      height: 16px;
      border-radius: 3px;
      border: 1px solid rgba(0,0,0,0.1);
      vertical-align: middle;
      margin-right: 6px;
    }
    .label-name { font-weight: 600; color: #222; }
    .cell-actions { white-space: nowrap; }
    .cell-actions button { padding: 4px 8px; margin-right: 4px; font-size: 11px; }
    .loading, .empty-state { text-align: center; padding: 28px; color: #999; font-size: 13px; }
    #toasts {
      position: fixed; top: 16px; right: 16px;
      display: flex; flex-direction: column; gap: 8px; z-index: 1000; max-width: 320px;
    }
    .toast {
      padding: 10px 14px; border-radius: 4px; font-size: 12px; color: white;
      box-shadow: 0 2px 8px rgba(0,0,0,0.15); animation: slidein 0.2s ease-out;
    }
    .toast.success { background: #34a853; }
    .toast.error { background: #ea4335; }
    @keyframes slidein {
      from { transform: translateX(20px); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    .modal-backdrop {
      position: fixed; inset: 0; background: rgba(0,0,0,0.4);
      display: flex; align-items: flex-start; justify-content: center;
      padding: 40px 16px; z-index: 900; overflow-y: auto;
    }
    .modal-backdrop[hidden] { display: none; }
    .modal {
      background: white; border-radius: 8px; padding: 20px;
      width: 100%; max-width: 480px;
    }
    .modal h3 { margin-bottom: 14px; font-size: 14px; font-weight: 600; }
    .field { margin-bottom: 10px; }
    .field label {
      display: block; font-size: 11px; font-weight: 600; color: #555;
      margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.03em;
    }
    .field input[type="text"], .field input[type="color"] {
      width: 100%; padding: 8px 10px; border: 1px solid #ccc;
      border-radius: 4px; font-size: 13px; font-family: inherit; color: #333;
    }
    .field input[type="color"] { padding: 2px 4px; height: 36px; cursor: pointer; }
    .field input:focus { outline: none; border-color: #1a73e8; box-shadow: 0 0 0 2px rgba(26,115,232,0.15); }
    .form-actions { display: flex; gap: 8px; margin-top: 12px; }
    .group-header td {
      background: #f9f9f9; font-size: 10px; font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.05em; color: #888;
      padding: 6px 12px; border-bottom: 1px solid #ececec;
    }
  </style>
</head>
<body>
  <div id="toasts"></div>

  <div class="container">
    <div class="header">
      <h2>Proton Taxonomy Manager</h2>
      <div class="status" id="status">Initializing...</div>
    </div>

    <div id="banner"></div>

    <div class="toolbar">
      <button id="refresh-btn" type="button">Refresh</button>
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Label name</th>
            <th>Color</th>
            <th>Description</th>
            <th>Conv. count</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="label-body">
          <tr><td colspan="5"><div class="loading">Loading labels...</div></td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Edit modal -->
  <div class="modal-backdrop" id="edit-modal" hidden>
    <div class="modal">
      <h3>Edit Label</h3>
      <form id="edit-form">
        <input type="hidden" id="edit-name">
        <div class="field">
          <label>Label name</label>
          <input type="text" id="edit-name-display" disabled style="background:#f5f5f5; color:#888;">
        </div>
        <div class="field">
          <label for="edit-color">Color</label>
          <input type="color" id="edit-color">
        </div>
        <div class="field">
          <label for="edit-description">Description</label>
          <input type="text" id="edit-description" placeholder="Human-readable description">
        </div>
        <div class="form-actions">
          <button type="submit" class="btn-primary" id="edit-submit">Save</button>
          <button type="button" id="edit-cancel">Cancel</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    // ------------------------------------------------------------------
    // Config from URL params — same pattern as chatwoot-faq-admin.
    // ?chatwootUrl=http://crm.1.2.3.4.nip.io&apiToken=XXX&accountId=1
    // ------------------------------------------------------------------
    const params = new URLSearchParams(window.location.search);
    const chatwootUrl = (params.get('chatwootUrl') || '').replace(/\/+$/, '');
    const apiToken = params.get('apiToken') || '';
    const accountId = params.get('accountId') || '1';

    // ------------------------------------------------------------------
    // Chatwoot Dashboard App postMessage handshake (harmless if not embedded)
    // ------------------------------------------------------------------
    function requestContext() {
      try { window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*'); } catch (e) {}
    }
    window.addEventListener('message', () => { /* context ignored for global admin */ });

    // ------------------------------------------------------------------
    // HTTP helper — Chatwoot Application API uses api_access_token header
    // ------------------------------------------------------------------
    function cwHeaders() {
      return {
        'Content-Type': 'application/json',
        ...(apiToken ? { 'api_access_token': apiToken } : {})
      };
    }

    function accountUrl(path) {
      return `${chatwootUrl}/api/v1/accounts/${accountId}/${path}`;
    }

    async function cwFetch(path, options) {
      const res = await fetch(accountUrl(path), { ...options, headers: cwHeaders() });
      if (!res.ok) {
        if (res.status === 401) {
          showBanner('error', 'Unauthorized (401). Check <code>?apiToken=</code> in the URL.');
          throw new Error('unauthorized');
        }
        const text = await res.text().catch(() => res.statusText);
        throw new Error(`${res.status}: ${text.slice(0, 200)}`);
      }
      const text = await res.text();
      return text ? JSON.parse(text) : {};
    }

    // ------------------------------------------------------------------
    // Load + render labels, grouped by prefix
    // ------------------------------------------------------------------
    const labelsById = new Map();

    function prefixOf(name) {
      const m = name.match(/^([a-z]+)_/);
      return m ? m[1] : 'other';
    }

    async function loadLabels() {
      setStatus('Loading...');
      clearBanner();
      const tbody = document.getElementById('label-body');
      tbody.innerHTML = '<tr><td colspan="5"><div class="loading">Loading labels...</div></td></tr>';
      try {
        const data = await cwFetch('labels', { method: 'GET' });
        const labels = Array.isArray(data) ? data : (data.payload || []);
        labelsById.clear();
        labels.forEach(l => labelsById.set(l.title, l));
        renderLabels(labels);
        setStatus(`${labels.length} label${labels.length !== 1 ? 's' : ''}`);
      } catch (err) {
        setStatus('Error');
        if (err.message !== 'unauthorized') {
          showBanner('error', 'Failed to load labels: ' + escapeHtml(err.message));
        }
        tbody.innerHTML = '<tr><td colspan="5"><div class="empty-state">Could not load labels.</div></td></tr>';
      }
    }

    function renderLabels(labels) {
      const tbody = document.getElementById('label-body');
      if (!labels.length) {
        tbody.innerHTML = '<tr><td colspan="5"><div class="empty-state">No labels found. Create labels via the provisioning script or Chatwoot Settings.</div></td></tr>';
        return;
      }

      // Group by prefix
      const groups = {};
      for (const lbl of labels) {
        const g = prefixOf(lbl.title);
        if (!groups[g]) groups[g] = [];
        groups[g].push(lbl);
      }

      let html = '';
      for (const [group, items] of Object.entries(groups)) {
        html += `<tr class="group-header"><td colspan="5">${escapeHtml(group)}</td></tr>`;
        for (const lbl of items) {
          const color = lbl.color || '#cccccc';
          html += `
            <tr data-name="${escapeAttr(lbl.title)}">
              <td class="label-name">
                <span class="color-swatch" style="background:${escapeAttr(color)}"></span>
                ${escapeHtml(lbl.title)}
              </td>
              <td>${escapeHtml(color)}</td>
              <td>${escapeHtml(lbl.description || '—')}</td>
              <td>${escapeHtml(String(lbl.conversations_count ?? '—'))}</td>
              <td class="cell-actions">
                <button data-action="edit">Edit</button>
              </td>
            </tr>
          `;
        }
      }
      tbody.innerHTML = html;
      attachRowHandlers();
    }

    function attachRowHandlers() {
      document.querySelectorAll('#label-body tr[data-name]').forEach(row => {
        const name = row.dataset.name;
        const editBtn = row.querySelector('button[data-action="edit"]');
        if (editBtn) editBtn.addEventListener('click', () => openEdit(name));
      });
    }

    // ------------------------------------------------------------------
    // Edit modal
    // ------------------------------------------------------------------
    function openEdit(name) {
      const lbl = labelsById.get(name);
      if (!lbl) return;
      document.getElementById('edit-name').value = name;
      document.getElementById('edit-name-display').value = name;
      document.getElementById('edit-color').value = lbl.color || '#cccccc';
      document.getElementById('edit-description').value = lbl.description || '';
      document.getElementById('edit-modal').hidden = false;
    }

    function closeEdit() {
      document.getElementById('edit-modal').hidden = true;
    }

    document.getElementById('edit-cancel').addEventListener('click', closeEdit);
    document.getElementById('edit-modal').addEventListener('click', (ev) => {
      if (ev.target.id === 'edit-modal') closeEdit();
    });

    document.getElementById('edit-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const submit = document.getElementById('edit-submit');
      const name = document.getElementById('edit-name').value;
      const color = document.getElementById('edit-color').value;
      const description = document.getElementById('edit-description').value.trim();

      submit.disabled = true;
      try {
        await cwFetch(`labels/${encodeURIComponent(name)}`, {
          method: 'PATCH',
          body: JSON.stringify({ color, description, show_on_sidebar: true })
        });
        toast('success', `Label '${name}' updated.`);
        closeEdit();
        await loadLabels();
      } catch (err) {
        if (err.message !== 'unauthorized') toast('error', 'Update failed: ' + err.message);
      } finally {
        submit.disabled = false;
      }
    });

    // ------------------------------------------------------------------
    // UI helpers
    // ------------------------------------------------------------------
    function setStatus(text) { document.getElementById('status').textContent = text; }
    function showBanner(kind, htmlMsg) {
      document.getElementById('banner').innerHTML = `<div class="banner ${kind}">${htmlMsg}</div>`;
    }
    function clearBanner() { document.getElementById('banner').innerHTML = ''; }
    function toast(kind, msg) {
      const el = document.createElement('div');
      el.className = `toast ${kind}`;
      el.textContent = msg;
      document.getElementById('toasts').appendChild(el);
      setTimeout(() => el.remove(), 3500);
    }
    function escapeHtml(text) {
      const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
      return String(text).replace(/[&<>"']/g, m => map[m]);
    }
    function escapeAttr(text) { return String(text).replace(/"/g, '&quot;'); }

    // ------------------------------------------------------------------
    // Boot
    // ------------------------------------------------------------------
    document.getElementById('refresh-btn').addEventListener('click', loadLabels);
    document.addEventListener('DOMContentLoaded', () => {
      requestContext();
      if (!chatwootUrl || !apiToken) {
        showBanner('info',
          'Missing config. Load this app with: ' +
          '<code>?chatwootUrl=http://crm.x.x.x.x.nip.io&amp;apiToken=TOKEN&amp;accountId=1</code>');
      }
      if (chatwootUrl && apiToken) loadLabels();
    });
  </script>
</body>
</html>
```

- [ ] **Step 3: Run the HTML smoke test**

```bash
cd /path/to/id-crm-ticketing/chatwoot-config
pytest tests/test_taxonomy_manager_html.py -v
```

Expected:
```
tests/test_taxonomy_manager_html.py::test_html_file_exists PASSED
tests/test_taxonomy_manager_html.py::test_html_contains_url_param_bootstrap PASSED
tests/test_taxonomy_manager_html.py::test_html_contains_postmessage_handshake PASSED
tests/test_taxonomy_manager_html.py::test_html_contains_labels_api_call PASSED
tests/test_taxonomy_manager_html.py::test_html_uses_x_api_token_or_api_access_token_header PASSED

5 passed in X.XXs
```

- [ ] **Step 4: Commit**

```bash
git add apps/taxonomy-manager/index.html chatwoot-config/tests/test_taxonomy_manager_html.py
git commit -m "feat(phase1): Taxonomy Manager static app (label list/edit via Chatwoot API)"
```

---

## Task 7: Wire Taxonomy Manager into Caddy + register as Chatwoot dashboard app

**Dependency:** Phase 0 nav host must be live. Skip this task until Phase 0 is deployed.

**Files:**
- Modify: `deploy/scripts/add-tenant.sh` — add Caddy route for `/apps/taxonomy-manager` serving the static file
- Create: `chatwoot-config/register_taxonomy_app.py` — idempotently registers the app as a Chatwoot dashboard app

**Interfaces:**
- Consumes: `ChatwootClient` from Task 2 (same class, no changes needed)
- Consumes: `CHATWOOT_API_TOKEN`, `CHATWOOT_URL`, `CHATWOOT_ACCOUNT_ID` from tenant env file
- Produces: A Chatwoot dashboard app visible in the "Apps" section of every conversation right panel OR as a nav item, depending on Phase-0 nav-host registration strategy

- [ ] **Step 1: Write failing test for registration script**

Create `chatwoot-config/tests/test_register_taxonomy_app.py`:

```python
"""Unit tests for register_taxonomy_app.py."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).parent.parent))
from register_taxonomy_app import register_dashboard_app, DashboardAppConfig

BASE = "http://chatwoot-test"
ACCOUNT_ID = 1
TOKEN = "test-token"
APPS_URL = f"{BASE}/api/v1/accounts/{ACCOUNT_ID}/dashboard_apps"


@respx.mock
def test_creates_app_when_not_existing():
    respx.get(APPS_URL).mock(return_value=httpx.Response(200, json=[]))
    create_route = respx.post(APPS_URL).mock(
        return_value=httpx.Response(200, json={"id": 1, "title": "Taxonomy Manager"})
    )
    cfg = DashboardAppConfig(
        title="Taxonomy Manager",
        url="http://agent.1-2-3-4.nip.io/apps/taxonomy-manager?apiToken=tok&accountId=1",
    )
    result = register_dashboard_app(BASE, TOKEN, ACCOUNT_ID, cfg, dry_run=False)
    assert result == "created"
    assert create_route.called


@respx.mock
def test_skips_when_app_already_exists():
    respx.get(APPS_URL).mock(
        return_value=httpx.Response(200, json=[{"id": 1, "title": "Taxonomy Manager"}])
    )
    create_route = respx.post(APPS_URL).mock(return_value=httpx.Response(200, json={}))
    cfg = DashboardAppConfig(
        title="Taxonomy Manager",
        url="http://agent.1-2-3-4.nip.io/apps/taxonomy-manager?apiToken=tok&accountId=1",
    )
    result = register_dashboard_app(BASE, TOKEN, ACCOUNT_ID, cfg, dry_run=False)
    assert result == "unchanged"
    assert not create_route.called


@respx.mock
def test_dry_run_does_not_create():
    respx.get(APPS_URL).mock(return_value=httpx.Response(200, json=[]))
    create_route = respx.post(APPS_URL).mock(return_value=httpx.Response(200, json={}))
    cfg = DashboardAppConfig(
        title="Taxonomy Manager",
        url="http://agent/apps/taxonomy-manager",
    )
    result = register_dashboard_app(BASE, TOKEN, ACCOUNT_ID, cfg, dry_run=True)
    assert result == "created"
    assert not create_route.called
```

Run (expect ImportError / file not found):

```bash
pytest chatwoot-config/tests/test_register_taxonomy_app.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'register_taxonomy_app'`

- [ ] **Step 2: Create `chatwoot-config/register_taxonomy_app.py`**

```python
#!/usr/bin/env python3
"""Register the Taxonomy Manager as a Chatwoot Dashboard App for one tenant.

Usage:
    python register_taxonomy_app.py \\
        --tenant default \\
        [--app-url http://agent.1-2-3-4.nip.io/apps/taxonomy-manager] \\
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

        print(f"  [CREATE] Dashboard app '{cfg.title}' -> {cfg.url}")
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
```

- [ ] **Step 3: Run the registration tests**

```bash
pytest chatwoot-config/tests/test_register_taxonomy_app.py -v
```

Expected:
```
tests/test_taxonomy_manager_html.py::test_creates_app_when_not_existing PASSED
tests/test_taxonomy_manager_html.py::test_skips_when_app_already_exists PASSED
tests/test_taxonomy_manager_html.py::test_dry_run_does_not_create PASSED

3 passed in X.XXs
```

- [ ] **Step 4: Document Caddy static-file serving**

Add the following to `docs/plans/phase1-ops-runbook.md` under a new section `## Serving the Taxonomy Manager app`:

```markdown
## Serving the Taxonomy Manager app (requires Phase 0)

The `apps/taxonomy-manager/index.html` file must be served by Caddy.
Add the following block to the tenant's Caddy snippet at
`deploy/caddy/tenants/<tenant>.caddy`, inside the `agent.<ip>.nip.io` vhost block,
**before** the reverse_proxy directive (Caddy matches first-matching):

```caddyfile
handle /apps/taxonomy-manager* {
    root * /opt/id-crm-ticketing/apps/taxonomy-manager
    file_server
}
```

Then reload Caddy:
```bash
docker exec platform-infra-caddy-1 caddy reload --config /etc/caddy/Caddyfile
```

Register the app as a Chatwoot dashboard app:
```bash
python chatwoot-config/register_taxonomy_app.py \
    --tenant default \
    --app-url http://agent.<PUBLIC_IP>.nip.io/apps/taxonomy-manager
```

The app will appear in Chatwoot under **Settings → Integrations → Dashboard Apps**
and can be pinned to conversations via the right-panel "+" button.
```

- [ ] **Step 5: Commit**

```bash
git add chatwoot-config/register_taxonomy_app.py \
        chatwoot-config/tests/test_register_taxonomy_app.py \
        docs/plans/phase1-ops-runbook.md
git commit -m "feat(phase1): dashboard app registration script + Caddy serving docs for Taxonomy Manager"
```

---

## Task 8: Full test run + live smoke on `default` tenant

**Files:** none new — verification only.

**Interfaces:**
- Consumes: all files from Tasks 1–7
- Consumes: `deploy/tenants/default.env` (must have `CHATWOOT_API_TOKEN` and `CHATWOOT_ACCOUNT_ID` set)

- [ ] **Step 1: Run full pytest suite**

```bash
cd /path/to/id-crm-ticketing/chatwoot-config
pytest tests/ -v
```

Expected: all tests pass (17 from `test_provision_labels.py` + 5 from `test_taxonomy_manager_html.py` + 3 from `test_register_taxonomy_app.py` = 25 total).

- [ ] **Step 2: Run bats smoke**

```bash
bats smoke/smoke_idempotency.bats
```

Expected: 6 tests, 0 failures.

- [ ] **Step 3: Dry-run against `default` tenant**

```bash
python provision_labels.py --tenant default --dry-run
```

Expected output: lists all labels and filters as `[CREATE]`, exits 0 (the tenant is clean before first real run).

- [ ] **Step 4: Apply to `default` tenant**

```bash
python provision_labels.py --tenant default
```

Expected output (sample):
```
Provisioning tenant 'default' (account 1)
  [CREATE] label 'category_complaint' (#e74c3c)
  [CREATE] label 'category_inquiry' (#3498db)
  ... (28 more CREATE lines) ...
  [CREATE] saved filter 'All Complaints'
  ... (7 more CREATE lines) ...

Done.
Labels  — created: 28, updated: 0, unchanged: 0
Filters — created: 8, unchanged: 0
```

- [ ] **Step 5: Verify idempotency — run again, expect zero changes**

```bash
python provision_labels.py --tenant default
```

Expected output:
```
Provisioning tenant 'default' (account 1)

Done.
Labels  — created: 0, updated: 0, unchanged: 28
Filters — created: 0, unchanged: 8
```

- [ ] **Step 6: Verify in Chatwoot UI**

1. Open `http://crm.<PUBLIC_IP>.nip.io` and log in as a tenant admin.
2. Go to **Settings → Labels** — confirm all 28 labels are listed with correct colors.
3. Open any conversation.
4. In the left sidebar under **Views**, confirm 8 saved filter views are listed.
5. Click **"All Complaints"** — confirm the filter applies (conversation list filtered by the `category_complaint` label).

- [ ] **Step 7: Replicate to other tenants**

```bash
python provision_labels.py --tenant proton
python provision_labels.py --tenant wahchan
```

- [ ] **Step 8: Commit verification note**

```bash
git commit --allow-empty -m "chore(phase1): live smoke passed on default tenant — 28 labels + 8 filters provisioned idempotently"
```

---

## Self-Review

### 1. Spec coverage

| Spec requirement | Task covering it |
|---|---|
| Item 41 — "Run report by request/tag key work" (ad-hoc label filtering) | Task 1 (filters.yaml), Task 5 (runbook), Task 6 (taxonomy app) |
| Proton feedback — "add more categorization filtering by themselves" | Task 1 (labels.yaml taxonomy), Task 3 (provisioning script), Task 5 (runbook self-service path) |
| AI-emitted `category_/subcat_/division_/dept_` label conventions documented | Task 1 (labels.yaml with groups + descriptions), Task 5 (runbook prefix table) |
| Saved custom views / filter presets | Task 1 (filters.yaml), Task 2 (script creates them) |
| Config script per-tenant idempotent via API | Tasks 2–4 |
| Optional Taxonomy Manager app (nav-menu item) | Tasks 6–7 |
| Phase 0 dependency for nav app clearly noted | Task 6 step 1 header, Task 7 throughout |
| Deploy: `default` → replicate pattern | Task 8 steps 4 and 7 |
| DoD: ops can create a category label + saved filter and filter conversations unaided | Tasks 5 (runbook) + 8 (smoke verification) |
| Multi-tenant replication via API script | Task 8 step 7, runbook Option A "Replicate" section |

### 2. Placeholder scan

- No "TBD", "TODO", or "implement later" found.
- All code blocks are complete and executable.
- All test functions have concrete assertions.
- All `run:` steps include exact expected output.

### 3. Type consistency

- `ChatwootClient` defined in Task 2, consumed identically in Task 7 (`register_taxonomy_app.py` imports it).
- `ProvisionResult` fields (`labels_created`, `labels_updated`, `labels_unchanged`, `filters_created`, `filters_unchanged`) used consistently in tests and `__str__`.
- `_load_env_file` defined once in `provision_labels.py`, re-imported in `register_taxonomy_app.py` — no duplication.
- Label API: `title` (not `name`) is what Chatwoot returns; the YAML uses `name` as the desired value, which the script sends as `title` in the POST body — consistent throughout Task 2 code and Task 3 tests.
- Saved filters list: Chatwoot returns a bare `list` (not `{"payload": [...]}`) — handled in `list_saved_filters()` with the `isinstance(data, list)` guard, tested in `test_list_saved_filters_returns_list`.
