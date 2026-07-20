from chatbot.features.chat.phone.twiml import connect_stream_twiml


def test_connect_stream_twiml_embeds_wss_url() -> None:
    xml = connect_stream_twiml("wss://example.test/voice/phone/stream")
    assert xml.startswith("<?xml")
    assert "<Connect>" in xml and "<Stream" in xml
    assert 'url="wss://example.test/voice/phone/stream"' in xml
