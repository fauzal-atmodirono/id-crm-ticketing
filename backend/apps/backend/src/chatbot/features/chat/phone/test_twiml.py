from chatbot.features.chat.phone.twiml import (
    GOOGLE_VOICE_EN_US,
    GOOGLE_VOICE_MS_MY,
    connect_stream_twiml,
    fallback_twiml,
)


def test_connect_stream_twiml_embeds_wss_url() -> None:
    xml = connect_stream_twiml("wss://example.test/voice/phone/stream")
    assert xml.startswith("<?xml")
    assert "<Connect>" in xml and "<Stream" in xml
    assert 'url="wss://example.test/voice/phone/stream"' in xml


def test_connect_stream_twiml_without_announcement_has_no_say() -> None:
    """Default (announcement=None) is byte-identical to before Task 6."""
    xml = connect_stream_twiml("wss://example.test/voice/phone/stream")
    assert "<Say>" not in xml


def test_connect_stream_twiml_with_announcement_says_before_connect() -> None:
    xml = connect_stream_twiml("wss://example.test/voice/phone/stream", "This call is recorded.")
    say_idx = xml.index("<Say>")
    connect_idx = xml.index("<Connect>")
    assert say_idx < connect_idx
    assert "This call is recorded." in xml


def test_fallback_twiml_says_message_then_hangs_up() -> None:
    xml = fallback_twiml([("Sorry, nobody is available.", "en-US", GOOGLE_VOICE_EN_US)])
    assert xml.startswith("<?xml")
    say_idx = xml.index("<Say")
    hangup_idx = xml.index("<Hangup/>")
    assert say_idx < hangup_idx
    assert "Sorry, nobody is available." in xml
    assert 'language="en-US"' in xml
    assert f'voice="{GOOGLE_VOICE_EN_US}"' in xml


def test_fallback_twiml_multiple_segments_each_get_own_say_language_and_voice() -> None:
    xml = fallback_twiml(
        [
            ("Sorry, nobody is available.", "en-US", GOOGLE_VOICE_EN_US),
            ("Maaf, tiada siapa tersedia.", "ms-MY", GOOGLE_VOICE_MS_MY),
        ]
    )
    en_idx = xml.index("Sorry, nobody is available.")
    ms_idx = xml.index("Maaf, tiada siapa tersedia.")
    hangup_idx = xml.index("<Hangup/>")
    assert en_idx < ms_idx < hangup_idx
    assert xml.count("<Say") == 2
    assert 'language="en-US"' in xml
    assert 'language="ms-MY"' in xml
    assert f'voice="{GOOGLE_VOICE_EN_US}"' in xml
    assert f'voice="{GOOGLE_VOICE_MS_MY}"' in xml


def test_google_voice_constants_match_deployed_ivr() -> None:
    """Reused, not invented -- see deploy/twilio/README.md and
    deploy/twilio/ivr-studio-flow.json, which already use these exact
    Google TTS voice ids for the same reason (Amazon Polly has no Malay
    voice)."""
    assert GOOGLE_VOICE_EN_US == "Google.en-US-Standard-C"
    assert GOOGLE_VOICE_MS_MY == "Google.ms-MY-Standard-A"
