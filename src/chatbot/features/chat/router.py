from __future__ import annotations

import os
import uuid

import structlog
from fastapi import APIRouter, Form, Response
from starlette.concurrency import run_in_threadpool

from chatbot.features.chat.schemas import ChatwootWebhookPayload, ZendeskWebhookPayload
from chatbot.features.chat.service import OrchestratorService

_log = structlog.get_logger(__name__)


class ChatRouter:
    """Class-based router wrapping webhook endpoints for chat and voice channels."""

    def __init__(self, orchestrator: OrchestratorService) -> None:
        self.orchestrator = orchestrator
        self.router = APIRouter(tags=["chat"])
        
        # Register API routes
        self.router.add_api_route(
            "/webhooks/chatwoot", self.chatwoot_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/webhooks/zendesk", self.zendesk_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/webhooks/voice/twilio", self.twilio_voice_welcome, methods=["POST"]
        )
        self.router.add_api_route(
            "/webhooks/voice/twilio/process", self.twilio_voice_process, methods=["POST"]
        )

    async def chatwoot_webhook(self, payload: ChatwootWebhookPayload) -> dict[str, str]:
        """Endpoint processing incoming customer messages from Chatwoot."""
        if payload.event != "message_created" or payload.message_type != "incoming":
            return {"status": "ignored"}

        content = (payload.content or "").strip()
        if not content:
            return {"status": "empty_content"}

        conv_id = str(payload.conversation.id)
        session_id = f"chatwoot-conv-{conv_id}"

        _log.info("chatwoot_webhook_received", conv_id=conv_id, session_id=session_id)

        # Process the conversational turn
        turn_result = await self.orchestrator.handle_turn(session_id=session_id, text=content)

        # If a reply is produced, post it back to Chatwoot
        if turn_result.reply:
            await self.orchestrator._chat_port.send_message(
                conversation_id=conv_id, text=turn_result.reply
            )

        return {"status": "ok"}

    async def zendesk_webhook(self, payload: ZendeskWebhookPayload) -> dict[str, str]:
        """Endpoint processing incoming customer messages from Zendesk Sunshine Conversations."""
        for event in payload.events:
            # We only process user message events
            if event.type != "conversation:message":
                continue
            
            author_role = event.payload.message.author.role
            if author_role != "user":
                continue

            content = event.payload.message.content.text
            if not content or not content.strip():
                continue

            conv_id = event.payload.conversation.id
            session_id = f"zendesk-conv-{conv_id}"

            _log.info("zendesk_webhook_received", conv_id=conv_id, session_id=session_id)

            # Process the conversational turn
            turn_result = await self.orchestrator.handle_turn(
                session_id=session_id, text=content.strip()
            )

            # If a reply is produced, post it back to Zendesk
            if turn_result.reply:
                await self.orchestrator._chat_port.send_message(
                    conversation_id=conv_id, text=turn_result.reply
                )

        return {"status": "ok"}

    async def twilio_voice_welcome(
        self,
        CallSid: str = Form(...),
        From: str = Form(...),
    ) -> Response:
        """Initial Twilio Voice entry point. Greets the caller and gathers their speech input."""
        _log.info("twilio_voice_call_received", call_sid=CallSid, caller=From)

        say_welcome = (
            "Halo, selamat datang di layanan dukungan otomatis kami. "
            "Silakan sebutkan keluhan Anda setelah nada berikut."
        )
        say_timeout = "Maaf, kami tidak mendengar suara Anda. Panggilan akan diakhiri."
        gather_elem = (
            '<Gather input="speech" timeout="5" '
            'action="/webhooks/voice/twilio/process" method="POST" '
            'language="id-ID" speechModel="phone_call"/>'
        )

        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Say language="id-ID" voice="Polly.Aditi">{say_welcome}</Say>\n'
            "  <Play>http://demo.choiceslides.com/sound/beep.wav</Play>\n"
            f"  {gather_elem}\n"
            f'  <Say language="id-ID" voice="Polly.Aditi">{say_timeout}</Say>\n'
            "  <Hangup/>\n"
            "</Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    async def twilio_voice_process(
        self,
        CallSid: str = Form(...),
        SpeechResult: str = Form(None),
        Confidence: float = Form(0.0),
    ) -> Response:
        """Processes Twilio speech recognition results and returns next voice actions."""
        session_id = f"voice-call-{CallSid}"

        gather_elem = (
            '<Gather input="speech" timeout="5" '
            'action="/webhooks/voice/twilio/process" method="POST" '
            'language="id-ID" speechModel="phone_call"/>'
        )

        # If no speech was gathered, ask again
        if not SpeechResult or not SpeechResult.strip():
            _log.info("twilio_voice_no_speech_detected", session_id=session_id)
            say_again = "Maaf, saya tidak mendengar keluhan Anda. Bisa tolong ulangi kembali?"
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f'  <Say language="id-ID" voice="Polly.Aditi">{say_again}</Say>\n'
                f"  {gather_elem}\n"
                "  <Hangup/>\n"
                "</Response>"
            )
            return Response(content=twiml, media_type="application/xml")

        _log.info(
            "twilio_voice_speech_received",
            session_id=session_id,
            text=SpeechResult,
            confidence=Confidence,
        )

        # Process turn through the unified agent
        turn_result = await self.orchestrator.handle_turn(
            session_id=session_id, text=SpeechResult.strip()
        )

        # If turn triggered human handoff, dial a support agent
        if turn_result.handoff:
            _log.info("twilio_voice_escalation_triggered", session_id=session_id)
            say_escalation = (
                "Laporan Anda memerlukan bantuan agen manusia. "
                "Mohon tunggu, kami sedang menyambungkan panggilan Anda."
            )
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f'  <Say language="id-ID" voice="Polly.Aditi">{say_escalation}</Say>\n'
                "  <Dial>+6281234567890</Dial>\n"
                "</Response>"
            )
            return Response(content=twiml, media_type="application/xml")

        # Synthesize reply using GCP TTS if provider is GCP, otherwise fallback to Twilio Say
        if self.orchestrator._settings.voice_provider == "gcp" and turn_result.reply:
            try:
                # Generate high-quality MP3 voice reply
                audio_bytes = await self.orchestrator._tts_port.synthesize(
                    text=turn_result.reply, language_code=self.orchestrator._settings.voice_default_lang
                )

                # Save MP3 audio to static files folder in a non-blocking way
                filename = f"{uuid.uuid4()}.mp3"
                os.makedirs("static/audio", exist_ok=True)
                audio_path = f"static/audio/{filename}"

                def _write_file(path: str, data: bytes) -> None:
                    with open(path, "wb") as f:
                        f.write(data)

                await run_in_threadpool(_write_file, audio_path, audio_bytes)

                # Public URL of the audio file (requires public host in production, mock for local)
                audio_url = f"/static/audio/{filename}"
                say_thanks = "Terima kasih telah menghubungi kami."

                twiml = (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    "<Response>\n"
                    f"  <Play>{audio_url}</Play>\n"
                    f"  {gather_elem}\n"
                    f'  <Say language="id-ID" voice="Polly.Aditi">{say_thanks}</Say>\n'
                    "  <Hangup/>\n"
                    "</Response>"
                )
                return Response(content=twiml, media_type="application/xml")
            except Exception as e:
                _log.error("gcp_tts_synthesis_failed_falling_back_to_twilio_say", error=str(e))

        # Fallback to Twilio's standard built-in text-to-speech voice
        reply = turn_result.reply or "Ada yang bisa kami bantu kembali?"
        say_thanks = "Terima kasih telah menghubungi kami."
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f'  <Say language="id-ID" voice="Polly.Aditi">{reply}</Say>\n'
            f"  {gather_elem}\n"
            f'  <Say language="id-ID" voice="Polly.Aditi">{say_thanks}</Say>\n'
            "  <Hangup/>\n"
            "</Response>"
        )
        return Response(content=twiml, media_type="application/xml")


def build_chat_router(orchestrator: OrchestratorService) -> APIRouter:
    """Builds and returns the configured FastAPI router instance."""
    return ChatRouter(orchestrator).router
