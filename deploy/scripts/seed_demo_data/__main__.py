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

    export CHATWOOT_DB_URL='postgresql://...'
    python3 -m seed_demo_data backdate --manifest <path> \\
        --database-url-env CHATWOOT_DB_URL [--execute]

Three subcommands, not one flag-driven command, because they have
genuinely different consequence profiles: `seed`/`purge` talk to the
Chatwoot Application API (Task 2's `client.py`) and need Chatwoot
credentials; `backdate` (Part B) talks *directly* to a tenant's Postgres
and deliberately takes its own connection string with no default and no
auto-discovery from the environment -- see `backdate.py`'s module
docstring for why that write needs its own, separate seriousness. That
connection string is named either inline (`--database-url`) or by
environment variable (`--database-url-env NAME`, preferred: same
explicitness, but the password never lands in `argv`/`ps`/shell history);
exactly one of the two is required.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# This package has no __init__.py on purpose (see generator.py/client.py's
# own docstrings) so pytest can put the directory on sys.path directly. But
# `python -m seed_demo_data` is invoked from `deploy/scripts/`, one level
# up, where this directory is NOT on sys.path -- so client.py's `from
# generator import ...` (and this file's own sibling imports below) would
# fail without this. Must run before any sibling import.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from backdate import (  # noqa: E402
    ManifestEntry,
    backdate_conversation,
    backdate_messages,
    describe_database_target,
    fetch_current_rows,
    load_manifest,
    select_backdate_targets,
    write_manifest,
)
from client import (  # noqa: E402
    TenantConfig,
    UnsafeInboxError,
    aclose,
    assert_inbox_is_safe_to_seed,
    configure,
    create_case,
    create_contact,
    create_nasabah_contact,
    create_rsa_incident,
    purge,
)
from generator import generate  # noqa: E402
from nasabah import generate_nasabah  # noqa: E402


# --- shared CLI plumbing ------------------------------------------------------


def _confirm(expected: str, prompt: str) -> bool:
    """Require the operator to type `expected` back exactly. Used before
    every write path (seed, purge, backdate --execute) -- the one thing
    standing between a typo'd flag and a live tenant's data."""
    typed = input(prompt)
    return typed == expected


