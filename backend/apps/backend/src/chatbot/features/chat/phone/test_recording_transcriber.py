"""Post-call transcription: the human agent's half of a call reaching the CRM.

The live transcript stops when `<Connect><Stream>` is replaced by `<Dial>`, so
everything the HUMAN agent says is invisible without this. These tests are
mostly about the failure paths: this runs as a background task off a Twilio
webhook, so a raised exception is an unretrieved-task error that helps nobody,
and a wrong "success" silently loses the record of a conversation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from chatbot.features.chat.phone.recording_transcriber import (
    fetch_recording,
    transcribe_and_attach,
    transcribe_recording,
)


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "phone_recording_transcription_enabled": True,
            "twilio_account_sid": "AC" + "0" * 32,
            "twilio_auth_token": "tok",
        }
    )


async def test_disabled_flag_does_no_work_at_all(settings):
    """Flag off must not download anything -- a Twilio round trip per call is
    real cost, and the acceptance bar for every flag here is byte-identical."""
    off = settings.model_copy(update={"phone_recording_transcription_enabled": False})
    with patch(
        "chatbot.features.chat.phone.recording_transcriber.fetch_recording", new=AsyncMock()
    ) as fetch:
        assert await transcribe_recording(off, "https://api.twilio.com/rec") is None
        fetch.assert_not_awaited()


async def test_missing_twilio_credentials_returns_none(settings):
    """Twilio recording URLs need account auth despite looking public."""
    bad = settings.model_copy(update={"twilio_auth_token": ""})
    assert await fetch_recording(bad, "https://api.twilio.com/rec") is None


async def test_empty_url_returns_none(settings):
    assert await fetch_recording(settings, "") is None


async def test_download_failure_returns_none_and_does_not_raise(settings):
    with patch(
        "chatbot.features.chat.phone.recording_transcriber.fetch_recording",
        new=AsyncMock(side_effect=None, return_value=None),
    ):
        assert await transcribe_recording(settings, "https://api.twilio.com/rec") is None


async def test_attach_writes_a_note_when_transcription_succeeds(settings):
    port = AsyncMock()
    with patch(
        "chatbot.features.chat.phone.recording_transcriber.transcribe_recording",
        new=AsyncMock(return_value="CUSTOMER: hello\nAGENT: hi there"),
    ):
        assert await transcribe_and_attach(settings, port, "42", "https://api.twilio.com/rec")
    port.append_conversation_comment.assert_awaited_once()
    body = port.append_conversation_comment.await_args.args[1]
    assert "CUSTOMER: hello" in body
    assert "human agent portion" in body


async def test_attach_writes_nothing_when_there_is_no_transcript(settings):
    """No transcript must leave the conversation exactly as it was -- an empty
    or placeholder note would be worse than none, since it reads as 'we
    captured this call and there was nothing in it'."""
    port = AsyncMock()
    with patch(
        "chatbot.features.chat.phone.recording_transcriber.transcribe_recording",
        new=AsyncMock(return_value=None),
    ):
        assert await transcribe_and_attach(settings, port, "42", "https://x") is False
    port.append_conversation_comment.assert_not_awaited()


async def test_attach_swallows_a_chatwoot_failure(settings):
    """Runs as a Starlette background task: an exception escaping here
    propagates through the remaining ASGI middleware after the response."""
    port = AsyncMock()
    port.append_conversation_comment.side_effect = RuntimeError("chatwoot down")
    with patch(
        "chatbot.features.chat.phone.recording_transcriber.transcribe_recording",
        new=AsyncMock(return_value="CUSTOMER: hi"),
    ):
        assert await transcribe_and_attach(settings, port, "42", "https://x") is False
