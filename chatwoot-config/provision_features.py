#!/usr/bin/env python3
"""Idempotently set a tenant's Chatwoot account feature flags: strip the
non-reusable enterprise/cloud cruft and enable the reusable capabilities we
make our own.

Account features are NOT in Chatwoot's public REST API — they live on the
account model — so this drives them through `rails runner` inside the tenant's
chatwoot-rails container (available locally and on the VM). Idempotent:
enable_features/disable_features are set-operations, safe to re-run.

Usage:
    python provision_features.py --tenant default [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys

# Non-reusable enterprise/cloud cruft — turned OFF.
DISABLE = [
    "captain_integration",        # replaced by our Copilot + Knowledge
    "captain_tasks",
    "custom_tools",
    "captain_document_auto_sync",
    "contact_chatwoot_support_team",  # Chatwoot's own support upsell
]

# White-label + reusable capabilities we make our own — turned ON.
ENABLE = [
    "disable_branding",  # remove "Powered by Chatwoot"
    "sla",               # powers escalation (Phase 2)
    "audit_logs",        # our audit trail / reporting
    "custom_roles",      # RBAC
]

RUBY_TEMPLATE = """
a = Account.find({account_id})
a.disable_features({disable})
a.enable_features({enable})
a.save!
on = a.reload.enabled_features.select {{ |_k, v| v }}.keys.sort
puts "ENABLED: " + on.join(", ")
"""


def _ruby_list(items: list[str]) -> str:
    return ", ".join(f"'{x}'" for x in items)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", default="default")
    p.add_argument("--account-id", type=int, default=1)
    p.add_argument("--container", default=None,
                   help="chatwoot-rails container (default: <tenant>-chatwoot-rails)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    container = args.container or f"{args.tenant}-chatwoot-rails"
    ruby = RUBY_TEMPLATE.format(
        account_id=args.account_id,
        disable=_ruby_list(DISABLE),
        enable=_ruby_list(ENABLE),
    )

    print(f"Tenant '{args.tenant}' (container {container})")
    print(f"  DISABLE: {', '.join(DISABLE)}")
    print(f"  ENABLE : {', '.join(ENABLE)}")
    if args.dry_run:
        print("[DRY RUN] would run the above via rails runner.")
        return 0

    proc = subprocess.run(
        ["docker", "exec", "-i", container, "bundle", "exec", "rails", "runner", "-"],
        input=ruby, text=True, capture_output=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("ENABLED:"):
            print("  " + line)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-500:])
        return proc.returncode
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
