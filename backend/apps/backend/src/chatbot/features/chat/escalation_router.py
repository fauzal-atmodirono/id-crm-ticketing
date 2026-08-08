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

import hmac
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.chat.escalation_ack import ack_transport
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.sla import ACKNOWLEDGED_STATE

if TYPE_CHECKING:
    from chatbot.features.chat.escalation_notifier import EscalationNotifier
    from chatbot.features.chat.pic_store import DealerStore, PicStore
    from chatbot.features.chat.ports import AuditLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_CWRequest = Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]


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


def build_escalation_router(
    notifier: EscalationNotifier,
    chatwoot_request: _CWRequest,
    settings: Settings,
    pic_store: PicStore | None = None,
    dealer_store: DealerStore | None = None,
    audit: AuditLogPort | None = None,
) -> APIRouter:
    router = APIRouter()
    auth = _require_api_key(settings)

    @router.post("/escalation/notify", dependencies=[Depends(auth)])
    async def notify(payload: _NotifyIn) -> dict[str, str]:
        transport = (
            "email" if payload.channel_type is None else ack_transport(payload.channel_type)
        )
        customer_email = (
            await _resolve_customer_email(chatwoot_request, payload.conversation_id)
            if transport == "email"
            else None
        )
        await notifier.notify_escalation(
            conv_id=payload.conversation_id,
            title=payload.title,
            body=payload.body,
            department=payload.department,
            dealer=payload.dealer,
            customer_email=customer_email,
            ack_transport=transport,
        )
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
                dealers = []
            for rec in dealers:
                for member in rec.emails:
                    _add(member, rec.dealer, "dealer")

        return {"contacts": out}

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

        await audit.append(
            AuditEntry(
                ticket_id=ticket_id,
                session_id=f"chatwoot-conv-{ticket_id}",
                actor=payload.actor,
                from_state="OPEN",
                to_state=ACKNOWLEDGED_STATE,
                at=datetime.now(UTC).isoformat(),
                remark=payload.remark,
            )
        )
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
