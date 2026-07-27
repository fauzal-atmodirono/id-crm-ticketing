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

import json
import logging

from sqlalchemy.exc import SQLAlchemyError

from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings
from app.db.models import AiAction
from app.db.session import async_session_maker
from app.services import categorize, lifecycle_store

logger = logging.getLogger(__name__)

# Lifecycle states (see the design doc's state machine).
ACTIVE = "active"
IDLE_WARNED = "idle_warned"
AWAITING_RESOLUTION = "awaiting_resolution"
AWAITING_SURVEY = "awaiting_survey"
CLOSED = "closed"

# Marker stamped on every customer-facing lifecycle message (disclaimer, idle
# warn/close, resolution prompt, surveys). The brain-swap orchestrator reads it
# to tell these system notices apart from the bot's own conversational replies
# so they never mask the customer's turn (see orchestrator._latest_incoming_text).
_LIFECYCLE_MARKER = {"proton_lifecycle": True}

# SOP verbatim message defaults (overridable via proton persona messages).
DISCLAIMER_DEFAULT = (
    "DISCLAIMER: "
    "Our customer support chatbot uses artificial intelligence (AI) to assist "
    "you. Responses are generated automatically based on your input. We do not "
    "guarantee the accuracy, completeness or timeliness of the information "
    "provided. You are encouraged to verify the content independently. For "
    "specific concerns or complex issues, please contact our live agent for "
    "further assistance."
)
IDLE_WARNING_DEFAULT = "Your chat will close in {{minutes}} minutes if we do not hear from you."
IDLE_CLOSE_DEFAULT = "Closed due to inactivity."
RESOLUTION_PROMPT_DEFAULT = "Is your case resolved? Please reply YES or NO."
SURVEY_AI_DEFAULT = "Thank you. Please rate our AI assistant from 1 to 5."
SURVEY_AGENT_DEFAULT = "Thank you. Please rate our support agent from 1 to 5."
THANKS_DEFAULT = "Thank you for your feedback!"
ASSIGN_AGENT_DEFAULT = "Thank you. We will assign an agent to assist you further."


def _created_by_ai_handoff(payload: dict) -> bool:
    """True when the backend stamped this conversation as an AI escalation/handoff.

    The backend ChatwootAdapter sets ``additional_attributes.ai_handoff`` when it
    creates a Chatwoot conversation to hand a chat to a human. Such a conversation
    is not a fresh customer contact — the customer already saw the AI disclaimer on
    the bot's first reply — so re-posting the disclaimer here would surface as the
    human agent's opening message on the customer's screen.
    """
    attrs = payload.get("additional_attributes")
    return bool(isinstance(attrs, dict) and attrs.get("ai_handoff"))


def _resolve_message(messages: dict | None, key: str, default: str) -> str:
    """Operator override if present and non-empty, else the hard-coded default."""
    if messages:
        val = messages.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return default


def _resolve_lifecycle_message(
    timing: dict | None, msgs: dict | None, key: str, default: str
) -> str:
    """Per-inbox override -> persona message -> SOP default. The per-inbox store
    key is f"{key}_message"; `key` is the persona key (e.g. "idle_close")."""
    per = (timing or {}).get(f"{key}_message")
    if isinstance(per, str) and per.strip():
        return per
    return _resolve_message(msgs, key, default)


def render_idle_warning(text: str, minutes: int) -> str:
    """Replace the {{minutes}} token with the effective close-grace value.
    A message without the token is returned unchanged (backward compatible)."""
    return text.replace("{{minutes}}", str(minutes))


async def _fetch_assistant_messages(inbox_id: int | None) -> dict | None:
    """Fetch operator-configured assistant messages for inbox_id, fail-open to None."""
    proton = get_proton_config_client()
    if proton is None or inbox_id is None:
        return None
    try:
        return await proton.get_assistant_messages(inbox_id)
    except Exception:
        logger.debug(
            "lifecycle: could not fetch assistant messages for inbox %s", inbox_id,
            exc_info=True,
        )
        return None


