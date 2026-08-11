"""Unit tests for After-hours routing and RSA 24/7 bypass (P11 Task 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatbot.features.chat.phone.after_hours import (
    AFTER_HOURS_PROMPT_EN,
    AFTER_HOURS_PROMPT_MS,
    VOICEMAIL_PROMPT,
    evaluate_after_hours_call,
)

_REPO_ROOT = Path(__file__).resolve().parents[8]
_STUDIO_FLOW_PATH = _REPO_ROOT / "deploy" / "twilio" / "ivr-studio-flow.json"

assert _STUDIO_FLOW_PATH.is_file(), f"Studio flow not found at {_STUDIO_FLOW_PATH}"


def _studio_prompt(state_name: str) -> str:
    """The `say` property of a named state in the Twilio Studio flow.

    See `test_dtmf_menu.py::_studio_prompt`. The previous version of the
    "matches appendix b verbatim" test below asserted hand-written text, which is
    how the shipped after-hours message came to quote operating hours of "8 AM to
    6 PM Monday through Friday" -- wrong, and silent about the weekend/public
    holiday window Appendix B publishes.
    """
    flow = json.loads(_STUDIO_FLOW_PATH.read_text(encoding="utf-8"))
    for state in flow["states"]:
        if state.get("name") == state_name:
            return str(state["properties"]["say"])
    raise AssertionError(f"state {state_name!r} not present in the Studio flow")


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "phone_after_hours_enabled": True,
            "phone_rsa_after_hours_bypass": True,
        }
    )


def test_the_message_text_matches_appendix_b_verbatim() -> None:
    assert AFTER_HOURS_PROMPT_EN == _studio_prompt("after_hours_en")
    assert AFTER_HOURS_PROMPT_MS == _studio_prompt("after_hours_ms")
    assert VOICEMAIL_PROMPT == _studio_prompt("vm_prompt")


def test_an_out_of_hours_call_plays_the_bilingual_after_hours_message(settings) -> None:
    res = evaluate_after_hours_call(
        is_rsa=False,
        is_within_business_hours=False,
        settings=settings,
    )
    assert res["action"] == "play_after_hours_voicemail"
    assert "prompt_en" in res
    assert "prompt_ms" in res


def test_an_out_of_hours_caller_who_selects_rsa_bypasses_the_message(settings) -> None:
    res = evaluate_after_hours_call(
        is_rsa=True,
        is_within_business_hours=False,
        settings=settings,
    )
    assert res["action"] == "route_rsa_bypass"
    assert res["reason"] == "rsa_24_7_bypass"


def test_an_out_of_hours_rsa_caller_reaches_the_rsa_target(settings) -> None:
    res = evaluate_after_hours_call(
        is_rsa=True,
        is_within_business_hours=False,
        settings=settings,
    )
    assert res["action"] == "route_rsa_bypass"


def test_an_out_of_hours_rsa_caller_never_reaches_voicemail(settings) -> None:
    res = evaluate_after_hours_call(
        is_rsa=True,
        is_within_business_hours=False,
        settings=settings,
    )
    assert res["action"] != "play_after_hours_voicemail"


def test_an_in_hours_call_is_completely_unchanged(settings) -> None:
    res = evaluate_after_hours_call(
        is_rsa=False,
        is_within_business_hours=True,
        settings=settings,
    )
    assert res["action"] == "connect_in_hours"


def test_the_bypass_flag_defaults_to_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one default-on flag in this programme, asserted the only honest way.

    `get_settings()` reads `os.environ`, so a bare call proves nothing about the
    *code* default when the variable happens to be exported -- and
    `check-suites-both-flag-states.sh` exists precisely to export things. Nine
    such vacuous defaults tests have been found in this run
    (`.superpowers/sdd/DISPATCH-RULES.md`, "the `Settings(_env_file=None)`
    trap"). So: delete the variable, **assert the delete worked**, then read the
    default. Removing the delenv breaks this test rather than hollowing it out.
    """
    import os

    from chatbot.platform.config import Settings

    monkeypatch.delenv("PHONE_RSA_AFTER_HOURS_BYPASS", raising=False)
    assert "PHONE_RSA_AFTER_HOURS_BYPASS" not in os.environ

    assert Settings().phone_rsa_after_hours_bypass is True


def test_disabling_the_bypass_is_logged_as_a_deliberate_configuration(settings) -> None:
    disabled_settings = settings.model_copy(update={"phone_rsa_after_hours_bypass": False})
    res = evaluate_after_hours_call(
        is_rsa=True,
        is_within_business_hours=False,
        settings=disabled_settings,
    )
    assert res["action"] == "play_after_hours_voicemail"


def test_a_business_hours_lookup_failure_treats_the_call_as_in_hours(settings) -> None:
    # Fail-open lookup failure (treated as within business hours)
    res = evaluate_after_hours_call(
        is_rsa=False,
        is_within_business_hours=True,  # Fail open
        settings=settings,
    )
    assert res["action"] == "connect_in_hours"
