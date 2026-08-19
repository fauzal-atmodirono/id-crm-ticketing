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


def test_client_dial_uses_the_long_form_with_parameters():
    """The shorthand <Client>id</Client> has nowhere to put context. The
    ringing browser needs to know who is calling and why BEFORE the agent
    decides to accept, and <Parameter> children are how Twilio delivers that
    (they arrive as call.customParameters in the JS SDK)."""
    from chatbot.features.chat.phone.handoff_target import HandoffTarget, dial_twiml

    xml = dial_twiml(
        HandoffTarget(kind="client", value="agent_17"),
        "https://example.test/webhooks/phone/dial-status",
        20,
        "",
        {"conversation_id": "42", "reason": "billing dispute"},
    )
    assert "<Client><Identity>agent_17</Identity>" in xml
    assert '<Parameter name="conversation_id" value="42"/>' in xml
    assert '<Parameter name="reason" value="billing dispute"/>' in xml
    assert 'timeout="20"' in xml


def test_client_dial_needs_no_caller_id():
    """Twilio error 13214 (a client: caller id rejected for a PSTN <Number>)
    is what motivates the caller-id guard elsewhere. It does not apply to
    <Client>, and emitting an empty callerId attribute would be junk TwiML."""
    from chatbot.features.chat.phone.handoff_target import HandoffTarget, dial_twiml

    xml = dial_twiml(
        HandoffTarget(kind="client", value="agent_17"), "https://e.test/a", 20, ""
    )
    assert "callerId" not in xml


def test_parameter_values_are_escaped():
    """`reason` and `summary` are MODEL-GENERATED strings going into an XML
    attribute. Unescaped, a quote character produces TwiML Twilio cannot
    parse -- which drops a call that is still live."""
    from chatbot.features.chat.phone.handoff_target import HandoffTarget, dial_twiml

    xml = dial_twiml(
        HandoffTarget(kind="client", value="agent_17"),
        "https://e.test/a",
        20,
        "",
        {"reason": 'he said "no" & left <angrily>'},
    )
    # `quoteattr` picks whichever quote delimiter (' or ") does not appear in
    # the value, escaping only what's actually structural for that choice --
    # e.g. a lone `"` with no `'` in the value is legally left unescaped
    # inside single-quote delimiters. So the load-bearing property isn't a
    # specific escape sequence, it's that `&`/`<` (always structural,
    # regardless of delimiter) are neutralised and the whole document parses.
    assert "&amp;" in xml
    assert "<angrily>" not in xml
    assert "&lt;angrily&gt;" in xml

    import xml.etree.ElementTree as ET

    ET.fromstring(xml)  # must parse


def test_number_dial_is_unchanged():
    """Regression guard: the PSTN path is the fallback that protects every
    caller when the softphone path finds nobody."""
    from chatbot.features.chat.phone.handoff_target import HandoffTarget, dial_twiml

    xml = dial_twiml(
        HandoffTarget(kind="pstn", value="+60388889999"), "https://e.test/a", 30, "+60311112222"
    )
    assert "<Number>+60388889999</Number>" in xml
    assert 'callerId="+60311112222"' in xml
    assert "<Identity>" not in xml


def test_fanout_emits_one_client_per_identity():
    from chatbot.features.chat.phone.handoff_target import fanout_twiml

    xml = fanout_twiml(["agent_1", "agent_2", "agent_3"], "https://e.test/f", 25)
    assert xml.count("<Client>") == 3
    assert "<Identity>agent_2</Identity>" in xml
    assert 'timeout="25"' in xml


def test_fanout_with_no_identities_returns_empty_string():
    """Callers must be able to ask "is there anyone to ring?" without
    building a <Dial> with zero nouns, which is a TwiML error."""
    from chatbot.features.chat.phone.handoff_target import fanout_twiml

    assert fanout_twiml([], "https://e.test/f", 25) == ""


def test_handoff_guardrails_are_in_the_phone_system_instruction():
    """Regression guard for the 2026-08-19 proton call where the model answered
    a question correctly and then escalated on the next turn, recording the
    reason "the user is asking a follow-up question" after the caller's Malay
    came through garbled. The instruction had nothing separating "I misheard"
    from "I cannot help", so any hard-to-hear turn escalated."""
    from chatbot.features.chat.router import _HANDOFF_GUARDRAILS

    g = _HANDOFF_GUARDRAILS.lower()
    assert "never hand off because you could not hear" in g
    assert "repeat or rephrase" in g
    assert "follow-up question" in g and "not a reason to hand off" in g
    assert "must" in g and "kb_search" in g