async def _fetch_lifecycle_timing(inbox_id: int | None) -> dict | None:
    """Fetch per-inbox lifecycle timing overrides for inbox_id, fail-open to None."""
    proton = get_proton_config_client()
    if proton is None or inbox_id is None:
        return None
    try:
        return await proton.get_assistant_lifecycle_timing(inbox_id)
    except Exception:
        logger.debug(
            "lifecycle: could not fetch lifecycle timing for inbox %s", inbox_id,
            exc_info=True,
        )
        return None


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


async def _inbox_channel(inbox_id: int | None) -> str | None:
    """Resolve the inbox channel_type (e.g. 'Channel::Email'), fail-open None."""
    if inbox_id is None:
        return None
    try:
        inbox = await get_chatwoot_client().get_inbox(inbox_id)
        return inbox.get("channel_type")
    except Exception:
        logger.warning(
            "lifecycle: could not resolve channel for inbox %s; defaulting to chat/disclaimer path",
            inbox_id,
            exc_info=True,
        )
        return None


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

    # AI escalation/handoff conversations are created by the backend when a chat is
    # handed to a human. The disclaimer belongs on the bot's first reply (shown in
    # the customer UI), not here — posting it would arrive as the human agent's
    # opening message. Seed/track the conversation, but post no greeting.
    if _created_by_ai_handoff(payload):
        logger.info(
            "lifecycle: conversation %s created by AI handoff; skipping greeting",
            conversation_id,
        )
        return

    settings = get_settings()
    channel_type = await _inbox_channel(inbox_id)

    # Email inbox: post the SOP once-per-thread auto-acknowledgement instead of
    # the AI disclaimer. Dedup is structural — conversation_created fires once
    # per conversation and seed_active only acts on a new row, so thread replies
    # and agent replies never re-trigger.
    # When email_autoack is disabled, post nothing and return — the AI disclaimer
    # text is wrong for an email thread, so we never fall through to it here.
    if channel_type == "Channel::Email":
        if not settings.email_autoack_enabled:
            return
        text = settings.email_autoack_template
        if not text:
            logger.warning(
                "lifecycle: email_autoack_enabled but template is empty for conversation %s; nothing posted",
                conversation_id,
            )
    else:
        if not settings.lifecycle_disclaimer_enabled:
            return
        text = await _welcome_text(inbox_id)

    if not text:
        return
    try:
        await get_chatwoot_client().create_message(
            conversation_id, text, private=False, content_attributes=_LIFECYCLE_MARKER
        )
    except Exception:
        logger.exception(
            "lifecycle: failed to post disclaimer/auto-ack for conversation %s",
            conversation_id,
        )


_YES_TOKENS = {"yes", "y", "ya", "yeah", "yep", "resolved", "ok", "okay", "sudah"}
_NO_TOKENS = {"no", "n", "nope", "not", "unresolved", "belum", "tidak"}


def parse_yes_no(text: str) -> bool | None:
    tokens = {t.strip(".,!?") for t in (text or "").lower().split()}
    if tokens & _YES_TOKENS:
        return True
    if tokens & _NO_TOKENS:
        return False
    return None


def parse_rating(text: str) -> int | None:
    for token in (text or "").replace("/", " ").split():
        cleaned = token.strip(".,!?")
        if cleaned.isdigit():
            value = int(cleaned)
            if 1 <= value <= 5:
                return value
    return None


async def _record_survey(
    conversation_id: int, variant: str, score: int | None, raw: str
) -> None:
    """Log the survey result to ai_actions (reuses the existing audit table).
    Fail-open: a DB blip must not break the flow."""
    output = str(score) if score is not None else json.dumps({"unparsed": raw})
    try:
        async with async_session_maker() as session:
            session.add(
                AiAction(
                    conversation_ref=f"chatwoot:{conversation_id}",
                    decision=f"survey_{variant}",
                    model="lifecycle",
                    output=output,
                )
            )
            await session.commit()
    except SQLAlchemyError:
        logger.exception(
            "lifecycle: failed to record survey for conversation %s", conversation_id
        )


async def _post(conversation_id: int, text: str) -> None:
    """Post a public message, swallowing transient errors."""
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.create_message(
            conversation_id, text, private=False, content_attributes=_LIFECYCLE_MARKER
        )
    except Exception:
        logger.exception("lifecycle: failed to post message to %s", conversation_id)


