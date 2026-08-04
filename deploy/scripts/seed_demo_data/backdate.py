"""Backdate command support (Package D, Task 3 Part B): shifts the
`created_at` of a seed run's Chatwoot conversations (and their messages)
into the past, because the Chatwoot Application API has no field to set a
conversation's `created_at` on create -- every conversation `client.py`'s
`create_case` posts lands with server "now" as its timestamp, which is why
`generator.py`'s carefully-spread-over-8-weeks `DemoCase.created_at` values
never actually reach Chatwoot. Without this, every aging-bucket /
week-over-week report reads flat: everything "created" in the same instant.

This is a **separate, opt-in, always-manual step** -- never run by `seed`
itself (see `__main__.py`'s `seed`/`backdate` subcommands) -- because it is a
direct write to a tenant's *application* database, not the Chatwoot API. It
is treated with the same seriousness as `client.purge()`: double-guarded so
a row is only ever touched when its id is in the manifest `seed` wrote AND
the row itself still carries this batch's `demo_seed` marker in
`custom_attributes` (never trust the manifest alone -- after a purge, an id
could be reassigned by Chatwoot to a genuine, unrelated conversation).

Schema note -- verified, not assumed: column names below (`conversations`:
`created_at`, `updated_at`, `last_activity_at`, `first_reply_created_at`,
`contact_last_seen_at`, `agent_last_seen_at`, `custom_attributes` jsonb;
`messages`: `created_at`, `updated_at`, `conversation_id`) were confirmed
against a live tenant's Postgres (`\\d conversations` / `\\d messages` against
the `default` tenant's `chatwoot_default` database, Chatwoot v4.15.1) --
not read off Chatwoot's Rails models blind. Both tables store timestamps as
Postgres `timestamp without time zone` (Rails' convention: always UTC,
tz-naive at the column level), which is why every write below strips tzinfo
before binding a parameter -- binding a tz-aware value to a naive column
would either raise or silently reinterpret the offset, depending on driver
version.

The exact UPDATE (a CTE capturing the pre-image `created_at`, then shifting
every other timestamp on the row by the same delta) was dry-run against a
scratch row inserted into the `default` tenant's live database inside a
transaction that was rolled back -- not merely reviewed by inspection -- to
confirm the guard predicate and the delta arithmetic both behave as
intended (relative gaps between a conversation and its messages are
preserved, not collapsed to a single instant).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- Public data shapes ------------------------------------------------------


@dataclass(frozen=True)
class ManifestEntry:
    """One conversation `seed` created, and the `DemoCase.created_at` it was
    generated with. `created_at` is what `backdate` will set the row to --
    not a timestamp read back from Chatwoot (the API never returns one that
    matches, since Chatwoot always stamps "now" on create)."""

    conversation_id: int
    created_at: datetime


# --- Manifest I/O -------------------------------------------------------------


def write_manifest(path: Path, *, batch_id: str, tenant: str, entries: list[ManifestEntry]) -> None:
    """Write the record of what `seed` actually created. This is the only
    thing that can drive `backdate` later: `generator.generate()`'s
    `created_at` values are relative to wall-clock `now` at generation time,
    so re-running `generate()` with the same seed later would NOT reproduce
    the same timestamps -- the manifest, not the generator, is the source of
    truth for what a given batch's conversations should be backdated to.

    Written atomically (temp file in the same directory, then `os.replace`):
    a full disk or a Ctrl-C mid-`write_text` must never leave a truncated,
    unparseable manifest on disk for a later `load_manifest` to trip over --
    by requirement 1 this file is the *only* thing that can drive `backdate`,
    so losing or corrupting it strands the batch permanently un-backdatable.
    `os.replace` is atomic on both POSIX and Windows, unlike a plain rename.
    """
    payload = {
        "batch_id": batch_id,
        "tenant": tenant,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "conversations": [
            {"conversation_id": e.conversation_id, "created_at": e.created_at.isoformat()} for e in entries
        ],
    }
    tmp_path = path.parent / f".{path.name}.tmp-{os.getpid()}"
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp_path, path)


def load_manifest(path: Path) -> tuple[str, str, list[ManifestEntry]]:
    """Read a manifest `seed` wrote. Returns `(tenant, batch_id, entries)`.

    `tenant` is surfaced (not just parsed and discarded) because `backdate`
    takes no `--tenant` flag of its own (see the module docstring): the
    manifest's recorded tenant is the only way its dry-run summary can
    answer "which tenant does this data belong to", and it's what the
    write-mode confirmation prompt asks the operator to type back -- see
    `__main__.py`'s `_cmd_backdate`.
    """
    data = json.loads(Path(path).read_text())
    tenant = data["tenant"]
    batch_id = data["batch_id"]
    entries = [
        ManifestEntry(conversation_id=int(item["conversation_id"]), created_at=datetime.fromisoformat(item["created_at"]))
        for item in data.get("conversations", [])
    ]
    return tenant, batch_id, entries


# --- pure selector (the tested surface) --------------------------------------


def select_backdate_targets(entries: list[ManifestEntry], rows: list[dict], batch_id: str) -> list[ManifestEntry]:
    """Which manifest entries are safe to backdate right now, given a live
    snapshot of the rows Chatwoot currently has for those ids.

    Double-guarded, deliberately as strict as `client.py`'s
    `selectable_for_purge`: an empty `batch_id` selects nothing; a
    manifest id absent from `rows` (row deleted, e.g. by a purge since the
    manifest was written) is never eligible; a row present but missing (or
    with a null/non-dict) `custom_attributes` is never eligible; and a row
    whose `custom_attributes.demo_seed` doesn't *exactly* equal `batch_id`
    -- including one belonging to a different batch, or a value that merely
    contains this batch's id as a substring -- is never eligible. The
    manifest alone is never trusted: it only proposes ids, `rows` (a fresh
    read of the database, taken immediately before the guarded UPDATE) is
    what actually authorizes touching one.

    Returns the eligible entries themselves -- `entry.created_at` is both
    the eligibility decision's input and its output: the timestamp each
    eligible id gets is exactly what the manifest recorded, unchanged.
    """
    if not batch_id:
        return []
    rows_by_id = {row.get("id"): row for row in rows}
    eligible: list[ManifestEntry] = []
    for entry in entries:
        row = rows_by_id.get(entry.conversation_id)
        if row is None:
            continue
        custom_attributes = row.get("custom_attributes")
        if not isinstance(custom_attributes, dict):
            continue
        if custom_attributes.get("demo_seed") != batch_id:
            continue
        eligible.append(entry)
    return eligible


# --- pure DSN redaction (the second tested surface) --------------------------

# Recognises `scheme://[user[:password]@]host[:port][/db][?...]`. Only the
# authority component (between `://` and the first of `/`, `?`, `#`) is
# inspected for credentials -- everything else is passed through untouched.
_URL_SHAPE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/?#]*)(.*)$", re.DOTALL)

# Recognises a libpq keyword=value DSN (what `psycopg.connect()` also
# accepts, e.g. "host=h port=5432 user=u password=SuperSecret123") *only*
# when the ENTIRE string decomposes into `key=value` tokens -- fail-closed:
# any leftover text that doesn't fit this shape means we don't understand
# the string well enough to claim we found every secret in it.
_KV_DSN_SHAPE_RE = re.compile(r"^(?:\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:'(?:\\.|[^'\\])*'|\S+)\s*)+$")
_KV_DSN_PASSWORD_RE = re.compile(r"(\bpassword\s*=\s*)('(?:\\.|[^'\\])*'|\S+)", re.IGNORECASE)

_UNREDACTABLE_PLACEHOLDER = "<unredactable DSN -- not shown>"


def redact_database_url(url: str) -> str:
    """Password-redacted form of a Postgres connection string, safe to
    print in a dry-run summary or log.

    `psycopg.connect()` accepts two shapes -- a `scheme://user:pass@host/db`
    URL, or a libpq `key=value ...` DSN -- and an operator's real password
    can validly appear in either. This function is deliberately
    **fail-closed**: it recognises exactly those two shapes and positively
    locates the password within each; a string that matches neither is
    never echoed verbatim, because a summary that silently prints an
    unredacted password (because some third DSN shape wasn't anticipated)
    is strictly worse than a summary that declines to show the DSN at all
    -- the caller's `Manifest tenant:` line right above it already answers
    the operator's real "which tenant am I about to write to" question.

    URL form: the password is whatever sits between the userinfo's first
    `:` and the authority's *last* `@` -- using the last `@` (not the
    first) means a password that itself contains a literal `@` is masked
    in full, not just up to its first character.
    """
    match = _URL_SHAPE_RE.match(url)
    if match:
        scheme, authority, remainder = match.group(1), match.group(2), match.group(3)
        at_index = authority.rfind("@")
        if at_index == -1:
            return url  # no userinfo at all -- nothing to redact
        userinfo, host_part = authority[:at_index], authority[at_index:]
        colon_index = userinfo.find(":")
        if colon_index == -1:
            return url  # userinfo present but no password -- nothing to redact
        user = userinfo[:colon_index]
        return f"{scheme}{user}:***{host_part}{remainder}"
    if _KV_DSN_SHAPE_RE.match(url):
        if _KV_DSN_PASSWORD_RE.search(url):
            return _KV_DSN_PASSWORD_RE.sub(r"\1***", url)
        return url  # entire string is recognised key=value pairs; no password= key present
    return _UNREDACTABLE_PLACEHOLDER


# --- thin SQL glue (real I/O, correct by inspection) --------------------------
#
# Not unit-tested, per the same rationale as client.py's create_*/purge:
# kept thin enough to review directly, with the actual guard-predicate
# behavior verified live (see module docstring) rather than mocked.


def _to_naive_utc(value: datetime) -> datetime:
    """Postgres `timestamp without time zone` columns hold UTC values with
    no offset stored: bind a tz-aware datetime and either the driver raises
    or (worse) silently reinterprets the offset. Normalize once, here."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def fetch_current_rows(cur: Any, conversation_ids: list[int]) -> list[dict]:
    """The live snapshot `select_backdate_targets` guards against. Selects
    only the columns the guard needs -- `id` to match the manifest,
    `custom_attributes` to check the marker."""
    if not conversation_ids:
        return []
    cur.execute(
        "SELECT id, custom_attributes FROM conversations WHERE id = ANY(%(ids)s)",
        {"ids": conversation_ids},
    )
    return [{"id": row[0], "custom_attributes": row[1]} for row in cur.fetchall()]


