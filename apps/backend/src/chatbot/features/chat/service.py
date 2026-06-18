from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from chatbot.features.chat.agents import build_ai_agent, build_summarizer_agent
from chatbot.features.chat.models import HandoffPayload, Message, TurnResult
from chatbot.features.chat.ports import (
    ChatPort,
    KnowledgePort,
    SpeechToTextPort,
    TextToSpeechPort,
    TicketingPort,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class OrchestratorService:
    """Core orchestrator driving conversational turns for both Chatbot and Voicebot."""

    def __init__(
        self,
        settings: Settings,
        chat_port: ChatPort,
        ticketing_port: TicketingPort,
        knowledge_port: KnowledgePort,
        stt_port: SpeechToTextPort,
        tts_port: TextToSpeechPort,
        runner_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._chat_port = chat_port
        self._ticketing_port = ticketing_port
        self._knowledge_port = knowledge_port
        self._stt_port = stt_port
        self._tts_port = tts_port

        # Initialize ADK agents
        self._support_agent = build_ai_agent(settings, ticketing_port, knowledge_port)
        self._summarizer_agent = build_summarizer_agent(settings)

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

        # 1. Short-circuit if AI is paused for this session (human has taken over)
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

        return TurnResult(
            reply=reply_text,
            language=session_state.get("language", "unknown"),
            sentiment=session_state.get("sentiment"),
            handoff=handoff_payload,
        )

    async def handle_voice_turn(
        self, session_id: str, audio_bytes: bytes, language_code: str = "en-US"
    ) -> tuple[bytes, TurnResult]:
        """Process a single audio-based voicebot turn."""
        _log.info("processing_voicebot_turn", session_id=session_id, size_bytes=len(audio_bytes))

        # 1. Speech-to-Text conversion
        try:
            transcript = await self._stt_port.transcribe(
                audio_content=audio_bytes, language_code=language_code
            )
        except Exception as e:
            _log.error("voice_stt_transcription_failed", session_id=session_id, error=str(e))
            # Return standard fallback audio
            fallback_text = (
                "Maaf, kami tidak dapat mendengar suara Anda dengan jelas. Mohon ulangi kembali."
            )
            err_audio = await self._tts_port.synthesize(
                text=fallback_text, language_code=language_code
            )
            return err_audio, TurnResult(reply=fallback_text)

        # 2. Run text-based orchestrator turn
        turn_result = await self.handle_turn(session_id=session_id, text=transcript.text)

        # 3. If a reply is generated, convert it back into Text-to-Speech audio bytes
        audio_reply = b""
        if turn_result.reply:
            try:
                audio_reply = await self._tts_port.synthesize(
                    text=turn_result.reply, language_code=language_code
                )
            except Exception as e:
                _log.error("voice_tts_synthesis_failed", session_id=session_id, error=str(e))

        return audio_reply, turn_result

    async def _escalate_handoff(self, session_id: str, reason: str) -> HandoffPayload:
        _log.info("escalating_handoff_started", session_id=session_id, reason=reason)

        # 1. Update session state first to prevent concurrent responses
        await self._ticketing_port.pause_ai_for_session(session_id)

        # 2. Extract recent transcript history for context
        history = self._history.get(session_id, [])
        chat_log = []
        for msg in history[-6:]:
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

        # 4. Open ticket in support CRM
        title = f"AI Escalation - {reason.replace('_', ' ').title()}"
        body = (
            f"Reason: {reason}\n"
            f"Urgency: {urgency.upper()}\n"
            f"Transcript Summary: {summary_text}\n\n"
            f"Recent Transcript Logs:\n"
        )
        for log_msg in chat_log:
            body += f"- {log_msg['role'].upper()}: {log_msg['text']}\n"

        ticket_id = await self._ticketing_port.create_ticket(
            session_id=session_id, title=title, body=body, urgency=urgency
        )

        # 5. Add private note banner
        note_content = (
            f"⚠️ AI ASSISTANT SUMMARY:\n"
            f"{summary_text}\n\n"
            f"Urgency: {urgency.upper()} | Language: {lang.upper()}"
        )
        await self._ticketing_port.add_private_note(ticket_id=ticket_id, text=note_content)

        _log.info("escalation_completed", session_id=session_id, ticket_id=ticket_id)
        return HandoffPayload(
            reason=reason,  # type: ignore[arg-type]
            language=lang,  # type: ignore[arg-type]
            summary=summary_text,
            urgency=urgency,  # type: ignore[arg-type]
        )
