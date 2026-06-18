from __future__ import annotations

import structlog
from fastapi import APIRouter

from chatbot.features.chat.schemas import ChatwootWebhookPayload, ZendeskWebhookPayload
from chatbot.features.chat.service import OrchestratorService

_log = structlog.get_logger(__name__)


class ChatRouter:
    """Class-based router wrapping CRM webhook endpoints."""

    def __init__(self, orchestrator: OrchestratorService) -> None:
        self.orchestrator = orchestrator
        self.router = APIRouter(tags=["chat"])

        self.router.add_api_route(
            "/webhooks/chatwoot", self.chatwoot_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/webhooks/zendesk", self.zendesk_webhook, methods=["POST"]
        )

    async def chatwoot_webhook(self, payload: ChatwootWebhookPayload) -> dict[str, str]:
        """Process incoming customer messages from Chatwoot."""
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
        """Process incoming customer messages from Zendesk Sunshine Conversations."""
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


def build_chat_router(orchestrator: OrchestratorService) -> APIRouter:
    """Builds and returns the configured FastAPI router instance."""
    return ChatRouter(orchestrator).router
