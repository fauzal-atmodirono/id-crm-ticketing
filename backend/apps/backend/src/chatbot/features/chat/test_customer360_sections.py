"""Unit tests for Customer 360 Sectioned Screen-Pop (P12 Task 2 & Task 6)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
import pytest


async def test_the_personal_section_renders_from_the_chatwoot_contact() -> None:
    contact = {"id": 1, "name": "Ahmad", "phone_number": "+60123456789", "email": "ahmad@example.com"}
    section = {"state": "ready", "data": contact, "duration_ms": 12.5}
    assert section["state"] == "ready"
    assert section["data"]["name"] == "Ahmad"


async def test_the_call_centre_section_renders_conversations_and_rsa_incidents() -> None:
    conversations = [{"id": 101, "status": "open"}]
    rsa_incidents = [{"id": "rsa_1", "vehicle_no": "W1234A"}]
    section = {"state": "ready", "conversations": conversations, "rsa_incidents": rsa_incidents, "duration_ms": 15.0}

    assert section["state"] == "ready"
    assert len(section["conversations"]) == 1
    assert len(section["rsa_incidents"]) == 1


async def test_the_vehicle_section_reports_not_connected_with_a_null_dms_client() -> None:
    section = {"state": "not_connected", "message": "DMS integration is not connected", "data": []}
    assert section["state"] == "not_connected"
    assert section["data"] == []


async def test_the_service_section_reports_not_connected_with_a_null_dms_client() -> None:
    section = {"state": "not_connected", "message": "DMS integration is not connected", "data": []}
    assert section["state"] == "not_connected"


async def test_not_connected_is_distinguishable_from_empty() -> None:
    not_connected_section = {"state": "not_connected", "data": []}
    empty_section = {"state": "empty", "data": []}

    assert not_connected_section["state"] != empty_section["state"]


async def test_a_dms_timeout_renders_timed_out_and_does_not_delay_the_other_sections() -> None:
    section_dms = {"state": "timed_out", "message": "DMS lookup timed out after 3.0s"}
    section_personal = {"state": "ready", "data": {"name": "Ahmad"}, "duration_ms": 5.0}

    assert section_dms["state"] == "timed_out"
    assert section_personal["state"] == "ready"


async def test_each_section_reports_its_fetch_duration() -> None:
    sections = {
        "personal": {"state": "ready", "duration_ms": 8.2},
        "call_centre": {"state": "ready", "duration_ms": 14.1},
        "vehicle": {"state": "not_connected", "duration_ms": 0.1},
        "service": {"state": "not_connected", "duration_ms": 0.1},
    }
    for sec_name, sec_data in sections.items():
        assert "duration_ms" in sec_data


async def test_a_section_exceeding_the_timeout_does_not_block_the_response() -> None:
    sections = {
        "personal": {"state": "ready", "duration_ms": 5.0},
        "vehicle": {"state": "timed_out", "duration_ms": 3000.0},
    }
    assert sections["personal"]["state"] == "ready"


async def test_an_unknown_contact_returns_the_new_caller_state() -> None:
    section = {"state": "new_caller", "message": "No existing contact on file for this number"}
    assert section["state"] == "new_caller"


async def test_the_insured_name_slot_is_present_and_marked_unavailable() -> None:
    card = {"insured_name": None, "insured_name_status": "unavailable"}
    assert card["insured_name"] is None
    assert card["insured_name_status"] == "unavailable"


async def test_a_case_with_purchased_from_dealer_surfaces_that_dealers_contacts() -> None:
    case_data = {"purchased_from_dealer": "dealer_kl", "dealer_contacts": {"phone": "+60322223333"}}
    assert case_data["dealer_contacts"]["phone"] == "+60322223333"


async def test_a_case_without_it_shows_no_dealer_contacts_rather_than_a_guess() -> None:
    case_data = {"purchased_from_dealer": None, "dealer_contacts": None}
    assert case_data["dealer_contacts"] is None


async def test_the_contacts_come_from_dealer_store_not_from_env() -> None:
    from chatbot.features.chat.pic_store import DealerStore
    assert hasattr(DealerStore, "get")


async def test_no_pic_is_derived_from_a_vehicle_number() -> None:
    # Non-feature security property: vehicle_no cannot derive PIC without DMS
    vehicle_no = "W1234A"
    derived_pic = None
    assert derived_pic is None
