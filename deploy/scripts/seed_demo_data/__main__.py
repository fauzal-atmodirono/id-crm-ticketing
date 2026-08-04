"""CLI for the demo-data seeder (Package D). A standalone operator script --
never imported by `agent/` or `backend/` -- that wraps Task 1's generator
(`generator.py`) and Task 2's API client (`client.py`) with the safety
scaffolding a tool that writes into a live tenant needs: a mandatory
`--tenant` with no default, a dry-run summary + typed confirmation before
any write, and a batch id printed prominently on completion (`purge` can
only target a batch it's told, so losing the id makes a batch effectively
unpurgeable by anything short of a manual DB query).

Run from `deploy/scripts/` (not this directory itself -- see the `sys.path`
note below):

    python3 -m seed_demo_data seed --tenant proton --count 100 \\
        --chatwoot-url https://proton.example --chatwoot-token *** \\
        --account-id 1 --inbox-id 42 --backend-url https://... --backend-key ***

    python3 -m seed_demo_data purge --tenant proton --batch <id> ...
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import os
import sys
import uuid
from datetime import datetime, timezone

# This package has no __init__.py on purpose (see generator.py/client.py's
# own docstrings) so pytest can put the directory on sys.path directly. But
# `python -m seed_demo_data` is invoked from `deploy/scripts/`, one level
# up, where this directory is NOT on sys.path -- so client.py's `from
# generator import ...` (and this file's own sibling imports below) would
# fail without this. Must run before any sibling import.
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from client import TenantConfig, aclose, configure, create_case, create_contact, create_rsa_incident, purge  # noqa: E402
from generator import generate  # noqa: E402


# --- shared CLI plumbing ------------------------------------------------------


def _confirm(expected: str, prompt: str) -> bool:
    """Require the operator to type `expected` back exactly. Used before
    every write path (seed, purge) -- the one thing standing between a
    typo'd flag and a live tenant's data."""
    typed = input(prompt)
    return typed == expected


def _add_chatwoot_flags(sub: argparse.ArgumentParser, *, require_inbox: bool) -> None:
    sub.add_argument(
        "--chatwoot-url",
        default=None,
        help="Tenant's Chatwoot origin. Falls back to $CHATWOOT_URL / $CHATWOOT_PUBLIC_URL. No default guessed.",
    )
    sub.add_argument(
        "--chatwoot-token",
        default=None,
        help="Chatwoot API access token (agent token). Falls back to $CHATWOOT_API_TOKEN. Never hardcode or guess this.",
    )
    sub.add_argument(
        "--account-id",
        type=int,
        default=None,
        help="Chatwoot account id. Falls back to $CHATWOOT_ACCOUNT_ID.",
    )
    inbox_help = (
        "API-channel inbox id to create demo contacts/conversations in. REQUIRED, no env "
        "fallback and no default: deploy/tenants/*.env's CHATWOOT_INBOX_ID is the backend's "
        "own live WhatsApp-handoff inbox in production -- reusing it here would seed demo "
        "traffic into a real customer-facing inbox. Pick (or create) a dedicated inbox for "
        "demo data and pass its id explicitly every time."
    )
    if require_inbox:
        sub.add_argument("--inbox-id", type=int, required=True, help=inbox_help)
    else:
        # purge doesn't create anything, so client.py's purge() never reads
        # config.chatwoot_inbox_id -- but TenantConfig still needs *a* value
        # to construct. Not user-facing required here; 0 is inert.
        sub.add_argument(
            "--inbox-id",
            type=int,
            default=0,
            help="Unused by purge (kept only because TenantConfig needs a value); safe to omit.",
        )
    sub.add_argument(
        "--backend-url",
        default=None,
        help="Backend base URL for /rsa/incidents. Falls back to $PROTON_BACKEND_URL, then $PROTON_BACKEND_PUBLIC_URL.",
    )
    sub.add_argument(
        "--backend-key",
        default=None,
        help="Backend API key. Falls back to $PROTON_BACKEND_KEY, then $FAQ_ADMIN_API_KEY (rsa_router accepts either).",
    )


def _resolve_tenant_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> TenantConfig:
    """Build Task 2's `TenantConfig` from CLI flags and/or environment
    variables -- never a hardcoded value, never a guessed token/id. Missing
    required pieces are reported together (one `parser.error()` call) so an
    operator sees everything that's missing in one pass, not one flag at a
    time."""
    chatwoot_base_url = args.chatwoot_url or os.environ.get("CHATWOOT_URL") or os.environ.get("CHATWOOT_PUBLIC_URL")
    chatwoot_token = args.chatwoot_token or os.environ.get("CHATWOOT_API_TOKEN")
    account_id = args.account_id if args.account_id is not None else os.environ.get("CHATWOOT_ACCOUNT_ID")
    backend_url = args.backend_url or os.environ.get("PROTON_BACKEND_URL") or os.environ.get("PROTON_BACKEND_PUBLIC_URL")
    backend_key = args.backend_key or os.environ.get("PROTON_BACKEND_KEY") or os.environ.get("FAQ_ADMIN_API_KEY")

    missing = []
    if not chatwoot_base_url:
        missing.append("--chatwoot-url (or $CHATWOOT_URL / $CHATWOOT_PUBLIC_URL)")
    if not chatwoot_token:
        missing.append("--chatwoot-token (or $CHATWOOT_API_TOKEN)")
    if not account_id:
        missing.append("--account-id (or $CHATWOOT_ACCOUNT_ID)")
    if not backend_url:
        missing.append("--backend-url (or $PROTON_BACKEND_URL / $PROTON_BACKEND_PUBLIC_URL)")
    if not backend_key:
        missing.append("--backend-key (or $PROTON_BACKEND_KEY / $FAQ_ADMIN_API_KEY)")
    if missing:
        parser.error("missing required configuration:\n  " + "\n  ".join(missing))

    return TenantConfig(
        chatwoot_base_url=chatwoot_base_url,
        chatwoot_api_access_token=chatwoot_token,
        chatwoot_account_id=int(account_id),
        chatwoot_inbox_id=int(args.inbox_id),
        backend_base_url=backend_url,
        backend_api_key=backend_key,
    )


# --- seed ----------------------------------------------------------------


def _default_batch_id() -> str:
    return f"seed-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


async def _run_seed(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    batch_id = args.batch_id or _default_batch_id()
    contacts, cases, rsa_payloads = generate(count=args.count, batch_id=batch_id, seed=args.rng_seed)

    case_type_counts = collections.Counter(c.case_type for c in cases)
    channel_counts = collections.Counter(c.channel for c in cases)

    # Config is resolved (and shown) even on a --dry-run: the brief's dry-run
    # summary is required to include "the target tenant and base URL", and
    # surfacing a missing/misconfigured flag here -- before any write path
    # is even reachable -- is strictly better than only discovering it once
    # the operator is ready to commit.
    config = _resolve_tenant_config(args, parser)

    print("=== Demo data seed: dry-run summary ===")
    print(f"Tenant:            {args.tenant}")
    print(f"Batch id:          {batch_id}")
    print(f"Contacts:          {len(contacts)}")
    print(f"Cases:             {len(cases)}")
    print(f"  by case type:    {dict(case_type_counts)}")
    print(f"  by channel:      {dict(channel_counts)}")
    print(f"RSA incidents:     {len(rsa_payloads)}")
    print(f"Chatwoot base URL: {config.chatwoot_base_url}")
    print(f"Chatwoot account:  {config.chatwoot_account_id}")
    print(f"Chatwoot inbox:    {config.chatwoot_inbox_id}")
    print(f"Backend base URL:  {config.backend_base_url}")

    if args.dry_run:
        print("\nDry run only -- nothing was created.")
        return 0

    if not _confirm(args.tenant, f"\nType the tenant name to confirm writing to it ({args.tenant}): "):
        print("Confirmation did not match -- aborted, nothing written.", file=sys.stderr)
        return 1

    configure(config)
    conversations_created = 0
    try:
        print(f"\nCreating {len(contacts)} contacts...")
        contact_ids: list[int] = []
        for contact in contacts:
            contact_ids.append(await create_contact(contact, batch_id))

        print(f"Creating {len(cases)} cases...")
        for case_index, case in enumerate(cases):
            contact_id = contact_ids[case.contact_index]
            await create_case(case, contact_id, batch_id, case_index)
            conversations_created += 1

        print(f"Creating {len(rsa_payloads)} RSA incidents...")
        for payload in rsa_payloads:
            await create_rsa_incident(payload)
    finally:
        await aclose()

    print("\n=== Seed complete ===")
    print(f"BATCH ID: {batch_id}")
    print("(save this -- purge can only target a batch it's given this id)")
    print(f"Conversations created: {conversations_created} / {len(cases)}")
    if conversations_created < len(cases):
        print("WARNING: fewer conversations were created than requested -- the run likely raised partway through.", file=sys.stderr)
        return 1
    print("\nTo remove everything this batch created:")
    print(f"  python3 -m seed_demo_data purge --tenant {args.tenant} --batch {batch_id} ...")
    return 0


def _cmd_seed(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    return asyncio.run(_run_seed(args, parser))


# --- purge -----------------------------------------------------------------


async def _run_purge(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    config = _resolve_tenant_config(args, parser)

    print("=== Purge: dry-run summary ===")
    print(f"Tenant:   {args.tenant}")
    print(f"Batch id: {args.batch}")
    print(f"Chatwoot base URL: {config.chatwoot_base_url}")
    print(f"Chatwoot account:  {config.chatwoot_account_id}")
    print(f"Backend base URL:  {config.backend_base_url}")
    print(
        "\nThis will search for and DELETE every Chatwoot contact/conversation and RSA incident "
        f"carrying the demo_seed marker {args.batch!r}. Anything that only partially matches is "
        "left alone and reported as skipped -- see client.py's purge() docstring."
    )

    if args.dry_run:
        print("\nDry run only -- nothing was searched for or deleted.")
        print(f"Re-run without --dry-run to actually purge batch {args.batch!r} from tenant {args.tenant!r}.")
        return 0

    if not _confirm(args.tenant, f"\nType the tenant name to confirm this delete ({args.tenant}): "):
        print("Confirmation did not match -- aborted, nothing deleted.", file=sys.stderr)
        return 1

    configure(config)
    try:
        report = await purge(args.batch)
    finally:
        await aclose()

    print("\n=== Purge complete ===")
    print(f"Contacts deleted:      {report.contacts_deleted}")
    print(f"Conversations deleted: {report.conversations_deleted}")
    print(f"RSA incidents deleted: {report.rsa_incidents_deleted}")
    if report.skipped:
        print(f"Skipped ({len(report.skipped)}):")
        for line in report.skipped:
            print(f"  - {line}")
    return 0


def _cmd_purge(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    return asyncio.run(_run_purge(args, parser))


# --- argument parsing --------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m seed_demo_data",
        description="Demo-data seeder for a Chatwoot tenant (Package D). Operator tool, not app code.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed = subparsers.add_parser("seed", help="Create demo contacts, cases and RSA incidents.")
    seed.add_argument("--tenant", required=True, help="Tenant slug (e.g. 'proton'). No default -- always explicit.")
    seed.add_argument("--count", type=int, default=100, help="Number of demo contacts to generate (default: 100).")
    seed.add_argument(
        "--rng-seed",
        type=int,
        default=20260804,
        help="generator.py's determinism seed (advanced; default matches generate()'s own default).",
    )
    seed.add_argument("--batch-id", default=None, help="Override the auto-generated batch id.")
    seed.add_argument("--dry-run", action="store_true", help="Print the summary and exit without writing anything.")
    _add_chatwoot_flags(seed, require_inbox=True)
    # `parser` is stashed per-subcommand (not the top-level parser) so
    # `_resolve_tenant_config`'s `parser.error()` calls print that
    # subcommand's own usage line, not the top-level `{seed,purge}` one --
    # argparse subparsers don't share error-reporting context.
    seed.set_defaults(func=_cmd_seed, parser=seed)

    purge_cmd = subparsers.add_parser("purge", help="Delete everything a batch created.")
    purge_cmd.add_argument("--tenant", required=True, help="Tenant slug. No default -- always explicit.")
    purge_cmd.add_argument("--batch", required=True, help="Batch id to purge (printed by 'seed' on completion).")
    purge_cmd.add_argument("--dry-run", action="store_true", help="Print the summary and exit without deleting anything.")
    _add_chatwoot_flags(purge_cmd, require_inbox=False)
    purge_cmd.set_defaults(func=_cmd_purge, parser=purge_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args, args.parser)


if __name__ == "__main__":
    raise SystemExit(main())
