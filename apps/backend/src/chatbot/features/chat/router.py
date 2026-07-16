from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import structlog
from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
from chatbot.features.chat.case_state import CaseState, record_transition
from chatbot.features.chat.handoff_bridge import HandoffBridge, SurveyEvent
from chatbot.features.chat.models import AgentMessageEvent
from chatbot.features.chat.phone.bridge import PhoneBridge
from chatbot.features.chat.phone.gemini_live import LiveSession, connect_live
from chatbot.features.chat.phone.handoff_csat_tools import REQUEST_HANDOFF_TOOL, SUBMIT_CSAT_TOOL
from chatbot.features.chat.phone.kb_tool import KB_SEARCH_TOOL
from chatbot.features.chat.phone.rate_limit import RateLimiter
from chatbot.features.chat.phone.token import mint_voice_token
from chatbot.features.chat.phone.twiml import connect_stream_twiml
from chatbot.features.chat.ports import AuditLogPort, HumanAgentBridgePort
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


class NpsRequest(BaseModel):
    session_id: str
    score: int = Field(ge=0, le=10)


class SlaEscalationPayload(BaseModel):
    ticket_id: str
    session_id: str = ""
    level: str = "escalate"
    pic_whatsapp: str = ""
    remark: str = ""


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


# Convert (rather than strip) Markdown to WhatsApp formatting for the agent reply,
# so answers stay structured/scannable: **x**/__x__ -> *x* (WhatsApp bold),
# `# Heading` -> *Heading*, `- item`/`* item` -> `• item`, links -> "label (url)".
# Line breaks are preserved (only intra-line space runs and 3+ blank lines collapse).
_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_MD_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$")
_MD_BLOCKQUOTE = re.compile(r"(?m)^[ \t]*>[ \t]?")
_MD_BULLET = re.compile(r"(?m)^[ \t]*[-+*][ \t]+")
_INLINE_WS = re.compile(r"[^\S\n]{2,}")
_MANY_NEWLINES = re.compile(r"\n{3,}")


def _md_to_whatsapp(text: str) -> str:
    """Render the agent's Markdown reply as WhatsApp-native formatting.

    Unlike `_sanitize_for_whatsapp` (which strips structure from noisy scraped
    card copy), this preserves the reply's structure — bold, headings, bullet
    lists, and line breaks — converted to what WhatsApp actually renders.
    """
    t = _MARKDOWN_LINK.sub(r"\1 (\2)", text)
    t = _MD_BOLD.sub(r"*\2*", t)
    t = _MD_HEADING.sub(r"*\1*", t)
    t = _MD_BLOCKQUOTE.sub("", t)
    t = _MD_BULLET.sub("• ", t)
    t = _INLINE_WS.sub(" ", t)
    t = _MANY_NEWLINES.sub("\n\n", t)
    return "\n".join(line.rstrip() for line in t.split("\n")).strip()


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
    text = _md_to_whatsapp(turn_result.reply or "")
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


_LiveFactory = Callable[..., AbstractAsyncContextManager[LiveSession]]


