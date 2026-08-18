"""The agent-side Voice token -- the one difference from the caller-side
token in token.py is incoming_allow=True, which is exactly what makes a leak
matter, so these tests are about that bit and about identity derivation."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone.agent_token import (
    agent_id_from_identity,
    agent_identity,
    mint_agent_voice_token,
)


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "twilio_account_sid": "AC" + "0" * 32,
            "twilio_api_key_sid": "SK" + "0" * 32,
            "twilio_api_key_secret": "secret-value",
            "twilio_twiml_app_sid": "AP" + "0" * 32,
            "phone_agent_token_ttl_seconds": 300,
        }
    )


def test_identity_round_trips():
    assert agent_identity(17) == "agent_17"
    assert agent_id_from_identity("agent_17") == 17


def test_identity_parse_rejects_junk():
    """A <Client> identity comes back from Twilio's callback as a string.
    Anything that is not one of OUR identities must be None, not a crash and
    not a coincidental integer."""
    for junk in ["", "17", "agent_", "agent_abc", "proton-web-caller", "agent_1_2"]:
        assert agent_id_from_identity(junk) is None


def test_token_grants_incoming(settings):
    """incoming_allow=True is the entire unlock: without it the browser can
    place calls but Twilio will not route a <Dial><Client> to it."""
    import jwt

    token = mint_agent_voice_token(settings, chatwoot_user_id=17)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["grants"]["voice"]["incoming"]["allow"] is True
    assert claims["grants"]["identity"] == "agent_17"


def test_caller_side_token_still_refuses_incoming(settings):
    """Regression guard: the demo customer softphone must never gain the
    ability to receive transferred calls."""
    import jwt

    from chatbot.features.chat.phone.token import mint_voice_token

    claims = jwt.decode(
        mint_voice_token(settings, "proton-web-caller"),
        options={"verify_signature": False},
    )
    # The twilio SDK's VoiceGrant.to_payload() only emits an "incoming" key
    # at all when incoming_allow is True (see grants.py) -- so the absence
    # of the key, not a literal `False`, IS the caller-side token's
    # "cannot receive calls" property. `.get(...) is False` treats both the
    # same, which is the correct reading; `["incoming"]["allow"]` would
    # KeyError here instead of asserting the thing this test is guarding.
    assert claims["grants"]["voice"].get("incoming", {}).get("allow", False) is False
