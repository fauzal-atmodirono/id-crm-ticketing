import pytest

from chatbot.features.logisly import milestones, trips


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    milestones.PENDING_TICKETS.clear()
    monkeypatch.setattr(trips, "TRIPS", trips.TripStore.seeded())
    monkeypatch.setattr(trips, "ACTIVE_SESSIONS", {})
    yield
    milestones.PENDING_TICKETS.clear()


def test_seed_has_milestones_and_facts():
    codes = dict(milestones.MILESTONES)
    for trip in trips.TRIPS.trips.values():
        assert trip["milestone"] in codes
        assert trip["facts"]
    t = trips.TRIPS.get("LGS-24017")
    assert [i.id for i in milestones.open_items(t, "assigned")] == ["unit_empty"]


def test_set_milestone_runs_system_checks():
    t = trips.TRIPS.set_milestone("LGS-24021", "at_pickup", "test")
    assert t["checklists"]["at_pickup"]["driver_app"]["status"] == "not_ok"
    assert t["events"][-1]["status"] == "milestone:at_pickup"
    assert trips.TRIPS.set_milestone("NOPE", "at_pickup", "test") is None


def test_describe_shows_milestone_label_and_items():
    text = trips.describe(trips.TRIPS.get("LGS-24017"))
    assert "Milestone: Pre check driver" in text
    assert "- unit_empty (driver): pending" in text


def test_context_lists_open_items_and_answer_facts():
    trips.TRIPS.set_milestone("LGS-24021", "at_pickup", "test")
    trip = trips.TRIPS.get("LGS-24021")
    milestones.record_item(trip, "at_pickup", "do_in_app", True, "", "driver", "crm-9")
    trips.register_session("crm-9", "LGS-24021", "", "Halo", milestone="at_pickup")
    ctx = trips.context_for_session("crm-9")
    assert "unit_photo" in ctx
    assert "DTS-240210" in ctx
    assert "record_checklist_tool" in ctx
    assert "DO sudah muncul di aplikasi driver" not in ctx


def test_describe_skips_system_events_and_has_no_trailing_space():
    trips.TRIPS.update("LGS-24017", "on_trip", "Sudah jalan dari pool.")
    trips.TRIPS.set_milestone("LGS-24017", "to_pickup", "gps")
    trips.TRIPS.update("LGS-24017", "alert:gps_stop", "GPS: berhenti")
    text = trips.describe(trips.TRIPS.get("LGS-24017"))
    assert "Update terakhir dari driver: Sudah jalan dari pool. (on_trip)" in text
    assert not any(line.endswith(" ") for line in text.splitlines())
