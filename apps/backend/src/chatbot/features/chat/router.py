from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import structlog
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
from chatbot.features.chat.handoff_bridge import HandoffBridge, SurveyEvent
from chatbot.features.chat.models import AgentMessageEvent
from chatbot.features.chat.ports import HumanAgentBridgePort
from chatbot.features.chat.schemas import (
    ChatwootWebhookPayload,
    TtsRequest,
    ZendeskEmailWebhookPayload,
    ZendeskHandbackPayload,
    ZendeskSupportWebhookPayload,
    ZendeskWebhookPayload,
)
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.twilio_signature import verify_twilio_signature

_log = structlog.get_logger(__name__)


class ChatTurnRequest(BaseModel):
    session_id: str
    text: str


class CsatRequest(BaseModel):
    session_id: str
    score: int = Field(ge=1, le=5)


class ChatTurnResponse(BaseModel):
    reply: str | None
    language: str | None = None
    sentiment: str | None = None
    handoff: dict[str, Any] | None = None
    forwarded_to_agent: bool = False
    products: list[dict[str, Any]] = []


def _handoff_dict(turn_result: Any) -> dict[str, Any] | None:
    if turn_result.handoff is None:
        return None
    return {
        "reason": turn_result.handoff.reason,
        "language": turn_result.handoff.language,
        "summary": turn_result.handoff.summary,
        "urgency": turn_result.handoff.urgency,
        "live_chat_available": turn_result.handoff.live_chat_available,
        "lead_details": turn_result.handoff.lead_details,
        "classification": turn_result.handoff.classification,
    }


