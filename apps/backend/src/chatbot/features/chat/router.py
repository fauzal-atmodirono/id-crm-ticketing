from __future__ import annotations

from typing import Any
from urllib.parse import quote

import structlog
from fastapi import APIRouter, File, Form, Response, UploadFile
from pydantic import BaseModel

from chatbot.features.chat.schemas import ChatwootWebhookPayload, ZendeskWebhookPayload
from chatbot.features.chat.service import OrchestratorService

_log = structlog.get_logger(__name__)


class ChatTurnRequest(BaseModel):
    session_id: str
    text: str


class ChatTurnResponse(BaseModel):
    reply: str | None
    language: str | None = None
    sentiment: str | None = None
    handoff: dict[str, Any] | None = None


def _handoff_dict(turn_result: Any) -> dict[str, Any] | None:
    if turn_result.handoff is None:
        return None
    return {
        "reason": turn_result.handoff.reason,
        "language": turn_result.handoff.language,
        "summary": turn_result.handoff.summary,
        "urgency": turn_result.handoff.urgency,
    }


class ChatRouter:
    """Class-based router for CRM webhooks plus the frontend-facing chat/voice endpoints."""

    def __init__(self, orchestrator: OrchestratorService) -> None:
        self.orchestrator = orchestrator
        self.router = APIRouter(tags=["chat"])

        self.router.add_api_route(
            "/webhooks/chatwoot", self.chatwoot_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/webhooks/zendesk", self.zendesk_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/chat/turn",
            self.chat_turn,
            methods=["POST"],
            response_model=ChatTurnResponse,
        )
        self.router.add_api_route(
            "/voice/turn",
            self.voice_turn,
            methods=["POST"],
        )

    # --- CRM webhooks -------------------------------------------------------

    async def chatwoot_webhook(self, payload: ChatwootWebhookPayload) -> dict[str, str]:
        if payload.event != "message_created" or payload.message_type != "incoming":
            return {"status": "ignored"}

        content = (payload.content or "").strip()
        if not content:
            return {"status": "empty_content"}

        conv_id = str(payload.conversation.id)
        session_id = f"chatwoot-conv-{conv_id}"

        _log.info("chatwoot_webhook_received", conv_id=conv_id, session_id=session_id)

        turn_result = await self.orchestrator.handle_turn(session_id=session_id, text=content)

        if turn_result.reply:
            await self.orchestrator._chat_port.send_message(
                conversation_id=conv_id, text=turn_result.reply
            )

        return {"status": "ok"}

    async def zendesk_webhook(self, payload: ZendeskWebhookPayload) -> dict[str, str]:
        for event in payload.events:
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

            turn_result = await self.orchestrator.handle_turn(
                session_id=session_id, text=content.strip()
            )

            if turn_result.reply:
                await self.orchestrator._chat_port.send_message(
                    conversation_id=conv_id, text=turn_result.reply
                )

        return {"status": "ok"}

    # --- Frontend-facing API (consumed by the Vue app) ----------------------

    async def chat_turn(self, req: ChatTurnRequest) -> ChatTurnResponse:
        """Run a single text chat turn and return the assistant's reply."""
        _log.info("chat_turn_received", session_id=req.session_id, text_length=len(req.text))
        result = await self.orchestrator.handle_turn(
            session_id=req.session_id, text=req.text
        )
        return ChatTurnResponse(
            reply=result.reply,
            language=result.language,
            sentiment=result.sentiment,
            handoff=_handoff_dict(result),
        )

    async def voice_turn(
        self,
        session_id: str = Form(...),
        audio: UploadFile = File(...),  # noqa: B008  (FastAPI file injection)
    ) -> Response:
        """Run a single voice turn. Returns MP3 audio with X-Reply-Text + X-Handoff-Reason headers."""
        audio_bytes = await audio.read()
        mime_type = audio.content_type or "audio/ogg"
        _log.info(
            "voice_turn_received",
            session_id=session_id,
            size_bytes=len(audio_bytes),
            mime_type=mime_type,
        )

        audio_reply, turn_result = await self.orchestrator.handle_voice_turn(
            session_id=session_id,
            audio_bytes=audio_bytes,
            audio_mime_type=mime_type,
        )

        headers: dict[str, str] = {}
        if turn_result.reply:
            # HTTP headers are latin-1; URL-encode the unicode reply text
            headers["X-Reply-Text"] = quote(turn_result.reply, safe="")
        if turn_result.handoff is not None:
            headers["X-Handoff-Reason"] = turn_result.handoff.reason

        return Response(content=audio_reply, media_type="audio/mpeg", headers=headers)


def build_chat_router(orchestrator: OrchestratorService) -> APIRouter:
    """Builds and returns the configured FastAPI router instance."""
    return ChatRouter(orchestrator).router
