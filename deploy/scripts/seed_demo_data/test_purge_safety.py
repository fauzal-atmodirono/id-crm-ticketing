"""The purge guard. This is the highest-consequence code in the package: it runs
against a tenant a client will see."""

from __future__ import annotations

from client import selectable_for_purge, selectable_rsa_for_purge

BATCH = "seed-2026-08-04-a"


def test_selects_only_objects_carrying_the_batch_marker():
    objects = [
        {"id": 1, "custom_attributes": {"demo_seed": BATCH}},
        {"id": 2, "custom_attributes": {}},
        {"id": 3, "custom_attributes": {"demo_seed": "some-other-batch"}},
        {"id": 4},
        {"id": 5, "custom_attributes": {"demo_seed": None}},
    ]
    assert [o["id"] for o in selectable_for_purge(objects, BATCH)] == [1]


def test_empty_batch_id_selects_nothing():
    objects = [{"id": 1, "custom_attributes": {"demo_seed": BATCH}}]
    assert selectable_for_purge(objects, "") == []


def test_real_customer_data_is_never_selected():
    objects = [{"id": 99, "name": "A Real Customer", "custom_attributes": {"vehicle_no": "WXY 1234"}}]
    assert selectable_for_purge(objects, BATCH) == []


# --- RSA incidents: no custom_attributes column, so the marker lives in
# created_by == "demo-seed:<batch_id>" instead (see generator.py's
# _make_rsa_incident docstring). Same strictness as selectable_for_purge,
# but by exact string equality on a different field. ------------------------


def test_rsa_selects_only_incidents_carrying_the_exact_created_by_marker():
    incidents = [
        {"id": "a", "created_by": f"demo-seed:{BATCH}"},
        {"id": "b", "created_by": "demo-seed:some-other-batch"},
        {"id": "c", "created_by": None},
        {"id": "d"},
        {"id": "e", "created_by": "staff.jane@proton.example"},
    ]
    assert [i["id"] for i in selectable_rsa_for_purge(incidents, BATCH)] == ["a"]


def test_rsa_empty_batch_id_selects_nothing():
    incidents = [{"id": "a", "created_by": f"demo-seed:{BATCH}"}]
    assert selectable_rsa_for_purge(incidents, "") == []


def test_rsa_a_created_by_that_merely_contains_the_marker_is_never_selected():
    # Substring containment is not equality. A real staff-entered incident
    # whose created_by happens to mention the batch id in passing (e.g. a
    # note referencing it) must never be treated as demo data.
    incidents = [{"id": "a", "created_by": f"demo-seed:{BATCH} - noted by ops"}]
    assert selectable_rsa_for_purge(incidents, BATCH) == []


def test_rsa_real_staff_entered_incident_is_never_selected():
    # A real staff-entered incident's created_by is a user identity, which
    # can never collide with the demo-seed:<batch_id> marker format.
    incidents = [{"id": "z", "vehicle_no": "WXY 1234", "created_by": "jane.doe@proton.example"}]
    assert selectable_rsa_for_purge(incidents, BATCH) == []
