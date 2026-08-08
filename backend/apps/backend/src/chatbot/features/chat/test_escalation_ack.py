"""P2 task 1 — which transport carries the customer acknowledgement.

The PIC and dealer legs are already channel-agnostic. Only the *customer*
acknowledgement is channel-specific, and it resolves to exactly one of three
transports. Keeping that decision pure means the notifier never has to learn
what WhatsApp is.
"""

from __future__ import annotations

from chatbot.features.chat.escalation_ack import ack_transport


def test_email_channel_resolves_to_the_email_transport():
    assert ack_transport("Channel::Email") == "email"


def test_whatsapp_and_twiliosms_resolve_to_the_conversation_transport():
    assert ack_transport("Channel::Whatsapp") == "conversation"
    assert ack_transport("Channel::TwilioSms") == "conversation"


def test_facebook_and_instagram_resolve_to_the_conversation_transport():
    assert ack_transport("Channel::FacebookPage") == "conversation"
    assert ack_transport("Channel::Instagram") == "conversation"


def test_web_widget_and_api_resolve_to_the_conversation_transport():
    assert ack_transport("Channel::WebWidget") == "conversation"
    assert ack_transport("Channel::Api") == "conversation"


def test_voice_resolves_to_none():
    """A voice call has no thread to post into and no address to mail. The
    caller was already spoken to; there is nothing to acknowledge in writing."""
    assert ack_transport("Channel::Voice") == "none"


def test_an_unknown_channel_type_falls_back_to_the_conversation_transport():
    """Not `none`. An unknown channel almost certainly has a conversation
    thread, and falling back to silence would reintroduce the exact defect this
    package exists to fix."""
    assert ack_transport("Channel::SomethingNew") == "conversation"


def test_a_none_channel_type_falls_back_to_the_conversation_transport():
    assert ack_transport(None) == "conversation"
    assert ack_transport("") == "conversation"
