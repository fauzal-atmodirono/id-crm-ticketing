from __future__ import annotations

import asyncio
import hmac
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
import structlog
from google.cloud import firestore

from chatbot.features.chat.models import AgentMessageEvent, HandoffOpenPayload
from chatbot.features.chat.ports import (
    ChatPort,
    ConversationLogPort,
    ConversationLogResult,
    HumanAgentBridgePort,
    TicketingPort,
)

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.zammad import ZammadClient
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_PRIORITY_MAP = {"low": "low", "medium": "medium", "high": "high", "urgent": "urgent"}

# Conversation statuses that still count as "live" — an existing one is reused so
# a returning customer stays in the same thread. A resolved conversation is a
# closed ticket, so the next contact opens a fresh one instead.
_ACTIVE_STATUSES = frozenset({"open", "pending", "snoozed"})


def _conversations_from(res: Any) -> list[dict[str, Any]]:
    """Normalize a Chatwoot conversation-list response to a list of dicts.

    The account API wraps results as ``{"payload": [...]}``; some endpoints
    return a bare array. Tolerate both.
    """
    if isinstance(res, list):
        return [c for c in res if isinstance(c, dict)]
    if isinstance(res, dict):
        payload = res.get("payload")
        if isinstance(payload, list):
            return [c for c in payload if isinstance(c, dict)]
    return []


def _contact_id_from(res: dict[str, Any] | None) -> int | None:
    """Extract a contact id from a Chatwoot contact-create response.

    The account-level create returns ``{"payload": {"contact": {"id": ...}}}``;
    older/other shapes put ``id`` at the top level.
    """
    if not res:
        return None
    contact = (
        res.get("payload", {}).get("contact") if isinstance(res.get("payload"), dict) else None
    )
    candidate = (contact or res).get("id")
    return int(candidate) if candidate is not None else None


