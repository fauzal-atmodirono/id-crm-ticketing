"""Mint the AGENT-side Twilio Voice access token.

Deliberately a separate module from `token.py` rather than a flag on
`mint_voice_token`. That function serves a public, unauthenticated SPA
endpoint and its `incoming_allow=False` is a security property, not a
default -- a shared function with an `incoming` parameter would put the
caller-side token one wrong argument away from being able to receive
transferred customer calls. Two functions cannot be confused at a call site.

The identity is `agent_<chatwoot_user_id>` and is ALWAYS derived from a
validated Chatwoot session by the caller (see `softphone_router.py`), never
from request data: a client-supplied identity would let any authenticated
agent register as a colleague and intercept their transferred calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_IDENTITY_PREFIX = "agent_"


def agent_identity(chatwoot_user_id: int) -> str:
    return f"{_IDENTITY_PREFIX}{chatwoot_user_id}"


def agent_id_from_identity(identity: str) -> int | None:
    """Inverse of `agent_identity`. `None` for anything that is not one of
    ours -- Twilio hands back whatever string was dialled, including the
    caller-side `proton-web-caller` identity, so this must never guess."""
    if not identity.startswith(_IDENTITY_PREFIX):
        return None
    suffix = identity[len(_IDENTITY_PREFIX) :]
    if not suffix.isdigit():
        return None
    return int(suffix)


def mint_agent_voice_token(settings: Settings, chatwoot_user_id: int) -> str:
    """Access token allowing this agent's browser to RECEIVE calls dialled to
    `agent_<id>`, and to place calls through our TwiML app."""
    identity = agent_identity(chatwoot_user_id)
    token = AccessToken(
        settings.twilio_account_sid,
        settings.twilio_api_key_sid,
        settings.twilio_api_key_secret,
        identity=identity,
        ttl=settings.phone_agent_token_ttl_seconds,
    )
    token.add_grant(
        VoiceGrant(
            outgoing_application_sid=settings.twilio_twiml_app_sid,
            incoming_allow=True,
        )
    )
    return str(token.to_jwt())