class ChatRouter:
    """Class-based router for CRM webhooks plus the frontend-facing chat/voice endpoints."""

    def __init__(
        self,
        orchestrator: OrchestratorService,
        handoff_bridge: HandoffBridge | None = None,
        human_agent_bridge: HumanAgentBridgePort | None = None,
        twilio_adapter: TwilioChannelAdapter | None = None,
        live_session_factory: _LiveFactory | None = None,
        audit_log: AuditLogPort | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self._handoff_bridge = handoff_bridge
        self._human_agent_bridge = human_agent_bridge
        self._twilio_adapter = twilio_adapter
        self._live_session_factory: _LiveFactory = live_session_factory or connect_live
        self._audit_log = audit_log
        self._phone_token_limiter = RateLimiter(
            orchestrator._settings.phone_token_rate_limit,
            orchestrator._settings.phone_token_rate_window_seconds,
        )
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
            "/webhooks/zendesk-sla-escalation",
            self.sla_escalation_webhook,
            methods=["POST"],
        )
        self.router.add_api_route(
            "/cases/{ticket_id}/audit",
            self.case_audit,
            methods=["GET"],
        )
        self.router.add_api_route(
            "/chat/turn",
            self.chat_turn,
            methods=["POST"],
            response_model=ChatTurnResponse,
        )
        self.router.add_api_route("/chat/csat", self.chat_csat, methods=["POST"])
        self.router.add_api_route("/chat/nps", self.chat_nps, methods=["POST"])
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
        self.router.add_api_route("/voice/phone/incoming", self.phone_incoming, methods=["POST"])
        self.router.add_api_route("/voice/phone/token", self.phone_token, methods=["POST"])
        self.router.add_api_websocket_route("/voice/phone/stream", self.phone_stream)

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

    async def sla_escalation_webhook(
        self,
        payload: SlaEscalationPayload,
        x_proton_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, str]:
        """SLA escalation webhook — records an audit transition and optionally alerts PIC via WhatsApp."""
        expected = self.orchestrator._settings.sla_webhook_secret
        if expected and (not x_proton_webhook_secret or x_proton_webhook_secret != expected):
            _log.warning("sla_escalation_webhook_signature_invalid")
            raise HTTPException(status_code=401, detail="Invalid signature")
        if self._audit_log is None:
            raise HTTPException(status_code=503, detail="Audit log not configured")
        to_state = CaseState.WIP if payload.level == "escalate" else CaseState.PENDING
        await record_transition(
            self._audit_log,
            ticket_id=payload.ticket_id,
            session_id=payload.session_id,
            actor="sla-automation",
            from_state=CaseState.OPEN,
            to_state=to_state,
            at=datetime.now(UTC).isoformat(),
            remark=payload.remark or f"SLA {payload.level}",
        )
        if payload.pic_whatsapp and self._twilio_adapter is not None:
            await self._twilio_adapter.send_message(
                conversation_id="whatsapp:" + payload.pic_whatsapp.removeprefix("whatsapp:"),
                text=f"⚠️ SLA {payload.level} on case {payload.ticket_id}. Please action.",
            )
        return {"status": "recorded"}

    async def case_audit(self, ticket_id: str) -> dict[str, Any]:
        """Retrieve audit trail for a case (ticket)."""
        if self._audit_log is None:
            raise HTTPException(status_code=503, detail="Audit log not configured")
        rows = await self._audit_log.list_for_ticket(ticket_id)
        return {"ticket_id": ticket_id, "audit": [asdict(r) for r in rows]}

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
        if not ticket_id:
            return {"status": "ignored"}
        session_id = f"email-{ticket_id}"

        state = await self.orchestrator.conversation_state(session_id)
        if state == "paused":
            return {"status": "paused"}

        # The trigger sends only the ticket id (safe JSON); fetch the customer's
        # message from Zendesk. Payload text, if present, is preferred (tests / legacy).
        text = (payload.text or "").strip()
        requester_name = payload.requester_name
        requester_email = payload.requester_email
        if not text:
            (
                fetched_text,
                fetched_name,
                fetched_email,
            ) = await self.orchestrator._conversation_log_port.get_latest_public_comment(ticket_id)
            text = (fetched_text or "").strip()
            requester_name = requester_name or fetched_name
            requester_email = requester_email or fetched_email
        if not text:
            return {"status": "ignored"}
        if _is_no_reply(requester_email):
            _log.info("zendesk_email_webhook_no_reply_skipped", ticket_id=ticket_id)
            return {"status": "ignored"}

        # Dedup guard: a Zendesk trigger can fire more than once for one comment
        # (create+update, retries), and if the requester happens to BE a Zendesk
        # agent, the AI's own public reply is re-delivered as customer input. In
        # both cases the fetched text equals either the last inbound we processed
        # or the last reply we posted — skip it so the AI never answers itself or
        # double-answers a message.
        last_inbound, last_reply = await self.orchestrator.get_email_dedup(session_id)
        if text in (last_inbound, last_reply):
            _log.info("zendesk_email_webhook_duplicate_skipped", ticket_id=ticket_id)
            return {"status": "duplicate"}

        # Claim this inbound BEFORE the (slow, multi-second) AI turn. Zendesk can
        # re-fire the trigger several times for one email, all arriving while the
        # first turn is still running; writing the marker now means those re-fires
        # hit the dedup check above and skip instead of racing through.
        await self.orchestrator.remember_email_exchange(session_id, inbound=text)

        _log.info("zendesk_email_received", session_id=session_id)
        return await self._handle_email_turn(ticket_id, session_id, state, text, requester_name)

    async def _handle_email_turn(
        self,
        ticket_id: str,
        session_id: str,
        state: str,
        text: str,
        requester_name: str | None,
    ) -> dict[str, str]:
        """Run the AI turn for a (deduped, non-paused) inbound email and post the
        reply, routing by conversation state. The inbound is already recorded by
        the caller; here we record the reply so it is never fed back as input."""
        if state == "awaiting_survey":
            await self._handle_email_survey(ticket_id, session_id, text)
            return {"status": "ok"}

        result = await self.orchestrator.handle_turn(session_id=session_id, text=text)
        await self.orchestrator.bind_email_ticket(session_id, ticket_id)

        port = self.orchestrator._conversation_log_port
        if await self.orchestrator.needs_handoff(session_id):
            summary = result.reply or "Customer requested a human agent."
            await self.orchestrator.begin_handoff(
                session_id, summary, channel="email", customer_name=requester_name
            )
            await port.post_public_reply(ticket_id, _EMAIL_HANDOFF_MESSAGE)
            await self.orchestrator.remember_email_exchange(
                session_id, reply=_EMAIL_HANDOFF_MESSAGE
            )
        else:
            await self._post_email_active_reply(ticket_id, session_id, result.reply or "")
            await self.orchestrator.remember_email_exchange(session_id, reply=result.reply or "")
        return {"status": "ok"}

    async def _post_email_active_reply(self, ticket_id: str, session_id: str, reply: str) -> None:
        port = self.orchestrator._conversation_log_port
        if not reply:
            _log.warning("zendesk_email_empty_reply", session_id=session_id)
            return
        if self.orchestrator._settings.email_draft_assist:
            await port.append_conversation_comment(ticket_id, f"[AI draft]\n{reply}")
        else:
            await port.post_public_reply(ticket_id, reply, status="pending")

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

    async def chat_nps(self, payload: NpsRequest) -> dict[str, str]:
        await self.orchestrator.record_nps(payload.session_id, payload.score, channel="web")
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

    def _phone_wss_url(self) -> str:
        base = self.orchestrator._settings.public_wss_base_url
        if not base:
            https = self.orchestrator._settings.twilio_webhook_base_url
            base = https.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base.rstrip('/')}/voice/phone/stream"

    async def phone_incoming(self) -> Response:
        """Twilio Voice webhook: bridge the call into a Media Stream."""
        xml = connect_stream_twiml(self._phone_wss_url())
        return Response(content=xml, media_type="application/xml")

    async def phone_token(self, request: Request) -> dict[str, str]:
        """Mint a Twilio Voice access token for the browser softphone.

        Unauthenticated by nature (a public SPA calls it), so it is hardened with
        defense-in-depth: an Origin allowlist (blocks cross-site browser abuse), a
        per-IP rate limit (bounds the billing blast radius), and a short token TTL
        (set in mint_voice_token). None is airtight alone; together they make
        leaked-token abuse low-value and self-limiting.
        """
        settings = self.orchestrator._settings
        origin = request.headers.get("origin")
        if origin not in settings.frontend_origins:
            _log.warning("phone_token_origin_rejected", origin=origin)
            raise HTTPException(status_code=403, detail="Origin not allowed")
        client_ip = request.client.host if request.client else "unknown"
        if not self._phone_token_limiter.allow(client_ip):
            _log.warning("phone_token_rate_limited", client_ip=client_ip)
            raise HTTPException(status_code=429, detail="Too many token requests")
        identity = "proton-web-caller"
        token = mint_voice_token(settings, identity)
        return {"token": token, "identity": identity}

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

    async def phone_stream(self, websocket: WebSocket) -> None:
        """WebSocket endpoint that Twilio Media Streams connects to.

        Opens a Gemini Live session, constructs a PhoneBridge, then:
        1. Reads the mandatory Twilio "start" event synchronously so stream_sid
           is guaranteed to be set before the pump begins forwarding audio frames.
        2. Runs two concurrent tasks: receiving remaining Twilio messages and
           pumping Gemini live events back to Twilio.
        3. On either task completing (pump exhausted or caller hung up), cancels
           the pending task and finalises the bridge.
        """
        await websocket.accept()
        system_instruction = (
            "You are Proton's friendly phone support agent. Answer spoken questions "
            "concisely and naturally. You are fully multilingual: ALWAYS reply in the SAME "
            "language the caller uses — English, Bahasa Melayu, or Chinese — and switch "
            "immediately if they switch. You speak Bahasa Melayu fluently; NEVER say you cannot "
            "speak a language and NEVER hand off merely because of language. "
            "Use the kb_search tool to ground answers in the "
            "Proton knowledge base before giving facts. If you cannot resolve the caller's "
            "issue, they ask for a human, or it is a complaint or sensitive matter, call "
            "request_human_handoff with a short reason and summary, then tell the caller a "
            "specialist will follow up. After you answer, ask if there is anything else you can "
            "help with, and keep helping across as many questions as the caller has. Do NOT ask "
            "for a rating mid-conversation. ONLY once the caller clearly signals they are finished "
            "(e.g. 'no, that's all', 'nothing else', 'goodbye'), ask 'How would you rate your "
            "experience from 1 to 5?' then STOP and wait — do not thank them, wrap up, or say "
            "anything else until the caller replies with a number. Only after they say a number, "
            "call submit_csat with it and then give a brief thank-you. Do NOT ask for a rating if "
            "you handed off to a human. No markdown — this is spoken aloud."
        )
        try:
            async with self._live_session_factory(
                self.orchestrator._settings,
                system_instruction,
                [KB_SEARCH_TOOL, REQUEST_HANDOFF_TOOL, SUBMIT_CSAT_TOOL],
            ) as live:
                bridge = PhoneBridge(
                    live,
                    self.orchestrator._knowledge_port,
                    self.orchestrator._conversation_log_port,
                    websocket.send_json,
                )

                # The Twilio Media Streams protocol always sends a "start" event
                # as the very first message. Handle it synchronously here so that
                # bridge.stream_sid is set before pump_task begins forwarding
                # audio frames — avoiding a race between the two concurrent tasks.
                start_raw = await websocket.receive_text()
                await bridge.handle_twilio(json.loads(start_raw))

                async def from_twilio() -> None:
                    while True:
                        raw = await websocket.receive_text()
                        await bridge.handle_twilio(json.loads(raw))

                twilio_task = asyncio.create_task(from_twilio())
                pump_task = asyncio.create_task(bridge.pump())
                done, pending = await asyncio.wait(
                    {pump_task, twilio_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                # Retrieve the completed task's exception so it isn't lost: a normal
                # caller hangup surfaces as WebSocketDisconnect (expected, ignore);
                # anything else is a real error worth logging.
                for t in done:
                    if not t.cancelled():
                        exc = t.exception()
                        if exc is not None and not isinstance(exc, WebSocketDisconnect):
                            _log.error("phone_stream_task_error", error=str(exc))
                await bridge.finalize()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            _log.error("phone_stream_failed", error=str(e))


def build_chat_router(
    orchestrator: OrchestratorService,
    handoff_bridge: HandoffBridge | None = None,
    human_agent_bridge: HumanAgentBridgePort | None = None,
    twilio_adapter: TwilioChannelAdapter | None = None,
    live_session_factory: _LiveFactory | None = None,
    audit_log: AuditLogPort | None = None,
) -> APIRouter:
    """Builds and returns the configured FastAPI router instance."""
    return ChatRouter(
        orchestrator=orchestrator,
        handoff_bridge=handoff_bridge,
        human_agent_bridge=human_agent_bridge,
        twilio_adapter=twilio_adapter,
        live_session_factory=live_session_factory,
        audit_log=audit_log,
    ).router
