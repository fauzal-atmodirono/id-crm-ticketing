"""Gemini-powered orchestration for incoming Chatwoot agent-bot events.

`handle_bot_event` is the entry point the `/webhooks/chatwoot/bot` route
background-dispatches to. It only acts on a genuinely actionable event — an
incoming customer message on a `pending` conversation, not authored by the
bot itself — then debounces per conversation: rapid-fire messages from the
same customer coalesce into a single Gemini call fired `DEBOUNCE_SECONDS`
after the *last* one for that conversation, which re-fetches the
conversation's current message history at that point rather than replaying
whatever payload originally tripped the debounce.

Every Gemini decision is logged to `ai_actions` before it's executed.
Execution never sends a reply automatically unless `AGENT_MODE=auto`; in
`suggest` mode (the default) a reply is posted as a private note and the
conversation is reopened for a human to review — the AI never auto-sends.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.ai import gemini
from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings
from app.db.models import AiAction
from app.db.session import async_session_maker
from app.services import lifecycle, lifecycle_store, sync

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 3.0


class _DebounceState:
    """One conversation's scheduled debounce task plus whether it has moved
    past the cancellable sleep into processing. Cancellation from
    `handle_bot_event` is only allowed while `processing` is False: once a
    task starts talking to Gemini/Chatwoot it must run to completion, or a
    burst of messages could leave partial side effects behind (e.g. an
    `ai_actions` row logged but the reply never posted). The event loop is
    single-threaded, so the flag flip in `_debounced_process` is atomic with
    respect to the flag check in `handle_bot_event`."""

    __slots__ = ("task", "processing")

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.processing = False


# One in-flight debounce per Chatwoot conversation id. A new eligible event
# for the same conversation cancels a still-sleeping task and reschedules
# (coalescing a burst into a single Gemini call); if the task is already
# processing it's left to finish and a fresh debounce is scheduled instead.
_pending_tasks: dict[int, _DebounceState] = {}

SYSTEM_PROMPT = (
    "You are a support agent for the company, handling a live customer "
    "conversation. Decide exactly one action by calling a function: "
    "send_reply to answer the customer directly, escalate_to_ticket if this "
    "needs a human specialist or can't be resolved from the conversation "
    "alone, or handoff_to_human for anything else you're unsure about. Keep "
    "replies short, friendly, and strictly grounded in what the conversation "
    "actually says — never invent facts, prices, policies, or commitments "
    "you can't verify from it. Always reply in the same language the customer "
    "is using."
)


def _sender_type(payload: dict) -> str | None:
    return (payload.get("sender") or {}).get("type")


def _is_eligible(payload: dict) -> bool:
    if payload.get("event") != "message_created":
        return False
    if payload.get("message_type") != "incoming":
        return False
    if _sender_type(payload) == "agent_bot":
        return False
    conversation = payload.get("conversation") or {}
    if conversation.get("status") != "pending":
        return False
    return conversation.get("id") is not None


def _incoming_customer_message(payload: dict) -> int | None:
    """Return the conversation id for an incoming, non-bot customer message,
    regardless of conversation status (the lifecycle pre-check needs to see
    open/resolved conversations, not just pending ones)."""
    if payload.get("event") != "message_created":
        return None
    if payload.get("message_type") != "incoming":
        return None
    if _sender_type(payload) == "agent_bot":
        return None
    conversation = payload.get("conversation") or {}
    return conversation.get("id")


_LIFECYCLE_CONSUMED: str = "_lifecycle_consumed"


def _lifecycle_consumed(payload: dict) -> bool:
    return bool(payload.get(_LIFECYCLE_CONSUMED))


async def _maybe_handle_lifecycle_reply(payload: dict) -> None:
    """When the feature is on, route survey/confirmation replies to the
    lifecycle parser (instead of Gemini) and reset the idle clock on a normal
    reply. Marks the payload consumed so `handle_bot_event` returns early.
    Fail-open: any error just falls through to the normal bot path."""
    settings = get_settings()
    if not settings.lifecycle_enabled:
        return
    conversation_id = _incoming_customer_message(payload)
    if conversation_id is None:
        return
    try:
        state = await lifecycle_store.get_state(conversation_id)
    except Exception:
        logger.debug(
            "orchestrator: lifecycle state lookup failed for %s", conversation_id,
            exc_info=True,
        )
        return

    if state in (lifecycle.AWAITING_RESOLUTION, lifecycle.AWAITING_SURVEY):
        text = payload.get("content") or ""
        try:
            await lifecycle.handle_lifecycle_reply(conversation_id, text, state)
        except Exception:
            logger.debug(
                "orchestrator: lifecycle reply handling failed for %s", conversation_id,
                exc_info=True,
            )
            return
        payload[_LIFECYCLE_CONSUMED] = True
        return
    if state == lifecycle.IDLE_WARNED:
        # Customer came back → reset the idle clock so the scanner won't close.
        try:
            await lifecycle_store.transition(conversation_id, lifecycle.ACTIVE)
        except Exception:
            logger.debug(
                "orchestrator: lifecycle idle-warn reset failed for %s", conversation_id,
                exc_info=True,
            )


async def handle_bot_event(payload: dict) -> asyncio.Task | None:
    """Filter the event, then (re)schedule the debounced processing task for
    its conversation. Returns the scheduled task (mainly so tests can await
    it deterministically) or None if the event wasn't actionable."""
    await _maybe_handle_lifecycle_reply(payload)
    if _lifecycle_consumed(payload):
        return None

    if not _is_eligible(payload):
        return None

    conversation_id = payload["conversation"]["id"]

    existing = _pending_tasks.get(conversation_id)
    if (
        existing is not None
        and existing.task is not None
        and not existing.task.done()
        and not existing.processing
    ):
        # Still in the debounce sleep: coalesce by cancelling and rescheduling.
        # If it's already processing we leave it to run to completion and just
        # schedule a fresh debounce for this newer message.
        existing.task.cancel()

    state = _DebounceState()
    state.task = asyncio.ensure_future(_debounced_process(conversation_id, state))
    _pending_tasks[conversation_id] = state
    return state.task


