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
import base64
import json
import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.ai import gemini
from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings
from app.db.models import AiAction
from app.db.session import async_session_maker
from app.services import lifecycle, lifecycle_store, whatsapp_format
from app.services.customer_context import format_customer_context
from app.services.media import fetch_attachment_bytes
from app.services.media_registry import drop_order

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

LANGUAGE_MATCH_INSTRUCTION = "Always reply in the same language the customer is using."

PENDING_KB_FALLBACK_MESSAGE = (
    "I'm still processing some reference material — please try again in a "
    "few minutes, or I can connect you with an agent."
)

SYSTEM_PROMPT = (
    "You are a support agent for the company, handling a live customer "
    "conversation. Decide exactly one action by calling a function: "
    "send_reply to answer the customer directly, escalate_to_ticket if this "
    "needs a human specialist or can't be resolved from the conversation "
    "alone, or handoff_to_human for anything else you're unsure about. Keep "
    "replies short, friendly, and strictly grounded in what the conversation "
    "actually says — never invent facts, prices, policies, or commitments "
    "you can't verify from it. " + LANGUAGE_MATCH_INSTRUCTION
)


def _persona_prompt(persona: dict | None) -> str:
    """Compose the agent-bot decision prompt from an assistant persona.

    None or all-empty persona -> the module SYSTEM_PROMPT verbatim (byte-identical
    default). Otherwise: base = instructions if set else SYSTEM_PROMPT; then append
    a Guardrails section and a language-preference line when present.

    LANGUAGE_MATCH_INSTRUCTION is always present in the output, even when custom
    instructions replace SYSTEM_PROMPT — operators routinely forget to restate it,
    and dropping it caused the bot to answer in the wrong language (WA-2/IVR-4).
    """
    if not persona:
        return SYSTEM_PROMPT
    instructions = (persona.get("instructions") or "").strip()
    guardrails = [g for g in (persona.get("guardrails") or []) if str(g).strip()]
    language = (persona.get("language") or "").strip()
    if not instructions and not guardrails and not language:
        return SYSTEM_PROMPT
    parts = [instructions or SYSTEM_PROMPT]
    if instructions:
        parts.append(LANGUAGE_MATCH_INSTRUCTION)
    if guardrails:
        parts.append("## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails))
    if language:
        parts.append(
            f"Prefer {language} when the customer's language is unclear, but "
            "always match the language the customer writes in."
        )
    return "\n\n".join(parts)


def _build_system_prompt(persona: dict | None, customer_context: str = "") -> str:
    """The persona prompt, plus this customer's profile when we have one.

    `customer_context` defaults to "" and appends nothing when empty, so every
    caller and every tenant that has no nasabah profile on the contact gets the
    persona prompt byte for byte. That default is what
    `test_no_persona_no_context_is_byte_identical_to_today` pins.

    The profile goes LAST, after the persona's guardrails. An operator-authored
    guardrail should constrain what the model does with the profile, not be
    buried under it.
    """
    base = _persona_prompt(persona)
    if not customer_context:
        return base
    return f"{base}\n\n{customer_context}"


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
        inbox_id: int | None = (payload.get("conversation") or {}).get("inbox_id")
        try:
            await lifecycle.handle_lifecycle_reply(conversation_id, text, state, inbox_id)
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


async def _fetch_messages(conversation_id: int) -> list[dict]:
    """Fetch the conversation's messages once (fresh, not from the trigger
    payload). Shared by _build_context and _build_thread."""
    chatwoot = get_chatwoot_client()
    raw_messages = await chatwoot.get_messages(conversation_id)
    if isinstance(raw_messages, dict):
        return raw_messages.get("payload") or []
    return raw_messages or []


def _build_context(message_list: list[dict]) -> str:
    """Last 20 non-private messages plus the contact's name/email."""
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


def _build_thread(message_list: list[dict]) -> list[dict]:
    """Map the last 20 non-private Chatwoot messages to copilot thread items:
    incoming (type 0) → user, outgoing (type 1) → assistant. Drops private,
    activity/template, and empty-content messages. Order preserved."""
    thread: list[dict] = []
    for message in message_list[-20:]:
        if message.get("private"):
            continue
        mtype = message.get("message_type")
        if mtype == 0:
            role = "user"
        elif mtype == 1:
            role = "assistant"
        else:
            continue
        content = (message.get("content") or "").strip()
        if not content:
            continue
        thread.append({"role": role, "content": content})
    return thread


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
                    # output_tokens/cached_tokens (P8 task 1 added the columns and
                    # the extraction in gemini.py; nothing wired them into a
                    # written row until this fix). Pass `decision`'s values
                    # through as-is -- None means "not captured" (e.g. no
                    # response object on the retry-exhausted fallback path) and
                    # must stay None, not become 0, or a real zero-token call
                    # becomes indistinguishable from one we never measured.
                    output_tokens=decision.output_tokens,
                    cached_tokens=decision.cached_tokens,
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
    conversation_data: dict | None = None
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

    # Fetch assistant persona for the inbox (fail-open: None on any error).
    # Shapes the Gemini decision prompt — custom instructions, guardrails, and
    # language override. No-op when proton is unset or persona is empty.
    persona: dict | None = None
    if proton is not None and inbox_id is not None:
        try:
            persona = await proton.get_assistant_persona(inbox_id)
        except Exception:
            logger.debug(
                "orchestrator: could not fetch assistant persona for inbox %s; "
                "proceeding with default system prompt",
                inbox_id,
            )

    # Fetch this contact's stored profile and fold it into the decision prompt.
    # Entirely best-effort: any failure, a conversation with no sender, or a
    # contact carrying attributes this build doesn't recognise all leave
    # customer_context empty, which makes _build_system_prompt a no-op. Runs as
    # part of a background task, so it must never raise (see CLAUDE.md).
    customer_context = ""
    try:
        if conversation_data is None:
            conversation_data = await chatwoot.get_conversation(conversation_id)
        sender = (conversation_data.get("meta") or {}).get("sender") or {}
        contact_id = sender.get("id")
        if contact_id is not None:
            contact = await chatwoot.get_contact(int(contact_id))
            body = contact.get("payload") if isinstance(contact, dict) else None
            body = body if isinstance(body, dict) else contact
            customer_context = format_customer_context(
                body.get("custom_attributes") if isinstance(body, dict) else None
            )
    except Exception:
        logger.debug(
            "orchestrator: could not resolve customer context for conversation %s; "
            "proceeding without a customer profile",
            conversation_id,
        )

    system_prompt = _build_system_prompt(persona, customer_context)

    # Fetch persona messages for the inbox (fail-open: None on any error).
    # Only handoff_message is consumed here; welcome_message is consumed at
    # conversation-created time by lifecycle.py.
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
        message_list = await _fetch_messages(conversation_id)
    except httpx.HTTPError:
        # Runs as (part of) a background task -- a transient Chatwoot failure
        # must be logged, not die as "Task exception was never retrieved".
        logger.exception(
            "orchestrator: failed to fetch messages for conversation %s, skipping",
            conversation_id,
        )
        return

    # Brain-swap: route the decision through the backend's full ADK agent
    # (answers KB/spec questions, hands off only on genuine intent). The legacy
    # gemini.decide router below is bypassed entirely when this flag is on.
    if settings.chat_agent_enabled:
        await _process_via_chat_agent(
            conversation_id, message_list, effective_mode, chatwoot, handoff_message,
            inbox_id=inbox_id,
        )
        return

    context = _build_context(message_list)

    decision = await gemini.decide(system_prompt, context)
    await _log_decision(conversation_id, decision)

    # KB-grounded reply: for a plain answer, source the text from the backend
    # copilot (same KB + assistant as the website) instead of the local draft.
    # Router (send_reply vs escalate/handoff) is unchanged; fail-open to draft.
    # If nothing is grounded but a KB document is still indexing, say so
    # instead of falling through to Gemini's own ungrounded guess (WA-4).
    if (
        settings.kb_grounded_replies
        and decision.action == "send_reply"
        and proton is not None
        and inbox_id is not None
    ):
        thread = _build_thread(message_list)
        if thread:
            answer = await proton.copilot_answer(
                f"chatwoot-conv-{conversation_id}", thread, inbox_id
            )
            if answer:
                decision.args["text"] = answer
            elif await proton.has_pending_kb_documents():
                decision.args["text"] = PENDING_KB_FALLBACK_MESSAGE

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


def _latest_incoming_messages(message_list: list[dict], greeting_text: str = "") -> list[dict]:
    """The trailing run of incoming (message_type=0) customer message dicts, in
    chronological order, since the last non-private outgoing message. Shared by
    `_latest_incoming_text` (which joins their text) and `_process_via_chat_agent`
    (which also scans them for attachments) so the greeting/lifecycle-skip rules
    that decide where the turn boundary sits live in exactly one place.

    Unlike `_latest_incoming_text`, messages with empty `content` (e.g. a voice
    note or photo with no caption) are kept — there's nothing to join into text,
    but there may still be an attachment worth inspecting.

    Two kinds of outgoing message are NOT the bot's reply and must not bound the
    turn: our own lifecycle notices (marked content_attributes.proton_lifecycle)
    and Chatwoot's native channel greeting (content == the inbox greeting_text)."""
    greeting = (greeting_text or "").strip()
    messages: list[dict] = []
    for message in reversed(message_list):
        if message.get("private"):
            continue
        ca = message.get("content_attributes")
        if isinstance(ca, dict) and ca.get("proton_lifecycle"):
            continue
        content = (message.get("content") or "").strip()
        mtype = message.get("message_type")
        if mtype == 1 and greeting and content == greeting:
            continue  # native channel greeting — not a real bot reply
        if mtype == 0:  # incoming (customer)
            messages.append(message)
        elif mtype == 1:  # outgoing (bot/agent) → older than the last reply
            break
    return list(reversed(messages))


def _latest_incoming_text(message_list: list[dict], greeting_text: str = "") -> str:
    """The trailing run of incoming customer messages (since the last non-private
    outgoing message), joined into one turn for the backend agent. The backend
    owns the rest of the multi-turn history keyed by the crm- session id, so we
    only send what the customer has said since the bot last spoke."""
    texts = [
        (message.get("content") or "").strip()
        for message in _latest_incoming_messages(message_list, greeting_text)
    ]
    return "\n".join(t for t in texts if t)


async def _log_chat_action(conversation_id: int, decision: str, output: str) -> None:
    """Audit row for a /chat/turn decision (mirrors _log_decision; a DB blip
    must never abort the turn)."""
    settings = get_settings()
    try:
        async with async_session_maker() as session:
            session.add(
                AiAction(
                    conversation_ref=f"chatwoot:{conversation_id}",
                    decision=decision,
                    model=settings.gemini_model,
                    # This path drives the backend's ADK agent via /chat/turn,
                    # not app.ai.gemini.decide() -- ProtonConfigClient.chat_turn's
                    # response has no usage_metadata to report, so all three
                    # token fields are genuinely unknown here, explicitly None
                    # (not 0) same as prompt_tokens above.
                    prompt_tokens=None,
                    output_tokens=None,
                    cached_tokens=None,
                    output=output[:2000],
                )
            )
            await session.commit()
    except SQLAlchemyError:
        logger.exception(
            "orchestrator: failed to log chat-agent ai_actions row for conversation %s",
            conversation_id,
        )


# Chatwoot channel types that deliver over WhatsApp (Twilio-WhatsApp is modelled
# as Channel::TwilioSms with medium=whatsapp; some tenants use Channel::Whatsapp).
_WHATSAPP_CHANNELS = {"Channel::TwilioSms", "Channel::Whatsapp"}


async def _format_reply_for_channel(
    reply: str, chatwoot, inbox_id: int | None
) -> list[str]:
    """Channel-aware outbound formatting (mirrors the standalone WhatsApp send
    path). WhatsApp can't render Markdown and Twilio rejects a body over 1600
    chars outright, so for a WhatsApp/Twilio inbox convert Markdown -> WhatsApp
    formatting and split into in-limit chunks. Every other channel (web widget,
    email) keeps the raw reply as a single message. Fail-open: if the channel
    can't be resolved, send the reply as-is."""
    channel = None
    if inbox_id is not None:
        try:
            inbox = await chatwoot.get_inbox(inbox_id)
            channel = (inbox or {}).get("channel_type")
        except Exception:
            logger.debug(
                "orchestrator: could not resolve channel for inbox %s; sending raw",
                inbox_id,
            )
    if channel in _WHATSAPP_CHANNELS:
        return whatsapp_format.chunk_whatsapp(whatsapp_format.md_to_whatsapp(reply))
    return [reply]


# Chatwoot attachment `file_type`s the chat-agent path understands. One of
# each per turn (YAGNI); a second attachment of the same kind is ignored.
#
# This is deliberately NARROWER than `media_registry.REGISTRY`, which also
# covers documents: `/chat/turn`'s request contract has exactly three media
# slots (audio_base64/image_base64/video_base64), so a PDF has nowhere to go
# on this path even though the registry can classify one. Widening this tuple
# without widening that contract would silently drop the extra kind. The
# agent-facing assist path has no such limit and does handle documents.
_MEDIA_KINDS = ("audio", "image", "video")


def _encoded(fetched: tuple[bytes, str] | None) -> tuple[str | None, str | None]:
    """(bytes, mime) -> (base64 str, mime) for the chat_turn payload."""
    if fetched is None:
        return None, None
    data, mime = fetched
    return base64.b64encode(data).decode(), mime


def _apply_media_budget(
    conversation_id: int, media: dict[str, tuple[bytes, str]], max_bytes: int
) -> None:
    """Drop media, in place, until the turn fits Gemini's inline request limit.

    The cap is a budget for the WHOLE turn, not per attachment: a 14 MB video
    plus a 5 MB photo is ~25 MB once google-genai base64-encodes it into the
    JSON body, so guarding each attachment on its own still produces a request
    Gemini rejects. An oversized single video is dropped first (its own
    dedicated log line), then whole attachments are dropped in
    the registry's drop priority (video first — biggest, and least often the
    whole message; audio last — a voice note usually IS the message) until the
    total fits. Never raises and never truncates a payload mid-stream: the turn
    proceeds on whatever media survives, text only if nothing does.
    """
    oversized_video = media.get("video")
    if oversized_video is not None and len(oversized_video[0]) > max_bytes:
        logger.warning(
            "orchestrator_video_too_large: conversation %s video %d bytes "
            "exceeds whatsapp_video_max_bytes %d; skipping video",
            conversation_id,
            len(oversized_video[0]),
            max_bytes,
        )
        media.pop("video")

    total = sum(len(data) for data, _ in media.values())
    for kind in drop_order(list(media)):
        if total <= max_bytes:
            return
        dropped = media.pop(kind, None)
        if dropped is None:
            continue
        total -= len(dropped[0])
        logger.warning(
            "orchestrator_media_budget_exceeded: conversation %s dropping %s "
            "(%d bytes) to fit whatsapp_video_max_bytes %d; %d bytes of media remain",
            conversation_id,
            kind,
            len(dropped[0]),
            max_bytes,
            total,
        )


async def _process_via_chat_agent(
    conversation_id: int,
    message_list: list[dict],
    effective_mode: str,
    chatwoot,
    handoff_message: str,
    inbox_id: int | None = None,
) -> None:
    """Drive one turn through the backend ADK agent (POST /chat/turn) and map the
    result onto Chatwoot. Chatwoot owns the handoff; the backend only signals it.
    Fail-open: a missing/failed backend degrades to a Chatwoot handoff so the
    conversation is never left silent."""
    greeting_text = ""
    if inbox_id is not None:
        try:
            inbox = await chatwoot.get_inbox(inbox_id)
            if isinstance(inbox, dict) and inbox.get("greeting_enabled"):
                greeting_text = inbox.get("greeting_message") or ""
        except Exception:
            greeting_text = ""  # fail-open: no greeting skip, behave as before

    trailing_messages = _latest_incoming_messages(message_list, greeting_text)
    texts = [(m.get("content") or "").strip() for m in trailing_messages]
    text = "\n".join(t for t in texts if t)

    settings = get_settings()
    # Voice-note / image / video understanding: pull the first audio, image and
    # video attachment (YAGNI — one of each per turn, not every attachment) off
    # the trailing incoming messages and fetch their bytes, but only when the
    # flag is on. `has_attachment` is tracked regardless of the flag purely so
    # an attachment-only message with no caption can be logged instead of
    # silently vanishing (see the empty-text short-circuit below).
    media: dict[str, tuple[bytes, str]] = {}
    has_attachment = False
    if settings.whatsapp_media_understanding_enabled:
        for message in trailing_messages:
            for attachment in message.get("attachments") or []:
                has_attachment = True
                file_type = attachment.get("file_type")
                data_url = attachment.get("data_url")
                if not data_url or file_type not in _MEDIA_KINDS or file_type in media:
                    continue
                fetched = await fetch_attachment_bytes(data_url, file_type_hint=file_type)
                if fetched is not None:
                    media[file_type] = fetched
        _apply_media_budget(conversation_id, media, settings.whatsapp_video_max_bytes)
    else:
        has_attachment = any(m.get("attachments") for m in trailing_messages)

    audio_base64, audio_mime_type = _encoded(media.get("audio"))
    image_base64, image_mime_type = _encoded(media.get("image"))
    video_base64, video_mime_type = _encoded(media.get("video"))

    if not text and audio_base64 is None and image_base64 is None and video_base64 is None:
        if has_attachment:
            # Pre-existing bug: an attachment-only message (voice note/photo,
            # no caption) used to hit this short-circuit and vanish with zero
            # logging. Always log the drop, flag on or off — when the flag is
            # off this is expected (attachments aren't understood yet); when
            # the flag is on it means every attachment on the turn failed to
            # fetch (see `fetch_attachment_bytes`'s own warning log for why).
            logger.warning(
                "orchestrator_attachment_only_message_dropped: conversation %s "
                "has attachment(s) but no usable text or fetched media "
                "(media_understanding_enabled=%s)",
                conversation_id,
                settings.whatsapp_media_understanding_enabled,
            )
        return

    proton = get_proton_config_client()
    result = (
        await proton.chat_turn(
            f"crm-{conversation_id}",
            text,
            inbox_id,
            audio_base64=audio_base64,
            audio_mime_type=audio_mime_type,
            image_base64=image_base64,
            image_mime_type=image_mime_type,
            video_base64=video_base64,
            video_mime_type=video_mime_type,
        )
        if proton is not None
        else None
    )

    if result is None:
        # Backend unconfigured/unreachable/error → hand off rather than go silent.
        await _log_chat_action(conversation_id, "chat_turn:unavailable", "backend unavailable")
        await _handoff_to_human_via_chatwoot(conversation_id, chatwoot, handoff_message)
        return

    if result.get("forwarded_to_agent"):
        # Already handed off; the human owns this conversation now. Nothing to do.
        await _log_chat_action(conversation_id, "chat_turn:forwarded", "")
        return

    handoff = result.get("handoff")
    if handoff:
        reason = (handoff or {}).get("reason", "") if isinstance(handoff, dict) else ""
        await _log_chat_action(conversation_id, "chat_turn:handoff", str(reason))
        await _handoff_to_human_via_chatwoot(conversation_id, chatwoot, handoff_message)
        return

    reply = (result.get("reply") or "").strip()
    if not reply:
        # No reply and no handoff (e.g. empty generation) → fail-open handoff.
        await _log_chat_action(conversation_id, "chat_turn:empty", "")
        await _handoff_to_human_via_chatwoot(conversation_id, chatwoot, handoff_message)
        return

    await _log_chat_action(conversation_id, "chat_turn:reply", reply)
    if effective_mode == "auto":
        # Format + split per channel: WhatsApp gets Markdown->WhatsApp conversion
        # and 1600-char chunking (else Twilio rejects the whole message); web/
        # email keep the raw reply.
        for chunk in await _format_reply_for_channel(reply, chatwoot, inbox_id):
            await chatwoot.create_message(
                conversation_id,
                chunk,
                private=False,
                token_override=settings.chatwoot_bot_token,
            )
        # Stays pending — auto-sent replies don't hand off to a human.
    else:
        await chatwoot.create_message(
            conversation_id,
            f"🤖 Suggested reply:\n\n{reply}",
            private=True,
        )
        await chatwoot.toggle_status(conversation_id, "open")


async def _handoff_to_human_via_chatwoot(
    conversation_id, chatwoot, handoff_message: str
) -> None:
    """Acknowledge the customer (best-effort) then reopen the conversation for
    a human. Used for `handoff_to_human` and, on Chatwoot-only tenants, for
    `escalate_to_ticket` too. The acknowledgment post is wrapped so a failed
    post never prevents the reopen: a handoff must ALWAYS leave the
    conversation visible to a human, never silently stuck in bot-pending. The
    text is the assistant's persona `handoff_message`, falling back to the
    tenant `handoff_default_message` (empty → nothing posted, reopen only)."""
    text = handoff_message or get_settings().handoff_default_message
    if text:
        try:
            await chatwoot.create_message(conversation_id, text, private=False)
        except httpx.HTTPError:
            logger.exception(
                "orchestrator: failed to post handoff acknowledgment for "
                "conversation %s; reopening for a human anyway",
                conversation_id,
            )
    await chatwoot.toggle_status(conversation_id, "open")
    proton = get_proton_config_client()
    if proton is not None:
        await proton.assign_agent(conversation_id)


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
        # No external ticketing backend: escalation always becomes a
        # Chatwoot handoff — acknowledge the customer and reopen for a human
        # agent.
        await _handoff_to_human_via_chatwoot(
            conversation_id, chatwoot, handoff_message
        )
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
        await _handoff_to_human_via_chatwoot(conversation_id, chatwoot, handoff_message)
