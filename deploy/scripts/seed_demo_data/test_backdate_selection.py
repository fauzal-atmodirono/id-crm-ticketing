"""The backdate double-guard, and the manifest that feeds it. Same
consequence class as test_purge_safety.py: this decides which rows a live
UPDATE against a tenant's Chatwoot database is allowed to touch. A row is
only ever eligible when its id is in the manifest AND the row itself still
carries this batch's demo_seed marker -- the manifest alone is never trusted,
because an id can be reused by real data after a purge.

The ids in play are Chatwoot DISPLAY ids (what `POST /conversations` returns
as `id`), not `conversations.id` primary keys. `fetch_current_rows` therefore
selects `display_id` scoped by `account_id`, and this selector matches on
that -- the primary key only ever comes back out of `backdate_conversation`'s
own guarded UPDATE, which is what the unguarded `messages` UPDATE is keyed
on.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backdate import ManifestEntry, load_manifest, select_backdate_targets, write_manifest

BATCH = "seed-2026-08-04-a"


def _entry(display_id: int, created_at: str = "2026-06-01T09:00:00+00:00") -> ManifestEntry:
    return ManifestEntry(display_id=display_id, created_at=datetime.fromisoformat(created_at))


def test_selects_only_ids_whose_row_still_carries_the_batch_marker():
    entries = [_entry(1), _entry(2), _entry(3)]
    rows = [
        {"display_id": 1, "custom_attributes": {"demo_seed": BATCH}},
        {"display_id": 2, "custom_attributes": {"demo_seed": BATCH}},
        {"display_id": 3, "custom_attributes": {"demo_seed": BATCH}},
    ]
    assert [e.display_id for e in select_backdate_targets(entries, rows, BATCH)] == [1, 2, 3]


def test_manifest_id_whose_row_lacks_the_marker_is_never_eligible():
    entries = [_entry(1)]
    rows = [{"display_id": 1, "custom_attributes": {}}]
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_manifest_id_whose_row_has_no_custom_attributes_at_all_is_never_eligible():
    entries = [_entry(1)]
    rows = [{"display_id": 1}]
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_manifest_id_whose_row_carries_a_different_batchs_marker_is_never_eligible():
    # This is the scenario the double-guard exists for: after a purge, id 1
    # could have been reassigned by Chatwoot to a genuine, unrelated
    # conversation created by a later demo batch (or real traffic). The
    # manifest still lists it, but the marker no longer matches.
    entries = [_entry(1)]
    rows = [{"display_id": 1, "custom_attributes": {"demo_seed": "some-other-batch"}}]
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_manifest_id_not_present_in_the_live_rows_is_never_eligible():
    # The row was deleted (e.g. by purge) since the manifest was written.
    entries = [_entry(1)]
    rows: list[dict] = []
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_empty_manifest_touches_nothing():
    rows = [{"display_id": 1, "custom_attributes": {"demo_seed": BATCH}}]
    assert select_backdate_targets([], rows, BATCH) == []


def test_empty_batch_id_selects_nothing_even_if_rows_would_otherwise_match():
    entries = [_entry(1)]
    rows = [{"display_id": 1, "custom_attributes": {"demo_seed": BATCH}}]
    assert select_backdate_targets(entries, rows, "") == []


def test_eligible_entries_keep_their_manifest_created_at_as_the_target_timestamp():
    entries = [_entry(1, "2026-05-15T03:04:05+00:00")]
    rows = [{"display_id": 1, "custom_attributes": {"demo_seed": BATCH}}]
    [selected] = select_backdate_targets(entries, rows, BATCH)
    assert selected.created_at == datetime(2026, 5, 15, 3, 4, 5, tzinfo=timezone.utc)


def test_a_marker_that_merely_contains_the_batch_id_is_not_equality_and_never_matches():
    entries = [_entry(1)]
    rows = [{"display_id": 1, "custom_attributes": {"demo_seed": f"{BATCH}-extra"}}]
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_a_row_from_another_account_sharing_the_display_id_is_not_matched_here():
    # fetch_current_rows scopes by account_id, so a foreign account's row can
    # never reach this selector -- but if one did, it would arrive without the
    # batch marker and still be refused. Both fences, not one.
    entries = [_entry(1)]
    rows = [{"display_id": 1, "custom_attributes": {"vehicle_no": "WXY 1234"}}]
    assert select_backdate_targets(entries, rows, BATCH) == []


# --- manifest round-trip ---------------------------------------------------
# write_manifest/load_manifest is the ONLY thing that can drive `backdate`, a
# destructive command, and its serializer is otherwise unpinned: a silent
# change to a key name or a timestamp format would strand a batch
# un-backdatable (or, worse, load ids that no longer mean what they meant).


def test_manifest_round_trips_tenant_batch_account_and_entries(tmp_path):
    entries = [
        _entry(11, "2026-06-01T09:00:00+00:00"),
        _entry(12, "2026-07-15T23:59:59.123456+00:00"),
    ]
    path = tmp_path / "seed-manifest-x.json"
    write_manifest(path, batch_id=BATCH, tenant="proton", account_id=3, entries=entries)

    tenant, batch_id, account_id, loaded = load_manifest(path)
    assert tenant == "proton"
    assert batch_id == BATCH
    assert account_id == 3
    assert loaded == entries


def test_manifest_round_trip_preserves_timezone_aware_microseconds(tmp_path):
    # backdate binds these straight into an UPDATE; a dropped microsecond or a
    # silently-naive datetime changes what a row gets written to.
    entry = _entry(1, "2026-05-15T03:04:05.678901+00:00")
    path = tmp_path / "m.json"
    write_manifest(path, batch_id=BATCH, tenant="proton", account_id=1, entries=[entry])
    _, _, _, [loaded] = load_manifest(path)
    assert loaded.created_at == datetime(2026, 5, 15, 3, 4, 5, 678901, tzinfo=timezone.utc)
    assert loaded.created_at.tzinfo is not None


def test_manifest_records_the_account_id_alongside_display_ids(tmp_path):
    # The whole point of the format change: a display id is meaningless
    # without the account that scopes it.
    path = tmp_path / "m.json"
    write_manifest(path, batch_id=BATCH, tenant="proton", account_id=7, entries=[_entry(42)])
    data = json.loads(path.read_text())
    assert data["account_id"] == 7
    assert data["conversations"][0]["display_id"] == 42


def test_an_empty_manifest_round_trips_as_empty_not_as_an_error(tmp_path):
    path = tmp_path / "m.json"
    write_manifest(path, batch_id=BATCH, tenant="proton", account_id=1, entries=[])
    tenant, batch_id, account_id, loaded = load_manifest(path)
    assert (tenant, batch_id, account_id, loaded) == ("proton", BATCH, 1, [])


def test_a_version_1_manifest_is_refused_rather_than_guessed_at(tmp_path):
    # Its ids are display ids that the old code used as primary keys, and it
    # records no account. Inventing an account_id would reintroduce exactly
    # the conflation the format change removes.
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "batch_id": BATCH,
                "tenant": "proton",
                "conversations": [{"conversation_id": 1, "created_at": "2026-06-01T09:00:00+00:00"}],
            }
        )
    )
    with pytest.raises(ValueError, match="account_id"):
        load_manifest(path)
