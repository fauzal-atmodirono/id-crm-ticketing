from chatbot.features.chat.phone.twiml import connect_stream_twiml, fallback_twiml


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
    xml = fallback_twiml("Sorry, nobody is available.")
    assert xml.startswith("<?xml")
    say_idx = xml.index("<Say>")
    hangup_idx = xml.index("<Hangup/>")
    assert say_idx < hangup_idx
    assert "Sorry, nobody is available." in xml
