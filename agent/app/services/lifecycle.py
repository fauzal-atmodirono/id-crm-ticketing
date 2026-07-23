"""Conversation lifecycle state machine for the Proton process-flow SOP.

Layered on top of Chatwoot's pending/open/resolved status, this drives the
per-conversation flow: disclaimer on create → (scanner) idle warn → auto-close →
"is your case resolved? YES/NO" → rating survey → resolved. State lives in the
`conversation_lifecycle` table (app.services.lifecycle_store) and is mirrored to
a `lifecycle_state` Chatwoot custom attribute for agent visibility.

Everything here is fail-open: a downstream failure logs and returns, it never
raises out of the background task / scanner tick that called it. Message
templates default to the SOP verbatim text and are overridden by the proton
assistant persona messages where those exist.
"""

from __future__ import annotations

import logging

from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings
from app.services import lifecycle_store

logger = logging.getLogger(__name__)

# Lifecycle states (see the design doc's state machine).
ACTIVE = "active"
IDLE_WARNED = "idle_warned"
AWAITING_RESOLUTION = "awaiting_resolution"
AWAITING_SURVEY = "awaiting_survey"
CLOSED = "closed"

# SOP verbatim message defaults (overridable via proton persona messages).
DISCLAIMER_DEFAULT = (
    "Our customer support chatbot uses artificial intelligence (AI) to assist "
    "you. Responses are generated automatically based on your input. We do not "
    "guarantee the accuracy, completeness or timeliness of the information "
    "provided. You are encouraged to verify the content independently. For "
    "specific concerns or complex issues, please contact our live agent for "
    "further assistance."
)
IDLE_WARNING_DEFAULT = "Your chat will close in 5 minutes if we do not hear from you."
IDLE_CLOSE_DEFAULT = "Closed due to inactivity."
RESOLUTION_PROMPT_DEFAULT = "Is your case resolved? Please reply YES or NO."
SURVEY_AI_DEFAULT = "Thank you. Please rate our AI assistant from 1 to 5."
SURVEY_AGENT_DEFAULT = "Thank you. Please rate our support agent from 1 to 5."
THANKS_DEFAULT = "Thank you for your feedback!"


async def _mirror_state(conversation_id: int, state: str) -> None:
    """Best-effort mirror of the lifecycle state into a Chatwoot custom
    attribute for agent visibility. Never raises."""
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.set_custom_attributes(
            conversation_id, {"lifecycle_state": state}
        )
    except Exception:
        logger.debug(
            "lifecycle: could not mirror state for conversation %s", conversation_id,
            exc_info=True,
        )


async def _welcome_text(inbox_id: int | None) -> str:
    """Persona welcome_message if configured, else the SOP disclaimer."""
    proton = get_proton_config_client()
    if proton is not None and inbox_id is not None:
        try:
            msgs = await proton.get_assistant_messages(inbox_id)
            if msgs and msgs.get("welcome"):
                return msgs["welcome"]
        except Exception:
            logger.debug("lifecycle: could not fetch welcome message", exc_info=True)
    return DISCLAIMER_DEFAULT


async def on_conversation_created(payload: dict) -> None:
    """Seed the lifecycle row ACTIVE and post the AI disclaimer/welcome once.

    Called as a background task from the Chatwoot webhook `conversation_created`
    event. Fail-open: any missing field or downstream error logs and returns.
    """
    conversation_id = payload.get("id")
    if conversation_id is None:
        logger.info("lifecycle: conversation_created without id, skipping")
        return

    channel = payload.get("channel")
    inbox_id = payload.get("inbox_id")

    try:
        await lifecycle_store.seed_active(conversation_id, channel=channel)
    except Exception:
        logger.exception(
            "lifecycle: failed to seed active row for conversation %s",
            conversation_id,
        )
        return
    await _mirror_state(conversation_id, ACTIVE)

    settings = get_settings()
    if not settings.lifecycle_disclaimer_enabled:
        return

    text = await _welcome_text(inbox_id)
    if not text:
        return
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.create_message(conversation_id, text, private=False)
    except Exception:
        logger.exception(
            "lifecycle: failed to post disclaimer for conversation %s",
            conversation_id,
        )
