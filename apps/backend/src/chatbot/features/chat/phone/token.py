"""Mint short-lived Twilio Voice access tokens for the browser softphone."""

from __future__ import annotations

from typing import TYPE_CHECKING

from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

if TYPE_CHECKING:
    from chatbot.platform.config import Settings


def mint_voice_token(settings: Settings, identity: str) -> str:
    """Access token granting outgoing calls to our TwiML app."""
    token = AccessToken(
        settings.twilio_account_sid,
        settings.twilio_api_key_sid,
        settings.twilio_api_key_secret,
        identity=identity,
    )
    token.add_grant(
        VoiceGrant(
            outgoing_application_sid=settings.twilio_twiml_app_sid,
            incoming_allow=False,
        )
    )
    return str(token.to_jwt())
