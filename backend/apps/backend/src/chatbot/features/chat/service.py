from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal

import structlog
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService, Session
from google.genai import Client, types

from chatbot.features.chat.adapters.bigquery_metrics import NoOpMetrics
from chatbot.features.chat.adapters.blob_free_session_service import BlobFreeSessionService
from chatbot.features.chat.adapters.firestore_session_service import FirestoreSessionService
from chatbot.features.chat.adapters.noop_conversation_log import NoOpConversationLog
from chatbot.features.chat.agents import build_ai_agent, build_summarizer_agent
from chatbot.features.chat.chat_persona import compose_chat_agent_instruction
from chatbot.features.chat.csat import record_csat_on_ticket
from chatbot.features.chat.detection import should_open_ticket
from chatbot.features.chat.handoff_bridge import HandoffBridge
from chatbot.features.chat.inbox_resolver import effective_assignment
from chatbot.features.chat.models import (
    HandoffOpenPayload,
    HandoffPayload,
    Message,
    ProductCard,
    Sentiment,
    TurnResult,
)
from chatbot.features.chat.nps import record_nps_on_ticket
from chatbot.features.chat.ports import (
    ChatPort,
    ConversationLogPort,
    ConversationLogResult,
    HumanAgentBridgePort,
    KnowledgePort,
    MetricsPort,
    TextToSpeechPort,
    TicketingPort,
)
from chatbot.features.chat.prompts import AGENT_INSTRUCTION, build_agent_instruction
from chatbot.features.metrics.events import build_turn_event
from chatbot.features.metrics.mapping import CATEGORY_TO_DIVISION

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

WHATSAPP_ACTIVE = "active"
WHATSAPP_PAUSED = "paused"
WHATSAPP_AWAITING_SURVEY = "awaiting_survey"
_HANDOFF_STATE_KEY = "whatsapp_handoff_state"


def _cards_from_state(raw: list[dict[str, Any]]) -> list[ProductCard]:
    """Map raw product_carousel state dicts to ProductCard dataclass instances."""
    cards: list[ProductCard] = []
    for item in raw:
        cards.append(
            ProductCard(
                title=str(item.get("title", "")),
                description=str(item.get("description", "")),
                image_url=item.get("image_url"),
                price=item.get("price"),
                url=item.get("url"),
            )
        )
    return cards


def _part_kind(part: Any) -> str:
    """Classify an ADK content Part for diagnostics (text / function_call / ...)."""
    if getattr(part, "text", None):
        return "text"
    if getattr(part, "function_call", None):
        return "function_call"
    if getattr(part, "function_response", None):
        return "function_response"
    if getattr(part, "inline_data", None):
        return "inline_data"
    return "other"


# Shown when the agent returns no text twice in a row (intermittent Gemini empty
# generation) and the turn is not a deliberate handoff — never leave the user with
# a blank reply / the frontend's "(no reply…)" placeholder.
_EMPTY_REPLY_FALLBACK = (
    "Maaf, saya tidak dapat memproses balasan tadi. Boleh anda ulang semula? "
    "(Sorry, I couldn't process that — could you please repeat?)"
)

# Shown when the ADK execution loop raises (Gemini/transport error). Kept as a
# module constant so the metrics layer can classify the turn as a fallback.
_TECH_ERROR_FALLBACK = "Maaf, terjadi kendala teknis. Mohon coba beberapa saat lagi."

# A turn counts as a fallback when the bot produced one of these canned replies
# (no real answer) rather than a genuine response.
_FALLBACK_REPLIES = frozenset({_EMPTY_REPLY_FALLBACK, _TECH_ERROR_FALLBACK})

# P7 task 1: the four sentiment levels a turn may resolve to once the
# classifier is enabled. Matches models.Sentiment verbatim -- kept as its own
# tuple (rather than typing.get_args(Sentiment)) so this stays a plain runtime
# membership check with no typing-introspection surprises.
_VALID_SENTIMENTS: tuple[Sentiment, ...] = ("positive", "neutral", "negative", "urgent")

# P7 task 11a: how long a classified sentiment may still colour the bot's tone.
#
# session_state["sentiment"] persists for the life of the conversation (and,
# with the Firestore session store, across process restarts), so without a
# freshness window a customer who was furious this morning would be answered
# apologetically this afternoon when they write back to say thanks. Fifteen
# minutes is roughly a live-chat sitting: long enough that consecutive turns of
# one exchange keep the register the customer's mood established, short enough
# that a resumed conversation starts from today's neutral wording again.
# Deliberately a module constant, not a setting -- it is a property of how
# conversations work, not something a tenant should tune.
_TONE_SENTIMENT_TTL_SECONDS = 900.0
# Tolerated clock skew for a stamp that appears to be in the future (two
# processes, or a Firestore session written by a differently-skewed instance).
# A stamp further ahead than this is treated as unusable, not as fresh.
_TONE_SENTIMENT_FUTURE_SKEW_SECONDS = 60.0


def _sentiment_is_fresh(raw_stamp: Any) -> bool:
    """Whether `session_state["sentiment_at"]` is recent enough to act on.

    A missing, unparseable, or absent stamp is NOT fresh: a sentiment with no
    evidence of when it was classified degrades to today's neutral wording
    rather than being applied optimistically. (Sessions that predate this
    change carry `sentiment` without a stamp -- they must not resurrect an old
    mood on their next turn.)
    """
    if not raw_stamp:
        return False
    try:
        stamped = datetime.fromisoformat(str(raw_stamp))
    except (TypeError, ValueError):
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - stamped).total_seconds()
    return -_TONE_SENTIMENT_FUTURE_SKEW_SECONDS <= age_seconds <= _TONE_SENTIMENT_TTL_SECONDS