async def _debounced_process(conversation_id: int, state: _DebounceState) -> None:
    # Resolve debounce duration: prefer the tenant-level setting from the
    # proton backend (if configured and reachable), fall back to the module
    # constant (also what tests monkeypatch to 0 to stay fast).
    proton = get_proton_config_client()
    if proton is not None:
        tenant_debounce = await proton.effective_debounce_seconds()
    else:
        tenant_debounce = None
    debounce = tenant_debounce if tenant_debounce is not None else DEBOUNCE_SECONDS

    try:
        await asyncio.sleep(debounce)
    except asyncio.CancelledError:
        # Superseded by a newer event for the same conversation; the newer
        # task is the one that'll actually process it.
        return

    # Past the sleep: from here on this task must run to completion —
    # handle_bot_event checks this flag before cancelling.
    state.processing = True
    try:
        await _process_conversation(conversation_id)
    finally:
        # Drop our own bookkeeping entry unless a newer debounce has already
        # replaced it, so the dict doesn't grow with finished conversations.
        if _pending_tasks.get(conversation_id) is state:
            _pending_tasks.pop(conversation_id, None)


async def _build_context(conversation_id: int) -> str:
    """Last 20 non-private messages plus the contact's name/email, fetched
    fresh (not from whatever payload originally triggered the debounce)."""
    chatwoot = get_chatwoot_client()
    raw_messages = await chatwoot.get_messages(conversation_id)
    if isinstance(raw_messages, dict):
        message_list = raw_messages.get("payload") or []
    else:
        message_list = raw_messages or []

    lines: list[str] = []
    contact_name: str | None = None
    contact_email: str | None = None
    for message in message_list[-20:]:
        if message.get("private"):
            continue
        sender = message.get("sender") or {}
        sender_name = sender.get("name", "Unknown")
        text = message.get("content") or ""
        lines.append(f"{sender_name}: {text}")

        if message.get("message_type") == 0 and contact_name is None:
            contact_name = sender.get("name")
            contact_email = sender.get("email")

    header = f"Customer: {contact_name or 'unknown'} <{contact_email or 'unknown'}>"
    transcript = "\n".join(lines) or "(no messages)"
    return f"{header}\n\n{transcript}"


async def _log_decision(conversation_id: int, decision: gemini.Decision) -> None:
    settings = get_settings()
    output = decision.raw_text if decision.raw_text is not None else json.dumps(decision.args)
    try:
        async with async_session_maker() as session:
            session.add(
                AiAction(
                    conversation_ref=f"chatwoot:{conversation_id}",
                    decision=decision.action,
                    model=settings.gemini_model,
                    prompt_tokens=decision.prompt_tokens,
                    output=output,
                )
            )
            await session.commit()
    except SQLAlchemyError:
        # Logging the decision is an audit trail, not a precondition for
        # executing it -- a DB blip here must not abort the decision itself.
        logger.exception(
            "orchestrator: failed to log ai_actions row for conversation %s "
            "(decision %r); continuing to execute it anyway",
            conversation_id,
            decision.action,
        )


