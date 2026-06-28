from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, InMemorySessionService, Session
from google.genai import Client, types

from chatbot.features.chat.adapters.firestore_session_service import FirestoreSessionService
from chatbot.features.chat.adapters.noop_conversation_log import NoOpConversationLog
from chatbot.features.chat.agents import build_ai_agent, build_summarizer_agent
from chatbot.features.chat.detection import should_open_ticket
from chatbot.features.chat.handoff_bridge import HandoffBridge
from chatbot.features.chat.models import (
    HandoffOpenPayload,
    HandoffPayload,
    Message,
    ProductCard,
    TurnResult,
)
from chatbot.features.chat.ports import (
    ChatPort,
    ConversationLogPort,
    HumanAgentBridgePort,
    KnowledgePort,
    TextToSpeechPort,
    TicketingPort,
)

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

        # Initialize ADK agents
        self._support_agent = build_ai_agent(settings, ticketing_port, knowledge_port)
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

    def _default_runner_factory(self, agent: Any) -> Runner:
        return Runner(
            agent=agent,
            app_name="chatbot",
            session_service=self._adk_sessions,
        )

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
        """Run the support agent once and extract reply text from ALL final parts.

        Returns ``(reply_text, final_part_kinds, final_event_seen)``. Gemini can
        place the reply after a function-call part (so reading ``parts[0]`` alone
        drops it) or, intermittently, return a final response with no text part at
        all — callers retry/fall back on an empty ``reply_text``.
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

    async def handle_turn(self, session_id: str, text: str) -> TurnResult:
        """Process a single text-based chatbot turn."""
        _log.info("processing_chatbot_turn", session_id=session_id, text_length=len(text))

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

        # 4. Formulate the GenAI content structure
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=text)],
        )

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
            return TurnResult(reply="Maaf, terjadi kendala teknis. Mohon coba beberapa saat lagi.")

        final_session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        session_state = final_session.state if final_session else {}

        handoff_payload = None
        if session_state.get("handoff_triggered") is True and not session_id.startswith(
            "whatsapp-"
        ):
            reason = session_state.get("handoff_reason", "help_request")

            # Execute human escalation handoff
            handoff_payload = await self._escalate_handoff(session_id, reason)
            reply_text = None  # Clear reply so chatbot doesn't post text when handing off
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

        return TurnResult(
            reply=reply_text,
            language=session_state.get("language", "unknown"),
            sentiment=session_state.get("sentiment"),
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
            await self._conversation_log_port.append_conversation_comment(
                ticket_id, body, status=status
            )
            state["conversation_ticket_id"] = ticket_id
            state["conversation_logged_count"] = len(history)
            await self._persist_session_state(session)
        except Exception as e:
            _log.error("capture_conversation_failed", session_id=session_id, error=str(e))

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
            try:
                await self._conversation_log_port.append_conversation_comment(
                    ticket_id, f"⭐ Customer satisfaction: {score}/5 (via {channel})"
                )
                await self._conversation_log_port.add_ticket_tag(ticket_id, f"csat_{score}")
            except Exception as e:
                _log.error("record_csat_log_failed", session_id=session_id, error=str(e))
        state["csat_score"] = score
        state[_HANDOFF_STATE_KEY] = WHATSAPP_ACTIVE
        state.pop("csat_nudged", None)
        state["handoff_triggered"] = False
        state["handoff_reason"] = ""
        await self._persist_session_state(session)
        return True

    async def whatsapp_state(self, session_id: str) -> str:
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if session is None:
            return WHATSAPP_ACTIVE
        return str(session.state.get(_HANDOFF_STATE_KEY) or WHATSAPP_ACTIVE)

    async def needs_whatsapp_handoff(self, session_id: str) -> bool:
        if await self.whatsapp_state(session_id) != WHATSAPP_ACTIVE:
            return False
        return await self._handoff_triggered(session_id)

    async def begin_whatsapp_handoff(
        self,
        session_id: str,
        summary: str,
        *,
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
                    subject=f"[WhatsApp] Conversation {session_id}",
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                )
                state["conversation_ticket_id"] = ticket_id
            await self._conversation_log_port.append_conversation_comment(
                ticket_id, f"[Handoff to human agent]\n{summary}", status="open"
            )
        except Exception as e:
            _log.error("begin_whatsapp_handoff_failed", session_id=session_id, error=str(e))
        state[_HANDOFF_STATE_KEY] = WHATSAPP_PAUSED
        await self._persist_session_state(session)

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
                contents=contents,
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

        new_message = types.Content(
            role="user",
            parts=[types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime_type)],
        )

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
            sentiment=session_state.get("sentiment"),
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

            ticket_id = await self._ticketing_port.create_ticket(
                session_id=session_id,
                title=title,
                body=body,
                urgency=urgency,
                customer_name=lead_details.get("customer_name"),
                customer_email=lead_details.get("customer_email"),
                customer_phone=lead_details.get("customer_phone"),
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
