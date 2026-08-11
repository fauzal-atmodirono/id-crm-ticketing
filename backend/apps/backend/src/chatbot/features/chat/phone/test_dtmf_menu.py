"""Unit tests for DTMF Menu (P11 Task 2)."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone.dtmf_menu import (
    PROMPT_EN,
    PROMPT_MS,
    build_dtmf_twiml,
    handle_dtmf_digit,
)


def test_the_english_menu_prompt_matches_appendix_b_verbatim() -> None:
    assert "Press 1 for Roadside Assistance." in PROMPT_EN
    assert "Press 2 for Sales." in PROMPT_EN
    assert "Press 3 for Service and Product Enquiries." in PROMPT_EN
    assert "Press 0 to repeat options." in PROMPT_EN


def test_the_malay_menu_prompt_matches_appendix_b_verbatim() -> None:
    assert "Tekan 1 untuk Bantuan Tunda dan Bantuan Tepi Jalan." in PROMPT_MS
    assert "Tekan 2 untuk Jualan." in PROMPT_MS
    assert "Tekan 3 for Pertanyaan Perkhidmatan dan Produk." in PROMPT_MS
    assert "Tekan 0 untuk ulang." in PROMPT_MS


def test_pressing_1_routes_to_the_rsa_path() -> None:
    res = handle_dtmf_digit("1")
    assert res["target"] == "rsa"
    assert res["action"] == "route_rsa"


def test_pressing_2_and_3_pass_inquiry_and_complaint_context_to_the_bridge() -> None:
    res2 = handle_dtmf_digit("2")
    assert res2["target"] == "bridge"
    assert "Sales" in res2["context"]

    res3 = handle_dtmf_digit("3")
    assert res3["target"] == "bridge"
    assert "Service" in res3["context"]


def test_pressing_0_repeats_the_menu_once() -> None:
    res = handle_dtmf_digit("0", repeat_count=0)
    assert res["target"] == "repeat_menu"
    assert res["repeat_count"] == 1


def test_a_second_zero_falls_through_to_the_conversational_bridge() -> None:
    res = handle_dtmf_digit("0", repeat_count=1)
    assert res["target"] == "bridge"
    assert res["action"] == "fallthrough"


def test_a_timeout_falls_through_to_the_conversational_bridge() -> None:
    res = handle_dtmf_digit(None)
    assert res["target"] == "bridge"
    assert res["action"] == "fallthrough"


def test_an_invalid_key_falls_through_rather_than_looping() -> None:
    res = handle_dtmf_digit("9")
    assert res["target"] == "bridge"
    assert res["action"] == "fallthrough"


def test_the_flag_off_goes_straight_to_the_bridge_exactly_as_today() -> None:
    twiml = build_dtmf_twiml(enabled=False)
    assert "<Gather" not in twiml
    assert "<Connect><Stream" in twiml