async def _process_conversation(conversation_id: int) -> None:
    settings = get_settings()
    chatwoot = get_chatwoot_client()

    # Resolve inbox_id for per-inbox mode lookup. We try a dedicated API call
    # rather than relying on the event payload (which isn't passed this deep)
    # so the decision is always based on fresh state.
    proton = get_proton_config_client()
    inbox_id: int | None = None
    if proton is not None:
        try:
            conversation_data = await chatwoot.get_conversation(conversation_id)
            inbox_id = conversation_data.get("inbox_id")
        except Exception:
            logger.debug(
                "orchestrator: could not fetch conversation %s for inbox_id lookup; "
                "will use global agent_mode",
                conversation_id,
            )

    # Resolve effective mode: per-inbox override from proton backend, or the
    # global setting. On any failure effective_inbox_mode returns None and we
    # fall back transparently (fail-open).
    if proton is not None and inbox_id is not None:
        per_inbox_mode = await proton.effective_inbox_mode(inbox_id)
    else:
        per_inbox_mode = None
    effective_mode = per_inbox_mode or settings.agent_mode

    if effective_mode == "off":
        logger.info(
            "orchestrator: inbox mode is 'off' for conversation %s (inbox %s); skipping",
            conversation_id,
            inbox_id,
        )
        return

    # Fetch persona messages for the inbox (fail-open: None on any error).
    # Only the handoff_message is consumed here; welcome_message has no trigger
    # in the agent-bot flow, and resolution_message is handled by sync.py.
    handoff_message = ""
    if proton is not None and inbox_id is not None:
        try:
            assistant_msgs = await proton.get_assistant_messages(inbox_id)
            if assistant_msgs:
                handoff_message = assistant_msgs.get("handoff", "") or ""
        except Exception:
            logger.debug(
                "orchestrator: could not fetch assistant messages for inbox %s; "
                "proceeding without handoff message",
                inbox_id,
            )

    try:
        context = await _build_context(conversation_id)
    except httpx.HTTPError:
        # Runs as (part of) a background task -- a transient Chatwoot failure
        # must be logged, not die as "Task exception was never retrieved".
        logger.exception(
            "orchestrator: failed to fetch messages for conversation %s, skipping",
            conversation_id,
        )
        return

    decision = await gemini.decide(SYSTEM_PROMPT, context)
    await _log_decision(conversation_id, decision)

    try:
        await _execute_decision(
            conversation_id, decision, effective_mode, chatwoot,
            handoff_message=handoff_message,
        )
    except httpx.HTTPError:
        logger.exception(
            "orchestrator: failed executing decision %r for conversation %s",
            decision.action,
            conversation_id,
        )


async def _execute_decision(
    conversation_id, decision, mode: str, chatwoot, *, handoff_message: str = ""
) -> None:
    if decision.action == "send_reply":
        text = decision.args.get("text", "")
        if mode == "auto":
            settings = get_settings()
            await chatwoot.create_message(
                conversation_id,
                text,
                private=False,
                token_override=settings.chatwoot_bot_token,
            )
            # Stays pending — auto-sent replies don't hand off to a human.
        else:
            await chatwoot.create_message(
                conversation_id,
                f"🤖 Suggested reply:\n\n{text}",
                private=True,
            )
            await chatwoot.toggle_status(conversation_id, "open")
    elif decision.action == "escalate_to_ticket":
        await sync.escalate_conversation(
            conversation_id,
            reason=decision.args.get("reason"),
            priority=decision.args.get("priority"),
            summary=decision.args.get("summary"),
        )
        await chatwoot.toggle_status(conversation_id, "open")
    else:
        # handoff_to_human, or any unrecognized action -- always hand off
        # rather than doing nothing.
        if decision.action != "handoff_to_human":
            logger.warning(
                "orchestrator: unrecognized decision action %r for conversation %s, "
                "handing off",
                decision.action,
                conversation_id,
            )
        # Post the persona handoff message publicly BEFORE reopening, so the
        # customer sees it while the conversation is still in bot-handled state.
        # Only posted when the message is non-empty (fail-open: empty string
        # when proton is unconfigured or the assistant has no handoff text).
        if handoff_message:
            await chatwoot.create_message(conversation_id, handoff_message, private=False)
        await chatwoot.toggle_status(conversation_id, "open")