def _probe_manifest_writable(manifest_path: Path, parser: argparse.ArgumentParser) -> None:
    """Fail here, before `configure()`/any Chatwoot write, if the manifest
    can't actually be written. Discovering a bad `--manifest-dir` only in
    the `finally` block after ~100 conversations already exist in a live
    tenant would strand that whole batch permanently un-backdatable, since
    the manifest is the only thing that can drive `backdate` (design
    requirement 1).

    Both the directory creation and the write are inside the same guarded
    block: a `mkdir` failure (e.g. an unwritable parent) must exit through
    the same clean `parser.error()` (exit 2) path as a write failure would,
    not escape as a raw traceback further down in `asyncio.run()`. The
    probe file's cleanup is best-effort: if `unlink()` itself fails (e.g.
    the directory became unwritable between the write and the unlink), that
    must not raise a second, unrelated exception on top of whatever this
    function is already reporting -- or mask a real problem that mkdir/
    write_text already surfaced by succeeding cleanly when they didn't.
    """
    probe = manifest_path.parent / f".seed_demo_data_write_probe_{os.getpid()}"
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("")
    except OSError as exc:
        parser.error(f"cannot write a manifest to {manifest_path.parent} ({exc}) -- fix --manifest-dir/--manifest-path first")
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort cleanup only -- see docstring


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
    manifest_path = args.manifest_path or (Path(args.manifest_dir) / f"seed-manifest-{batch_id}.json")

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
    # Surfaced because it is the one proportion an operator cannot see from
    # the counts above and that a report will make obvious: dealer TAT needs
    # a non-empty minority, not every case (spec §3).
    print(f"  dealer-escalated:{sum(1 for c in cases if c.dealer)} of {len(cases)}")
    print(f"RSA incidents:     {len(rsa_payloads)}")
    print(f"Manifest will be written to: {manifest_path}")
    print(f"Chatwoot base URL: {config.chatwoot_base_url}")
    print(f"Chatwoot account:  {config.chatwoot_account_id}")
    print(f"Chatwoot inbox:    {config.chatwoot_inbox_id}")
    print(f"Backend base URL:  {config.backend_base_url}")

    if args.dry_run:
        print("\nDry run only -- nothing was created.")
        return 0

    # Pre-flight the INBOX before the confirmation prompt, not after it.
    # Chatwoot silently forces every conversation on a bot-enabled inbox to
    # status 'pending' -- the exact trigger the agent-bot orchestrator acts
    # on -- so a wrong --inbox-id turns a seed run into one AI reply per
    # case against a live tenant. An operator who is about to type the
    # tenant name to authorise writes deserves to know the target inbox
    # already passed. This needs configured HTTP clients, so it is the one
    # thing that runs before the prompt; it only ever reads.
    configure(config)
    try:
        try:
            inbox = await assert_inbox_is_safe_to_seed(config.chatwoot_inbox_id)
        except UnsafeInboxError as exc:
            print(f"\nRefusing to seed: {exc}", file=sys.stderr)
            return 1
        print(f"Inbox pre-flight:  OK ({inbox.get('name')!r}, channel {inbox.get('channel_type')}, no agent bot)")

        if not _confirm(args.tenant, f"\nType the tenant name to confirm writing to it ({args.tenant}): "):
            print("Confirmation did not match -- aborted, nothing written.", file=sys.stderr)
            return 1

        # Pre-flight: prove the manifest can actually be written BEFORE anything
        # is created in the tenant -- see _probe_manifest_writable's docstring.
        _probe_manifest_writable(manifest_path, parser)

        manifest_entries: list[ManifestEntry] = []
        try:
            print(f"\nCreating {len(contacts)} contacts...")
            contact_ids: list[int] = []
            for contact in contacts:
                contact_ids.append(await create_contact(contact, batch_id))

            print(f"Creating {len(cases)} cases...")
            for case_index, case in enumerate(cases):
                contact_id = contact_ids[case.contact_index]
                display_id = await create_case(case, contacts[case.contact_index], contact_id, batch_id, case_index)
                manifest_entries.append(ManifestEntry(display_id=display_id, created_at=case.created_at))

            print(f"Creating {len(rsa_payloads)} RSA incidents...")
            for payload in rsa_payloads:
                await create_rsa_incident(payload)
        finally:
            # Write whatever was actually created, no matter how the block
            # above ended. A failure writing the manifest here must never
            # mask an exception raised by the creation loop above (that's
            # the more important error to surface) -- so it's caught and
            # reported, not re-raised, with the raw ids printed as a
            # last-resort fallback since they'd otherwise be lost.
            try:
                write_manifest(
                    manifest_path,
                    batch_id=batch_id,
                    tenant=args.tenant,
                    account_id=config.chatwoot_account_id,
                    entries=manifest_entries,
                )
            except OSError as manifest_exc:
                print(f"WARNING: failed to write manifest to {manifest_path}: {manifest_exc}", file=sys.stderr)
                if manifest_entries:
                    ids = [e.display_id for e in manifest_entries]
                    print(
                        f"Conversation DISPLAY ids created so far in account "
                        f"{config.chatwoot_account_id} (SAVE THIS -- backdate needs both): {ids}",
                        file=sys.stderr,
                    )
    finally:
        await aclose()

    print("\n=== Seed complete ===")
    print(f"BATCH ID: {batch_id}")
    print("(save this -- purge and backdate can only target a batch they're given this id)")
    print(f"Manifest: {manifest_path}")
    print(f"Conversations created: {len(manifest_entries)} / {len(cases)}")
    if len(manifest_entries) < len(cases):
        print("WARNING: fewer conversations were created than requested -- the run likely raised partway through.", file=sys.stderr)
        return 1
    print("\nTo review what backdating this batch would touch (dry-run, the default -- nothing is written):")
    print(f"  export CHATWOOT_DB_URL='<tenant's Chatwoot DB URL>'   # keeps the password out of argv/ps/history")
    print(f"  python3 -m seed_demo_data backdate --manifest {manifest_path} --database-url-env CHATWOOT_DB_URL")
    print("Add --execute only after reviewing that dry-run output, to actually apply it.")
    print("\nTo remove everything this batch created:")
    print(f"  python3 -m seed_demo_data purge --tenant {args.tenant} --batch {batch_id} ...")
    return 0


