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
from chatbot.features.routing.channels import canonical_channel

if TYPE_CHECKING:
    from chatbot.features.chat.escalation_notifier import EscalationNotifier
    from chatbot.features.chat.pic_registry import PicRegistry
    from chatbot.features.routing.service import RoutingService
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_PRIORITY_MAP = {"low": "low", "medium": "medium", "high": "high", "urgent": "urgent"}

# additional_attributes marker stamped on conversations created by an AI
# escalation/handoff (not a fresh customer chat). The agent sync service reads
# this off the conversation_created webhook to skip its AI-disclaimer greeting —
# that greeting belongs on the bot's first reply, not on the human-handoff
# conversation (where it would surface as the agent's opening message).
_AI_HANDOFF_ATTRS = {"ai_handoff": True}

# Conversation statuses that still count as "live" — an existing one is reused so
# a returning customer stays in the same thread. A resolved conversation is a
# closed ticket, so the next contact opens a fresh one instead.
_ACTIVE_STATUSES = frozenset({"open", "pending", "snoozed"})

# Package C Task 4 review fix: features.metrics.mapping.CATEGORY_TO_DIVISION's
# canonical values ("Aftersales") are what transcript_classifier.classify()
# validates/returns, and what set_ticket_classification below writes into
# `case_category` and the `division_<slug>` label. But the Cases List UI's
# `division` custom attribute (fork patch 0043) is populated everywhere else
# by the demo seeder (deploy/scripts/seed_demo_data) in the deck's DISPLAY
# vocabulary, which differs from canonical in exactly this one case
# ("After Sales" vs "Aftersales") — every other division's display and
# canonical spelling are identical. Left untranslated, a phone-classified
# Aftersales conversation would show as a second, separate "Aftersales"
# filter option next to the seeder's "After Sales" in the same dropdown.
_DIVISION_DISPLAY = {"Aftersales": "After Sales"}


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

    def __init__(
        self,
        settings: Settings,
        pic_registry: PicRegistry | None = None,  # type: ignore[type-arg]  # Any to avoid circular import
        escalation_notifier: EscalationNotifier | None = None,  # type: ignore[type-arg]
    ) -> None:
        self._settings = settings
        self._pic_registry = pic_registry
        self._escalation_notifier = escalation_notifier
        self._paused_sessions: set[str] = set()
        self._conv_by_session: dict[str, str] = {}
        self._routing_service: RoutingService | None = None
        self._channel_cache: str | None = None

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
        (``ai-escalation``) and any additional label an operator wants applied
        (e.g. for filtering agents' views).
        """
        return [
            label.strip()
            for label in self._settings.chatwoot_escalation_label.split(",")
            if label.strip()
        ]

    def _is_complaint(self, reason: str | None, urgency: str | None) -> bool:
        """A handoff is a complaint (-> escalation notification + complaint label)
        only when the AI flagged dissatisfaction or high urgency. A plain "talk to
        a human" request stays a live Chatwoot conversation, so agents aren't
        handed a confusing complaint for a conversation they should be having in
        the chat.
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

    async def _resolve_conv_channel(self) -> str:
        """Resolve the routing channel for AI-escalated conversations.

        All such conversations are created in the configured
        ``chatwoot_inbox_id`` inbox, so the channel is that inbox's label.
        Cached for the process lifetime (inbox config is static). Falls back
        to ``"web"`` when the inbox can't be resolved (Chatwoot unreachable,
        not found) — and does NOT cache that fallback, so a transient failure
        retries on the next escalation.
        """
        if self._channel_cache is not None:
            return self._channel_cache
        inbox_id = self._settings.chatwoot_inbox_id
        for inbox in await self.list_inboxes():
            if inbox.get("id") == inbox_id:
                channel = canonical_channel(inbox.get("channel_type"))
                self._channel_cache = channel
                return channel
        return "web"

    async def _assign_conversation(self, conv_id: str, fallback_team_id: int | None = None) -> None:
        """Assign to an agent (routing) or a team (fallback).

        When routing_enabled and a RoutingService is wired, resolve the real
        inbox channel (cached) and pick an available agent honoring channel
        priority. The channel resolution only runs inside this branch — no extra
        GET when routing is off. Otherwise assign the fallback team — which the
        caller may set to a PIC-derived team (open_handoff) so Phase 2
        department→PIC routing is preserved. create_ticket passes no fallback,
        so it uses the global chatwoot_agent_team_id.
        """
        if self._settings.routing_enabled and self._routing_service is not None:
            channel = await self._resolve_conv_channel()
            agent_id = await self._routing_service.pick_agent(channel)
            if agent_id is not None:
                await self._request(
                    "POST",
                    f"/conversations/{conv_id}/assignments",
                    {"assignee_id": agent_id},
                )
                return
        team_id = (
            fallback_team_id
            if fallback_team_id is not None
            else (self._settings.chatwoot_agent_team_id or None)
        )
        if team_id:
            await self._request(
                "POST",
                f"/conversations/{conv_id}/assignments",
                {"team_id": team_id},
            )

    async def _pic_label(self, department: str | None) -> str | None:
        """Return the ``pic_<name_slug>`` label for the resolved PIC, or None.

        Satisfies spec item 12: tag the escalated conversation with the PIC's
        identity so agents can filter/route by PIC in Chatwoot. Uses the same
        lower-snake convention as the other dimension labels.
        """
        if self._pic_registry is None or not department:
            return None
        key = department.removeprefix("dept_")
        pic = await self._pic_registry.lookup(key)
        if pic is None:
            return None
        slug = pic.pic_name.strip().lower().replace(" ", "_")
        return f"pic_{slug}"

    def _complaint_labels(self, reason: str | None, urgency: str | None) -> list[str]:
        """The `escalate` complaint label, applied only for genuine complaints
        (see `_is_complaint`) so a plain "talk to a human" handoff doesn't carry
        it.
        """
        label = self._settings.chatwoot_complaint_label.strip()
        if label and self._is_complaint(reason, urgency):
            return [label]
        return []

    async def _fire_escalation(
        self,
        conv_id: str,
        title: str,
        body: str,
        urgency: str | None,
        reason: str | None,
        department: str | None = None,
    ) -> None:
        """Fire escalation side-effects (email + CC, WA alert, case_state).

        Chatwoot-first: the notification always fires for a complaint, and
        references the Chatwoot conversation. No-op when the case is not a
        complaint. Errors from side-effects are swallowed so the turn never
        breaks.
        """
        if not self._is_complaint(reason, urgency):
            return

        # Fire escalation side-effects (email + CC, WA alert, case_state write).
        # PIC resolution (for the email/WA target) happens inside the notifier.
        if self._escalation_notifier is not None:
            await self._escalation_notifier.notify(
                conv_id=conv_id,
                title=title,
                body=body,
                department=department,
            )

    @staticmethod
    def _dimension_labels(
        division: str | None,
        department: str | None,
        sla_minutes: int | None,
    ) -> list[str]:
        """Encode the AI classification as Chatwoot conversation labels.

        category/subcategory moved to custom attributes (case_category/
        case_subcategory) — see the custom_attributes block at each call site.
        Uses the SAME tag-name convention the Zendesk metrics ``mapping.py``
        already parses (``division_*``, ``dept_*``, ``sla_<int>``) so the batch
        sync can read the dimensions straight back off the conversation. These
        are merged into the SINGLE final labels call alongside the escalation
        labels — a separate labels POST would needlessly re-fire the
        conversation_updated webhook.
        """

        def _norm(v: str) -> str:
            return v.strip().lower().replace(" ", "_")

        labels: list[str] = []
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

    async def _merge_custom_attributes(self, ticket_id: str, attributes: dict[str, Any]) -> None:
        """Merge-set conversation custom attributes without clobbering
        existing ones. Chatwoot's custom-attributes endpoint REPLACES the
        whole object -- ``ConversationsController#custom_attributes`` does
        ``@conversation.custom_attributes = params.permit(custom_attributes:
        {})[:custom_attributes]``, an assignment, not a merge. Same footgun
        this codebase has now hit three times: the labels endpoint
        (``add_ticket_tag``, above), and ``agent/app/clients/chatwoot.py``'s
        ``set_custom_attributes`` in the sibling service (that docstring
        quotes the exact Rails line). GET the conversation, union its
        current ``custom_attributes`` with the new ones (new values win on
        conflict), POST the union. EVERY custom-attribute writer in this
        adapter (``create_ticket``, ``open_handoff``,
        ``set_ticket_external_id``, ``set_ticket_classification``,
        ``set_call_recording``) goes through this one helper -- a caller
        writing one key must never silently erase every other key already
        on the conversation (classification, external_id, a live recording,
        lifecycle_state stamped by the sibling service, csat/nps, ...).

        Fail the write ENTIRELY if the read fails -- do NOT fall back to
        posting just ``attributes``: posting a set we cannot prove is
        complete is exactly the clobber this exists to prevent, merely
        narrowed to the read-failure window. Losing this one write is
        recoverable (a later call re-establishes it); wiping a real
        conversation's attributes is not.

        Deliberately excluded from the ERROR path: Chatwoot being
        DELIBERATELY disabled (``chatwoot_enabled=False``) also makes
        ``_request`` return ``None`` (it never issues the GET at all), but
        that is expected, quiet behaviour, not a failure -- logging it at
        ERROR would fire on every single write for such a tenant and look
        like a standing outage. Mirrors ``PhoneBridge._create_ticket_at_
        start``'s identical disabled-vs-failed distinction.
        """
        res = await self._request("GET", f"/conversations/{ticket_id}")
        if res is None:
            if self._settings.chatwoot_enabled:
                _log.error("chatwoot_custom_attributes_read_failed", ticket_id=ticket_id)
            else:
                _log.info("chatwoot_custom_attributes_write_skipped_disabled", ticket_id=ticket_id)
            return
        existing = res.get("custom_attributes") if isinstance(res, dict) else None
        current = existing if isinstance(existing, dict) else {}
        merged = {**current, **attributes}
        await self._request(
            "POST", f"/conversations/{ticket_id}/custom_attributes", {"custom_attributes": merged}
        )

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
        additional_attributes: dict[str, Any] | None = None,
    ) -> str:
        """Return the conversation id for a session, creating it in the API-channel
        inbox if it does not exist yet.

        Passing ``contact_id`` + ``inbox_id`` + ``source_id == session_id`` lets
        Chatwoot build the contact_inbox for us while keeping the source_id equal
        to the session id, so the mapping stays deterministic. On a cache miss we
        first reuse an existing active conversation (``search_existing``) so a
        restart does not create a duplicate; ``rotate_conversation_ticket`` sets it
        False to force a brand-new conversation. ``additional_attributes`` is merged
        into the create body (only applied when a NEW conversation is created — a
        reused one keeps its original attributes).
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
        if additional_attributes:
            payload["additional_attributes"] = additional_attributes
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

    # --- Customer 360 lookup (public reads; no create/mutate side effects) ---
    async def search_contacts(self, query: str) -> list[dict[str, Any]]:
        """Search Chatwoot contacts by free-text query (name/email/phone/identifier).

        Wraps the same ``/contacts/search`` endpoint ``_find_or_create_contact``
        already calls internally on its duplicate-identifier fallback path, but
        exposed publicly and generalized to any query string (not just a session
        id) so callers like the Customer 360 lookup can search by phone number.
        """
        res = await self._request("GET", f"/contacts/search?q={quote(query, safe='')}", None)
        payload = (res or {}).get("payload") if isinstance(res, dict) else None
        return [c for c in (payload or []) if isinstance(c, dict)]

    async def list_contact_conversations(self, contact_id: int) -> list[dict[str, Any]]:
        """Return ALL conversations (any status, any inbox) for a contact.

        Unlike ``_existing_conversation_id`` (which filters to our inbox's
        active-only conversations to decide whether to reuse one for a new
        message), this is a read-only cross-channel history view for Customer
        360 — resolved conversations and other inboxes are included on purpose.
        """
        res = await self._request("GET", f"/contacts/{contact_id}/conversations", None)
        return _conversations_from(res)

    async def list_conversations(self, *, max_pages: int = 5) -> list[dict[str, Any]]:
        """Page the account conversations endpoint, filtered to our inbox.

        Same endpoint/response shape ``presence.fetch_agent_open_counts`` and
        ``metrics.sync.fetch_conversations`` already page (``{"data":
        {"payload": [...]}}``, no ``next_page`` field — stop on an empty page).
        ``max_pages`` caps latency for this live-request lookup path (unlike
        the batch BigQuery sync, which pages to exhaustion); a Customer 360
        vehicle-number search only needs a best-effort recent-conversation
        match, not a full historical scan.
        """
        conversations: list[dict[str, Any]] = []
        inbox_id = self._settings.chatwoot_inbox_id
        for page_num in range(1, max_pages + 1):
            res = await self._request("GET", f"/conversations?status=all&page={page_num}")
            if not isinstance(res, dict):
                break
            data = res.get("data")
            batch = data.get("payload") if isinstance(data, dict) else res.get("payload")
            if not isinstance(batch, list) or not batch:
                break
            conversations.extend(
                c for c in batch if isinstance(c, dict) and c.get("inbox_id") in (inbox_id, None)
            )
        return conversations

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
        case_type: str | None = None,
        vehicle_model: str | None = None,
        division: str | None = None,
        department: str | None = None,
        sla_minutes: int | None = None,
    ) -> str:
        conv_id = await self._find_or_create_conversation(
            session_id, customer_name, customer_phone, additional_attributes=_AI_HANDOFF_ATTRS
        )
        # Post the customer's issue as an INCOMING message (before labelling, which
        # is what fires the webhook) so a downstream sync can identify the customer
        # from an incoming message and title the ticket.
        await self.forward_customer_message(conv_id, session_id, title)
        priority = _PRIORITY_MAP.get(urgency.lower(), "medium")
        await self._request(
            "POST", f"/conversations/{conv_id}/toggle_priority", {"priority": priority}
        )
        await self._assign_conversation(conv_id)
        await self.add_private_note(conv_id, f"[AI escalation] {title}\n\n{body}")
        # Fire escalation side-effects (email + CC, WA alert, case_state) —
        # Chatwoot-only.
        await self._fire_escalation(conv_id, title, body, urgency, None, department=department)
        # case_category/case_subcategory/case_type/vehicle_model + sla_minutes as
        # custom attributes — case_category/subcategory/case_type/vehicle_model
        # are List-type Chatwoot attribute definitions (see
        # chatwoot-config/provision_case_taxonomy.py), so Chatwoot's own native
        # sidebar enforces single-select exclusivity.
        custom_attrs: dict[str, Any] = {}
        if sla_minutes is not None:
            custom_attrs["sla_minutes"] = sla_minutes
        if category:
            custom_attrs["case_category"] = category
        if subcategory:
            custom_attrs["case_subcategory"] = subcategory
        if case_type:
            custom_attrs["case_type"] = case_type
        if vehicle_model:
            custom_attrs["vehicle_model"] = vehicle_model
        if custom_attrs:
            # Merge-safe (see _merge_custom_attributes): _find_or_create_
            # conversation above can REUSE an existing active conversation
            # (search_existing defaults True), which may already carry
            # attributes from a prior escalation on the same session -- a
            # plain assign here would blank those.
            await self._merge_custom_attributes(conv_id, custom_attrs)
        # Apply the escalation labels LAST: a downstream sync may act on a
        # conversation_updated carrying the escalate label, so nothing must update
        # the conversation after this or each update re-triggers that sync.
        # The AI-classification dimension labels ride in this SAME single call so
        # the batch metrics sync can read them back — a separate labels POST would
        # needlessly re-fire the webhook.
        dimension_labels = self._dimension_labels(division, department, sla_minutes)
        pic_lbl = await self._pic_label(department)
        await self._request(
            "POST",
            f"/conversations/{conv_id}/labels",
            {
                "labels": list(
                    dict.fromkeys(
                        dimension_labels
                        + ([pic_lbl] if pic_lbl else [])
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

    async def find_conversation_ticket(self, session_id: str) -> str | None:
        """Resolve an EXISTING conversation for this session WITHOUT ever
        creating one (Package C Task 5 review fix, Critical 2). Used by the
        recording-status callback, which can fire well after the call ends
        -- and after the conversation may already have been resolved by
        finalize() -- on a different process than the one that handled the
        WebSocket, so the in-process ``_conv_by_session`` cache cannot be
        relied on alone.

        Unlike ``_existing_conversation_id`` (used by ``_find_or_create_
        conversation``'s reuse check, which is deliberately ACTIVE-only --
        a resolved conversation is a closed ticket that the next customer
        message should NOT land back in), this checks every status: a
        conversation that finalize() already resolved is exactly the one a
        recording made during that same call belongs to.

        Matches on ``source_id == session_id`` -- the exact field
        ``_find_or_create_conversation`` sets on create, so this is a
        precise lookup, not a heuristic. Returns None (never a fabricated
        id) when no contact or no matching conversation exists, so the
        caller can skip rather than risk attaching data to -- or worse,
        creating -- the wrong conversation.
        """
        if session_id in self._conv_by_session:
            return self._conv_by_session[session_id]
        search = await self._request(
            "GET", f"/contacts/search?q={quote(session_id, safe='')}", None
        )
        contact_id: int | None = None
        for contact in (search or {}).get("payload") or []:
            if contact.get("identifier") == session_id and contact.get("id") is not None:
                contact_id = int(contact["id"])
                break
        if contact_id is None:
            return None
        res = await self._request("GET", f"/contacts/{contact_id}/conversations", None)
        for conv in _conversations_from(res):
            source_id = conv.get("source_id")
            if source_id is not None and str(source_id) == session_id:
                cid = conv.get("id")
                if cid is not None:
                    ticket_id = str(cid)
                    self._conv_by_session[session_id] = ticket_id
                    return ticket_id
        return None

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
        """Add ONE tag without clobbering the conversation's other labels.

        Chatwoot's labels endpoint REPLACES the whole set -- POSTing
        ``{"labels": [tag]}`` (the old body here) doesn't add `tag`, it
        DELETES every other label on the conversation (PIC, escalation,
        dealer, category, an earlier csat/nps tag, a division_<slug> this
        same call sequence just wrote in ``set_ticket_classification`` --
        anything). This is the same footgun `agent/app/clients/chatwoot.py`
        documents and works around for the sibling `custom_attributes`
        endpoint (`set_custom_attributes`) and this same `labels` endpoint
        (`add_labels`) -- both live in a different service, so the fix has
        to be duplicated here rather than shared. Every caller in THIS
        codebase (csat.py, nps.py, set_ticket_classification's division
        label) wants additive behaviour, matching this method's own
        Protocol docstring ("does not replace tags") -- so GET the current
        set and POST the union.

        Fail-open on the READ, not by falling back to posting just `[tag]`:
        if the GET fails we cannot know what's already there, and posting a
        set we can't prove is complete would silently wipe everything else.
        Losing this one tag is recoverable (nothing here treats it as fatal);
        wiping a real conversation's labels is not. So: log and skip the
        write entirely rather than guess.
        """
        res = await self._request("GET", f"/conversations/{ticket_id}/labels")
        if res is None:
            _log.error("chatwoot_add_ticket_tag_read_failed", ticket_id=ticket_id, tag=tag)
            return
        payload = res.get("payload") if isinstance(res, dict) else None
        current = [str(x) for x in payload] if isinstance(payload, list) else []
        union = list(dict.fromkeys([*current, tag]))  # preserve order, dedup
        await self._request("POST", f"/conversations/{ticket_id}/labels", {"labels": union})

    async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None:
        await self.send_message(ticket_id, text)
        if status == "solved":
            await self._request(
                "POST", f"/conversations/{ticket_id}/toggle_status", {"status": "resolved"}
            )

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        # Chatwoot has no external_id on conversations; store it as a custom
        # attribute. Merge-safe (see _merge_custom_attributes) -- this must
        # not erase classification/recording/lifecycle attributes already
        # written by other callers on the SAME conversation.
        await self._merge_custom_attributes(ticket_id, {"external_id": external_id})

    async def set_ticket_classification(
        self,
        ticket_id: str,
        *,
        case_type: str | None = None,
        division: str | None = None,
        concern: str | None = None,
    ) -> None:
        # Package C Task 4 (review fix): `division` alone is NOT enough for
        # Package E's reporting to pick this conversation up --
        # features.metrics.mapping.map_chatwoot_conversation_to_row reads
        # either a `division_<slug>` LABEL or the `case_category` custom
        # attribute, never the `division` attribute (that one is the Cases
        # List UI's own field, fork patch 0043). So this writes THREE things,
        # each in the spelling its one reader expects:
        #   - `division` custom attribute: DISPLAY spelling (_DIVISION_DISPLAY),
        #     matching what the demo seeder already writes there, so the Cases
        #     List UI's division filter doesn't grow a duplicate entry.
        #   - `case_category` custom attribute: CANONICAL spelling (division,
        #     verbatim -- classify() already validated it against
        #     mapping.CATEGORY_TO_DIVISION's own vocabulary), which mapping.py
        #     reads directly.
        #   - `division_<slug>` label, via the SAME `_dimension_labels`
        #     convention real-traffic AI classification already uses, so a
        #     phone-classified division lands in the exact same warehouse
        #     bucket as everything else instead of a byte-different one.
        # case_type needs no extra write -- mapping.py already reads it
        # straight off the `case_type` custom attribute. `_merge_custom_
        # attributes` (and add_ticket_tag, built the same way) already fail
        # open on a read failure, so no try/except is needed at this layer.
        custom_attrs: dict[str, str] = {}
        if case_type:
            custom_attrs["case_type"] = case_type
        if division:
            custom_attrs["division"] = _DIVISION_DISPLAY.get(division, division)
            custom_attrs["case_category"] = division
        if concern:
            custom_attrs["concern"] = concern
        if custom_attrs:
            await self._merge_custom_attributes(ticket_id, custom_attrs)
        if division:
            for label in self._dimension_labels(division, None, None):
                await self.add_ticket_tag(ticket_id, label)

    async def set_call_recording(
        self,
        ticket_id: str,
        *,
        recording_sid: str,
        recording_duration: str,
        recording_url: str,
    ) -> None:
        # Package C Task 5 compliance: recordings are gated behind the
        # call_recording.listen permission (features/authz/seed.py), so the
        # raw Twilio URL must NEVER appear in agent-visible conversation
        # text -- this writes custom attributes ONLY, never a comment/note
        # (contrast set_ticket_classification, whose fields are meant to be
        # agent-visible). Merge-safe (see _merge_custom_attributes): this
        # callback typically fires well after finalize() has already
        # written case_type/case_category/external_id on the SAME
        # conversation, so a plain assign here would blank every one of
        # them (and everything Package E's reporting reads) on every
        # recorded call -- a review-caught Critical, not hypothetical.
        # Merging is also what makes this naturally idempotent: a retried
        # callback delivery with the same values just re-unions the same
        # three keys rather than attaching the recording a second time.
        await self._merge_custom_attributes(
            ticket_id,
            {
                "recording_sid": recording_sid,
                "recording_duration": recording_duration,
                "recording_url": recording_url,
            },
        )

    async def get_inbox_working_hours(self, inbox_id: int) -> dict[str, Any] | None:
        """GET one inbox's business-hours config for the Task 6 handoff
        gate. Same ``_request`` fail-open shape as ``list_inboxes`` just
        above (returns ``None`` on any failure, including Chatwoot being
        disabled) -- the caller (``HandoffTargetResolver``) treats that
        identically to "no hours configured", i.e. always open."""
        res = await self._request("GET", f"/inboxes/{inbox_id}")
        return res if isinstance(res, dict) else None

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
            payload.session_id,
            payload.customer_name,
            payload.customer_phone,
            additional_attributes=_AI_HANDOFF_ATTRS,
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
        # Fire escalation side-effects (email + CC, WA alert, case_state) —
        # Chatwoot-only.
        # Title comes from the AI summary; body carries the summary + full transcript.
        await self._fire_escalation(
            conv_id,
            payload.ai_summary,
            note,
            payload.urgency,
            payload.reason,
            department=payload.department,
        )
        await self._request(
            "POST", f"/conversations/{conv_id}/toggle_priority", {"priority": payload.urgency}
        )
        # Use PIC team_id if available; fall back to the global setting.
        team_id_to_use = None
        if self._pic_registry is not None and payload.department:
            _key = payload.department.removeprefix("dept_")
            _pic = await self._pic_registry.lookup(_key)
            if _pic is not None and _pic.chatwoot_team_id is not None:
                team_id_to_use = _pic.chatwoot_team_id
        if team_id_to_use is None:
            team_id_to_use = self._settings.chatwoot_agent_team_id or None
        await self._assign_conversation(conv_id, fallback_team_id=team_id_to_use)
        # case_category/case_subcategory/case_type/vehicle_model + sla_minutes as
        # custom attributes — case_category/subcategory/case_type/vehicle_model
        # are List-type Chatwoot attribute definitions (see
        # chatwoot-config/provision_case_taxonomy.py), so Chatwoot's own native
        # sidebar enforces single-select exclusivity.
        custom_attrs: dict[str, Any] = {}
        if payload.sla_minutes is not None:
            custom_attrs["sla_minutes"] = payload.sla_minutes
        if payload.category:
            custom_attrs["case_category"] = payload.category
        if payload.subcategory:
            custom_attrs["case_subcategory"] = payload.subcategory
        if payload.case_type:
            custom_attrs["case_type"] = payload.case_type
        if payload.vehicle_model:
            custom_attrs["vehicle_model"] = payload.vehicle_model
        if custom_attrs:
            # Merge-safe (see _merge_custom_attributes) -- this conversation
            # may be a reused active one carrying prior attributes.
            await self._merge_custom_attributes(conv_id, custom_attrs)
        # Apply the escalation labels LAST: a downstream sync may act on a
        # conversation_updated carrying the escalate label, so nothing must update
        # the conversation after this or each update re-triggers that sync.
        # The AI-classification dimension labels ride in this SAME single call so
        # the batch metrics sync can read them back — a separate labels POST would
        # needlessly re-fire the webhook.
        dimension_labels = self._dimension_labels(
            payload.division,
            payload.department,
            payload.sla_minutes,
        )
        pic_lbl = await self._pic_label(payload.department)
        await self._request(
            "POST",
            f"/conversations/{conv_id}/labels",
            {
                "labels": list(
                    dict.fromkeys(
                        dimension_labels
                        + ([pic_lbl] if pic_lbl else [])
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

    async def list_inboxes(self) -> list[dict[str, Any]]:
        """Return all inboxes for the configured Chatwoot account.

        Calls ``GET /api/v1/accounts/{id}/inboxes`` and returns a list of dicts
        each carrying ``{id, name, channel_type}``. On any failure (Chatwoot
        unreachable, not enabled, bad auth) returns ``[]`` so callers can degrade
        cleanly without raising.
        """
        res = await self._request("GET", "/inboxes")
        if not isinstance(res, dict):
            return []
        payload = res.get("payload")
        if not isinstance(payload, list):
            return []
        inboxes: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            inbox_id = item.get("id")
            if inbox_id is None:
                continue
            inboxes.append(
                {
                    "id": int(inbox_id),
                    "name": str(item.get("name", "")),
                    "channel_type": str(item.get("channel_type") or item.get("channel", "") or ""),
                }
            )
        return inboxes

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
