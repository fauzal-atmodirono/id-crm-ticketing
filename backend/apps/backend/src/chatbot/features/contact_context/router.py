"""Chat-side caller context: who is this customer, minimally (2026-11-09).

The phone line looks the caller up before the greeting (task 1,
`features/chat/phone/caller_context.py`). The chat bot (the `agent/`
service answering WhatsApp / API-inbox messages) should know the same, and
no more: first name, car model(s), plate last 4, usual dealer, open case
count. Never the phone number, the surname or a full plate (Malaysia PDPA):
the bot CONFIRMS ("is this Ahmad? about your X50?"), it does not recite.

`POST /agent/contact-context` with JSON ``{"phone": "..."}`` (a body, so the
number never sits in a URL, an access log or httpx's request log) reuses
the phone path's bounded, fail-open `CallerContextService.lookup`
(read-only here) and returns that minimal view plus a ready-made prompt
paragraph. Guarded by CHAT_CALLER_CONTEXT_ENABLED (default false -> not
mounted / 404), the backend's shared key (`x-api-key == PROTON_BACKEND_KEY`,
constant-time; blank key fails closed) and a rate limit (120/min per client
IP, counted before the key check so guessing is bounded too).

Reachability: the CRM vhost does not proxy `/agent/*`, but the test
console's authenticated `/apps/test-console/api/*` proxy forwards every
backend path, so a console user who also holds PROTON_BACKEND_KEY could
call it (a Caddy block denying `/apps/test-console/api/agent/*` is planned at
deploy). PROTON_BACKEND_KEY is also present in agents' browsers (CRM strip);
the view is less than Customer 360 already shows them. No log line here
carries the number unmasked.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from chatbot.features.chat.phone.caller_context import (
    CallerContext,
    _plate_ending,
    dealer_display,
    mask,
    normalise_mobile,
)
from chatbot.features.chat.phone.rate_limit import RateLimiter

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

Lookup = Callable[[str], Awaitable[CallerContext]]

_TAG = re.compile(r"\[\s*demo\s*\]", re.IGNORECASE)
_PLACEHOLDERS = frozenset({"", "customer", "caller", "unknown", "phone caller"})
_MAX_NAME = 40
_CALLS_PER_MINUTE = 120


class ContactContextRequest(BaseModel):
    phone: str = ""


def _first_name(name: str) -> str:
    """First word of the contact name, "[DEMO]" tag removed; "" for a
    placeholder or a name with no letters."""
    text = " ".join(_TAG.sub(" ", name or "").split())
    if text.lower() in _PLACEHOLDERS or not re.search(r"[^\W\d_]", text):
        return ""
    return text.split(" ")[0][:_MAX_NAME]


def contact_view(ctx: CallerContext | None) -> dict[str, Any]:
    """What the model may know. No phone, no surname, no full plate, no ids."""
    if ctx is None or not ctx.candidates:
        return {"match": "none", "customers": []}
    customers = []
    for c in ctx.candidates:
        vehicles = []
        for v in c.vehicles:
            item = {"model": v.model} if v.model else {}
            ending = _plate_ending(v.plate)
            if ending:
                item["plate_ending"] = ending
            if item:
                vehicles.append(item)
        customers.append(
            {
                "first_name": _first_name(c.name),
                "vehicles": vehicles,
                "usual_dealer": dealer_display(c.last_dealer),
                "open_cases": c.open_cases,
            }
        )
    return {
        "match": "single" if len(customers) == 1 else "multiple_people",
        "customers": customers,
    }


def _car(v: dict[str, str]) -> str:
    model = v.get("model") or "car"
    ending = v.get("plate_ending")
    return f"{model} (plate ending {ending})" if ending else model


_PRIVACY = (
    "PRIVACY: never write the customer's phone number, surname, full plate number, "
    "address or any other personal detail from our records. You may confirm what the "
    "customer tells you, and ask a confirming question using only a first name and a "
    "car model. If the customer says this is not them, treat them as a new customer."
)


def chat_context_instruction(view: dict[str, Any]) -> str:
    """The prompt paragraph for the chat bot; "" when nothing is known."""
    customers = view.get("customers") or []
    if not customers:
        return ""
    head = "CUSTOMER CONTEXT (from our CRM records, not written by the customer): "
    if len(customers) > 1:
        return (
            head + "this phone number is shared by several customers. Do not guess who this "
            "is and do not list names: if it matters, ask for their name and which car "
            "the message is about. " + _PRIVACY
        )
    c = customers[0]
    facts = []
    if c.get("first_name"):
        facts.append(f"first name {c['first_name']}")
    cars = c.get("vehicles") or []
    if len(cars) == 1:
        facts.append(f"car {_car(cars[0])}")
    elif cars:
        facts.append("cars on file: " + "; ".join(_car(v) for v in cars))
    if c.get("usual_dealer"):
        facts.append(f"usual dealer {c['usual_dealer']}")
    if c.get("open_cases"):
        facts.append(f"{c['open_cases']} open case(s)")
    if not facts:
        return ""
    confirm = (
        "If you have not already confirmed it in this conversation, confirm once, "
        "naturally and in one sentence, who you are talking to"
    )
    confirm += " and which car the message is about. " if cars else ". "
    which = (
        "They have more than one car: ask which one (you may name the models, never the plates). "
        if len(cars) > 1
        else ""
    )
    return head + "this customer matches " + ", ".join(facts) + ". " + confirm + which + _PRIVACY


def build_contact_context_router(settings: Settings, lookup: Lookup) -> APIRouter:
    router = APIRouter()

    limiter = RateLimiter(_CALLS_PER_MINUTE, 60.0)

    @router.post("/agent/contact-context")
    async def contact_context(
        payload: ContactContextRequest,
        request: Request,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if not settings.chat_caller_context_enabled:
            raise HTTPException(status_code=404, detail="Not found")
        if not limiter.allow(request.client.host if request.client else "unknown"):
            raise HTTPException(status_code=429, detail="Too many requests")
        key = settings.proton_backend_key
        if (
            not key
            or not x_api_key
            or not hmac.compare_digest(x_api_key.encode("utf-8"), key.encode("utf-8"))
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        number = normalise_mobile(payload.phone)
        if number is None:
            return {"view": contact_view(None), "instruction": ""}
        try:
            ctx = await lookup(number)
        except Exception as e:  # the lookup is fail-open already; belt and braces
            _log.warning(
                "chat_contact_context_failed", phone=mask(number), error_type=type(e).__name__
            )
            ctx = None
        view = contact_view(ctx)
        return {"view": view, "instruction": chat_context_instruction(view)}

    return router