def backdate_conversation(cur: Any, conversation_id: int, new_created_at: datetime, batch_id: str) -> datetime | None:
    """Shift one conversation's own timestamps by a single delta
    (`new_created_at - <the row's current created_at>`), so `created_at`
    lands exactly on the manifest's value while `updated_at`,
    `last_activity_at`, `first_reply_created_at`, `contact_last_seen_at`
    and `agent_last_seen_at` all move by the same amount -- preserving how
    long after creation each of those actually happened, rather than
    collapsing them all onto one instant. NULLable columns are left NULL
    if they already were (a case that was never replied to has no
    `first_reply_created_at`, and backdating it must not invent one).

    The guard predicate (`id = ... AND custom_attributes->>'demo_seed' =
    ...`) is re-applied here, on the write itself -- not just earlier via
    `select_backdate_targets` on a snapshot read -- so a row that changed
    between the snapshot read and this call (another process, or simply
    time passing) still can't be written unless it currently carries the
    marker. This is the "single SQL predicate" the design calls for.

    Returns the row's pre-backdate `created_at` (needed by the caller to
    shift `messages` by the same delta), or `None` if the guard didn't
    match anything (0 rows updated) -- the id was gone, or its marker no
    longer matched.
    """
    new_created_at = _to_naive_utc(new_created_at)
    cur.execute(
        """
        WITH old AS (
            SELECT created_at FROM conversations
            WHERE id = %(id)s AND custom_attributes->>'demo_seed' = %(batch_id)s
        )
        UPDATE conversations c
        SET created_at = %(new_created_at)s,
            updated_at = %(new_created_at)s + (c.updated_at - old.created_at),
            last_activity_at = %(new_created_at)s + (c.last_activity_at - old.created_at),
            first_reply_created_at = CASE WHEN c.first_reply_created_at IS NOT NULL
                THEN %(new_created_at)s + (c.first_reply_created_at - old.created_at) ELSE NULL END,
            contact_last_seen_at = CASE WHEN c.contact_last_seen_at IS NOT NULL
                THEN %(new_created_at)s + (c.contact_last_seen_at - old.created_at) ELSE NULL END,
            agent_last_seen_at = CASE WHEN c.agent_last_seen_at IS NOT NULL
                THEN %(new_created_at)s + (c.agent_last_seen_at - old.created_at) ELSE NULL END
        FROM old
        WHERE c.id = %(id)s AND c.custom_attributes->>'demo_seed' = %(batch_id)s
        RETURNING old.created_at
        """,
        {"id": conversation_id, "batch_id": batch_id, "new_created_at": new_created_at},
    )
    row = cur.fetchone()
    return row[0] if row is not None else None


def backdate_messages(cur: Any, conversation_id: int, new_created_at: datetime, old_created_at: datetime) -> int:
    """Shift every message on this conversation by the same delta the
    conversation itself just moved by, so a message can never end up
    predating or outliving its (now backdated) conversation, and the
    relative gaps between messages -- customer message, then the agent's
    replies -- are preserved rather than collapsed onto one timestamp.

    No `demo_seed` guard here: `messages` has no `custom_attributes`
    column of its own (unlike `conversations`), so its only handle is
    `conversation_id` -- which is safe to trust here specifically because
    this is only ever called right after `backdate_conversation` returned
    non-`None` for that same id in the same transaction, i.e. the
    conversation-level guard already passed for this exact id.

    Returns the number of message rows updated.
    """
    new_created_at = _to_naive_utc(new_created_at)
    old_created_at = _to_naive_utc(old_created_at)
    cur.execute(
        """
        UPDATE messages
        SET created_at = created_at + (%(new_created_at)s - %(old_created_at)s),
            updated_at = updated_at + (%(new_created_at)s - %(old_created_at)s)
        WHERE conversation_id = %(id)s
        """,
        {"id": conversation_id, "new_created_at": new_created_at, "old_created_at": old_created_at},
    )
    return cur.rowcount