_WHATSAPP_MARKDOWN_CHARS = re.compile(r"[*_~`]+")
# `[label](https://url)` / `![label](https://url)` -> "label (url)". WhatsApp
# renders the raw markdown otherwise and only auto-links bare URLs.
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]+)\]\((https?://[^\s)]+)\)")
# Markdown headings / blockquotes / list bullets at the start of a line.
_MARKDOWN_LINE_PREFIX = re.compile(r"(?m)^[ \t]*(?:#{1,6}|>|[-+])\s+")


def _sanitize_for_whatsapp(text: str) -> str:
    """Make agent/scraped text safe and readable on WhatsApp.

    WhatsApp only auto-links bare URLs and uses single `*`/`_`/`` ` `` as
    bold/italic/monospace toggles. LLM replies and scraped copy bring markdown
    that WhatsApp renders literally or mis-toggles, so we:

    - rewrite `[label](url)` links to "label (url)" (bare, clickable URL);
    - drop heading/blockquote/list markers at line starts;
    - strip stray `*_~`` formatting characters (footnote markers etc.);
    - collapse the run-on whitespace from scraped HTML.
    """
    delinked = _MARKDOWN_LINK.sub(r"\1 (\2)", text)
    deprefixed = _MARKDOWN_LINE_PREFIX.sub("", delinked)
    cleaned = _WHATSAPP_MARKDOWN_CHARS.sub(" ", deprefixed)
    return re.sub(r"\s+", " ", cleaned).strip()


_MAX_WHATSAPP_CARDS = 5

_HANDOFF_MESSAGE = "You're being connected to a human agent who will continue helping you here shortly. \U0001f9d1‍\U0001f4bc"
_SURVEY_MESSAGE = (
    "Thanks for chatting with our support team! How would you rate your experience? "
    "Reply with a number 1-5 (1 = poor, 5 = excellent)."
)
_CSAT_NUDGE = "Please reply with a number 1-5."
_CSAT_THANKS = (
    "Thank you for your feedback! \U0001f64f You're back with our automated assistant "
    "whenever you need anything."
)

_EMAIL_HANDOFF_MESSAGE = (
    "Thanks for reaching out. I'm connecting you with a specialist from our team "
    "who will reply to this email shortly."
)
_EMAIL_SURVEY_MESSAGE = (
    "Thanks for contacting Proton support! How would you rate your experience? "
    "Reply with a number from 1 to 5 (1 = poor, 5 = excellent)."
)
_EMAIL_CSAT_NUDGE = "Please reply with a single number from 1 to 5 to rate your experience."
_EMAIL_CSAT_THANKS = "Thank you for your feedback! Reply any time if you need further help."

_NO_REPLY_RE = re.compile(r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster)", re.IGNORECASE)


def _is_no_reply(email: str | None) -> bool:
    """True for automated/system senders we must never auto-reply to (loop guard)."""
    return bool(email and _NO_REPLY_RE.search(email))


def _card_caption(product: Any) -> str:
    """Build the caption for a single WhatsApp image card (bold title + details)."""
    caption = f"*{_sanitize_for_whatsapp(product.title)}*"
    price = _sanitize_for_whatsapp(getattr(product, "price", "") or "")
    if price:
        caption += f" — {price}"
    desc = _sanitize_for_whatsapp(getattr(product, "description", "") or "")
    if desc:
        caption += f"\n{desc}"
    if getattr(product, "url", None):
        caption += f"\n{product.url}"
    return caption


def _whatsapp_reply_text(turn_result: Any) -> str:
    """Flatten a turn's reply + product carousel into WhatsApp-friendly text.

    WhatsApp can't render the product carousel (it's a frontend widget), so the
    model cards are serialized as a numbered list — bold title (WhatsApp `*...*`),
    price, sanitized description, and link. Scraped text is sanitized so stray
    markdown characters don't garble the message. Other channels keep the rich
    carousel via `_products_list`.
    """
    text = _sanitize_for_whatsapp(turn_result.reply or "")
    products = getattr(turn_result, "products", None) or []
    if not products:
        return text

    blocks: list[str] = [text] if text else []
    for i, p in enumerate(products, start=1):
        title = _sanitize_for_whatsapp(p.title)
        header = f"*{i}. {title}*"
        price = _sanitize_for_whatsapp(p.price or "")
        if price:
            header += f" — {price}"
        block = [header]
        desc = _sanitize_for_whatsapp(p.description or "")
        if desc:
            block.append(desc)
        if p.url:
            block.append(p.url)
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def _products_list(turn_result: Any) -> list[dict[str, Any]]:
    return [
        {
            "title": p.title,
            "description": p.description,
            "image_url": p.image_url,
            "price": p.price,
            "url": p.url,
        }
        for p in turn_result.products
    ]


class ChatRouter:
    """Class-based router for CRM webhooks plus the frontend-facing chat/voice endpoints."""

    def __init__(
        self,
        orchestrator: OrchestratorService,
        handoff_bridge: HandoffBridge | None = None,
        human_agent_bridge: HumanAgentBridgePort | None = None,
        twilio_adapter: TwilioChannelAdapter | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self._handoff_bridge = handoff_bridge
        self._human_agent_bridge = human_agent_bridge
        self._twilio_adapter = twilio_adapter
        self.router = APIRouter(tags=["chat"])

        self.router.add_api_route("/webhooks/chatwoot", self.chatwoot_webhook, methods=["POST"])
        self.router.add_api_route("/webhooks/zendesk", self.zendesk_webhook, methods=["POST"])
        self.router.add_api_route("/webhooks/sunshine", self.sunshine_webhook, methods=["POST"])
        self.router.add_api_route(
            "/webhooks/zendesk-support", self.zendesk_support_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/webhooks/zendesk-handback", self.zendesk_handback_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/webhooks/zendesk-email", self.zendesk_email_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/webhooks/twilio-whatsapp", self.twilio_whatsapp_webhook, methods=["POST"]
        )
        self.router.add_api_route(
            "/chat/turn",
            self.chat_turn,
            methods=["POST"],
            response_model=ChatTurnResponse,
        )
        self.router.add_api_route("/chat/csat", self.chat_csat, methods=["POST"])
        self.router.add_api_route(
            "/chat/stream/{session_id}",
            self.chat_stream,
            methods=["GET"],
        )
        self.router.add_api_route(
            "/voice/turn",
            self.voice_turn,
            methods=["POST"],
        )
        self.router.add_api_route(
            "/voice/tts",
            self.voice_tts,
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

    async def twilio_whatsapp_webhook(self, request: Request) -> Response:
        """Inbound WhatsApp message from Twilio. Verify signature, run the AI,
        reply via Twilio REST, and mirror the conversation into Zendesk."""
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        token = self.orchestrator._settings.twilio_auth_token
        # Twilio signs the exact PUBLIC url it POSTs to. Behind a tunnel/proxy
        # (cloudflared, ngrok) request.url reports the local scheme/host, which
        # breaks the signature. Prefer the configured public base when set.
        base = self.orchestrator._settings.twilio_webhook_base_url
        verify_url = f"{base.rstrip('/')}/webhooks/twilio-whatsapp" if base else str(request.url)
        signature = request.headers.get("X-Twilio-Signature")
        if token and not verify_twilio_signature(token, verify_url, params, signature):
            _log.warning("twilio_whatsapp_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")

        from_addr = params.get("From", "")
        body = (params.get("Body") or "").strip()
        if not from_addr or not body:
            return Response(status_code=200)

        e164 = from_addr.replace("whatsapp:", "")
        session_id = f"whatsapp-{e164}"
        profile_name = params.get("ProfileName")
        _log.info("twilio_whatsapp_received", session_id=session_id)

        state = await self.orchestrator.whatsapp_state(session_id)
        if state == "awaiting_survey":
            await self._handle_whatsapp_survey(from_addr, session_id, body)
            return Response(status_code=200)
        if state == "paused":
            await self.orchestrator.forward_whatsapp_to_agent(session_id, body)
            return Response(status_code=200)

        result = await self.orchestrator.handle_turn(session_id=session_id, text=body)

        if await self.orchestrator.needs_whatsapp_handoff(session_id):
            summary = result.reply or "Customer requested a human agent."
            await self.orchestrator.begin_whatsapp_handoff(
                session_id, summary, customer_name=profile_name, customer_phone=e164
            )
            if self._twilio_adapter is not None:
                await self._twilio_adapter.send_message(
                    conversation_id=from_addr, text=_HANDOFF_MESSAGE
                )
        else:
            await self._send_whatsapp_reply(from_addr, result)

        await self.orchestrator.capture_conversation(
            session_id, channel="WhatsApp", customer_name=profile_name, customer_phone=e164
        )
        return Response(status_code=200)

    async def _handle_whatsapp_survey(self, from_addr: str, session_id: str, body: str) -> None:
        score = OrchestratorService.parse_csat(body)
        if score is not None:
            await self.orchestrator.record_csat(session_id, score, channel="WhatsApp")
            if self._twilio_adapter is not None:
                await self._twilio_adapter.send_message(
                    conversation_id=from_addr, text=_CSAT_THANKS
                )
            return
        # Invalid: nudge once, then stop trapping the customer.
        if await self.orchestrator.consume_survey_nudge(session_id):
            if self._twilio_adapter is not None:
                await self._twilio_adapter.send_message(conversation_id=from_addr, text=_CSAT_NUDGE)
            return
        # Second invalid → resume AI and process this message normally.
        await self.orchestrator.resume_ai(session_id)
        result = await self.orchestrator.handle_turn(session_id=session_id, text=body)
        await self._send_whatsapp_reply(from_addr, result)
        await self.orchestrator.capture_conversation(session_id, channel="WhatsApp")

    async def _send_whatsapp_reply(self, to: str, result: Any) -> None:
        """Send the AI reply to WhatsApp.

        When the turn produced model cards WITH images, send them as a sequence
        of image cards (lead-in text + one media message per model) — the closest
        thing to a carousel the WhatsApp sandbox allows without approved
        templates. Otherwise fall back to a single flattened text reply.
        """
        adapter = self._twilio_adapter
        if adapter is None:
            return

        products = getattr(result, "products", None) or []
        cards = [p for p in products if getattr(p, "image_url", None)][:_MAX_WHATSAPP_CARDS]
        if cards:
            lead = _sanitize_for_whatsapp(result.reply or "")
            if lead:
                await adapter.send_message(conversation_id=to, text=lead)
            for product in cards:
                await adapter.send_message(
                    conversation_id=to,
                    text=_card_caption(product),
                    media_url=product.image_url,
                )
            return

        text = _whatsapp_reply_text(result)
        if text:
            await adapter.send_message(conversation_id=to, text=text)

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

    async def sunshine_webhook(
        self,
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        """Receive a Sunshine Conversations webhook payload.

        We only fan out `business`-author message events — those represent the
        Zendesk agent replying from their workspace. Customer (`user`) events
        are echoes of what the frontend already showed when the user pressed
        Send, so we ignore them.
        """
        if self._handoff_bridge is None or self._human_agent_bridge is None:
            raise HTTPException(status_code=503, detail="Handoff bridge not configured")

        body = await request.body()
        if not self._human_agent_bridge.verify_webhook_signature(body, x_api_key):
            _log.warning("sunshine_webhook_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON") from None

        events = self._human_agent_bridge.parse_webhook_events(payload)
        for event in events:
            await self._handoff_bridge.publish(event)
            await self._relay_agent_reply_to_whatsapp(event)

        _log.info("sunshine_webhook_received", event_count=len(events))
        return {"status": "ok", "delivered": str(len(events))}

    async def _relay_agent_reply_to_whatsapp(self, event: AgentMessageEvent) -> None:
        """Bridge a human agent's Sunshine reply back to the customer's WhatsApp.

        Web chat receives agent replies over SSE (`/chat/stream/{session_id}`);
        WhatsApp has no such subscriber, so for `whatsapp-*` sessions we push the
        reply out through Twilio. Other channels are unaffected.
        """
        if self._twilio_adapter is None or self._handoff_bridge is None:
            return
        session_id = await self._handoff_bridge.session_id_for(event.conversation_id)
        if not session_id or not session_id.startswith("whatsapp-"):
            return
        to = "whatsapp:" + session_id[len("whatsapp-") :]
        await self._twilio_adapter.send_message(
            conversation_id=to, text=_sanitize_for_whatsapp(event.text)
        )

    async def zendesk_support_webhook(
        self,
        payload: ZendeskSupportWebhookPayload,
        x_proton_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, str]:
        """Receive a Zendesk Support webhook comment payload.

        Triggers on public agent replies on standard Support tickets, and
        relays them to the active SSE channel.
        """
        if self._handoff_bridge is None:
            raise HTTPException(status_code=503, detail="Handoff bridge not configured")

        expected_secret = self.orchestrator._settings.zendesk_support_webhook_secret
        if expected_secret and (
            not x_proton_webhook_secret or x_proton_webhook_secret != expected_secret
        ):
            _log.warning("zendesk_support_webhook_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")

        session_id = payload.session_id
        if not session_id or not session_id.strip():
            return {"status": "ignored"}

        if session_id.startswith("whatsapp-") and self._twilio_adapter is not None:
            to = "whatsapp:" + session_id[len("whatsapp-") :]
            await self._twilio_adapter.send_message(
                conversation_id=to, text=_sanitize_for_whatsapp(payload.text)
            )
            return {"status": "relayed"}

        if not session_id.startswith("sim-"):
            return {"status": "ignored"}

        # Check if the session is actually handed off / active in our bridge
        is_active = await self._handoff_bridge.is_handed_off(session_id)
        if not is_active:
            _log.info("zendesk_support_webhook_session_not_active", session_id=session_id)
            return {"status": "session_not_active"}

        conversation_id = await self._handoff_bridge.conversation_id_for(session_id)
        if not conversation_id:
            conversation_id = session_id

        event = AgentMessageEvent(
            conversation_id=conversation_id,
            author_name=payload.author_name,
            text=payload.text,
            timestamp=datetime.now(UTC),
        )

        # Populate cache mapping if missing
        if conversation_id not in self._handoff_bridge._conv_cache:
            self._handoff_bridge._conv_cache[conversation_id] = session_id
            self._handoff_bridge._session_cache[session_id] = conversation_id

        await self._handoff_bridge.publish(event)
        _log.info("zendesk_support_webhook_processed", session_id=session_id)
        return {"status": "ok"}

    async def zendesk_handback_webhook(
        self,
        payload: ZendeskHandbackPayload,
        x_proton_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, str]:
        """Receive a Zendesk/CRM ticket status update webhook.

        When a ticket is resolved or closed, this webhook unpauses the AI
        for the session so the bot takes back control of subsequent messages.
        """
        expected_secret = self.orchestrator._settings.zendesk_support_webhook_secret
        if expected_secret and (
            not x_proton_webhook_secret or x_proton_webhook_secret != expected_secret
        ):
            _log.warning("zendesk_handback_webhook_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")

        session_id = payload.session_id
        if not session_id or not session_id.strip():
            return {"status": "ignored"}

        _log.info("zendesk_handback_webhook_received", session_id=session_id, status=payload.status)

        # Unpause AI for the session
        await self.orchestrator._ticketing_port.unpause_ai_for_session(session_id)

        if session_id.startswith("whatsapp-"):
            # The handback trigger fires on EVERY update to a solved ticket
            # (including our own CSAT comment + tag writes), so only start the
            # survey on a genuine handoff (paused) -> solved transition. Once the
            # survey is sent (awaiting_survey) or completed (active), re-fires are
            # ignored — preventing duplicate survey messages.
            if await self.orchestrator.conversation_state(session_id) == "paused":
                await self.orchestrator.begin_survey(session_id)
                if self._twilio_adapter is not None:
                    to = "whatsapp:" + session_id[len("whatsapp-") :]
                    await self._twilio_adapter.send_message(
                        conversation_id=to, text=_SURVEY_MESSAGE
                    )
        elif session_id.startswith("email-"):
            # Only on a genuine handoff (paused) -> solved transition; re-fires
            # from our own survey/CSAT writes are end-user-agnostic and ignored.
            if await self.orchestrator.conversation_state(session_id) == "paused":
                ticket_id = session_id[len("email-") :]
                await self.orchestrator.begin_survey(session_id)
                await self.orchestrator._conversation_log_port.post_public_reply(
                    ticket_id, _EMAIL_SURVEY_MESSAGE
                )
        else:
            await self.orchestrator.begin_survey(session_id)
            if self._handoff_bridge is not None:
                await self._handoff_bridge.publish_survey(session_id)

        # Close the live agent stream AFTER the survey event is queued, so the
        # web client receives the survey event before the stream terminates.
        if self._handoff_bridge is not None:
            await self._handoff_bridge.unregister(session_id)

        return {"status": "unpaused", "session_id": session_id}

    async def zendesk_email_webhook(
        self,
        payload: ZendeskEmailWebhookPayload,
        x_proton_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, str]:
        """Inbound customer email, delivered by a Zendesk trigger.

        The ticket IS the conversation (session_id = email-<ticket_id>). We run
        the AI, post the reply as a public comment (Zendesk emails it), and route
        by conversation state. Paused sessions are handled by a human in Zendesk,
        so we no-op.
        """
        expected_secret = self.orchestrator._settings.zendesk_support_webhook_secret
        if expected_secret and (
            not x_proton_webhook_secret or x_proton_webhook_secret != expected_secret
        ):
            _log.warning("zendesk_email_webhook_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")

        ticket_id = payload.ticket_id.strip()
        text = (payload.text or "").strip()
        if not ticket_id or not text:
            return {"status": "ignored"}
        if _is_no_reply(payload.requester_email):
            _log.info("zendesk_email_webhook_no_reply_skipped", ticket_id=ticket_id)
            return {"status": "ignored"}

        session_id = f"email-{ticket_id}"
        _log.info("zendesk_email_received", session_id=session_id)

        state = await self.orchestrator.conversation_state(session_id)
        if state == "awaiting_survey":
            await self._handle_email_survey(ticket_id, session_id, text)
            return {"status": "ok"}
        if state == "paused":
            return {"status": "paused"}

        result = await self.orchestrator.handle_turn(session_id=session_id, text=text)
        await self.orchestrator.bind_email_ticket(session_id, ticket_id)

        port = self.orchestrator._conversation_log_port
        if await self.orchestrator.needs_handoff(session_id):
            summary = result.reply or "Customer requested a human agent."
            await self.orchestrator.begin_handoff(
                session_id, summary, channel="email", customer_name=payload.requester_name
            )
            await port.post_public_reply(ticket_id, _EMAIL_HANDOFF_MESSAGE)
        else:
            reply = result.reply or ""
            if reply:
                if self.orchestrator._settings.email_draft_assist:
                    await port.append_conversation_comment(ticket_id, f"[AI draft]\n{reply}")
                else:
                    await port.post_public_reply(ticket_id, reply, status="pending")
            else:
                _log.warning("zendesk_email_empty_reply", session_id=session_id)
        return {"status": "ok"}

    async def _handle_email_survey(self, ticket_id: str, session_id: str, body: str) -> None:
        port = self.orchestrator._conversation_log_port
        score = OrchestratorService.parse_csat(body)
        if score is not None:
            await self.orchestrator.record_csat(session_id, score, channel="email")
            await port.post_public_reply(ticket_id, _EMAIL_CSAT_THANKS, status="solved")
            return
        if await self.orchestrator.consume_survey_nudge(session_id):
            await port.post_public_reply(ticket_id, _EMAIL_CSAT_NUDGE)
            return
        # Second invalid → resume AI and process this message as a normal turn.
        await self.orchestrator.resume_ai(session_id)
        result = await self.orchestrator.handle_turn(session_id=session_id, text=body)
        await self.orchestrator.bind_email_ticket(session_id, ticket_id)
        reply = getattr(result, "reply", "") or ""
        if reply:
            await port.post_public_reply(ticket_id, reply, status="pending")

    # --- Frontend-facing API (consumed by the Vue app) ----------------------

    async def chat_turn(self, req: ChatTurnRequest) -> ChatTurnResponse:
        """Run a single text chat turn and return the assistant's reply."""
        _log.info("chat_turn_received", session_id=req.session_id, text_length=len(req.text))
        result = await self.orchestrator.handle_turn(session_id=req.session_id, text=req.text)
        return ChatTurnResponse(
            reply=result.reply,
            language=result.language,
            sentiment=result.sentiment,
            handoff=_handoff_dict(result),
            forwarded_to_agent=result.forwarded_to_agent,
            products=_products_list(result),
        )

    async def chat_csat(self, payload: CsatRequest) -> dict[str, str]:
        await self.orchestrator.record_csat(payload.session_id, payload.score, channel="web")
        return {
            "status": "ok",
            "message": "Thank you for your feedback!",
        }

    async def chat_stream(self, session_id: str) -> StreamingResponse:
        """SSE stream of human-agent messages for a handed-off session.

        Emits one event per agent message. A 30-second heartbeat keeps the
        connection alive through HTTP proxies. The stream terminates when the
        session is unregistered (e.g. `New session` clicked) or when the
        client disconnects.
        """
        if self._handoff_bridge is None:
            raise HTTPException(status_code=503, detail="Live chat bridge disabled")

        bridge = self._handoff_bridge

        async def event_source() -> AsyncIterator[bytes]:
            queue = bridge.subscribe(session_id)
            try:
                yield b": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    except TimeoutError:
                        yield b": ping\n\n"
                        continue

                    if event is None:
                        # Session unregistered — close the stream.
                        return

                    if isinstance(event, SurveyEvent):
                        yield b'event: survey\ndata: {"type": "survey"}\n\n'
                        continue

                    data = json.dumps(
                        {
                            "type": "agent_message",
                            "author_name": event.author_name,
                            "text": event.text,
                            "timestamp": event.timestamp.isoformat(),
                        }
                    )
                    yield f"event: agent_message\ndata: {data}\n\n".encode()
            finally:
                bridge.unsubscribe(session_id, queue)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async def voice_turn(
        self,
        session_id: str = Form(...),
        audio: UploadFile = File(...),  # noqa: B008  (FastAPI file injection)
    ) -> Response:
        """Run a single voice turn. Returns MP3 audio with X-Reply-Text + X-Handoff-* headers."""
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
            headers["X-Handoff-Urgency"] = turn_result.handoff.urgency
            headers["X-Handoff-Language"] = turn_result.handoff.language
            headers["X-Handoff-Live-Chat"] = "1" if turn_result.handoff.live_chat_available else "0"
            if turn_result.handoff.summary:
                headers["X-Handoff-Summary"] = quote(turn_result.handoff.summary, safe="")

        if turn_result.forwarded_to_agent:
            headers["X-Forwarded-To-Agent"] = "1"
        if turn_result.user_transcription:
            headers["X-User-Transcription"] = quote(turn_result.user_transcription, safe="")

        return Response(content=audio_reply, media_type="audio/mpeg", headers=headers)

    async def voice_tts(self, payload: TtsRequest) -> Response:
        """Synthesize text into speech audio."""
        _log.info("voice_tts_received", text_length=len(payload.text), language=payload.language)
        try:
            audio_bytes = await self.orchestrator._tts_port.synthesize(
                text=payload.text, language_code=payload.language
            )
            return Response(content=audio_bytes, media_type="audio/mpeg")
        except Exception as e:
            _log.error("voice_tts_synthesis_failed", error=str(e))
            raise HTTPException(status_code=500, detail="Voice synthesis failed") from e


def build_chat_router(
    orchestrator: OrchestratorService,
    handoff_bridge: HandoffBridge | None = None,
    human_agent_bridge: HumanAgentBridgePort | None = None,
    twilio_adapter: TwilioChannelAdapter | None = None,
) -> APIRouter:
    """Builds and returns the configured FastAPI router instance."""
    return ChatRouter(
        orchestrator=orchestrator,
        handoff_bridge=handoff_bridge,
        human_agent_bridge=human_agent_bridge,
        twilio_adapter=twilio_adapter,
    ).router