def _media_kinds_in_user_content(ctx: Any) -> tuple[bool, bool]:
    """(has_image, has_video) for the turn this ADK invocation is serving.

    Read off `ReadonlyContext.user_content` -- the very `types.Content`
    `handle_turn` assembled and handed to the runner -- so the media signal
    cannot drift from what the model was actually sent, and cannot leak into a
    later turn the way a mutable per-session flag could.

    AUDIO IS DELIBERATELY NOT MEDIA HERE: a voice note gives the model nothing
    to look at, so the diagnosis instruction ("describe the specific thing you
    observe") would be asking for a description of something that doesn't
    exist. Only image/* and video/* count.

    Fail-open to (False, False) -- i.e. today's instruction -- for any ctx
    shape that doesn't expose readable parts.
    """
    has_image = False
    has_video = False
    try:
        parts = getattr(getattr(ctx, "user_content", None), "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                continue
            mime = str(getattr(inline, "mime_type", "") or "")
            if mime.startswith("image/"):
                has_image = True
            elif mime.startswith("video/"):
                has_video = True
    except Exception:
        return False, False
    return has_image, has_video


class OrchestratorService:
    """Core orchestrator driving conversational turns for both Chatbot and Voicebot."""

    def __init__(
        self,
        settings: Settings,
        chat_port: ChatPort,
        ticketing_port: TicketingPort,
        knowledge_port: KnowledgePort,
        tts_port: TextToSpeechPort,
        human_agent_bridge: HumanAgentBridgePort | None = None,
        handoff_bridge: HandoffBridge | None = None,
        conversation_log_port: ConversationLogPort | None = None,
        runner_factory: Callable[[Any], Any] | None = None,
        metrics_port: MetricsPort | None = None,
        assignment_store: Any | None = None,
        assistants_store: Any | None = None,
        tenant_settings_store: Any | None = None,
    ) -> None:
        self._settings = settings
        self._chat_port = chat_port
        self._ticketing_port = ticketing_port
        self._knowledge_port = knowledge_port
        self._tts_port = tts_port
        self._human_agent_bridge = human_agent_bridge
        self._handoff_bridge = handoff_bridge
        self._conversation_log_port: ConversationLogPort = (
            conversation_log_port or NoOpConversationLog()
        )
        self._metrics: MetricsPort = metrics_port or NoOpMetrics()
        self._assignment_store = assignment_store
        self._assistants_store = assistants_store
        self._tenant_settings_store = tenant_settings_store

        # Per-session override map: session_id -> instruction string.
        # Empty by default → every session gets AGENT_INSTRUCTION (no behaviour
        # change until a caller registers a persona via this dict).
        # This holds the PERSONA-ONLY composition — exactly the string that
        # shipped before P7 — and is what the per-turn composer falls back to
        # if anything goes wrong, so a failure can only ever cost a tenant the
        # new per-turn sections, never today's wording.
        self._instruction_by_session: dict[str, str] = {}

        # session_id -> the assistant resolved for the most recent turn (or
        # None). The per-turn instruction composer needs the resolved persona
        # object itself, not just the string built from it, to read the
        # operator's tone_* / media_diagnosis_instruction overrides while the
        # model is running. Re-resolved every turn by _register_chat_persona,
        # so an operator's edit takes effect on the next message.
        self._assistant_by_session: dict[str, Any] = {}

        # Initialize ADK agents
        self._support_agent = build_ai_agent(
            settings,
            ticketing_port,
            knowledge_port,
            instruction_provider=self._chat_instruction_provider,
        )
        self._summarizer_agent = build_summarizer_agent(settings)

        # Initialize raw GenAI client for transcription/STT
        if settings.google_genai_use_vertexai:
            self._genai_client = Client(
                vertexai=True,
                project=settings.vertex_project_id,
                location=settings.vertex_location,
            )
        else:
            self._genai_client = Client()

        # ADK runner session storage
        self._adk_sessions: BaseSessionService
        if settings.session_store == "firestore":
            self._adk_sessions = FirestoreSessionService(settings)
        else:
            self._adk_sessions = InMemorySessionService()  # type: ignore[no-untyped-call]
        self._runner_factory = runner_factory or self._default_runner_factory

        # Shared conversation history dictionary (session_id -> list of Messages)
        self._history: dict[str, list[Message]] = {}

        # Per-session user-turn counter — incremented each time handle_turn appends a
        # user message. Maintained separately from self._history because
        # InMemorySessionService.get_session uses deepcopy, which means
        # _sync_history_from_state resets self._history on every turn; this counter
        # accumulates reliably for the lifetime of the service instance.
        self._user_turn_counts: dict[str, int] = {}

    def _default_runner_factory(self, agent: Any) -> Runner:
        # The runner gets the session store wrapped so inline audio/image/video
        # blobs reach Gemini on the turn that carried them but are never
        # written to (nor replayed from) the stored session — see
        # adapters/blob_free_session_service.py. A fresh wrapper per run keeps
        # the full-event map bounded to one invocation. Everything else in this
        # class keeps talking to self._adk_sessions directly.
        return Runner(
            agent=agent,
            app_name="chatbot",
            session_service=BlobFreeSessionService(self._adk_sessions),
        )

    def _chat_instruction_provider(self, ctx: Any) -> str:
        """ADK InstructionProvider: the instruction for THIS LLM request.

        Reads the session id from the ReadonlyContext via ``ctx.session.id``
        (the public property exposed by google.adk.agents.ReadonlyContext).
        Any exception reading the session id → fail-open, return AGENT_INSTRUCTION.

        This is called by ADK before EVERY LLM request, not once per turn —
        which is what makes the per-turn sections in _compose_turn_instruction
        able to see state the current turn produced. Any failure in that
        composition degrades to the persona-only instruction registered for
        the session (today's wording), never to an exception on the customer's
        turn and never to a half-composed prompt.
        """
        try:
            session_id = ctx.session.id
        except Exception:
            return AGENT_INSTRUCTION
        registered = self._instruction_by_session.get(session_id, AGENT_INSTRUCTION)
        try:
            return self._compose_turn_instruction(session_id, registered, ctx)
        except Exception as e:
            _log.warning(
                "chat_turn_instruction_composition_failed",
                session_id=session_id,
                error=str(e),
            )
            return registered

    def _compose_turn_instruction(self, session_id: str, registered: str, ctx: Any) -> str:
        """Layer P7's per-request sections onto the persona-only instruction.

        Both flags off → returns ``registered`` untouched, so a tenant that
        has opted into neither feature gets the byte-identical pre-P7 string
        and pays one dict lookup for it.

        MEDIA (task 8): `_media_kinds_in_user_content` derives has_image /
        has_video from this invocation's own user content, so the diagnosis
        instruction appears on exactly the turns that carry a photo or video.

        TONE (task 2), and what the customer actually experiences:

        The turn's sentiment is produced BY the turn's `classify_ticket_tool`
        call, so no once-per-session registration could ever reflect it. It
        works here because a turn is one agent run made of several LLM
        requests: the model calls its tools (request 1), ADK re-resolves this
        provider, and the request that writes the customer-facing reply sees
        the sentiment the tool just recorded. **On the customer's first angry
        message the reply is therefore already in the measured/apologetic
        register — provided the model called `classify_ticket_tool` on that
        turn, which AGENT_INSTRUCTION mandates for negative tone and before
        any escalation.** If the model answers an angry message with no tool
        call at all, that single reply keeps today's wording and the adjusted
        register arrives on the following turn (the sentiment is carried in
        session state, bounded by _TONE_SENTIMENT_TTL_SECONDS). There is no
        second Gemini round-trip either way: re-composing a string is free.

        Tone requires `sentiment_classifier_enabled` as well as
        `sentiment_tone_adjustment_enabled` — with no classifier nothing ever
        writes a sentiment, so the tone flag alone would silently pin every
        conversation to the "neutral" slot and change the register of tenants
        who set a `tone_neutral` override without ever opting into sentiment.

        Fail-open detail worth knowing: when the assistant could not be
        resolved (no inbox, no store, or a tenant-store outage — all of which
        `_resolve_chat_assistant` collapses to None), the built-in per-sentiment
        and media default wordings apply. The operator's own custom text is
        absent for that turn, but the block is never empty and the turn never
        raises.
        """
        media_enabled = self._settings.media_diagnosis_prompt_enabled
        tone_enabled = (
            self._settings.sentiment_classifier_enabled
            and self._settings.sentiment_tone_adjustment_enabled
        )
        if not media_enabled and not tone_enabled:
            return registered
        assistant = self._assistant_by_session.get(session_id)
        has_image, has_video = _media_kinds_in_user_content(ctx)
        base = build_agent_instruction(
            media_diagnosis_prompt_enabled=media_enabled,
            has_image=has_image,
            has_video=has_video,
            assistant=assistant,
        )
        return compose_chat_agent_instruction(
            base,
            assistant,
            sentiment=self._tone_sentiment(ctx) if tone_enabled else None,
            tone_adjustment_enabled=tone_enabled,
        )

    def _tone_sentiment(self, ctx: Any) -> Sentiment:
        """The sentiment tone selection may act on for this LLM request.

        Never `None`: an absent, unrecognised or stale value resolves to
        "neutral", whose default body reproduces today's "## Tone" paragraph
        verbatim. `None` would read as "we looked and it was fine" (task 1's
        reasoning) and, worse here, an empty tone block would silently change
        the bot's register with nothing to show something went wrong.

        Staleness is the reason the freshness stamp exists — see
        `_sentiment_is_fresh` and `_TONE_SENTIMENT_TTL_SECONDS`. Note this is
        deliberately NOT `_resolve_sentiment`: that one reports what the turn
        classified (for the API response and the Chatwoot attribute) and must
        not be time-bounded, this one decides how to speak right now and must
        be.
        """
        raw: Any = None
        stamp: Any = None
        state = getattr(ctx, "state", None)
        if state is not None:
            raw = state.get("sentiment")
            stamp = state.get("sentiment_at")
        if raw not in _VALID_SENTIMENTS or not _sentiment_is_fresh(stamp):
            return "neutral"
        return raw  # type: ignore[no-any-return]  # narrowed by the membership check above

    async def _resolve_chat_assistant(self, inbox_id: int | None) -> Any:
        """Resolve the assistant for a given inbox_id.

        Fail-open: inbox_id is None, no assistants_store, unresolvable, or any
        exception → None. Reuses effective_assignment exactly as the copilot does.
        """
        if inbox_id is None or self._assistants_store is None:
            return None
        try:
            eff = await effective_assignment(
                self._assignment_store,
                self._assistants_store,
                self._tenant_settings_store,
                self._settings,
                inbox_id,
            )
            assistant_id = eff.get("assistant_id") if eff else None
            if assistant_id:
                return await self._assistants_store.get(assistant_id)
            return await self._assistants_store.get_default()
        except Exception:
            return None

    async def _register_chat_persona(self, session_id: str, inbox_id: int | None) -> None:
        """Resolve + register the operator persona for this session.

        Composes AGENT_INSTRUCTION with the assistant persona. If the result
        differs from the base (i.e. there is a real persona), stores it in
        _instruction_by_session so _chat_instruction_provider picks it up.
        Pops the key (restores default) if the persona is empty or any error
        occurs. Fail-open: never raises.

        Also parks the resolved assistant OBJECT for the turn
        (_assistant_by_session), because P7's per-request composer needs to
        read the operator's tone_*/media_diagnosis_instruction overrides off it
        while the model is running — long after this coroutine returned. The
        string alone can't carry those: which tone slot applies isn't known
        until the turn's sentiment is classified. This resolve stays exactly
        one tenant-store read per turn, as before.
        """
        try:
            assistant = await self._resolve_chat_assistant(inbox_id)
            self._assistant_by_session[session_id] = assistant
            composed = compose_chat_agent_instruction(AGENT_INSTRUCTION, assistant)
            if composed != AGENT_INSTRUCTION:
                self._instruction_by_session[session_id] = composed
            else:
                self._instruction_by_session.pop(session_id, None)
        except Exception:
            self._assistant_by_session.pop(session_id, None)
            self._instruction_by_session.pop(session_id, None)

    def _sync_history_from_state(self, session_id: str, session: Session) -> list[dict[str, Any]]:
        state_history = session.state.setdefault("chat_history", [])
        self._history[session_id] = [
            Message(
                role=m["role"],
                text=m["text"],
                timestamp=datetime.fromisoformat(m["timestamp"])
                if "timestamp" in m
                else datetime.now(UTC),
            )
            for m in state_history
        ]
        return state_history

    async def _get_or_create_session(self, session_id: str) -> tuple[Session, list[dict[str, Any]]]:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if not session:
            session = await self._adk_sessions.create_session(
                app_name="chatbot",
                user_id=session_id,
                session_id=session_id,
                state={
                    "session_id": session_id,
                    "handoff_triggered": False,
                    "handoff_reason": "",
                    "chat_history": [],
                },
            )
        state_history = self._sync_history_from_state(session_id, session)
        return session, state_history

    async def _append_history_message(
        self,
        session_id: str,
        session: Session,
        state_history: list[dict[str, Any]],
        role: Literal["user", "assistant", "system"],
        text: str,
    ) -> None:
        msg = Message(role=role, text=text, timestamp=datetime.now(UTC))
        self._history.setdefault(session_id, []).append(msg)
        state_history.append(
            {
                "role": role,
                "text": text,
                "timestamp": msg.timestamp.isoformat(),
            }
        )
        sessions_service = self._adk_sessions
        if isinstance(sessions_service, FirestoreSessionService):

            def _write_state() -> None:
                sessions_service._collection().document(session.id).set(
                    session.model_dump(mode="json")
                )

            await asyncio.to_thread(_write_state)

    async def _run_support_agent(
        self, session_id: str, new_message: types.Content
    ) -> tuple[str, list[str], bool]:
        """Run the support agent, retrying once on a transient run failure.

        The ADK run can raise an intermittent connection error (e.g. the Gemini
        endpoint closing the socket: ``RemoteDisconnected``) that would otherwise
        send the customer the generic error fallback. Retry the whole run once
        before letting the exception propagate to the caller's fallback handler.
        Returns ``(reply_text, final_part_kinds, final_event_seen)``.
        """
        try:
            return await self._invoke_support_agent(session_id, new_message)
        except Exception as e:
            _log.warning(
                "adk_execution_transient_error_retrying",
                session_id=session_id,
                error=str(e),
                attempt=1,
            )
            return await self._invoke_support_agent(session_id, new_message)

    async def _invoke_support_agent(
        self, session_id: str, new_message: types.Content
    ) -> tuple[str, list[str], bool]:
        """Run the support agent once and extract reply text from ALL final parts.

        Gemini can place the reply after a function-call part (so reading
        ``parts[0]`` alone drops it) or, intermittently, return a final response
        with no text part at all — callers retry/fall back on an empty
        ``reply_text``.
        """
        runner = self._runner_factory(self._support_agent)
        reply_text = ""
        final_part_kinds: list[str] = []
        final_event_seen = False
        async for event in runner.run_async(
            user_id=session_id, session_id=session_id, new_message=new_message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_event_seen = True
                final_part_kinds = [_part_kind(p) for p in event.content.parts]
                texts = [p.text for p in event.content.parts if p.text]
                if texts:
                    reply_text = "".join(texts)
        return reply_text, final_part_kinds, final_event_seen

    async def _handoff_triggered(self, session_id: str) -> bool:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        return bool(session and session.state.get("handoff_triggered") is True)

    async def _clear_session_key(self, session_id: str, key: str) -> None:
        """Remove a one-shot key from persisted session state (e.g. product_carousel).

        Re-fetches so it doesn't clobber writes made earlier in the turn. Persists
        for both the Firestore store (production) and the in-memory store (which
        hands back copies, so the backing object must be mutated directly).
        """
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if not session or key not in session.state:
            return
        session.state.pop(key, None)
        if isinstance(self._adk_sessions, FirestoreSessionService):
            sessions_service = self._adk_sessions

            def _write() -> None:
                sessions_service._collection().document(session.id).set(
                    session.model_dump(mode="json")
                )

            await asyncio.to_thread(_write)
        else:
            # In-memory store: get_session returns a copy, so mutate the backing
            # object directly (best-effort; no-op if the layout differs).
            store = getattr(self._adk_sessions, "sessions", None)
            if isinstance(store, dict):
                stored = store.get("chatbot", {}).get(session_id, {}).get(session_id)
                if stored is not None:
                    stored.state.pop(key, None)

    async def _emit_turn_metrics(
        self,
        session_id: str,
        t0: float,
        *,
        bot_reply: str | None,
        handed_off: bool,
    ) -> None:
        """Best-effort: emit one turn event. Never raises into the turn."""
        try:
            turn_count = self._user_turn_counts.get(session_id, 1)
            event = build_turn_event(
                session_id=session_id,
                occurred_at=datetime.now(UTC),
                latency_ms=int((perf_counter() - t0) * 1000),
                turn_count=turn_count,
                is_fallback=bot_reply in _FALLBACK_REPLIES,
                handed_off=handed_off,
            )
            await self._metrics.emit_turn(event)
        except Exception as e:  # instrumentation must never break the turn
            _log.error("emit_turn_metrics_failed", session_id=session_id, error=str(e))

    def _resolve_sentiment(self, session_state: dict[str, Any]) -> Sentiment | None:
        """Resolve the turn's reportable sentiment from raw session state.

        Off (`sentiment_classifier_enabled=False`): always `None`, regardless
        of what (if anything) happens to be in `session_state["sentiment"]` --
        matches pre-P7 behaviour byte-for-byte, since nothing ever wrote this
        key before this package.

        On: one of the four valid levels passes through unchanged. Anything
        else -- the key is absent (the model omitted the tool argument),
        `None`, or an unrecognised value -- falls back to "neutral", never
        `None`. `None` used to mean "never classified", which reads
        identically to "we looked and it was fine"; once a classifier exists
        that reading is no longer honest, and "neutral" is the safe
        interpretation because it never trips the `detection.py` escalation
        gate the way a stale/garbage value could if left unrecognised there
        instead of normalised here.
        """
        if not self._settings.sentiment_classifier_enabled:
            return None
        raw = session_state.get("sentiment")
        if raw in _VALID_SENTIMENTS:
            return raw  # type: ignore[no-any-return]  # narrowed by the membership check above
        return "neutral"

    async def handle_turn(
        self,
        session_id: str,
        text: str,
        inbox_id: int | None = None,
        audio_base64: str | None = None,
        audio_mime_type: str | None = None,
        image_base64: str | None = None,
        image_mime_type: str | None = None,
        video_base64: str | None = None,
        video_mime_type: str | None = None,
    ) -> TurnResult:
        """Process a single text-based chatbot turn."""
        _log.info("processing_chatbot_turn", session_id=session_id, text_length=len(text))
        t0 = perf_counter()

        # 0. If the session is already handed off to a human agent and we have
        #    a live bridge, relay this turn straight to Sunshine Conversations
        #    and return — the agent's reply arrives async over /chat/stream.
        if self._handoff_bridge is not None and self._human_agent_bridge is not None:
            conv_id = await self._handoff_bridge.conversation_id_for(session_id)
            if conv_id is not None:
                # Load or create session to write history
                session, state_history = await self._get_or_create_session(session_id)
                await self._append_history_message(session_id, session, state_history, "user", text)

                try:
                    await self._human_agent_bridge.forward_customer_message(
                        conversation_id=conv_id,
                        user_external_id=session_id,
                        text=text,
                    )
                    # Automatically save user messages during active handoff
                    await self._handoff_bridge.save_message(session_id, "user", text)
                    return TurnResult(reply=None, forwarded_to_agent=True)
                except Exception as e:
                    _log.error(
                        "forward_customer_message_failed",
                        session_id=session_id,
                        error=str(e),
                    )
                    return TurnResult(
                        reply=(
                            "Sorry, we couldn't deliver that to the agent. "
                            "Please try again in a moment."
                        ),
                    )

        # 1. Short-circuit if AI is paused for this session (human has taken over
        #    but no live bridge is configured — fall back to ticket-only mode).
        if await self._ticketing_port.is_ai_paused(session_id):
            _log.info("ai_paused_short_circuiting_turn", session_id=session_id)
            return TurnResult(reply=None)

        # Retrieve or create the ADK session state so we have the persistent context
        session, state_history = await self._get_or_create_session(session_id)

        # 2. Append user message to history
        await self._append_history_message(session_id, session, state_history, "user", text)
        self._user_turn_counts[session_id] = self._user_turn_counts.get(session_id, 0) + 1

        # 4. Formulate the GenAI content structure
        parts: list[types.Part] = [types.Part.from_text(text=text)]
        if audio_base64 and audio_mime_type:
            try:
                parts.append(
                    types.Part.from_bytes(
                        data=base64.b64decode(audio_base64), mime_type=audio_mime_type
                    )
                )
            except Exception:
                _log.warning("handle_turn_audio_decode_failed", session_id=session_id)
        if image_base64 and image_mime_type:
            try:
                parts.append(
                    types.Part.from_bytes(
                        data=base64.b64decode(image_base64), mime_type=image_mime_type
                    )
                )
            except Exception:
                _log.warning("handle_turn_image_decode_failed", session_id=session_id)
        if video_base64 and video_mime_type:
            try:
                parts.append(
                    types.Part.from_bytes(
                        data=base64.b64decode(video_base64), mime_type=video_mime_type
                    )
                )
            except Exception:
                _log.warning("handle_turn_video_decode_failed", session_id=session_id)
        new_message = types.Content(role="user", parts=parts)

        # Register the operator persona for this session (fail-open: no-op when
        # inbox_id is None or stores are not wired).
        await self._register_chat_persona(session_id, inbox_id)

        # 5. Run the ADK Agent
        reply_text: str | None = ""
        try:
            reply_text, part_kinds, _seen = await self._run_support_agent(session_id, new_message)
            # An empty reply that ISN'T a deliberate handoff is almost always an
            # intermittent Gemini generation miss — retry once, then fall back, so
            # the user never sees a blank turn / the "(no reply…)" placeholder.
            if not reply_text and not await self._handoff_triggered(session_id):
                _log.warning(
                    "chat_turn_empty_reply_text",
                    session_id=session_id,
                    final_part_kinds=part_kinds,
                    attempt=1,
                )
                reply_text, part_kinds, _seen = await self._run_support_agent(
                    session_id, new_message
                )
                if not reply_text and not await self._handoff_triggered(session_id):
                    _log.warning(
                        "chat_turn_empty_reply_after_retry",
                        session_id=session_id,
                        final_part_kinds=part_kinds,
                    )
                    reply_text = _EMPTY_REPLY_FALLBACK
        except Exception as e:
            _log.exception("adk_execution_loop_failed", session_id=session_id, error=str(e))
            await self._emit_turn_metrics(
                session_id, t0, bot_reply=_TECH_ERROR_FALLBACK, handed_off=False
            )
            return TurnResult(reply=_TECH_ERROR_FALLBACK)

        final_session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        session_state = final_session.state if final_session else {}

        handoff_payload = None
        handoff_triggered = session_state.get("handoff_triggered") is True
        if handoff_triggered and not (
            session_id.startswith("whatsapp-")
            or session_id.startswith("email-")
            or session_id.startswith("crm-")
        ):
            reason = session_state.get("handoff_reason", "help_request")

            # Execute human escalation handoff
            handoff_payload = await self._escalate_handoff(session_id, reason)
            reply_text = None  # Clear reply so chatbot doesn't post text when handing off
        elif handoff_triggered and session_id.startswith("crm-"):
            # CRM-owned handoff: the caller (the CRM agent service) owns the
            # actual handoff in its own system (Chatwoot). Signal the reason
            # only — no summarizer, no live bridge, no backend ticket, and do
            # NOT pause AI here (Chatwoot drives the reopen). Suppress the reply.
            handoff_payload = HandoffPayload(
                reason=session_state.get("handoff_reason", "help_request"),
                language=session_state.get("language", "unknown"),
            )
            reply_text = None
        elif reply_text:
            # 6. Append assistant message to history (skip when handing off).
            updated_session = await self._adk_sessions.get_session(
                app_name="chatbot", user_id=session_id, session_id=session_id
            )
            if updated_session:
                state_history = updated_session.state.setdefault("chat_history", [])
                await self._append_history_message(
                    session_id, updated_session, state_history, "assistant", reply_text
                )

        products = _cards_from_state(session_state.get("product_carousel", []) or [])
        if products:
            # The carousel is a one-shot for the turn that produced it — clear it so
            # it doesn't ride along on every subsequent reply.
            await self._clear_session_key(session_id, "product_carousel")

        await self._emit_turn_metrics(
            session_id,
            t0,
            bot_reply=reply_text,
            handed_off=handoff_payload is not None,
        )
        return TurnResult(
            reply=reply_text,
            language=session_state.get("language", "unknown"),
            sentiment=self._resolve_sentiment(session_state),
            handoff=handoff_payload,
            products=products,
        )

    async def capture_conversation(
        self,
        session_id: str,
        *,
        channel: str = "whatsapp",
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> None:
        """Mirror new conversation turns into the support system via the gate.

        The ticket id and how many messages have already been logged are persisted
        IN THE SESSION STATE (Firestore), so a single conversation maps to a single
        ticket and only new turns are appended — even across backend restarts or
        multiple instances. Opens the ticket when the detection gate fires,
        otherwise logs as a solved record.
        """
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        state = session.state
        history = state.get("chat_history", []) or []

        start = int(state.get("conversation_logged_count", 0) or 0)
        new_msgs = history[start:]
        if not new_msgs:
            return

        status = "open" if should_open_ticket(state) else "solved"
        subject = f"[{channel}] Conversation {session_id}"
        try:
            ticket_id = state.get("conversation_ticket_id")
            if not ticket_id:
                ticket_id = await self._conversation_log_port.ensure_conversation_ticket(
                    session_id=session_id,
                    subject=subject,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                )
            body = "\n".join(f"{m['role'].upper()}: {m['text']}" for m in new_msgs)
            result = await self._conversation_log_port.append_conversation_comment(
                ticket_id, body, status=status
            )
            if result == ConversationLogResult.TICKET_CLOSED:
                # The pinned ticket was closed and can't take comments anymore.
                # Rotate to a fresh ticket and retry once so the turn still mirrors.
                _log.info(
                    "rotating_closed_conversation_ticket",
                    session_id=session_id,
                    closed_ticket_id=ticket_id,
                )
                ticket_id = await self._conversation_log_port.rotate_conversation_ticket(
                    session_id=session_id,
                    subject=subject,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                )
                result = await self._conversation_log_port.append_conversation_comment(
                    ticket_id, body, status=status
                )
            # Always keep the (possibly rotated) ticket id, but only mark the turns
            # logged when the append actually landed — a transient failure leaves
            # the count unadvanced so the next capture retries these messages.
            state["conversation_ticket_id"] = ticket_id

            # P7 task 1: stamp sentiment as a conversation custom attribute so
            # it reaches BigQuery via the existing mapping (a sentiment nobody
            # can report is half a feature). Reuses set_ticket_classification
            # -- the SAME merge-safe custom-attributes path case_type/division
            # already go through -- rather than adding new Chatwoot API
            # surface. None (flag off, or nothing resolved yet) means skip:
            # no call at all, so a disabled tenant's Fake/adapter never even
            # sees the new kwarg. Its own try/except keeps a Chatwoot write
            # failure from undoing the comment mirroring that already
            # succeeded above -- fail-open, must never break the turn.
            sentiment_value = self._resolve_sentiment(state)
            if sentiment_value is not None:
                try:
                    await self._conversation_log_port.set_ticket_classification(
                        ticket_id, sentiment=sentiment_value
                    )
                except Exception as e:
                    _log.warning(
                        "sentiment_custom_attribute_write_failed",
                        session_id=session_id,
                        ticket_id=ticket_id,
                        error=str(e),
                    )

            if result == ConversationLogResult.OK:
                state["conversation_logged_count"] = len(history)
            else:
                _log.warning(
                    "conversation_mirror_incomplete",
                    session_id=session_id,
                    ticket_id=ticket_id,
                    result=result.value,
                )
            await self._persist_session_state(session)
        except Exception as e:
            _log.error("capture_conversation_failed", session_id=session_id, error=str(e))

    async def bind_email_ticket(self, session_id: str, ticket_id: str) -> None:
        """Seed the existing email ticket id (a Zendesk ticket, or a Chatwoot
        conversation id under the email inbox) into session state so the
        handoff/CSAT paths reuse it instead of creating a duplicate ticket."""
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        if session.state.get("conversation_ticket_id") != ticket_id:
            session.state["conversation_ticket_id"] = ticket_id
            await self._conversation_log_port.set_ticket_external_id(ticket_id, session_id)
            await self._persist_session_state(session)

    async def get_email_dedup(self, session_id: str) -> tuple[str | None, str | None]:
        """Return ``(last_inbound_text, last_reply_text)`` recorded for this email
        session, used by the router to skip re-fired triggers and AI-reply
        feedback loops. ``(None, None)`` when there is no prior exchange."""
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return (None, None)
        return (
            session.state.get("last_email_inbound"),
            session.state.get("last_email_reply"),
        )

    async def remember_email_exchange(
        self, session_id: str, *, inbound: str | None = None, reply: str | None = None
    ) -> None:
        """Persist the last customer message and/or AI reply for an email session
        so the next trigger can dedup against them. Only writes (and persists)
        when a value actually changes.

        Creates the session if absent: the inbound claim happens on the FIRST
        email of a ticket, before handle_turn has created the session, and the
        claim must be durable so concurrent re-fired triggers see it and skip.
        """
        session, _ = await self._get_or_create_session(session_id)
        changed = False
        if inbound is not None and session.state.get("last_email_inbound") != inbound:
            session.state["last_email_inbound"] = inbound
            changed = True
        if reply is not None and session.state.get("last_email_reply") != reply:
            session.state["last_email_reply"] = reply
            changed = True
        if changed:
            await self._persist_session_state(session)

    @staticmethod
    def parse_csat(text: str) -> int | None:
        """Return the first standalone 1-5 rating in the text, else None."""
        for token in re.findall(r"\d+", text or ""):
            if token in {"1", "2", "3", "4", "5"}:
                return int(token)
        return None

    async def record_csat(self, session_id: str, score: int, channel: str = "whatsapp") -> bool:
        """Record a CSAT score: private comment + tag + session state; resume AI."""
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return False
        state = session.state
        ticket_id = state.get("conversation_ticket_id")
        if ticket_id:
            await record_csat_on_ticket(self._conversation_log_port, ticket_id, score, channel)
        state["csat_score"] = score
        state[_HANDOFF_STATE_KEY] = WHATSAPP_ACTIVE
        state.pop("csat_nudged", None)
        state["handoff_triggered"] = False
        state["handoff_reason"] = ""
        await self._persist_session_state(session)
        return True

    async def record_nps(self, session_id: str, score: int, channel: str = "web") -> bool:
        """Record an NPS score: comment + `nps_<score>` tag + session state.

        Decoupled from the handoff/CSAT survey flow — does NOT touch handoff
        state. Tags the conversation ticket only when one exists (web `sim-`
        sessions without a ticket store the score in state but post no tag).
        """
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return False
        state = session.state
        ticket_id = state.get("conversation_ticket_id")
        if ticket_id:
            await record_nps_on_ticket(self._conversation_log_port, ticket_id, score, channel)
        state["nps_score"] = score
        await self._persist_session_state(session)
        return True

    async def conversation_state(self, session_id: str) -> str:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return WHATSAPP_ACTIVE
        return str(session.state.get(_HANDOFF_STATE_KEY) or WHATSAPP_ACTIVE)

    async def whatsapp_state(self, session_id: str) -> str:
        return await self.conversation_state(session_id)

    async def needs_handoff(self, session_id: str) -> bool:
        if await self.conversation_state(session_id) != WHATSAPP_ACTIVE:
            return False
        return await self._handoff_triggered(session_id)

    async def needs_whatsapp_handoff(self, session_id: str) -> bool:
        return await self.needs_handoff(session_id)

    async def begin_handoff(
        self,
        session_id: str,
        summary: str,
        *,
        channel: str = "whatsapp",
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        state = session.state
        try:
            ticket_id = state.get("conversation_ticket_id")
            if not ticket_id:
                ticket_id = await self._conversation_log_port.ensure_conversation_ticket(
                    session_id=session_id,
                    subject=f"[{channel}] Conversation {session_id}",
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                )
                state["conversation_ticket_id"] = ticket_id
            await self._conversation_log_port.append_conversation_comment(
                ticket_id, f"[Handoff to human agent]\n{summary}", status="open"
            )
        except Exception as e:
            _log.error("begin_handoff_failed", session_id=session_id, error=str(e))
        state[_HANDOFF_STATE_KEY] = WHATSAPP_PAUSED
        await self._persist_session_state(session)

    async def begin_whatsapp_handoff(
        self,
        session_id: str,
        summary: str,
        *,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> None:
        await self.begin_handoff(
            session_id,
            summary,
            channel="WhatsApp",
            customer_name=customer_name,
            customer_phone=customer_phone,
        )

    async def forward_whatsapp_to_agent(self, session_id: str, text: str) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        ticket_id = session.state.get("conversation_ticket_id")
        if not ticket_id:
            return
        await self._conversation_log_port.append_conversation_comment(
            ticket_id, f"Customer (WhatsApp): {text}"
        )

    async def begin_survey(self, session_id: str) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        session.state[_HANDOFF_STATE_KEY] = WHATSAPP_AWAITING_SURVEY
        session.state.pop("csat_nudged", None)
        await self._persist_session_state(session)

    async def consume_survey_nudge(self, session_id: str) -> bool:
        """Return True the first time (and mark nudged); False thereafter."""
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return False
        if session.state.get("csat_nudged"):
            return False
        session.state["csat_nudged"] = True
        await self._persist_session_state(session)
        return True

    async def resume_ai(self, session_id: str) -> None:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return
        session.state[_HANDOFF_STATE_KEY] = WHATSAPP_ACTIVE
        session.state.pop("csat_nudged", None)
        session.state["handoff_triggered"] = False
        session.state["handoff_reason"] = ""
        await self._persist_session_state(session)

    async def _persist_session_state(self, session: Any) -> None:
        """Write back mutations to ``session.state``.

        The in-memory store and test doubles hand back the live object (mutation
        already sticks), so only the Firestore store needs an explicit write.
        """
        if isinstance(self._adk_sessions, FirestoreSessionService):
            sessions_service = self._adk_sessions

            def _write() -> None:
                sessions_service._collection().document(session.id).set(
                    session.model_dump(mode="json")
                )

            await asyncio.to_thread(_write)

    async def _transcribe_audio(
        self,
        audio_bytes: bytes,
        audio_mime_type: str,
        session_id: str,
    ) -> str:
        """Call Gemini to transcribe user audio verbatim."""
        try:
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime_type),
                        types.Part.from_text(
                            text="Transcribe this audio verbatim. Output only the transcription, "
                            "without any extra text, corrections, or formatting."
                        ),
                    ],
                )
            ]
            response = await self._genai_client.aio.models.generate_content(
                model=self._settings.gemini_model,
                # list[Content] is valid input; installing Pillow (via reportlab) shifts
                # google-genai's overload resolution so mypy flags this valid call.
                contents=contents,  # type: ignore[arg-type]
            )
            transcription = (response.text or "").strip()
            _log.info(
                "voice_turn_transcription_completed",
                session_id=session_id,
                length=len(transcription),
            )
            return transcription
        except Exception as e:
            _log.error("voice_turn_transcription_failed", session_id=session_id, error=str(e))
            return ""

    async def _handle_voice_handoff(
        self,
        session_id: str,
        transcription: str,
    ) -> tuple[bytes, TurnResult] | None:
        """Helper to transcribe and forward voice message to agent if AI is paused."""
        if not await self._ticketing_port.is_ai_paused(session_id):
            return None

        if self._handoff_bridge is not None and self._human_agent_bridge is not None:
            conv_id = await self._handoff_bridge.conversation_id_for(session_id)
            if conv_id is not None:
                _log.info("ai_paused_transcribing_voice_turn_for_agent", session_id=session_id)
                try:
                    text_to_forward = transcription or "[audio]"
                    handoff_session = await self._adk_sessions.get_session(
                        app_name="chatbot", user_id=session_id, session_id=session_id
                    )
                    if handoff_session:
                        state_history = handoff_session.state.setdefault("chat_history", [])
                        await self._append_history_message(
                            session_id, handoff_session, state_history, "user", text_to_forward
                        )

                    # Forward message to Sunshine/Zendesk agent
                    await self._human_agent_bridge.forward_customer_message(
                        conversation_id=conv_id,
                        user_external_id=session_id,
                        text=text_to_forward,
                    )
                    # Save message to Firestore
                    await self._handoff_bridge.save_message(session_id, "user", text_to_forward)

                    return b"", TurnResult(
                        reply=None,
                        forwarded_to_agent=True,
                        user_transcription=text_to_forward,
                    )
                except Exception as e:
                    _log.error(
                        "forward_customer_voice_message_failed",
                        session_id=session_id,
                        error=str(e),
                    )
        _log.info("ai_paused_short_circuiting_voice_turn", session_id=session_id)
        return b"", TurnResult(reply=None)

    @staticmethod
    def _voice_message(
        transcription: str, audio_bytes: bytes, audio_mime_type: str
    ) -> types.Content:
        """The user Content for a voice turn: the transcription as text, when we
        have one, plus the raw audio.

        The audio is stripped from the persisted session (see
        `BlobFreeSessionService`) so whole-session rewrites stay small. Without
        the transcription riding along as text, every later turn would see
        nothing but a placeholder and the conversation would forget what the
        caller actually said. The text supplements the audio rather than
        replacing it, so this turn still reaches the model with the real voice —
        including tone a transcript cannot carry.
        """
        parts: list[types.Part] = []
        if transcription:
            parts.append(types.Part.from_text(text=transcription))
        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime_type))
        return types.Content(role="user", parts=parts)

    async def handle_voice_turn(
        self,
        session_id: str,
        audio_bytes: bytes,
        audio_mime_type: str = "audio/ogg",
        language_code: str = "en-US",
    ) -> tuple[bytes, TurnResult]:
        """Process a single audio-based voicebot turn end-to-end through Gemini.

        Sends the audio bytes directly to ADK as a multimodal Part — no explicit
        transcription step — then synthesizes the text reply via Gemini TTS.
        """
        _log.info(
            "processing_voicebot_turn",
            session_id=session_id,
            size_bytes=len(audio_bytes),
            mime_type=audio_mime_type,
        )

        # Retrieve or create the ADK session state first so we have the persistent context
        session, state_history = await self._get_or_create_session(session_id)

        transcription = await self._transcribe_audio(
            audio_bytes=audio_bytes,
            audio_mime_type=audio_mime_type,
            session_id=session_id,
        )

        handoff_res = await self._handle_voice_handoff(
            session_id=session_id,
            transcription=transcription,
        )
        if handoff_res is not None:
            return handoff_res

        # Append user voice input to history
        text_for_history = transcription or "[audio]"
        await self._append_history_message(
            session_id, session, state_history, "user", text_for_history
        )

        new_message = self._voice_message(transcription, audio_bytes, audio_mime_type)

        reply_text: str | None = ""
        final_event_seen = False
        final_part_kinds: list[str] = []
        try:
            reply_text, final_part_kinds, final_event_seen = await self._run_support_agent(
                session_id, new_message
            )
            # Retry once on an empty (non-handoff) reply — see handle_turn; an empty
            # reply here would otherwise yield silent audio and the UI placeholder.
            if not reply_text and not await self._handoff_triggered(session_id):
                _log.warning(
                    "voice_turn_empty_reply_text",
                    session_id=session_id,
                    final_part_kinds=final_part_kinds,
                    final_event_seen=final_event_seen,
                    attempt=1,
                )
                reply_text, final_part_kinds, final_event_seen = await self._run_support_agent(
                    session_id, new_message
                )
                if not reply_text and not await self._handoff_triggered(session_id):
                    _log.warning(
                        "voice_turn_empty_reply_after_retry",
                        session_id=session_id,
                        final_part_kinds=final_part_kinds,
                    )
                    reply_text = _EMPTY_REPLY_FALLBACK
        except Exception as e:
            _log.exception("adk_voice_execution_failed", session_id=session_id, error=str(e))
            fallback_text = "Maaf, terjadi kendala teknis. Mohon coba beberapa saat lagi."
            err_audio = await self._tts_port.synthesize(
                text=fallback_text, language_code=language_code
            )
            return err_audio, TurnResult(reply=fallback_text)

        if reply_text:
            # Re-fetch session to make sure we don't overwrite changes made by tools
            updated_session = await self._adk_sessions.get_session(
                app_name="chatbot", user_id=session_id, session_id=session_id
            )
            if updated_session:
                state_history = updated_session.state.setdefault("chat_history", [])
                await self._append_history_message(
                    session_id, updated_session, state_history, "assistant", reply_text
                )

        final_session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        session_state = final_session.state if final_session else {}

        handoff_payload = None
        if session_state.get("handoff_triggered") is True:
            reason = session_state.get("handoff_reason", "help_request")
            handoff_payload = await self._escalate_handoff(session_id, reason)
            reply_text = None

        audio_reply = b""
        if reply_text:
            try:
                audio_reply = await self._tts_port.synthesize(
                    text=reply_text, language_code=language_code
                )
                if not audio_reply:
                    # TTS returned no bytes without raising — surfaces as an empty
                    # audio reply in the UI; log so the cause is visible.
                    _log.warning(
                        "voice_tts_returned_empty_audio",
                        session_id=session_id,
                        reply_preview=reply_text[:100],
                    )
            except Exception as e:
                _log.error("voice_tts_synthesis_failed", session_id=session_id, error=str(e))
        elif handoff_payload is None:
            # Unexpected: empty reply that wasn't a handoff and survived the
            # retry + fallback above. final_part_kinds disambiguates a tool-only
            # turn from a genuinely empty generation.
            _log.warning(
                "voice_turn_empty_reply_unexpected",
                session_id=session_id,
                has_transcription=bool(transcription),
                final_event_seen=final_event_seen,
                final_part_kinds=final_part_kinds,
            )

        turn_result = TurnResult(
            reply=reply_text,
            language=session_state.get("language", "unknown"),
            sentiment=self._resolve_sentiment(session_state),
            handoff=handoff_payload,
            user_transcription=transcription or None,
        )
        return audio_reply, turn_result

    async def _escalate_handoff(self, session_id: str, reason: str) -> HandoffPayload:
        _log.info("escalating_handoff_started", session_id=session_id, reason=reason)

        # 1. Update session state first to prevent concurrent responses
        await self._ticketing_port.pause_ai_for_session(session_id)

        # Retrieve ADK session state to gather history and tools modifications
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        session_state = session.state if session else {}

        # 2. Extract recent transcript history for context
        state_history = session_state.get("chat_history", [])
        chat_log = []
        for msg in state_history:
            chat_log.append(
                {
                    "role": msg["role"],
                    "text": msg["text"],
                    "timestamp": msg.get("timestamp") or datetime.now(UTC).isoformat(),
                }
            )

        # 3. Call summarizer agent to generate structured details
        summary_text = "Customer requested human agent assistance."
        urgency = "medium"
        lang = "en"

        try:
            runner = self._runner_factory(self._summarizer_agent)
            payload = json.dumps({"history": chat_log})
            new_message = types.Content(
                role="user",
                parts=[types.Part.from_text(text=payload)],
            )

            res_summary = ""
            async for event in runner.run_async(
                user_id=f"sum-{session_id}",
                session_id=f"sum-{session_id}",
                new_message=new_message,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    res_summary = event.content.parts[0].text or ""

            if res_summary:
                # Parse JSON output from summarizer
                data = json.loads(res_summary)
                summary_text = data.get("summary", summary_text)
                urgency = data.get("urgency", urgency)
                lang = data.get("language", lang)
        except Exception as e:
            _log.warning("handoff_summarization_failed", error=str(e))

        lead_details = session_state.get("lead_details") or {}

        # Override urgency if ticket classification priority is set
        priority = session_state.get("priority")
        if priority:
            priority_mapping = {
                "low": "low",
                "medium": "medium",
                "high": "high",
                "normal": "medium",
                "urgent": "high",
                "critical": "high",
            }
            urgency = priority_mapping.get(priority.lower(), urgency)

        # 4. Open a live Sunshine Conversations bridge (if configured) so the
        #    customer can continue talking to the agent inside our own UI.
        live_chat_available = await self._open_live_bridge(
            session_id=session_id,
            summary_text=summary_text,
            urgency=urgency,
            language=lang,
            chat_log=chat_log,
            lead_details=lead_details,
            classification=session_state,
        )

        ticket_id = None
        if not live_chat_available:
            # Fall back to standard Support ticket-only mode
            title = f"AI Escalation - {reason.replace('_', ' ').title()}"
            body = (
                f"Reason: {reason}\n"
                f"Urgency: {urgency.upper()}\n"
                f"Transcript Summary: {summary_text}\n\n"
            )
            if lead_details:
                body += (
                    "--- CUSTOMER LEAD DETAILS ---\n"
                    f"Name: {lead_details.get('customer_name')}\n"
                    f"Phone: {lead_details.get('customer_phone')}\n"
                    f"Email: {lead_details.get('customer_email')}\n"
                    f"Preferred Model: {lead_details.get('preferred_model')}\n"
                    f"Preferred Dealer: {lead_details.get('preferred_dealer')}\n"
                    "-----------------------------\n\n"
                )

            body += "Recent Transcript Logs:\n"
            for log_msg in chat_log:
                body += f"- {log_msg['role'].upper()}: {log_msg['text']}\n"

            _raw_cat = session_state.get("category")
            ticket_id = await self._ticketing_port.create_ticket(
                session_id=session_id,
                title=title,
                body=body,
                urgency=urgency,
                customer_name=lead_details.get("customer_name"),
                customer_email=lead_details.get("customer_email"),
                customer_phone=lead_details.get("customer_phone"),
                category=_raw_cat,
                subcategory=session_state.get("subcategory"),
                case_type=session_state.get("case_type"),
                vehicle_model=session_state.get("vehicle_model"),
                division=(CATEGORY_TO_DIVISION.get(str(_raw_cat).lower()) if _raw_cat else None),
                sla_minutes=session_state.get("sla_minutes"),
            )

            # Add private note banner
            note_content = (
                f"⚠️ AI ASSISTANT SUMMARY:\n"
                f"{summary_text}\n\n"
                f"Urgency: {urgency.upper()} | Language: {lang.upper()}\n"
            )
            category = session_state.get("category")
            if category:
                note_content += (
                    f"Category: {category} | Subcategory: {session_state.get('subcategory')}\n"
                    f"Priority: {session_state.get('priority')} | SLA: {session_state.get('sla_minutes')}m\n"
                )
            await self._ticketing_port.add_private_note(ticket_id=ticket_id, text=note_content)

        _log.info(
            "escalation_completed",
            session_id=session_id,
            ticket_id=ticket_id,
            live_chat_available=live_chat_available,
        )
        return HandoffPayload(
            reason=reason,  # type: ignore[arg-type]
            language=lang,  # type: ignore[arg-type]
            summary=summary_text,
            urgency=urgency,  # type: ignore[arg-type]
            live_chat_available=live_chat_available,
            lead_details=lead_details or None,
            classification={
                "category": session_state.get("category"),
                "subcategory": session_state.get("subcategory"),
                "priority": session_state.get("priority"),
                "sla_minutes": session_state.get("sla_minutes"),
            }
            if session_state.get("category")
            else None,
        )

    async def _open_live_bridge(
        self,
        session_id: str,
        summary_text: str,
        urgency: str,
        language: str,
        chat_log: list[dict[str, str]],
        lead_details: dict[str, Any] | None = None,
        classification: dict[str, Any] | None = None,
    ) -> bool:
        if self._human_agent_bridge is None or self._handoff_bridge is None:
            return False

        transcript = tuple(
            Message(
                role=entry["role"],  # type: ignore[arg-type]
                text=entry["text"],
                timestamp=datetime.fromisoformat(entry["timestamp"])
                if isinstance(entry.get("timestamp"), str)
                else datetime.now(UTC),
            )
            for entry in chat_log
        )

        lead = lead_details or {}
        customer_name = lead.get("customer_name") or f"Proton AI Customer ({session_id})"
        customer_email = lead.get("customer_email") or f"{session_id}@proton.devoteam.example"
        customer_phone = lead.get("customer_phone")
        preferred_model = lead.get("preferred_model")

        cls = classification or {}
        _raw_cat = cls.get("category")
        payload = HandoffOpenPayload(
            session_id=session_id,
            customer_name=customer_name,
            customer_email=customer_email,
            ai_summary=summary_text,
            transcript=transcript,
            urgency=urgency,  # type: ignore[arg-type]
            language=language,  # type: ignore[arg-type]
            customer_phone=customer_phone,
            preferred_model=preferred_model,
            category=_raw_cat,
            subcategory=cls.get("subcategory"),
            case_type=cls.get("case_type"),
            vehicle_model=cls.get("vehicle_model"),
            division=(CATEGORY_TO_DIVISION.get(str(_raw_cat).lower()) if _raw_cat else None),
            department=cls.get("department"),
            sla_minutes=cls.get("sla_minutes"),
            reason=str(cls.get("handoff_reason") or "help_request"),
        )

        try:
            conversation_id = await self._human_agent_bridge.open_handoff(payload)
        except Exception as e:
            _log.error(
                "sunshine_open_handoff_failed",
                session_id=session_id,
                error=str(e),
            )
            return False

        # Build initial transcript payload to save in Firestore
        full_transcript = [
            {
                "role": msg.role,
                "text": msg.text,
                "timestamp": msg.timestamp.isoformat()
                if msg.timestamp
                else datetime.now(UTC).isoformat(),
            }
            for msg in transcript
        ]
        await self._handoff_bridge.register(session_id, conversation_id, transcript=full_transcript)
        return True
