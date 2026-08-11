"""Unit tests for Voicemail Ingestion (P11 Task 4)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from chatbot.features.chat.phone.voicemail_ingest import (
    process_voicemail_webhook,
    reset_processed_voicemails,
)
from chatbot.features.metrics.business_hours import next_working_instant


@pytest.fixture(autouse=True)
def _clean_state():
    reset_processed_voicemails()
    yield
    reset_processed_voicemails()


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(update={"phone_voicemail_ingest_enabled": True})


async def test_a_voicemail_creates_a_conversation_on_the_phone_inbox(settings) -> None:
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(payload, settings)
    assert res["status"] == "created"
    assert res["conversation"]["inbox_id"] == "phone_inbox"


async def test_the_audio_is_attached_to_the_conversation(settings) -> None:
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(payload, settings)
    assert res["conversation"]["audio_url"] == "https://api.twilio.com/RE123.mp3"


async def test_the_voicemail_is_transcribed_into_the_conversation(settings) -> None:
    mock_transcriber = AsyncMock(return_value="I need help with my booking.")
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(payload, settings, transcriber_func=mock_transcriber)
    assert res["conversation"]["transcript"] == "I need help with my booking."


async def test_the_caller_is_matched_to_an_existing_contact_by_number(settings) -> None:
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(payload, settings)
    assert res["conversation"]["contact_phone"] == "+60123456789"


async def test_an_unknown_caller_creates_a_contact(settings) -> None:
    payload = {"RecordingSid": "RE124", "RecordingUrl": "https://api.twilio.com/RE124.mp3", "From": "+60198765432"}
    res = await process_voicemail_webhook(payload, settings)
    assert res["conversation"]["contact_phone"] == "+60198765432"


_MON_TO_FRI_9_TO_5 = {
    "working_hours_enabled": True,
    "timezone": "Asia/Kuala_Lumpur",
    "working_hours": [
        {"day_of_week": dow, "open_hour": 9, "open_minutes": 0, "close_hour": 17, "close_minutes": 0}
        for dow in (1, 2, 3, 4, 5)
    ],
}


async def test_attend_after_is_set_to_the_next_working_instant(settings) -> None:
    """This asserted only that the key exists, while the code set `now + 12h`.

    A Friday 18:00 voicemail was therefore stamped Saturday 06:00 -- a promise
    of attention on a day nobody is rostered, against an after-hours message
    that says "next business day". Tying the field to `next_working_instant`
    itself (rather than to a second hand-computed expectation) is what makes a
    return to any flat offset fail here.
    """
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(
        payload, settings, inbox_working_hours=_MON_TO_FRI_9_TO_5
    )

    created = datetime.fromisoformat(res["conversation"]["created_at"])
    expected = next_working_instant(created, _MON_TO_FRI_9_TO_5)
    assert res["conversation"]["attend_after"] == expected.isoformat()
    assert res["conversation"]["attend_after"] != (created + timedelta(hours=12)).isoformat()


async def test_with_no_working_hours_the_voicemail_is_attendable_immediately(settings) -> None:
    """The fail-open direction, stated as a test: unknown hours must mean "now",
    never a later fabricated time. `next_working_instant` returns its input when
    an inbox has no working-hours config, and there is no route yet to supply
    one, so this is today's actual behaviour rather than an edge case.
    """
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(payload, settings)

    assert res["conversation"]["attend_after"] == res["conversation"]["created_at"]


async def test_a_transcription_failure_still_creates_the_conversation_with_the_audio(settings) -> None:
    mock_transcriber = AsyncMock(side_effect=Exception("Speech-to-text service timeout"))
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(payload, settings, transcriber_func=mock_transcriber)

    # Audio conversation created despite transcript failure
    assert res["status"] == "created"
    assert res["conversation"]["audio_url"] == "https://api.twilio.com/RE123.mp3"
    assert "[Transcription unavailable]" in res["conversation"]["transcript"]


async def test_a_duplicate_webhook_delivery_does_not_create_two_conversations(settings) -> None:
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res1 = await process_voicemail_webhook(payload, settings)
    res2 = await process_voicemail_webhook(payload, settings)

    assert res1["status"] == "created"
    assert res2["status"] == "duplicate"


async def test_the_flag_off_leaves_the_voicemail_in_twilio_as_today(settings) -> None:
    off_settings = settings.model_copy(update={"phone_voicemail_ingest_enabled": False})
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(payload, off_settings)
    assert res["status"] == "skipped"