def _cmd_seed(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    return asyncio.run(_run_seed(args, parser))


# --- seed-nasabah ------------------------------------------------------------


async def _run_nasabah_seed(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Create `--count` synthetic nasabah contacts on the target tenant.

    Contacts only -- no conversations, no RSA rows. The Bahana Phase 0 demo
    needs profiles visible in the agent sidebar and recognisable by the bot;
    it never opens a seeded case. Generating cases too would mean porting the
    whole automotive case/division/RSA vocabulary to a securities one for
    surfaces the demo never visits (design spec §5.3).
    """
    batch_id = args.batch_id or _default_batch_id()
    config = _resolve_tenant_config(args, parser)
    configure(config)

    # Deliberately no assert_inbox_is_safe_to_seed() call here, unlike
    # _run_seed. That guard refuses an inbox that either has an agent bot
    # attached or isn't Channel::Api -- and the Bahana demo's target inbox
    # is a Twilio WhatsApp inbox (Channel::TwilioSms) with the agent bot
    # deliberately attached, i.e. it trips BOTH refusal conditions by
    # design. Calling the guard here would refuse the exact inbox this
    # command exists to seed.
    #
    # Omitting it is safe only because this path creates contacts, and
    # nothing else: no conversation is created, so nothing lands in
    # `pending` and orchestrator.py never fires; no message is posted, so
    # nothing is delivered over a real transport. Those are the two
    # hazards the guard exists to prevent, and neither is reachable here.
    #
    # TRIPWIRE: if this function ever grows conversation or message
    # creation, that reasoning stops holding and the guard's hazard is
    # back -- unguarded, that's ~140 Gemini calls and 140 AI replies
    # posted into a tenant a client can see, the exact scenario
    # assert_inbox_is_safe_to_seed was written to prevent. Add the guard
    # call back the moment this path stops being contacts-only.
    people = generate_nasabah(
        args.count,
        batch_id=batch_id,
        seed=args.rng_seed,
        pinned_phone=args.pinned_phone,
        pinned_name=args.pinned_name,
    )

    if args.pinned_phone:
        print(f"Pinned demo handset {args.pinned_phone} -> {people[0].name}")

    created = 0
    try:
        for nasabah in people:
            await create_nasabah_contact(nasabah, batch_id)
            created += 1
            if created % 10 == 0:
                print(f"  {created}/{len(people)} nasabah created")
    finally:
        await aclose()

    print(f"Created {created} nasabah contacts on tenant {args.tenant!r}.")
    print(f"BATCH ID: {batch_id}")
    # `purge` spells this flag --batch, not --batch-id. Getting it wrong here
    # sends an operator hunting for a command that argparse rejects.
    print(f"Purge with: python3 -m seed_demo_data purge --tenant {args.tenant} --batch {batch_id}")
    return 0


def _cmd_seed_nasabah(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    return asyncio.run(_run_nasabah_seed(args, parser))


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


# --- backdate (Part B) ------------------------------------------------------


def _resolve_database_url(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    """The tenant database DSN, from exactly one of `--database-url` (value on
    the command line) or `--database-url-env` (name of an environment
    variable holding it).

    `--database-url` puts a production database password into `argv` -- into
    `ps` output for every user on the box, and into the operator's shell
    history -- on the single command in this package that writes SQL into a
    tenant's application database. `--database-url-env` removes that without
    weakening the "no auto-discovery from the environment" constraint: the
    operator still names the variable explicitly and there is still no
    default, no fallback and no guess. Neither flag is required on its own;
    exactly one of the two must be given (argparse enforces that), so there
    is no path to an implicit target.
    """
    if args.database_url is not None:
        return args.database_url
    value = os.environ.get(args.database_url_env)
    if not value:
        parser.error(
            f"--database-url-env named ${args.database_url_env}, but that environment "
            "variable is unset or empty. Export it (e.g. "
            f"export {args.database_url_env}='postgresql://...') and re-run."
        )
    return value


def _cmd_backdate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    import psycopg  # local import: only backdate needs a Postgres driver

    database_url = _resolve_database_url(args, parser)
    tenant, batch_id, account_id, entries = load_manifest(args.manifest)
    print("=== Backdate: dry-run summary ===")
    print(f"Manifest:          {args.manifest}")
    print(f"Manifest tenant:   {tenant}")
    print(f"Batch id:          {batch_id}")
    print(f"Chatwoot account:  {account_id}")
    print(f"Manifest entries:  {len(entries)}")
    print(f"Target database:   {describe_database_target(database_url)}")

    if not entries:
        print("Manifest has no conversation entries -- nothing to do.")
        return 0

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            rows = fetch_current_rows(cur, account_id, [e.display_id for e in entries])
        eligible = select_backdate_targets(entries, rows, batch_id)
        eligible_ids = {e.display_id for e in eligible}
        skipped = [e for e in entries if e.display_id not in eligible_ids]

        print(f"\nEligible to backdate ({len(eligible)}):")
        for entry in eligible:
            print(f"  conversation #{entry.display_id} -> created_at {entry.created_at.isoformat()}")
        if skipped:
            print(
                f"\nSkipped ({len(skipped)}) -- display id not found in account {account_id} of this "
                f"database, or its demo_seed marker no longer matches batch {batch_id!r} "
                "(possibly reused by real data after a purge):"
            )
            for entry in skipped:
                print(f"  conversation #{entry.display_id}")

        if not args.execute:
            print("\nDry run only -- no changes written. Re-run with --execute to apply.")
            return 0

        if not eligible:
            print("\nNothing eligible -- nothing to write.")
            return 0

        # Confirm the TENANT, not the batch id: the batch id was just
        # printed a few lines above (a copy-paste, not a check), and this is
        # the one command that writes SQL straight into a tenant database --
        # typing the tenant name back is what actually makes an operator
        # verify "am I pointed at the right one" before it happens.
        if not _confirm(tenant, f"\nType the tenant name (from the manifest) to confirm writing to this database ({tenant}): "):
            print("Confirmation did not match -- aborted, nothing written.", file=sys.stderr)
            return 1

        conversations_updated = 0
        messages_updated = 0
        skipped_at_write_time = 0
        with conn.cursor() as cur:
            for entry in eligible:
                result = backdate_conversation(cur, account_id, entry.display_id, entry.created_at, batch_id)
                if result is None:
                    # The guard was re-checked at write time (see
                    # backdate_conversation's docstring) and didn't match --
                    # the row changed between our snapshot read and now.
                    # Not fatal: skip and keep going.
                    skipped_at_write_time += 1
                    continue
                # The PRIMARY KEY the guarded UPDATE just matched -- not the
                # manifest's display id. messages.conversation_id is an FK to
                # this, and this statement has no marker of its own to check.
                conversation_pk, old_created_at = result
                conversations_updated += 1
                messages_updated += backdate_messages(cur, conversation_pk, entry.created_at, old_created_at)
        conn.commit()

    print("\n=== Backdate complete ===")
    print(f"Conversations backdated: {conversations_updated}")
    print(f"Messages backdated:      {messages_updated}")
    if skipped_at_write_time:
        print(f"Skipped at write time (guard no longer matched): {skipped_at_write_time}")
    return 0


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
    seed.add_argument(
        "--manifest-path",
        type=Path,
        default=None,
        help="Exact path to write the batch's manifest to. Overrides --manifest-dir.",
    )
    seed.add_argument(
        "--manifest-dir",
        default=".",
        help="Directory to write 'seed-manifest-<batch-id>.json' into (default: current directory).",
    )
    _add_chatwoot_flags(seed, require_inbox=True)
    # `parser` is stashed per-subcommand (not the top-level parser) so
    # `_resolve_tenant_config`'s `parser.error()` calls print that
    # subcommand's own usage line, not the top-level `{seed,purge,backdate}`
    # one -- argparse subparsers don't share error-reporting context.
    seed.set_defaults(func=_cmd_seed, parser=seed)

    nasabah_cmd = subparsers.add_parser(
        "seed-nasabah",
        help="Create synthetic nasabah contacts (Bahana demo; contacts only, no cases)",
    )
    nasabah_cmd.add_argument("--tenant", required=True, help="Tenant slug (e.g. 'bahana')")
    nasabah_cmd.add_argument(
        "--count", type=int, default=25, help="Number of nasabah contacts (default: 25)"
    )
    nasabah_cmd.add_argument(
        "--pinned-phone",
        default=None,
        help=(
            "E.164 phone of the handset the demo will be performed from. "
            "Replaces the first nasabah's number so the bot recognises it. "
            "This is the ONLY routable number the seeder will ever write."
        ),
    )
    nasabah_cmd.add_argument(
        "--pinned-name",
        default=None,
        help="Display name for the pinned demo contact (still prefixed [DEMO])",
    )
    nasabah_cmd.add_argument(
        "--rng-seed",
        type=int,
        default=20260822,
        help="nasabah.py's determinism seed (advanced; default matches generate_nasabah()'s own default).",
    )
    nasabah_cmd.add_argument("--batch-id", default=None, help="Override the auto-generated batch id.")
    _add_chatwoot_flags(nasabah_cmd, require_inbox=True)
    # `parser=nasabah_cmd` mirrors the `seed` subparser: `_resolve_tenant_config`
    # calls `parser.error()`, and stashing the SUBcommand's parser is what makes
    # that print this subcommand's usage line instead of the top-level one.
    nasabah_cmd.set_defaults(func=_cmd_seed_nasabah, parser=nasabah_cmd)

    purge_cmd = subparsers.add_parser("purge", help="Delete everything a batch created.")
    purge_cmd.add_argument("--tenant", required=True, help="Tenant slug. No default -- always explicit.")
    purge_cmd.add_argument("--batch", required=True, help="Batch id to purge (printed by 'seed' on completion).")
    purge_cmd.add_argument("--dry-run", action="store_true", help="Print the summary and exit without deleting anything.")
    _add_chatwoot_flags(purge_cmd, require_inbox=False)
    purge_cmd.set_defaults(func=_cmd_purge, parser=purge_cmd)

    backdate_cmd = subparsers.add_parser(
        "backdate",
        help="Shift a batch's conversations' created_at into the past (direct DB write; see backdate.py).",
    )
    # Exactly one of the two, and no default for either: the operator always
    # names the target explicitly, but is not forced to put a production
    # password into argv (and therefore into `ps` and shell history) to do it.
    db_target = backdate_cmd.add_mutually_exclusive_group(required=True)
    db_target.add_argument(
        "--database-url",
        default=None,
        help=(
            "Tenant's Chatwoot Postgres connection string (e.g. "
            "postgresql://chatwoot_<tenant>:***@host:5432/chatwoot_<tenant>). "
            "No default, no auto-discovery. WARNING: this puts the database "
            "password in argv, visible in `ps` and saved to shell history -- "
            "prefer --database-url-env."
        ),
    )
    db_target.add_argument(
        "--database-url-env",
        default=None,
        metavar="NAME",
        help=(
            "Name of an environment variable holding the connection string "
            "(e.g. --database-url-env CHATWOOT_DB_URL). Still fully explicit -- "
            "nothing is discovered or guessed, you name the variable -- but the "
            "password never reaches argv."
        ),
    )
    backdate_cmd.add_argument("--manifest", required=True, type=Path, help="Manifest path 'seed' printed on completion.")
    backdate_cmd.add_argument(
        "--execute",
        action="store_true",
        help="Actually write the backdate (after a typed confirmation). Without this flag: dry-run only.",
    )
    backdate_cmd.set_defaults(func=_cmd_backdate, parser=backdate_cmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args, args.parser)


if __name__ == "__main__":
    raise SystemExit(main())
