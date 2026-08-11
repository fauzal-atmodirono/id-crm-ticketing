"""Unit tests for Voicemail Ingestion (P11 Task 4)."""

from __future__ import annotations

from unittest.mock import AsyncMock
import pytest

from chatbot.features.chat.phone.voicemail_ingest import (
    process_voicemail_webhook,
    reset_processed_voicemails,
)


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


async def test_attend_after_is_set_to_the_next_working_instant(settings) -> None:
    payload = {"RecordingSid": "RE123", "RecordingUrl": "https://api.twilio.com/RE123.mp3", "From": "+60123456789"}
    res = await process_voicemail_webhook(payload, settings)
    assert "attend_after" in res["conversation"]


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
