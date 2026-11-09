"""POST /escalation/notify -- two-thread email escalation (EM-7) for a
natively-escalated Email-channel conversation -- plus GET /escalation/contacts,
the sender allowlist that makes the reply half of that loop safe.

POST /escalation/notify is called by the agent/ service's sync.maybe_escalate()
when a human applies the `escalate` label to a conversation on an Email inbox.
Deliberately separate from EscalationNotifier.notify(), which is the AI's own
autonomous escalation path (fired from ChatwootAdapter._fire_escalation) and
never reaches this endpoint -- the codebase already suppresses the `escalate`
label on AI-driven escalations to avoid the two paths colliding.

GET /escalation/contacts exists because escalation mail now carries a
correlation token (Reply-To + [CASE-n] subject, see escalation_notifier.py)
so a dealer/PIC's reply can be matched back onto the conversation it came
from. Matching a token alone isn't enough authorization, though -- a
conversation id is guessable, so anyone who can send mail could forge a
[CASE-n] subject and inject a private note into a stranger's case. The
agent/ service is expected to only link a reply when its From address
appears in this list, i.e. it's someone the escalation mail was actually
sent to.

GET /escalation/departments exists so the agent/ service's AI-suggested-
department feature (agent/app/services/dept_suggestion.py) can classify a
conversation only against departments that actually have a PIC configured --
suggesting a department with no PIC would recreate exactly the silent-failure
escalation this feature exists to prevent. Same auth, same `PicStore`
instance as /escalation/contacts.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.chat.audit_events import EVENT_REPLY_LINK, record_event
from chatbot.features.chat.escalation_ack import ack_transport
from chatbot.features.chat.escalation_context import derive_context
from chatbot.features.chat.handoff_note import is_phone_conversation
from chatbot.features.chat.sla import ACKNOWLEDGED_STATE

if TYPE_CHECKING:
    from chatbot.features.chat.escalation_notifier import EscalationNotifier
    from chatbot.features.chat.pic_store import DealerStore, PicStore
    from chatbot.features.chat.ports import AuditLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_CWRequest = Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]
_MergeAttrs = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]
_AddLabel = Callable[[str, str], Coroutine[Any, Any, None]]

# The agent service's once-per-escalation stamp (agent/app/services/sync.py
# _NOTIFIED_ATTR). Written here only when this endpoint itself adds a
# dealer_<slug> label -- see _fill_dealer.
_NOTIFIED_ATTR = "escalation_notified_at"
_SLUG_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class _NotifyIn(BaseModel):
    conversation_id: str
    title: str
    body: str
    department: str | None = None
    dealer: str | None = None
    # P2: the Chatwoot channel the case came from. The agent sends the raw
    # channel and this service resolves the acknowledgement transport, so the
    # channel table lives in exactly one place. Absent (a pre-P2 agent) is
    # treated as Email, which is the only channel that used to reach here.
    channel_type: str | None = None
    # The customer leg's subject. `title` carries the customer's own first
    # message, which is right for the PIC/dealer inboxes and wrong for the
    # customer -- see EscalationNotifier.notify_escalation. Absent keeps the
    # pre-2026-08-19 subject, so an older agent service is unaffected.
    customer_subject: str | None = None
    # RFC Message-ID of the customer's own first email (Chatwoot keeps it on
    # the message's `source_id`), so the acknowledgement threads onto their
    # mail instead of arriving as a new one. Absent = unthreaded, as before.
    customer_in_reply_to: str | None = None
    # FIX-17: the human who escalated and the case's SLA status at that moment,
    # so the audit trail names a person rather than a service. All optional --
    # an agent service that predates them is unaffected.
    actor_name: str | None = None
    actor_user_id: int | None = None
    sla_status: str | None = None


class _AcknowledgeIn(BaseModel):
    conversation_id: str
    actor: str = "escalation-reply"
    remark: str = ""


def _require_api_key(settings: Settings):
    """401s unless x-api-key matches proton_backend_key -- the same key the
    agent/ service already authenticates its other backend calls with."""

    def _check(x_api_key: str | None = Header(default=None)) -> None:
        if (
            not x_api_key
            or not settings.proton_backend_key
            or not hmac.compare_digest(x_api_key, settings.proton_backend_key)
        ):
            raise HTTPException(status_code=401, detail="Missing or invalid API key")

    return _check


async def _resolve_customer_email(chatwoot_request: _CWRequest, conv_id: str) -> str | None:
    """Best-effort lookup of the conversation's contact email via
    GET /conversations/{id} (meta.sender.email). None on any failure or
    missing field -- the caller sends the ack only when this resolves."""
    try:
        data = await chatwoot_request("GET", f"/conversations/{conv_id}", None)
    except Exception:
        _log.warning("escalation_notify_customer_email_lookup_failed", conv_id=conv_id)
        return None
    if not isinstance(data, dict):
        return None
    sender = (data.get("meta") or {}).get("sender") or {}
    email = sender.get("email")
    return str(email) if email else None


# FIX-58: a notify call can outlive the agent's HTTP timeout (99-123 s were
# observed under VM load against a 60 s client timeout), so the agent never
# learns the first call succeeded, never stamps escalation_notified_at, and
# the next conversation_updated event re-runs the whole escalation -- duplicate
# customer ack, PIC forward and dealer forward. The window below makes a retry
# for the same (conversation, recipient set) share the first call's outcome.
#
# In-process on purpose: the notify route has no cheap durable store of its own
# (the only per-case state it writes is a Chatwoot custom attribute, which the
# agent reads on its next event, not this route). The cache is therefore lost on
# a backend restart; a retry landing after a restart inside the window can
# still double-send. Accepted for the demo window; a Firestore-backed key is the
# Wave 2 upgrade if restarts mid-escalation prove real.
NOTIFY_IDEMPOTENCY_WINDOW_SECONDS = 30 * 60
# /escalation/contacts is hit on every inbound PIC/dealer reply by the agent
# (10 s client timeout). PIC and dealer lists change by hand a few times a day,
# so a short TTL turns a full Firestore scan per reply into one per window.
CONTACTS_CACHE_TTL_SECONDS = 30.0


class ContactsCache:
    """TTL cache for the /escalation/contacts allowlist, with explicit
    invalidation and a single-flight guard.

    The Escalation Routing admin router calls ``invalidate`` on every write, so
    an operator's edit is reflected on the very next lookup (a new PIC's reply
    is linked, a removed PIC is refused) instead of up to a TTL later. The
    generation counter stops a scan that was already running when the edit
    landed from storing its stale result afterwards. ``lock`` makes concurrent
    misses share one Firestore scan instead of each doing their own.
    """

    def __init__(self, ttl: float = CONTACTS_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._entry: tuple[float, dict[str, list[dict[str, str]]]] | None = None
        self._generation = 0
        self.lock = asyncio.Lock()

    @property
    def generation(self) -> int:
        return self._generation

    def get(self) -> dict[str, list[dict[str, str]]] | None:
        if self._entry is None or time.monotonic() - self._entry[0] >= self._ttl:
            return None
        return self._entry[1]

    def put(self, value: dict[str, list[dict[str, str]]], generation: int) -> None:
        if generation == self._generation:
            self._entry = (time.monotonic(), value)

    def invalidate(self) -> None:
        self._generation += 1
        self._entry = None


def build_escalation_router(
    notifier: EscalationNotifier,
    chatwoot_request: _CWRequest,
    settings: Settings,
    pic_store: PicStore | None = None,
    dealer_store: DealerStore | None = None,
    audit: AuditLogPort | None = None,
    contacts_cache: ContactsCache | None = None,
    merge_attributes: _MergeAttrs | None = None,
    add_label: _AddLabel | None = None,
) -> APIRouter:
    """``merge_attributes`` / ``add_label`` (merge-safe Chatwoot writers) are
    used only with ``handoff_note_enabled``, by the one-click dealer
    escalation in ``_do_notify``."""
    router = APIRouter()
    auth = _require_api_key(settings)
    # key -> (monotonic start time, shared task). The task, not the result, is
    # stored so a retry arriving while the first call is still running joins it
    # instead of starting a second send.
    inflight: dict[tuple[str, str, str, str, str], tuple[float, asyncio.Task[dict[str, str]]]] = {}
    cache = contacts_cache if contacts_cache is not None else ContactsCache()

    @router.post("/escalation/notify", dependencies=[Depends(auth)])
    async def notify(payload: _NotifyIn) -> dict[str, str]:
        key = (
            payload.conversation_id,
            (payload.department or "").strip().lower(),
            (payload.dealer or "").strip().lower(),
            (payload.channel_type or "").strip().lower(),
            # Content hash: the agent clears escalation_notified_at on
            # de-escalation, so a genuine re-escalation of the same
            # department/dealer within the window must send again. Only an
            # identical payload (a timeout retry) is suppressed.
            hashlib.sha256(f"{payload.title}\x00{payload.body}".encode()).hexdigest()[:16],
        )
        now = time.monotonic()
        for k in [k for k, (t, _) in inflight.items() if now - t > NOTIFY_IDEMPOTENCY_WINDOW_SECONDS]:
            del inflight[k]
        entry = inflight.get(key)
        if entry is not None and not (
            entry[1].done() and (entry[1].cancelled() or entry[1].exception() is not None)
        ):
            _log.warning(
                "escalation_notify_duplicate_suppressed",
                conv_id=payload.conversation_id,
                department=payload.department,
                dealer=payload.dealer,
            )
            # shield: a caller that gives up must not cancel the shared send.
            return await asyncio.shield(entry[1])
        task = asyncio.ensure_future(_do_notify(payload))
        inflight[key] = (now, task)
        return await asyncio.shield(task)

    async def _read_conversation(conv_id: str) -> dict[str, Any] | None:
        try:
            data = await chatwoot_request("GET", f"/conversations/{conv_id}", None)
        except Exception:
            _log.warning("escalation_notify_conversation_read_failed", conv_id=conv_id)
            return None
        return data if isinstance(data, dict) else None

    def _confirmed_dealer(conv: dict[str, Any] | None) -> str | None:
        """The dealer the customer named and that matched one configured
        dealer (caller_dealer, written by the phone caller context or the
        chat handoff note). Never the history dealer, never raw text."""
        attrs = (conv or {}).get("custom_attributes") or {}
        slug = str(attrs.get("caller_dealer") or "").strip().lower() if isinstance(attrs, dict) else ""
        return slug if _SLUG_RE.match(slug) else None

    async def _label_dealer(conv_id: str, slug: str) -> None:
        """Make the dealer escalation visible like a hand-added one: the
        dealer_<slug> label (reports, dealer_escalated_at, the ladder).
        Stamp the agent's once-per-escalation guard FIRST: the label fires
        a conversation_updated that re-enters maybe_escalate, and with the
        stamp in place that echo is skipped instead of escalating twice."""
        if merge_attributes is None or add_label is None:
            return
        try:
            await merge_attributes(conv_id, {_NOTIFIED_ATTR: datetime.now(UTC).isoformat()})
            await add_label(conv_id, f"dealer_{slug}")
        except Exception as e:
            _log.warning(
                "escalation_notify_dealer_label_failed", conv_id=conv_id, error=str(e)
            )

    async def _do_notify(payload: _NotifyIn) -> dict[str, str]:
        transport = (
            "email" if payload.channel_type is None else ack_transport(payload.channel_type)
        )
        dealer = payload.dealer
        filled_dealer: str | None = None
        if settings.handoff_note_enabled and (transport == "conversation" or not dealer):
            # 2026-11-09 brief 3 (features/chat/handoff_note.py): a phone
            # call sits in the API inbox, so its channel type says
            # "conversation" -- but there is no thread to write to; the
            # caller was spoken to. And `escalate` alone escalates to the
            # dealer the customer confirmed.
            conv = await _read_conversation(payload.conversation_id)
            if transport == "conversation" and is_phone_conversation(conv):
                transport = "none"
            if not dealer:
                filled_dealer = _confirmed_dealer(conv)
                dealer = filled_dealer
        customer_email = (
            await _resolve_customer_email(chatwoot_request, payload.conversation_id)
            if transport == "email"
            else None
        )
        # Only forwarded when known, so a call without them is identical to the
        # pre-FIX-17 call. The agent service sends none of these today, so with
        # an audit store wired the backend derives them (escalation_context):
        # the SLA status from the case's own trail, the actor from the
        # conversation's "<name> added escalate" activity.
        actor_name = payload.actor_name
        actor_user_id = payload.actor_user_id
        sla_status = payload.sla_status
        actor_source: str | None = None
        if audit is not None:
            derived_actor, derived_sla, actor_source = await derive_context(
                audit,
                chatwoot_request,
                payload.conversation_id,
                need_actor=actor_name is None,
                need_sla=sla_status is None,
            )
            if actor_name is None:
                actor_name = derived_actor
            else:
                actor_source = None
            if sla_status is None:
                sla_status = derived_sla
        audit_ctx = {
            k: v
            for k, v in (
                ("actor_name", actor_name),
                ("actor_user_id", actor_user_id),
                ("sla_status", sla_status),
                ("actor_source", actor_source),
            )
            if v is not None
        }
        await notifier.notify_escalation(
            conv_id=payload.conversation_id,
            title=payload.title,
            body=payload.body,
            department=payload.department,
            dealer=dealer,
            customer_email=customer_email,
            ack_transport=transport,
            customer_subject=payload.customer_subject,
            customer_in_reply_to=payload.customer_in_reply_to,
            **audit_ctx,
        )
        if filled_dealer:
            await _label_dealer(payload.conversation_id, filled_dealer)
        return {"status": "ok"}

    @router.get("/escalation/contacts", dependencies=[Depends(auth)])
    async def contacts() -> dict[str, list[dict[str, str]]]:
        """Every address the escalation mail can reach, for the agent
        service's reply-sender allowlist -- see this module's docstring for
        why the allowlist exists at all.

        Best-effort: a store failure (Firestore hiccup, etc.) yields fewer
        contacts, never a 5xx. The agent side treats a short/empty list as
        "sender unknown" and simply doesn't link the reply -- degrading to
        "reply not linked" is always safe, where a 5xx here would just make
        the caller retry against the same failing store for no benefit.
        """
        cached = cache.get()
        if cached is not None:
            return cached
        # Single flight: concurrent misses queue here and the later ones find
        # the first one's result on the re-check, so one scan serves them all.
        async with cache.lock:
            cached = cache.get()
            if cached is not None:
                return cached
            generation = cache.generation
            result, degraded = await _scan_contacts()
            # Never cache a degraded or empty answer: a Firestore hiccup would
            # otherwise pin "no contacts" (every reply refused) for the TTL.
            if not degraded and result["contacts"]:
                cache.put(result, generation)
            return result

    async def _scan_contacts() -> tuple[dict[str, list[dict[str, str]]], bool]:
        degraded = False
        seen: set[str] = set()
        out: list[dict[str, str]] = []

        def _add(email: str | None, name: str, kind: str) -> None:
            key = (email or "").strip().lower()
            if not key or key in seen:
                return
            seen.add(key)
            out.append({"email": key, "name": name, "kind": kind})

        if pic_store is not None:
            try:
                pics = await pic_store.list_all()
            except Exception:
                _log.warning("escalation_contacts_pic_store_failed")
                degraded = True
                pics = []
            for rec in pics:
                _add(rec.pic_email, rec.pic_name, "pic")
                for cc in rec.cc_emails:
                    _add(cc, f"{rec.pic_name} (CC)", "pic")

        if dealer_store is not None:
            try:
                dealers = await dealer_store.list_all()
            except Exception:
                _log.warning("escalation_contacts_dealer_store_failed")
                degraded = True
                dealers = []
            for rec in dealers:
                for member in rec.emails:
                    _add(member, rec.dealer, "dealer")
                # The ladder's named roles. Steps 3 and 4 mail a Dealer
                # Principal and a Dealer Owner who may appear NOWHERE in the
                # flat group above -- that is the whole point of the roles.
                # Leaving them off this list means their replies are refused
                # as unknown senders, so the ladder never learns the dealer
                # engaged and climbs to the next person anyway: exactly the
                # duplicate escalation it exists to prevent.
                for role, address in (rec.contacts or {}).items():
                    _add(address, f"{rec.dealer} ({role.replace('_', ' ')})", "dealer")
                # A CC'd manager who hits reply-all is answering the
                # escalation too.
                for cc in rec.cc_emails:
                    _add(cc, f"{rec.dealer} (CC)", "dealer")

        return {"contacts": out}, degraded

    @router.post("/escalation/acknowledge", dependencies=[Depends(auth)])
    async def acknowledge(payload: _AcknowledgeIn) -> dict[str, str]:
        """Record that someone has acknowledged the customer on this case.

        Called by the agent/ service's reply linker when a PIC or dealer's
        emailed reply is matched back onto its case. The SLA engine reads the
        resulting ACKNOWLEDGED transition for the first-response breach only
        when ``sla_acknowledgement_enabled`` is on, so calling this is always
        safe -- at worst it writes an audit row nothing reads yet.

        Idempotent: the reply linker can legitimately fire twice for the same
        case (a second reply on the same thread), and an acknowledgement is a
        fact that has either happened or not -- recording it twice would
        double-count in any audit-derived report.
        """
        if audit is None:
            raise HTTPException(status_code=404, detail="Audit log not configured")
        ticket_id = payload.conversation_id
        try:
            existing = await audit.list_for_ticket(ticket_id)
        except Exception:
            # Same posture as the read endpoints above: a store failure must
            # not 5xx the caller. Skipping is the safe direction -- it can
            # only leave an ack unrecorded, never invent one.
            _log.warning("escalation_acknowledge_list_failed", ticket_id=ticket_id)
            return {"status": "skipped"}
        if any(e.to_state == ACKNOWLEDGED_STATE for e in existing):
            return {"status": "duplicate"}

        recorded = await record_event(
            audit,
            event_type=EVENT_REPLY_LINK,
            target=f"case:{ticket_id}",
            ticket_id=ticket_id,
            actor_name=payload.actor,
            from_state="OPEN",
            to_state=ACKNOWLEDGED_STATE,
            remarks=payload.remark,
        )
        if not recorded:
            # The row IS the acknowledgement (the SLA engine reads it), so a
            # failed write must not be reported as success: 503 lets the
            # reply linker retry instead of believing the case was acknowledged.
            _log.error("escalation_acknowledge_not_recorded", ticket_id=ticket_id)
            raise HTTPException(status_code=503, detail="Acknowledgement not recorded")
        _log.info("escalation_acknowledged", ticket_id=ticket_id, actor=payload.actor)
        return {"status": "ok"}

    @router.get("/escalation/departments", dependencies=[Depends(auth)])
    async def departments() -> dict[str, list[str]]:
        """Department keys that currently have a PIC configured, for the
        agent service's AI-suggested-department feature. Suggesting a
        department with no PIC would silently escalate to nobody -- the
        exact failure that feature exists to prevent -- so candidates come
        from this store, never a static list.

        Best-effort, same posture as /escalation/contacts: a store failure
        yields fewer (or zero) departments, never a 5xx. The agent side
        treats an empty list as "nothing to suggest" and posts no note --
        degrading to "no suggestion" is always safe, where a 5xx here would
        just make the caller retry against the same failing store.
        """
        if pic_store is None:
            return {"departments": []}
        try:
            pics = await pic_store.list_all()
        except Exception:
            _log.warning("escalation_departments_pic_store_failed")
            return {"departments": []}
        seen: set[str] = set()
        out: list[str] = []
        for rec in pics:
            key = (rec.department or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return {"departments": out}

    return router
