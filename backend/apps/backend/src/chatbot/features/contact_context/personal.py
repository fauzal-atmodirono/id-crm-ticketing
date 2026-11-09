"""Personal service on every channel: ONE wording source (2026-11-09, R47).

The user, after testing the Ahmad persona: "if we have the information from
them, please be personal so the user dont have spend time on answering the
name, contact questions ... but if the number unrecognized in our contact
then we serve it as new customer" -- and "not only for call but also for
chat and voicebot".

Before this module the phone line and the chat bot each had their own
CONFIRM-only wording ("Am I speaking with Ahmad?"), which on the phone lost
to the fast-start greeting cue and dodged "who am I?" under a privacy rule
that forbade ANY detail from our records. Here, for every channel:

* **Recognised** (exactly one record on the number): greet by first name from
  the first sentence, mention the car model where natural, never ask for what
  we already have (name, number, car, dealer) -- use it; "who am I?" gets a
  yes with first name, car model(s) and usual dealer; several cars -> ask
  which (models only); no first name -> ask it once; "that is not me" ->
  apologise, new customer.
* **Shared number** (several records): unchanged -- ask the name, never list.
* **Not recognised**: serve as a NEW customer; ask name, plate, dealer
  naturally, one at a time, at most three, only when it helps.
* **Privacy floor** (one line, the same on all channels): first name, car
  model(s), plate ending (last 4) and usual dealer may be used freely; never a
  full phone number, a full plate, an address, an IC number or a surname.

Pure strings, no I/O: the inputs are the already-minimised views
(``caller_view`` / ``contact_view`` customer dicts: ``first_name``,
``vehicles`` = ``[{"model", "plate_ending"}]``, ``usual_dealer``), so nothing
here can leak what those views never carried.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

Channel = Literal["call", "chat"]

PRIVACY_FLOOR = (
    "PRIVACY: you may use the customer's first name, car model(s), the last 4 "
    "characters of a plate and their usual dealer freely; NEVER read out or write a "
    "full phone number, a full plate number, an address, an IC number or a surname "
    "from our records. "
)

CHAT_HEAD = "CUSTOMER CONTEXT (from our CRM records, not written by the customer): "

# The phone's new-caller paragraph, unchanged since task 1 (R47: "phone:
# existing _UNKNOWN + save_caller_details").
_CALL_UNKNOWN = (
    "This caller is not in our records yet. Naturally, ONE question at a time, and at "
    "most three questions before you start helping, ask for: their name, their car "
    "registration number, and which Proton dealer or service centre they use (or the "
    "nearest one). If they decline or are in a hurry, help them anyway. "
)

_CHAT_UNKNOWN = (
    CHAT_HEAD + "this customer's phone number is not in our records, so serve them as a "
    "NEW customer. Help them with what they ask first. Only when it matters (for "
    "example a booking, a complaint or an escalation), ask naturally, one question at a "
    "time and at most three questions in total, for their name, their car registration "
    "number and which Proton dealer or service centre they use. Never hold back help "
    "to ask for these. "
)


# /chat/turn + /voice/turn, recognised customer on a trusted number (R51):
# book_test_drive_tool is given the number server-side, so the model never
# needs to ask for it -- and still never reads it out (PRIVACY_FLOOR).
BOOKING_LINE = (
    "For a booking you already have their phone number, so do not ask for it; you may "
    "ask for their full name and email if the booking needs them. "
)


def _car(v: Mapping[str, Any]) -> str:
    model = str(v.get("model") or "") or "car"
    ending = str(v.get("plate_ending") or "")
    return f"{model} (plate ending {ending})" if ending else model


def _cars(customer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = customer.get("vehicles") or []
    return [v for v in raw if isinstance(v, Mapping) and (v.get("model") or v.get("plate_ending"))]


def _facts(customer: Mapping[str, Any], cars: list[Mapping[str, Any]], extra: Sequence[str]) -> str:
    parts = []
    first = str(customer.get("first_name") or "")
    if first:
        parts.append(f"first name {first}")
    if len(cars) == 1:
        parts.append(f"car {_car(cars[0])}")
    elif cars:
        parts.append("cars on file: " + "; ".join(_car(v) for v in cars))
    dealer = str(customer.get("usual_dealer") or "")
    if dealer:
        parts.append(f"usual dealer {dealer}")
    parts.extend(x for x in extra if x)
    return ", ".join(parts) or "no details on file"


def _greeting(first: str, cars: list[Mapping[str, Any]], when: str) -> str:
    single_model = str(cars[0].get("model") or "") if len(cars) == 1 else ""
    if not first:
        text = f"We have no first name on file: in your {when}, greet them and ask for their name once. "
        if single_model:
            text += f"Refer to their car as their {single_model}. "
        return text
    if single_model:
        return (
            f"Be personal from your {when}: greet them by first name and, where natural, "
            f"mention their car model, e.g. 'Hi {first}, this is Proton. How can I help "
            f"with your {single_model} today?'. "
        )
    return (
        f"Be personal from your {when}: greet them by first name, e.g. 'Hi {first}, this "
        "is Proton. How can I help today?'. "
    )


def has_facts(customer: Mapping[str, Any]) -> bool:
    """A record identifies someone only with a first name, a car or a dealer
    (R50): a bare "Customer" contact on the number is an unknown customer."""
    return bool(customer.get("first_name") or _cars(customer) or customer.get("usual_dealer"))


def _join(items: list[str], last: str) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def recognised_paragraph(
    customer: Mapping[str, Any], *, channel: Channel, extra_facts: Sequence[str] = ()
) -> str:
    """The paragraph for a number that matches exactly one customer; the
    unknown paragraph when the record carries nothing that identifies them."""
    if not has_facts(customer):
        return unknown_paragraph(channel=channel)
    cars = _cars(customer)
    first = str(customer.get("first_name") or "")
    dealer = str(customer.get("usual_dealer") or "")
    facts = _facts(customer, cars, extra_facts)
    if channel == "call":
        head = f"This caller's phone number matches ONE customer in our records: {facts}. "
        when = "first sentence"
        who = "caller"
    else:
        head = CHAT_HEAD + f"this customer matches ONE customer in our records: {facts}. "
        when = (
            "first reply in this conversation (only once: if you have already greeted "
            "them, do not greet them again)"
        )
        who = "customer"
    text = head + _greeting(first, cars, when)
    if len(cars) > 1:
        text += (
            "They have more than one car on file: ask which car this is about (name the "
            "models, or the plate ending if two models are the same). "
        )
    # Only what is actually on file (R50); the number is, since it matched.
    have = (["name"] if first else []) + ["phone number"]
    have += (["car"] if cars else []) + (["dealer"] if dealer else [])
    text += f"Never ask for what we already have (their {_join(have, 'or')}); use it instead"
    use = []
    if dealer:
        use.append(f"offer their usual dealer ({dealer}) for a booking")
    if cars:
        use.append("refer to their car by its model")
    text += (", e.g. " + " and ".join(use) + ". ") if use else ". "
    if not cars:
        text += "Ask for their car registration number and model only if it matters. "
    known = (["first name"] if first else []) + (["car model(s)"] if cars else [])
    known += ["usual dealer"] if dealer else []
    text += (
        "If they ask who they are or whether you know them, say yes and tell them their "
        f"{_join(known, 'and')}. "
    )
    not_them = f"they are not {first}" if first else "this is not them"
    text += f"If the {who} says {not_them}, apologise and treat them as a new customer. "
    return text + PRIVACY_FLOOR


def shared_paragraph(first_names: Sequence[str], *, channel: Channel) -> str:
    """Several customers on one number: unchanged behaviour (ask, never list)."""
    if channel == "call":
        names = ", ".join(n for n in first_names if n) or "unnamed"
        return (
            "This phone number is shared by several customers in our records (first "
            f"names: {names}). Do not guess and do NOT list these names: ask the caller "
            "for their name, and if it matches one of them treat the caller as that "
            "customer, then confirm which car the call is about. If it matches none, "
            "treat them as a new caller. "
        )
    return (
        CHAT_HEAD + "this phone number is shared by several customers. Do not guess who "
        "this is and do not list names: if it matters, ask for their name and which car "
        "the message is about. " + PRIVACY_FLOOR
    )


def unknown_paragraph(*, channel: Channel) -> str:
    """A number that matches nobody: serve them as a new customer."""
    return _CALL_UNKNOWN if channel == "call" else _CHAT_UNKNOWN
