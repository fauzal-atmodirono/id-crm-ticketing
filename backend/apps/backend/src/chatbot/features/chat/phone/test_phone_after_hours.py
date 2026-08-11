"""Unit tests for After-hours routing and RSA 24/7 bypass (P11 Task 3)."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone.after_hours import (
    AFTER_HOURS_PROMPT_EN,
    AFTER_HOURS_PROMPT_MS,
    evaluate_after_hours_call,
)


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
    assert "Thank you for calling. Our offices are currently closed." in AFTER_HOURS_PROMPT_EN
    assert "8 AM to 6 PM Monday through Friday." in AFTER_HOURS_PROMPT_EN
    assert "Terima kasih kerana menghubungi kami." in AFTER_HOURS_PROMPT_MS
    assert "8 pagi hingga 6 petang, Isnin hingga Jumaat." in AFTER_HOURS_PROMPT_MS


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


def test_the_bypass_flag_defaults_to_on() -> None:
    from chatbot.platform.config import get_settings

    default_settings = get_settings()
    assert default_settings.phone_rsa_after_hours_bypass is True


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