class ChatwootAdapter(ChatPort, TicketingPort, ConversationLogPort, HumanAgentBridgePort):
    """Chatwoot-backed adapter. A Chatwoot *conversation* is the unit of work:
    escalation = an assigned, prioritized, labelled conversation (there is no
    ticket object). Conversations are keyed by ``source_id == session_id`` in the
    configured API-channel inbox.
    """

    def __init__(self, settings: Settings, zammad: ZammadClient | None = None) -> None:
        self._settings = settings
        self._zammad = zammad
        self._paused_sessions: set[str] = set()
        self._conv_by_session: dict[str, str] = {}

    def _direct_zammad_active(self) -> bool:
        """True when this adapter owns Zammad ticketing directly.

        In direct mode the backend POSTs the Zammad ticket itself and MUST NOT
        also apply the `escalate` complaint label — that label is what the
        external agent-service sync (if ever wired) turns into a ticket, so
        applying both would spawn a duplicate ticket.
        """
        return self._settings.zammad_direct_ticketing and self._zammad is not None

    def _synth_customer_email(self, session_id: str) -> str:
        """Deterministic synthetic email for a session's Chatwoot contact.

        Web/WhatsApp customers have no real email, but downstream ticketing keyed
        by email needs one. Sanitize the session id to a valid local-part.
        """
        local = re.sub(r"[^a-zA-Z0-9._-]", "", session_id) or "customer"
        return f"{local}@{self._settings.chatwoot_customer_email_domain}"

    def _escalation_labels(self) -> list[str]:
        """Labels applied to an escalated conversation.

        Comma-separated so one escalation can carry both our own marker
        (``ai-escalation``) and a label that triggers a downstream integration
        (e.g. the shared instance's ``escalate`` → Zammad-ticket sync).
        """
        return [
            label.strip()
            for label in self._settings.chatwoot_escalation_label.split(",")
            if label.strip()
        ]

    def _is_complaint(self, reason: str | None, urgency: str | None) -> bool:
        """A handoff is a complaint (-> back-office Zammad ticket) only when the AI
        flagged dissatisfaction or high urgency. A plain "talk to a human" request
        stays a live Chatwoot conversation, so agents aren't handed a confusing
        ticket for a conversation they should be having in the chat.
        """
        if urgency and urgency.strip().lower() in {"high", "urgent", "critical"}:
            return True
        if reason:
            complaint_reasons = {
                r.strip().lower()
                for r in self._settings.chatwoot_complaint_reasons.split(",")
                if r.strip()
            }
            if reason.strip().lower() in complaint_reasons:
                return True
        return False

    def _complaint_labels(self, reason: str | None, urgency: str | None) -> list[str]:
        """The Zammad-ticketing `escalate` label, applied only for complaints in
        LEGACY mode. When direct Zammad ticketing is active the backend creates
        the ticket itself, so this label is suppressed to avoid a double-ticket if
        the external agent-service sync is ever wired to it.
        """
        if self._direct_zammad_active():
            return []
        label = self._settings.chatwoot_complaint_label.strip()
        if label and self._is_complaint(reason, urgency):
            return [label]
        return []

    async def _escalate_to_zammad(
        self,
        conv_id: str,
        title: str,
        body: str,
        session_id: str,
        urgency: str | None,
        reason: str | None,
    ) -> None:
        """Create a Zammad ticket directly for a complaint and leave a Chatwoot
        private note linking to it. No-op when direct ticketing is inactive or the
        case is not a complaint. Errors are swallowed by ZammadClient (returns
        None) so the turn never breaks.
        """
        if not self._direct_zammad_active() or self._zammad is None:
            return
        if not self._is_complaint(reason, urgency):
            return
        ticket = await self._zammad.create_ticket(
            title=title,
            body=body,
            customer_email=self._synth_customer_email(session_id),
            priority=(urgency or "medium"),
        )
        if ticket is None:
            return
        zoom_url = f"{self._settings.zammad_api_url.rstrip('/')}/#ticket/zoom/{ticket.id}"
        await self.add_private_note(
            conv_id, f"Escalated to Zammad ticket #{ticket.number}: {zoom_url}"
        )

    @staticmethod
    def _dimension_labels(
        category: str | None,
        subcategory: str | None,
        division: str | None,
        department: str | None,
        sla_minutes: int | None,
    ) -> list[str]:
        """Encode the AI classification as Chatwoot conversation labels.

        Uses the SAME tag-name convention the Zendesk metrics ``mapping.py``
        already parses (``category_*``, ``subcat_*``, ``division_*``, ``dept_*``,
        ``sla_<int>``) so the batch sync can read the dimensions straight back
        off the conversation. These are merged into the SINGLE final labels call
        alongside the escalation labels — a separate labels POST would re-fire the
        conversation_updated webhook and spawn a duplicate Zammad ticket.
        """

        def _norm(v: str) -> str:
            return v.strip().lower().replace(" ", "_")

        labels: list[str] = []
        if category:
            labels.append(f"category_{_norm(category)}")
        if subcategory:
            labels.append(f"subcat_{_norm(subcategory)}")
        if division:
            labels.append(f"division_{_norm(division)}")
        if department:
            labels.append(f"dept_{_norm(department)}")
        if sla_minutes is not None:
            labels.append(f"sla_{sla_minutes}")
        return labels

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not self._settings.chatwoot_enabled:
            _log.info("chatwoot_disabled_skipping_request", path=path)
            return None
        url = f"{self._base()}{path}"
        # Send the token in BOTH header forms. Some reverse proxies (e.g. the
        # shared instance's) strip request headers containing underscores, which
        # would drop `api_access_token`; Rails maps the dash form to the same
        # value, so `Api-Access-Token` survives the proxy. Sending both works
        # whether Chatwoot is reached directly or through such a proxy.
        token = self._settings.chatwoot_api_token
        headers = {
            "Content-Type": "application/json",
            "api_access_token": token,
            "Api-Access-Token": token,
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(method, url, json=payload, headers=headers, timeout=10.0)
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("chatwoot_request_failed", method=method, path=path, error=str(e))
            return None

    async def _find_or_create_contact(
        self,
        session_id: str,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> int | None:
        """Return a Chatwoot contact id for the session, creating one if needed.

        The Application-API conversation create needs a contact to hang the
        conversation off (its ConversationBuilder requires a contact_inbox). We
        key the contact by ``identifier == session_id`` so repeat escalations
        for the same customer reuse one contact; a duplicate-identifier 422 on
        create is recovered by searching for the existing contact.
        """
        payload: dict[str, Any] = {
            "inbox_id": self._settings.chatwoot_inbox_id,
            "name": customer_name or "Customer",
            "identifier": session_id,
            "email": self._synth_customer_email(session_id),
        }
        if customer_phone:
            payload["phone_number"] = customer_phone
        res = await self._request("POST", "/contacts", payload)
        contact_id = _contact_id_from(res)
        if contact_id is not None:
            return contact_id
        # Create returned no id (most commonly a duplicate-identifier 422). Fall
        # back to searching for the contact we previously created. Encode the query
        # — WhatsApp session ids contain '+', which would otherwise decode to a
        # space server-side and miss the contact.
        search = await self._request(
            "GET", f"/contacts/search?q={quote(session_id, safe='')}", None
        )
        for contact in (search or {}).get("payload") or []:
            if contact.get("identifier") == session_id and contact.get("id") is not None:
                return int(contact["id"])
        return None

    async def _existing_conversation_id(self, contact_id: int | None) -> str | None:
        """Return an existing ACTIVE conversation id for this contact, if any.

        Only open/pending/snoozed conversations in our inbox are reused — a prior
        RESOLVED conversation is a closed ticket, so the next contact starts a
        fresh one. This is what prevents a cache-clearing restart from creating a
        duplicate live conversation for a session that already has one.
        """
        if contact_id is None:
            return None
        res = await self._request("GET", f"/contacts/{contact_id}/conversations", None)
        inbox_id = self._settings.chatwoot_inbox_id
        active = [
            c
            for c in _conversations_from(res)
            if c.get("status") in _ACTIVE_STATUSES and c.get("inbox_id") in (inbox_id, None)
        ]
        if not active:
            return None
        latest = max(active, key=lambda c: c.get("last_activity_at") or c.get("created_at") or 0)
        cid = latest.get("id")
        return str(cid) if cid is not None else None

    async def _find_or_create_conversation(
        self,
        session_id: str,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        *,
        search_existing: bool = True,
    ) -> str:
        """Return the conversation id for a session, creating it in the API-channel
        inbox if it does not exist yet.

        Passing ``contact_id`` + ``inbox_id`` + ``source_id == session_id`` lets
        Chatwoot build the contact_inbox for us while keeping the source_id equal
        to the session id, so the mapping stays deterministic. On a cache miss we
        first reuse an existing active conversation (``search_existing``) so a
        restart does not create a duplicate; ``rotate_conversation_ticket`` sets it
        False to force a brand-new conversation.
        """
        if session_id in self._conv_by_session:
            return self._conv_by_session[session_id]
        contact_id = await self._find_or_create_contact(session_id, customer_name, customer_phone)
        if search_existing:
            existing = await self._existing_conversation_id(contact_id)
            if existing is not None:
                self._conv_by_session[session_id] = existing
                return existing
        payload: dict[str, Any] = {
            "source_id": session_id,
            "inbox_id": self._settings.chatwoot_inbox_id,
        }
        if contact_id is not None:
            payload["contact_id"] = contact_id
        res = await self._request("POST", "/conversations", payload)
        if res and "id" in res:
            conv_id = str(res["id"])
            self._conv_by_session[session_id] = conv_id
            return conv_id
        # No id back. When Chatwoot is enabled this is a real failure (network,
        # auth, bad inbox) — make it observable and do NOT cache the fallback so
        # a later turn retries the create instead of firing 404s at a bogus id.
        if self._settings.chatwoot_enabled:
            _log.warning("chatwoot_conversation_create_failed", session_id=session_id)
        return session_id

    # --- ChatPort ---
    async def send_message(self, conversation_id: str, text: str) -> None:
        _log.info("sending_chatwoot_message", conversation_id=conversation_id)
        await self._request(
            "POST",
            f"/conversations/{conversation_id}/messages",
            {"content": text, "message_type": "outgoing"},
        )

    # --- TicketingPort ---
    async def create_ticket(
        self,
        session_id: str,
        title: str,
        body: str,
        urgency: str,
        customer_name: str | None = None,
        customer_email: str | None = None,  # noqa: ARG002
        customer_phone: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        division: str | None = None,
        department: str | None = None,
        sla_minutes: int | None = None,
    ) -> str:
        conv_id = await self._find_or_create_conversation(session_id, customer_name, customer_phone)
        # Post the customer's issue as an INCOMING message (before labelling, which
        # is what fires the webhook) so a downstream sync can identify the customer
        # from an incoming message and title the ticket.
        await self.forward_customer_message(conv_id, session_id, title)
        priority = _PRIORITY_MAP.get(urgency.lower(), "medium")
        await self._request(
            "POST", f"/conversations/{conv_id}/toggle_priority", {"priority": priority}
        )
        if self._settings.chatwoot_agent_team_id:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/assignments",
                {"team_id": self._settings.chatwoot_agent_team_id},
            )
        await self.add_private_note(conv_id, f"[AI escalation] {title}\n\n{body}")
        # Direct Zammad ticketing: on a complaint, POST the back-office ticket
        # ourselves and drop a Chatwoot private note linking to it (instead of
        # relying on the `escalate` label + external agent-service sync).
        await self._escalate_to_zammad(conv_id, title, body, session_id, urgency, None)
        # Also persist sla_minutes as a custom attribute — a plain number is
        # cleaner there than encoded in a label, and it survives even if the label
        # is later edited by an agent.
        if sla_minutes is not None:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/custom_attributes",
                {"custom_attributes": {"sla_minutes": sla_minutes}},
            )
        # Apply the escalation labels LAST: a downstream sync escalates on a
        # conversation_updated carrying the escalate label, so nothing must update
        # the conversation after this or each update re-triggers a duplicate ticket.
        # The AI-classification dimension labels ride in this SAME single call so
        # the batch metrics sync can read them back — a separate labels POST would
        # re-fire the webhook and spawn a duplicate Zammad ticket.
        dimension_labels = self._dimension_labels(
            category, subcategory, division, department, sla_minutes
        )
        await self._request(
            "POST",
            f"/conversations/{conv_id}/labels",
            {
                "labels": list(
                    dict.fromkeys(
                        dimension_labels
                        + self._escalation_labels()
                        + self._complaint_labels(None, urgency)
                    )
                )
            },
        )
        return conv_id

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        _log.info("adding_chatwoot_private_note", ticket_id=ticket_id)
        await self._request(
            "POST",
            f"/conversations/{ticket_id}/messages",
            {"content": text, "message_type": "outgoing", "private": True},
        )

    async def pause_ai_for_session(self, session_id: str) -> None:
        _log.info("pausing_ai_for_session", session_id=session_id)
        self._paused_sessions.add(session_id)
        await self._persist_pause(session_id, paused=True)

    async def unpause_ai_for_session(self, session_id: str) -> None:
        _log.info("unpausing_ai_for_session", session_id=session_id)
        self._paused_sessions.discard(session_id)
        # Resolving closes the ticket: forget the cached conversation so the next
        # contact opens a fresh one instead of landing in the resolved thread.
        self._conv_by_session.pop(session_id, None)
        await self._persist_pause(session_id, paused=False)

    async def is_ai_paused(self, session_id: str) -> bool:
        if session_id in self._paused_sessions:
            return True
        if self._settings.handoff_store != "firestore":
            return False
        try:
            client = firestore.Client(
                project=self._settings.firestore_project_id,
                database=self._settings.firestore_database_id,
            )
            snap = await asyncio.to_thread(
                lambda: client.collection("paused_sessions").document(session_id).get()
            )
            if snap.exists and snap.get("paused") is True:  # type: ignore[union-attr]
                self._paused_sessions.add(session_id)
                return True
        except Exception as e:
            _log.error("failed_to_read_pause_state", session_id=session_id, error=str(e))
        return False

    async def _persist_pause(self, session_id: str, *, paused: bool) -> None:
        if self._settings.handoff_store != "firestore":
            return
        try:
            client = firestore.Client(
                project=self._settings.firestore_project_id,
                database=self._settings.firestore_database_id,
            )
            doc = client.collection("paused_sessions").document(session_id)
            if paused:
                await asyncio.to_thread(lambda: doc.set({"paused": True}))
            else:
                await asyncio.to_thread(doc.delete)
        except Exception as e:
            _log.error("failed_to_persist_pause_state", session_id=session_id, error=str(e))

    # --- ConversationLogPort ---
    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,  # noqa: ARG002
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        return await self._find_or_create_conversation(session_id, customer_name, customer_phone)

    async def rotate_conversation_ticket(
        self,
        session_id: str,
        subject: str,  # noqa: ARG002
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        # Force a brand-new conversation (skip active-conversation reuse) — this is
        # the explicit "close this thread, open a fresh one" path.
        self._conv_by_session.pop(session_id, None)
        return await self._find_or_create_conversation(
            session_id, customer_name, customer_phone, search_existing=False
        )

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        res = await self._request(
            "POST",
            f"/conversations/{ticket_id}/messages",
            {"content": text, "message_type": "outgoing", "private": True},
        )
        if res is None:
            return ConversationLogResult.FAILED
        if status == "solved":
            await self._request(
                "POST", f"/conversations/{ticket_id}/toggle_status", {"status": "resolved"}
            )
        # NOTE: unlike Zendesk tickets, a Chatwoot conversation still accepts
        # messages after being resolved, so there is no "closed" state that
        # forces rotation — we never return TICKET_CLOSED. None == transient
        # failure (FAILED); any dict == accepted (OK).
        return ConversationLogResult.OK

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        await self._request("POST", f"/conversations/{ticket_id}/labels", {"labels": [tag]})

    async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None:
        await self.send_message(ticket_id, text)
        if status == "solved":
            await self._request(
                "POST", f"/conversations/{ticket_id}/toggle_status", {"status": "resolved"}
            )

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        # Chatwoot has no external_id on conversations; store it as a custom attribute.
        await self._request(
            "POST",
            f"/conversations/{ticket_id}/custom_attributes",
            {"custom_attributes": {"external_id": external_id}},
        )

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        res = await self._request("GET", f"/conversations/{ticket_id}/messages")
        payload = res.get("payload", []) if isinstance(res, dict) else []
        incoming = [m for m in payload if m.get("message_type") == 0]  # 0 == incoming
        if not incoming:
            return ("", None, None)
        last = incoming[-1]
        sender = last.get("sender") or {}
        return (last.get("content", ""), sender.get("name"), sender.get("email"))

    # --- HumanAgentBridgePort ---
    async def open_handoff(self, payload: HandoffOpenPayload) -> str:
        conv_id = await self._find_or_create_conversation(
            payload.session_id, payload.customer_name, payload.customer_phone
        )
        # Post the customer's latest message as an INCOMING message (before
        # labelling, which fires the webhook) so a downstream sync can identify the
        # customer from an incoming message and build the ticket.
        last_customer_text = next(
            (
                m.text
                for m in reversed(payload.transcript)
                if m.role == "user" and (m.text or "").strip()
            ),
            None,
        )
        await self.forward_customer_message(
            conv_id, payload.session_id, last_customer_text or payload.ai_summary
        )
        transcript = "\n".join(f"{m.role}: {m.text}" for m in payload.transcript)
        note = f"[AI handoff] {payload.ai_summary}\n\n--- transcript ---\n{transcript}"
        await self.add_private_note(conv_id, note)
        # Direct Zammad ticketing: on a complaint, POST the back-office ticket
        # ourselves and drop a Chatwoot private note linking to it (instead of
        # relying on the `escalate` label + external agent-service sync). Title
        # comes from the AI summary; body carries the summary + full transcript.
        await self._escalate_to_zammad(
            conv_id,
            payload.ai_summary,
            note,
            payload.session_id,
            payload.urgency,
            payload.reason,
        )
        await self._request(
            "POST", f"/conversations/{conv_id}/toggle_priority", {"priority": payload.urgency}
        )
        if self._settings.chatwoot_agent_team_id:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/assignments",
                {"team_id": self._settings.chatwoot_agent_team_id},
            )
        if payload.sla_minutes is not None:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/custom_attributes",
                {"custom_attributes": {"sla_minutes": payload.sla_minutes}},
            )
        # Apply the escalation labels LAST: a downstream sync escalates on a
        # conversation_updated carrying the escalate label, so nothing must update
        # the conversation after this or each update re-triggers a duplicate ticket.
        # The AI-classification dimension labels ride in this SAME single call so
        # the batch metrics sync can read them back — a separate labels POST would
        # re-fire the webhook and spawn a duplicate Zammad ticket.
        dimension_labels = self._dimension_labels(
            payload.category,
            payload.subcategory,
            payload.division,
            payload.department,
            payload.sla_minutes,
        )
        await self._request(
            "POST",
            f"/conversations/{conv_id}/labels",
            {
                "labels": list(
                    dict.fromkeys(
                        dimension_labels
                        + self._escalation_labels()
                        + self._complaint_labels(payload.reason, payload.urgency)
                    )
                )
            },
        )
        return conv_id

    async def forward_customer_message(
        self,
        conversation_id: str,
        user_external_id: str,  # noqa: ARG002
        text: str,
    ) -> None:
        await self._request(
            "POST",
            f"/conversations/{conversation_id}/messages",
            {"content": text, "message_type": "incoming"},
        )

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:  # noqa: ARG002
        expected = self._settings.chatwoot_webhook_secret
        if not expected:
            return True
        if not signature:
            return False
        return hmac.compare_digest(signature, expected)

    def parse_webhook_events(self, payload: dict[str, object]) -> list[AgentMessageEvent]:
        if payload.get("event") != "message_created":
            return []
        if payload.get("message_type") != "outgoing" or payload.get("private") is True:
            return []
        content = str(payload.get("content") or "").strip()
        if not content:
            return []
        conv = payload.get("conversation") or {}
        conv_id = str(conv.get("id")) if isinstance(conv, dict) else ""
        sender = payload.get("sender") or {}
        author = (
            str(sender.get("name")) if isinstance(sender, dict) and sender.get("name") else "Agent"
        )
        return [
            AgentMessageEvent(
                conversation_id=conv_id,
                author_name=author,
                text=content,
                timestamp=datetime.now(UTC),
            )
        ]
