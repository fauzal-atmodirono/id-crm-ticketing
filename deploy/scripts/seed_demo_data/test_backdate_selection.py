"""The backdate double-guard. Same consequence class as test_purge_safety.py:
this decides which rows a live UPDATE against a tenant's Chatwoot database is
allowed to touch. A row is only ever eligible when its id is in the manifest
AND the row itself still carries this batch's demo_seed marker -- the
manifest alone is never trusted, because an id can be reused by real data
after a purge."""

from __future__ import annotations

from datetime import datetime, timezone

from backdate import ManifestEntry, select_backdate_targets

BATCH = "seed-2026-08-04-a"


def _entry(conversation_id: int, created_at: str = "2026-06-01T09:00:00+00:00") -> ManifestEntry:
    return ManifestEntry(conversation_id=conversation_id, created_at=datetime.fromisoformat(created_at))


def test_selects_only_ids_whose_row_still_carries_the_batch_marker():
    entries = [_entry(1), _entry(2), _entry(3)]
    rows = [
        {"id": 1, "custom_attributes": {"demo_seed": BATCH}},
        {"id": 2, "custom_attributes": {"demo_seed": BATCH}},
        {"id": 3, "custom_attributes": {"demo_seed": BATCH}},
    ]
    assert [e.conversation_id for e in select_backdate_targets(entries, rows, BATCH)] == [1, 2, 3]


def test_manifest_id_whose_row_lacks_the_marker_is_never_eligible():
    entries = [_entry(1)]
    rows = [{"id": 1, "custom_attributes": {}}]
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_manifest_id_whose_row_has_no_custom_attributes_at_all_is_never_eligible():
    entries = [_entry(1)]
    rows = [{"id": 1}]
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_manifest_id_whose_row_carries_a_different_batchs_marker_is_never_eligible():
    # This is the scenario the double-guard exists for: after a purge, id 1
    # could have been reassigned by Chatwoot to a genuine, unrelated
    # conversation created by a later demo batch (or real traffic). The
    # manifest still lists it, but the marker no longer matches.
    entries = [_entry(1)]
    rows = [{"id": 1, "custom_attributes": {"demo_seed": "some-other-batch"}}]
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_manifest_id_not_present_in_the_live_rows_is_never_eligible():
    # The row was deleted (e.g. by purge) since the manifest was written.
    entries = [_entry(1)]
    rows: list[dict] = []
    assert select_backdate_targets(entries, rows, BATCH) == []


def test_empty_manifest_touches_nothing():
    rows = [{"id": 1, "custom_attributes": {"demo_seed": BATCH}}]
    assert select_backdate_targets([], rows, BATCH) == []


def test_empty_batch_id_selects_nothing_even_if_rows_would_otherwise_match():
    entries = [_entry(1)]
    rows = [{"id": 1, "custom_attributes": {"demo_seed": BATCH}}]
    assert select_backdate_targets(entries, rows, "") == []


def test_eligible_entries_keep_their_manifest_created_at_as_the_target_timestamp():
    entries = [_entry(1, "2026-05-15T03:04:05+00:00")]
    rows = [{"id": 1, "custom_attributes": {"demo_seed": BATCH}}]
    [selected] = select_backdate_targets(entries, rows, BATCH)
    assert selected.created_at == datetime(2026, 5, 15, 3, 4, 5, tzinfo=timezone.utc)


def test_a_marker_that_merely_contains_the_batch_id_is_not_equality_and_never_matches():
    entries = [_entry(1)]
    rows = [{"id": 1, "custom_attributes": {"demo_seed": f"{BATCH}-extra"}}]
    assert select_backdate_targets(entries, rows, BATCH) == []
