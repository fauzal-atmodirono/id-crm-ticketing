"""Unit tests for DTMF Menu (P11 Task 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatbot.features.chat.phone.dtmf_menu import (
    LANGUAGE_GATHER_PROMPT,
    PROMPT_EN,
    PROMPT_MS,
    build_dtmf_twiml,
    handle_dtmf_digit,
)

_REPO_ROOT = Path(__file__).resolve().parents[8]
_STUDIO_FLOW_PATH = _REPO_ROOT / "deploy" / "twilio" / "ivr-studio-flow.json"

# Fail here rather than with an empty-dict mystery if this file moves depth.
assert _STUDIO_FLOW_PATH.is_file(), f"Studio flow not found at {_STUDIO_FLOW_PATH}"


def _studio_prompt(state_name: str) -> str:
    """The `say` property of a named state in the Twilio Studio flow.

    Appendix B's wording lives in `deploy/twilio/ivr-studio-flow.json`, and the
    plan's instruction for this task was to reuse it **verbatim**. The two
    "matches appendix b verbatim" tests below previously asserted hand-written
    text against itself, which is how the shipped menu came to say "Sales" for
    option 2 and "Service and Product Enquiries" for option 3 where Appendix B
    says Inquiry and Complaint -- with a green test named for the opposite.
    Reading the source of truth here is what makes the name true.
    """
    flow = json.loads(_STUDIO_FLOW_PATH.read_text(encoding="utf-8"))
    for state in flow["states"]:
        if state.get("name") == state_name:
            return str(state["properties"]["say"])
    raise AssertionError(f"state {state_name!r} not present in the Studio flow")


def test_the_english_menu_prompt_matches_appendix_b_verbatim() -> None:
    assert PROMPT_EN == _studio_prompt("main_menu_en")


def test_the_malay_menu_prompt_matches_appendix_b_verbatim() -> None:
    assert PROMPT_MS == _studio_prompt("main_menu_ms")


def test_the_language_gather_prompt_matches_appendix_b_verbatim() -> None:
    assert LANGUAGE_GATHER_PROMPT == _studio_prompt("language_gather")


def test_pressing_1_routes_to_the_rsa_path() -> None:
    res = handle_dtmf_digit("1")
    assert res["target"] == "rsa"
    assert res["action"] == "route_rsa"


def test_pressing_2_and_3_pass_inquiry_and_complaint_context_to_the_bridge() -> None:
    res2 = handle_dtmf_digit("2")
    assert res2["target"] == "bridge"
    assert res2["context"] == "Inquiry"

    res3 = handle_dtmf_digit("3")
    assert res3["target"] == "bridge"
    # Appendix B's option 3 is Complaint. It previously produced "Service
    # Enquiry", so a complaint reached the bridge mislabelled -- and this test,
    # named for inquiry and complaint, asserted "Service" and passed.
    assert res3["context"] == "Complaint"


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
