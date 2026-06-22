from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import Client, types

from chatbot.features.chat.agents import build_ai_agent, build_summarizer_agent
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
    HumanAgentBridgePort,
    KnowledgePort,
    TextToSpeechPort,
    TicketingPort,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


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
        runner_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._chat_port = chat_port
        self._ticketing_port = ticketing_port
        self._knowledge_port = knowledge_port
        self._tts_port = tts_port
        self._human_agent_bridge = human_agent_bridge
        self._handoff_bridge = handoff_bridge

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

    async def handle_turn(self, session_id: str, text: str) -> TurnResult:
        """Process a single text-based chatbot turn."""
        _log.info("processing_chatbot_turn", session_id=session_id, text_length=len(text))

        # 0. If the session is already handed off to a human agent and we have
        #    a live bridge, relay this turn straight to Sunshine Conversations
        #    and return — the agent's reply arrives async over /chat/stream.
        if self._handoff_bridge is not None and self._human_agent_bridge is not None:
            conv_id = await self._handoff_bridge.conversation_id_for(session_id)
            if conv_id is not None:
                self._history.setdefault(session_id, []).append(
                    Message(role="user", text=text, timestamp=datetime.now(UTC))
                )
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

        # 2. Append user message to history
        self._history.setdefault(session_id, []).append(
            Message(role="user", text=text, timestamp=datetime.now(UTC))
        )

        # 3. Retrieve or create the ADK session state
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if not session:
            await self._adk_sessions.create_session(
                app_name="chatbot",
                user_id=session_id,
                session_id=session_id,
                state={
                    "session_id": session_id,
                    "handoff_triggered": False,
                    "handoff_reason": "",
                },
            )

        # 4. Formulate the GenAI content structure
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=text)],
        )

        # 5. Run the ADK Agent
        runner = self._runner_factory(self._support_agent)
        reply_text: str | None = ""
        try:
            async for event in runner.run_async(
                user_id=session_id,
                session_id=session_id,
                new_message=new_message,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    reply_text = event.content.parts[0].text or ""
        except Exception as e:
            _log.exception("adk_execution_loop_failed", session_id=session_id, error=str(e))
            return TurnResult(reply="Maaf, terjadi kendala teknis. Mohon coba beberapa saat lagi.")

        # 6. Append assistant message to history if generated
        if reply_text:
            self._history[session_id].append(
                Message(role="assistant", text=reply_text, timestamp=datetime.now(UTC))
            )

        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        session_state = session.state if session else {}

        handoff_payload = None
        if session_state.get("handoff_triggered") is True:
            reason = session_state.get("handoff_reason", "help_request")

            # Execute human escalation handoff
            handoff_payload = await self._escalate_handoff(session_id, reason)
            reply_text = None  # Clear reply so chatbot doesn't post text when handing off

        products = _cards_from_state(session_state.get("product_carousel", []) or [])

        return TurnResult(
            reply=reply_text,
            language=session_state.get("language", "unknown"),
            sentiment=session_state.get("sentiment"),
            handoff=handoff_payload,
            products=products,
        )

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
                    # Append user message to history
                    self._history.setdefault(session_id, []).append(
                        Message(role="user", text=text_to_forward, timestamp=datetime.now(UTC))
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

        self._history.setdefault(session_id, []).append(
            Message(role="user", text=transcription or "[audio]", timestamp=datetime.now(UTC))
        )

        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        if not session:
            await self._adk_sessions.create_session(
                app_name="chatbot",
                user_id=session_id,
                session_id=session_id,
                state={
                    "session_id": session_id,
                    "handoff_triggered": False,
                    "handoff_reason": "",
                },
            )

        new_message = types.Content(
            role="user",
            parts=[types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime_type)],
        )

        runner = self._runner_factory(self._support_agent)
        reply_text: str | None = ""
        try:
            async for event in runner.run_async(
                user_id=session_id,
                session_id=session_id,
                new_message=new_message,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    reply_text = event.content.parts[0].text or ""
        except Exception as e:
            _log.exception("adk_voice_execution_failed", session_id=session_id, error=str(e))
            fallback_text = "Maaf, terjadi kendala teknis. Mohon coba beberapa saat lagi."
            err_audio = await self._tts_port.synthesize(
                text=fallback_text, language_code=language_code
            )
            return err_audio, TurnResult(reply=fallback_text)

        if reply_text:
            self._history[session_id].append(
                Message(role="assistant", text=reply_text, timestamp=datetime.now(UTC))
            )

        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        session_state = session.state if session else {}

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
            except Exception as e:
                _log.error("voice_tts_synthesis_failed", session_id=session_id, error=str(e))

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

        # 2. Extract recent transcript history for context
        history = self._history.get(session_id, [])
        chat_log = []
        for msg in history:
            chat_log.append({"role": msg.role, "text": msg.text})

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

        # Retrieve ADK session state to gather tools modifications
        session = await self._adk_sessions.get_session(
            app_name="chatbot", user_id=session_id, session_id=session_id
        )
        session_state = session.state if session else {}
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
            Message(role=entry["role"], text=entry["text"], timestamp=datetime.now(UTC))  # type: ignore[arg-type]
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
            for msg in self._history.get(session_id, [])
        ]
        await self._handoff_bridge.register(session_id, conversation_id, transcript=full_transcript)
        return True
