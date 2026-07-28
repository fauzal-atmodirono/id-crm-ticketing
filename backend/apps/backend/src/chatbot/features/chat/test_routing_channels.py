from __future__ import annotations

from chatbot.features.routing.channels import canonical_channel, CANONICAL_CHANNELS


def test_canonical_channels_tuple():
    assert CANONICAL_CHANNELS == ("whatsapp", "call", "email", "social", "web")


def test_mapping():
    assert canonical_channel("Channel::TwilioSms") == "whatsapp"
    assert canonical_channel("Channel::Whatsapp") == "whatsapp"
    assert canonical_channel("Channel::Voice") == "call"
    assert canonical_channel("Channel::Email") == "email"
    assert canonical_channel("Channel::FacebookPage") == "social"
    assert canonical_channel("Channel::Instagram") == "social"
    assert canonical_channel("Channel::WebWidget") == "web"
    assert canonical_channel("Channel::Api") == "web"
    assert canonical_channel(None) == "web"
    assert canonical_channel("") == "web"
