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
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from chatbot.features.chat.escalation_notifier import EscalationNotifier
    from chatbot.features.chat.pic_store import DealerStore, PicStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_CWRequest = Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]


class _NotifyIn(BaseModel):
    conversation_id: str
    title: str
    body: str
    department: str | None = None
    dealer: str | None = None


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
) -> APIRouter:
    router = APIRouter()
    auth = _require_api_key(settings)

    @router.post("/escalation/notify", dependencies=[Depends(auth)])
    async def notify(payload: _NotifyIn) -> dict[str, str]:
        customer_email = await _resolve_customer_email(chatwoot_request, payload.conversation_id)
        await notifier.notify_email_channel_escalation(
            conv_id=payload.conversation_id,
            title=payload.title,
            body=payload.body,
            department=payload.department,
            dealer=payload.dealer,
            customer_email=customer_email,
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

    return router
