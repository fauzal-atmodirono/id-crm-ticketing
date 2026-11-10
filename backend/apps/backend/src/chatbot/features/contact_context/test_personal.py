"""Personal service on every channel (brief 6, ruling R47): one wording source.

All numbers are fake (+600000000xx).
"""

from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.contact_context.personal import (
    BOOKING_LINE,
    PRIVACY_FLOOR,
    recognised_paragraph,
    shared_paragraph,
    unknown_paragraph,
)

AHMAD: dict[str, Any] = {
    "first_name": "Ahmad",
    "vehicles": [{"model": "Proton X50", "plate_ending": "1101"}],
    "usual_dealer": "Petaling Jaya",
}
LIM: dict[str, Any] = {
    "first_name": "Lim",
    "vehicles": [
        {"model": "Proton X70", "plate_ending": "1103"},
        {"model": "Proton Persona", "plate_ending": "1104"},
    ],
    "usual_dealer": "Petaling Jaya",
}
NAMELESS: dict[str, Any] = {
    "first_name": "",
    "vehicles": [{"model": "Proton Saga"}],
    "usual_dealer": "",
}

CHANNELS = ("call", "chat")


@pytest.mark.parametrize("channel", CHANNELS)
def test_recognised_single_greets_by_first_name_with_the_car(channel: str) -> None:
    text = recognised_paragraph(AHMAD, channel=channel)  # type: ignore[arg-type]
    assert "Ahmad" in text and "Proton X50" in text and "Petaling Jaya" in text
    assert "greet them by first name" in text
    assert "Hi Ahmad" in text
    # never re-ask what we have
    assert "Never ask for what we already have" in text
    # who am I
    assert "who they are" in text and "say yes" in text
    # not them -> new customer
    assert "apologise" in text and "new customer" in text
    # the old confirm-only wording is gone
    assert "Am I speaking" not in text and "confirm identity" not in text
    assert PRIVACY_FLOOR in text


@pytest.mark.parametrize("channel", CHANNELS)
def test_recognised_several_cars_greets_and_asks_which(channel: str) -> None:
    text = recognised_paragraph(LIM, channel=channel)  # type: ignore[arg-type]
    assert "Hi Lim" in text
    assert "which car" in text
    assert "Proton X70" in text and "Proton Persona" in text
    assert PRIVACY_FLOOR in text


@pytest.mark.parametrize("channel", CHANNELS)
def test_recognised_without_first_name_asks_the_name_once(channel: str) -> None:
    text = recognised_paragraph(NAMELESS, channel=channel)  # type: ignore[arg-type]
    assert "ask for their name once" in text
    assert "Proton Saga" in text
    assert "Hi " not in text
    assert PRIVACY_FLOOR in text


def test_chat_keeps_its_open_case_fact_phone_has_none() -> None:
    chat = recognised_paragraph(AHMAD, channel="chat", extra_facts=["1 open case(s)"])
    assert "1 open case(s)" in chat
    assert "open case" not in recognised_paragraph(AHMAD, channel="call")


def test_chat_greets_only_once_per_conversation() -> None:
    text = recognised_paragraph(AHMAD, channel="chat")
    assert "do not greet them again" in text


def test_shared_number_asks_the_name_and_never_lists() -> None:
    call = shared_paragraph(["Ahmad", "Siti"], channel="call")
    assert "ask the caller for their name" in call and "do NOT list" in call
    chat = shared_paragraph(["Ahmad", "Siti"], channel="chat")
    assert "Ahmad" not in chat and "Siti" not in chat
    assert "do not list names" in chat
    assert PRIVACY_FLOOR in chat


def test_unknown_is_served_as_a_new_customer() -> None:
    call = unknown_paragraph(channel="call")
    assert "at most three questions" in call and "registration" in call
    chat = unknown_paragraph(channel="chat")
    assert "NEW customer" in chat
    assert "at most three" in chat and "one question at a time" in chat
    assert "booking" in chat and "complaint" in chat
    assert "Never hold back help" in chat


def test_privacy_floor_names_what_may_and_may_never_be_used() -> None:
    for word in ("first name", "car model", "last 4", "usual dealer"):
        assert word in PRIVACY_FLOOR
    for word in ("full phone number", "full plate number", "address", "IC number", "surname"):
        assert word in PRIVACY_FLOOR


# --- fix round 1 (R50, M-2) ----------------------------------------------------

EMPTY: dict[str, Any] = {"first_name": "", "vehicles": [], "usual_dealer": ""}


@pytest.mark.parametrize("channel", CHANNELS)
def test_an_empty_record_is_an_unknown_customer(channel: str) -> None:
    text = recognised_paragraph(EMPTY, channel=channel)  # type: ignore[arg-type]
    assert text == unknown_paragraph(channel=channel)  # type: ignore[arg-type]
    # an open-case count alone identifies nobody either
    assert recognised_paragraph(EMPTY, channel="chat", extra_facts=["2 open case(s)"]) == (
        unknown_paragraph(channel="chat")
    )


def test_partial_record_never_ask_list_is_only_what_is_on_file() -> None:
    dealer_only = {"first_name": "", "vehicles": [], "usual_dealer": "Petaling Jaya"}
    text = recognised_paragraph(dealer_only, channel="chat")
    assert "Never ask for what we already have (their phone number or dealer)" in text
    assert "tell them their usual dealer" in text
    assert "first name" not in text.split("who they are")[1].split(".")[0]
    car_only = recognised_paragraph(
        {"first_name": "", "vehicles": [{"model": "Proton Saga"}], "usual_dealer": ""},
        channel="call",
    )
    assert "Never ask for what we already have (their phone number or car)" in car_only
    assert "tell them their car model(s)" in car_only


def test_full_record_never_ask_list_has_everything() -> None:
    text = recognised_paragraph(AHMAD, channel="call")
    assert "(their name, phone number, car or dealer)" in text
    assert "tell them their first name, car model(s) and usual dealer" in text


def test_two_cars_may_be_told_apart_by_plate_ending_only() -> None:
    text = recognised_paragraph(LIM, channel="chat")
    assert "or the plate ending if two models are the same" in text
    assert "never the plates" not in text


def test_booking_line_mentions_the_known_number_without_reading_it() -> None:
    assert "already have their phone number" in BOOKING_LINE
    assert "full name and email" in BOOKING_LINE