async def _resolve(conversation_id: int) -> None:
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.toggle_status(conversation_id, "resolved")
    except Exception:
        logger.exception("lifecycle: failed to resolve conversation %s", conversation_id)


async def _reopen(conversation_id: int) -> None:
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.toggle_status(conversation_id, "open")
    except Exception:
        logger.exception("lifecycle: failed to reopen conversation %s", conversation_id)


async def handle_lifecycle_reply(
    conversation_id: int,
    text: str,
    state: str,
    inbox_id: int | None = None,
) -> None:
    """Route a customer reply for a conversation mid-lifecycle. Called from the
    orchestrator's pre-check (before its pending-only filter) so it fires even
    on open/resolved conversations."""
    settings = get_settings()
    msgs = await _fetch_assistant_messages(inbox_id)
    timing = await _fetch_lifecycle_timing(inbox_id)

    if state == AWAITING_RESOLUTION:
        answer = parse_yes_no(text)
        if answer is False or answer is None:
            # Not resolved (or unclear → err toward a human): reopen for an agent.
            await _post(
                conversation_id,
                _resolve_lifecycle_message(timing, msgs, "assign_agent", ASSIGN_AGENT_DEFAULT),
            )
            await _reopen(conversation_id)
            await lifecycle_store.transition(conversation_id, CLOSED)
            await _mirror_state(conversation_id, CLOSED)
            return
        # answer is True → resolved by the bot.
        if settings.lifecycle_survey_enabled:
            await _post(
                conversation_id,
                _resolve_lifecycle_message(timing, msgs, "survey_ai", SURVEY_AI_DEFAULT),
            )
            await lifecycle_store.transition(
                conversation_id, AWAITING_SURVEY, survey_variant="ai"
            )
            await _mirror_state(conversation_id, AWAITING_SURVEY)
        else:
            await lifecycle_store.transition(conversation_id, CLOSED)
            await _mirror_state(conversation_id, CLOSED)
            await categorize.maybe_categorize(conversation_id)
            await _resolve(conversation_id)
        return

    if state == AWAITING_SURVEY:
        row = await lifecycle_store.get_row(conversation_id)
        variant = row.survey_variant if row is not None else "ai"
        score = parse_rating(text)
        await _record_survey(conversation_id, variant or "ai", score, text)
        await _post(
            conversation_id,
            _resolve_lifecycle_message(timing, msgs, "thanks", THANKS_DEFAULT),
        )
        # CLOSED is set BEFORE resolving so the resulting status webhook sees a
        # terminal lifecycle and on_human_resolved skips (no double survey).
        await lifecycle_store.transition(conversation_id, CLOSED)
        await _mirror_state(conversation_id, CLOSED)
        await categorize.maybe_categorize(conversation_id)
        await _resolve(conversation_id)
        return


async def on_human_resolved(payload: dict) -> None:
    """When a human resolves a conversation, post the agent-performance survey.

    Skipped when the lifecycle already ended (CLOSED) or is mid-survey — this is
    what prevents the bot's own survey-complete resolve from re-triggering a
    survey. Fail-open."""
    settings = get_settings()
    if not settings.lifecycle_survey_enabled:
        return
    conversation_id = payload.get("id")
    if conversation_id is None or payload.get("status") != "resolved":
        return

    try:
        state = await lifecycle_store.get_state(conversation_id)
    except Exception:
        logger.exception(
            "lifecycle: failed to read state for conversation %s in on_human_resolved",
            conversation_id,
        )
        return
    if state in (CLOSED, AWAITING_SURVEY, AWAITING_RESOLUTION):
        return  # lifecycle already owns the ending

    inbox_id: int | None = payload.get("inbox_id")
    msgs = await _fetch_assistant_messages(inbox_id)
    timing = await _fetch_lifecycle_timing(inbox_id)
    await _post(
        conversation_id,
        _resolve_lifecycle_message(timing, msgs, "survey_agent", SURVEY_AGENT_DEFAULT),
    )
    try:
        await lifecycle_store.transition(
            conversation_id, AWAITING_SURVEY, survey_variant="agent"
        )
    except Exception:
        logger.exception(
            "lifecycle: failed to transition conversation %s to AWAITING_SURVEY",
            conversation_id,
        )
        return
    await _mirror_state(conversation_id, AWAITING_SURVEY)
