"""Tests for the Logisly milestone checklist engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chatbot.features.logisly import milestones as m

WIB = timezone(timedelta(hours=7))


@pytest.fixture(autouse=True)
def _clear_tickets():
    m.PENDING_TICKETS.clear()
    yield
    m.PENDING_TICKETS.clear()


@pytest.fixture
def trip():
    return {
        "order_id": "ORD-1",
        "driver_name": "Budi",
        "plate": "B 1234 XX",
        "shipper_kind": "shopee",
        "facts": {},
        "checklists": {},
        "driver_app_link": "https://example.test/app",
    }


def _ids(items):
    return [i.id for i in items]


def test_checklists_match_spec():
    assert _ids(m.CHECKLISTS["at_pickup"]) == [
        "unit_photo",
        "onsite",
        "load_eta",
        "driver_app",
        "do_in_app",
    ]
    assert m.CHECKLISTS["to_pickup"] == []
    teams = {i.team for items in m.CHECKLISTS.values() for i in items}
    assert teams <= {None, "alloc", "ct", "sales", "mm", "warehouse"}


def test_system_checks_fill_from_facts(trip):
    trip["facts"] = {"driver_app_active": False, "do_in_app": True, "shopee_onsite": True}
    bad = m.apply_system_checks(trip, "at_pickup")
    assert _ids(bad) == ["driver_app"]
    assert _ids(m.open_items(trip, "at_pickup")) == ["unit_photo", "load_eta"]


def test_missing_fact_is_asked(trip):
    trip["facts"] = {"driver_app_active": True, "shopee_onsite": True}
    m.apply_system_checks(trip, "at_pickup")
    assert "do_in_app" in _ids(m.open_items(trip, "at_pickup"))


def test_shopee_items_only_for_shopee_trips(trip):
    trip["shipper_kind"] = "fmcg"
    assert "onsite" not in _ids(m.checklist_for(trip, "at_pickup"))


def test_opening_asks_only_open_items_and_fixes(trip):
    trip["facts"] = {"driver_app_active": False, "do_in_app": True, "shopee_onsite": True}
    m.apply_system_checks(trip, "at_pickup")
    msg = m.opening_message(trip, "at_pickup")
    assert msg is not None
    assert trip["driver_app_link"] in msg
    assert m.CHECKLISTS["at_pickup"][0].ask in msg
    assert m.CHECKLISTS["at_pickup"][4].ask not in msg
    assert msg.count("?") <= 2


def test_opening_none_when_nothing_open(trip):
    trip["facts"] = {
        "driver_app_active": True,
        "do_in_app": True,
        "shopee_onsite": True,
    }
    m.apply_system_checks(trip, "at_pickup")
    m.record_item(trip, "at_pickup", "unit_photo", True, "ok", "photo", None)
    m.record_item(trip, "at_pickup", "load_eta", True, "09.00", "driver", None)
    assert m.opening_message(trip, "at_pickup") is None
    assert m.is_complete(trip, "at_pickup")


def test_not_ok_queues_ticket_with_team(trip):
    r = m.record_item(
        trip, "at_pickup", "unit_photo", False, "plat di foto B 1234 XX", "photo", "crm-7"
    )
    assert r == "not_ok"
    assert m.PENDING_TICKETS[-1]["team"] == "alloc"
    assert m.PENDING_TICKETS[-1]["session_id"] == "crm-7"


def test_not_ok_without_team_queues_nothing(trip):
    m.record_item(trip, "assigned", "unit_empty", False, "masih isi", "driver", None)
    assert m.PENDING_TICKETS == []
    assert trip["checklists"]["assigned"]["unit_empty"]["status"] == "not_ok"


def test_record_overwrites_and_ticket_once(trip):
    for _ in range(2):
        m.record_item(trip, "at_pickup", "unit_photo", False, "x", "photo", "crm-7")
    assert len(m.PENDING_TICKETS) == 1
    assert m.record_item(trip, "at_pickup", "unit_photo", True, "y", "photo", "crm-7") == "ok"
    assert trip["checklists"]["at_pickup"]["unit_photo"]["status"] == "ok"


def test_record_unknown_item_is_rejected(trip):
    assert m.record_item(trip, "at_pickup", "nope", True, "", "driver", None).startswith("ERROR")
    assert m.record_item(trip, "bogus", "unit_photo", True, "", "driver", None).startswith("ERROR")
    assert trip["checklists"] == {}


def test_is_quiet():
    d = lambda h, mi: datetime(2026, 11, 11, h, mi, tzinfo=WIB)  # noqa: E731
    assert m.is_quiet(d(23, 30))
    assert m.is_quiet(d(4, 59))
    assert not m.is_quiet(d(5, 0))
    assert not m.is_quiet(d(21, 59))
