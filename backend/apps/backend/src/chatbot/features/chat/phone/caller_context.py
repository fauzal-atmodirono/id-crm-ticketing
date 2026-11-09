"""Who is calling: caller context for the AI-first phone line.

Proton's phone line is AI-first: the caller talks straight to the Gemini Live
assistant, with no press-a-number menu. Before this module the assistant knew
nothing about the caller -- the CRM conversation was created anonymously, the
caller's number never reached the voice session, and Customer 360, duplicate
detection and dealer routing all had nothing to key on (RFP 3.3.2, 4.25,
4.31, 4.41-4.46).

What lives here, and why each piece is shaped the way it is:

* **Number handling.** ``usable_caller_id`` decides whether Twilio's ``From``
  is a real number (a browser call arrives as ``client:...``, a withheld one
  as ``anonymous``); ``normalise_mobile`` turns a spoken or typed Malaysian
  number (``01x...``, ``601x...``, ``+601x...``) into E.164.
* **A signed hand-over.** The number travels from ``/voice/phone/incoming``
  (where Twilio's signature proves it) to the Media Streams websocket (which
  anyone can open) as a ``<Parameter>``. ``sign_caller`` binds it to the
  CallSid and its source with an HMAC under the Twilio auth token, so a forged
  websocket ``start`` frame cannot claim to be someone else's number and have
  the assistant greet them by name.
* **A bounded lookup** (``CallerContextService.lookup``): the Chatwoot contact
  by phone (the same ``/contacts/search`` + digits-only, country-code tolerant
  match Customer 360 uses), then that contact's conversations for vehicles,
  the last dealer label and open cases. It runs BEFORE the Live session opens
  (the instruction is fixed at session-open), so it is time-boxed
  (``phone_caller_lookup_timeout_seconds``, 1.5 s) and fills its result in
  place: whatever finished before the deadline is used, and any failure falls
  back to "ask the caller". It never raises.
* **PDPA-shaped model view** (``caller_view``/``build_caller_instruction``):
  the model gets a first name, car models, the last four characters of a
  plate, a dealer display name and an open-case count -- never the phone
  number, the full name or the full plate. It is told to CONFIRM ("am I
  speaking with Ahmad? is this about your X50?"), never to recite.
* **Saving what the caller says** (``CallerContextService.save``): conversation
  custom attributes (``caller_name``, ``vehicle_plate`` -- the case field
  duplicate detection reads --, ``vehicle_model``, ``caller_dealer_text``,
  ``caller_dealer``) plus the contact's EMPTY fields only (a placeholder name,
  a missing ``vehicle_no``). A real contact name is never overwritten: a
  family member calling from the owner's phone must not rename the owner.

Contact attribute keys follow what the codebase already reads: ``vehicle_no``
on the contact (Cases list "Car Plate", Customer 360 plate search, duplicate
detection), ``vehicle_model``/``vehicle_plate`` on the conversation (proton's
only defined conversation vehicle attribute is ``vehicle_model``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.customer360_router import _phones_match, _plate_key
from chatbot.features.privacy.pii import mask_phone_value

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

SOURCE_CALLER_ID = "caller_id"
SOURCE_SPOKEN = "spoken"
SOURCE_WEB_CALL_AS = "web_call_as"
SOURCES = frozenset({SOURCE_CALLER_ID, SOURCE_SPOKEN, SOURCE_WEB_CALL_AS})

_WITHHELD = frozenset({"anonymous", "restricted", "unknown", "private", "unavailable"})
_MIN_E164_DIGITS = 8
_MAX_E164_DIGITS = 15
_MY_INTL_DIGITS = (10, 12)  # 60 + 8..10
_MY_NATIONAL_DIGITS = (9, 11)  # 0 + 8..10

# Bounds on what one lookup may fan out to (a shared dealer number could
# match many contacts).
_MAX_CANDIDATES = 3
_MAX_VEHICLES = 5
_OPEN_STATUSES = frozenset({"open", "pending", "snoozed"})
_PLACEHOLDER_NAMES = frozenset(
    {"", "customer", "caller", "unknown", "phone caller", "new caller", "new customer", "anonymous", "guest"}
)
_DEALER_LABEL = re.compile(r"^dealer_(.+)$")

# Dealer list is consulted on every call; it changes when an operator edits
# the Escalation Routing page, so a short cache is plenty.
_DEALER_CACHE_SECONDS = 300.0

# Told-number lookups allowed per number per window, across all calls.
_SPOKEN_FAILURES_PER_NUMBER = 5
_SPOKEN_FAILURES_PER_NUMBER_DAY = 10
_SPOKEN_DAY_SECONDS = 24 * 3600.0
_SPOKEN_FAILURES_GLOBAL = 100
_SPOKEN_LOOKUP_WINDOW_SECONDS = 3600.0
_SPOKEN_LOOKUP_MAP_CAP = 2048
_DEALER_FETCH_TIMEOUT_SECONDS = 1.5

_MAX_NAME = 80
_MAX_MODEL = 40
_MAX_DEALER_TEXT = 80
_MAX_PLATE = 12

# Generic words people say around a dealer's name; they never identify one.
_DEALER_STOPWORDS = frozenset(
    {
        "proton",
        "dealer",
        "dealership",
        "service",
        "centre",
        "center",
        "pusat",
        "servis",
        "perkhidmatan",
        "cawangan",
        "branch",
        "sdn",
        "bhd",
        "the",
        "at",
        "in",
        "di",
        "outlet",
        "showroom",
        "edaran",
        "workshop",
        "my",
        "a",
        "of",
        "3s",
        "4s",
        "near",
        "nearest",
        "dekat",
        "yang",
    }
)


# --- numbers ------------------------------------------------------------------


def normalise_mobile(raw: str | None) -> str | None:  # noqa: PLR0911 -- one return per accepted form
    """A spoken/typed number -> E.164, or None if it is not one.

    ``+<digits>`` is taken as already international (8-15 digits). Without a
    plus, Malaysian forms are recognised: ``60...`` (international without
    the plus) and ``0...`` (national trunk prefix). Anything else is refused
    rather than guessed at -- a wrong guess looks up somebody else.
    """
    if not raw:
        return None
    text = str(raw).strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if text.startswith("+"):
        if _MIN_E164_DIGITS <= len(digits) <= _MAX_E164_DIGITS:
            return f"+{digits}"
        return None
    if digits.startswith("60") and _MY_INTL_DIGITS[0] <= len(digits) <= _MY_INTL_DIGITS[1]:
        return f"+{digits}"
    if digits.startswith("0") and _MY_NATIONAL_DIGITS[0] <= len(digits) <= _MY_NATIONAL_DIGITS[1]:
        return f"+6{digits}"
    return None


def usable_caller_id(raw: str | None) -> str | None:
    """Twilio's ``From`` as E.164 when it is a real number, else None.

    A browser/softphone call (``client:...``), a SIP URI and a withheld
    number all mean "we do not know who this is" -- the assistant asks.
    """
    if not raw:
        return None
    text = str(raw).strip()
    lowered = text.lower()
    if lowered.startswith(("client:", "sip:")) or lowered in _WITHHELD:
        return None
    return normalise_mobile(text)


def mask(phone: str | None) -> str:
    """Log-safe form of a number (``+60•••••0101``)."""
    return mask_phone_value(phone) if phone else "none"


def sign_caller(secret: str, call_sid: str, source: str, phone: str) -> str:
    msg = f"{call_sid}|{source}|{phone}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def verify_caller(
    secret: str, call_sid: str, source: str, phone: str, signature: str | None
) -> bool:
    if not secret or not signature or not call_sid or not phone:
        return False
    return hmac.compare_digest(sign_caller(secret, call_sid, source, phone), signature)


# --- dealers --------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-z]+", text.lower()) if t and t not in _DEALER_STOPWORDS]


def match_dealer(spoken: str | None, slugs: list[str]) -> str | None:
    """Map what the caller said to ONE configured dealer slug, or None.

    Confident only: every meaningful word of the slug must appear in what was
    said, and exactly one dealer may qualify. Two candidates ("motor" with
    two "... Motor" dealers) is not a match -- a wrong dealer label routes a
    complaint to the wrong business, which is worse than no label.
    """
    said = _tokens(spoken or "")
    if not said:
        return None
    said_set = set(said)
    compact = "".join(said)
    hits = []
    for slug in dict.fromkeys(s.strip().lower() for s in slugs if s and s.strip()):
        want = _tokens(slug.replace("_", " "))
        if not want:
            continue
        # A one-word slug ("sentul") must be the WHOLE meaningful thing said:
        # one shared word is too weak ("Sentul Jaya Motors" is not "sentul").
        multi_word = len(want) >= 2 and set(want) <= said_set  # noqa: PLR2004
        if multi_word or "".join(want) == compact:
            hits.append(slug)
    return hits[0] if len(hits) == 1 else None


def dealer_display(slug: str | None) -> str:
    return (slug or "").replace("_", " ").strip().title()


# --- what we know ---------------------------------------------------------------


@dataclass(frozen=True)
class CallerVehicle:
    plate: str = ""
    model: str = ""


@dataclass(frozen=True)
class CallerCandidate:
    contact_id: int
    name: str
    first_name: str
    vehicles: tuple[CallerVehicle, ...] = ()
    last_dealer: str = ""
    open_cases: int = 0
    contact_plate: str = ""
    contact_model: str = ""


# Exactly the seeding convention ("[DEMO] " prefix, case-sensitive), the same
# test the web call-as contact guard uses (str.startswith(DEMO_PREFIX)).
DEMO_PREFIX = "[DEMO] "
_DEMO_TAG = re.compile(r"^\[DEMO\] ")
_NAME_TOKEN = re.compile(r"[^\W\d_]+")
_MIN_NAME_PREFIX = 3


def _first_name(name: str | None) -> str:
    """First word of a contact name; "" for placeholders. A leading [DEMO]
    tag (seeded demo contacts) is not part of anyone's name."""
    text = " ".join(_DEMO_TAG.sub("", name or "").split())
    if text.lower() in _PLACEHOLDER_NAMES or not re.search(r"[^\W\d_]", text):
        return ""
    return text.split(" ")[0][:_MAX_NAME]


# Given-name parts so common in Malaysia (or not names at all: bin/binti,
# a/l, a/p) that on their own they identify nobody (R37). They never match
# by themselves; the next word has to.
_COMMON_NAME_PARTS = frozenset(
    {
        "muhammad",
        "mohammad",
        "mohammed",
        "muhamad",
        "mohamad",
        "mohamed",
        "mohd",
        "muhd",
        "mhd",
        "md",
        "nur",
        "nurul",
        "noor",
        "nor",
        "siti",
        "abdul",
        "abd",
        "bin",
        "binti",
        "bte",
        "bt",
        "al",
        "ap",
        # lineage / royal prefixes: part of the name, but shared by many
        "wan",
        "nik",
        "syed",
        "sharifah",
        "tengku",
        "tunku",
        "raja",
        "che",
        "megat",
        "puteri",
        "awang",
        "dayang",
    }
)
# Honorifics are not names at all: they are dropped before the two spoken
# words are counted, and never match (round 3). "Tan Sri"/"Puan Sri" are
# joined first so "Tan" the surname is untouched.
_HONORIFICS = frozenset(
    {
        "seri",
        "sri",
        "tun",
        "tohpuan",
        "prof",
        "ustaz",
        "ustazah",
        "mdm",
        "miss",
        "sir",
        "encik",
        "en",
        "puan",
        "pn",
        "cik",
        "tuan",
        "dato",
        "datuk",
        "datin",
        "tansri",
        "puansri",
        "haji",
        "hajah",
        "hj",
        "dr",
        "mr",
        "mrs",
        "ms",
        "madam",
    }
)
_SPOKEN_NAME_WORDS = 2


def _name_tokens(text: str | None) -> list[str]:
    raw = _DEMO_TAG.sub("", text or "").casefold()
    raw = re.sub(r"\ba\s*/\s*([lp])\b", r"a\1", raw)  # a/l, a/p -> al, ap
    raw = re.sub(r"\b(tan|puan)\s+sri\b", r"\1sri", raw)
    raw = re.sub(r"\btoh\s+puan\b", "tohpuan", raw)
    return [t for t in _NAME_TOKEN.findall(raw) if t not in _HONORIFICS]


def _distinctive(tokens: list[str]) -> str:
    return next((t for t in tokens if t not in _COMMON_NAME_PARTS), "")


def names_match(said: str | None, record_name: str | None) -> bool:
    """Does the name the caller gave fit the record? (R37)

    Server-side, so a told number is only confirmed by what the CALLER said,
    never by the model echoing the record. Only the first two spoken words
    count (a string of every common name part must not match everybody);
    common parts (Muhammad, Nur, Siti, bin, ...) never match on their own.
    The first distinctive spoken word must equal the record's first
    distinctive word, or be a prefix of it of at least three letters
    ("Far" for "Farid") -- never the other way round. Honorifics (Encik,
    Dato', Dr, ...) are dropped before counting. A placeholder record
    ("Customer", "Phone caller", "Unknown" -- the adapter creates those WITH
    real phones) never matches anything.
    """
    if _is_placeholder_name(record_name):
        return False
    want = _distinctive(_name_tokens(record_name))
    got = _distinctive(_name_tokens(said)[:_SPOKEN_NAME_WORDS])
    if not want or not got:
        return False
    return got == want or (len(got) >= _MIN_NAME_PREFIX and want.startswith(got))


def names_contradict(said: str | None, record_name: str | None) -> bool:
    """Does a name the caller gave LATER rule the confirmed record out?

    Only a distinctive spoken word that does not fit does: "Muhammad" (or
    "Encik") after a confirmed "Muhammad Hafiz" says nothing new.
    """
    if not _distinctive(_name_tokens(said)[:_SPOKEN_NAME_WORDS]):
        return False
    return not names_match(said, record_name)


def parse_number_list(raw: str | None) -> frozenset[str]:
    """A comma list of numbers -> normalised E.164 set; junk entries dropped."""
    out = set()
    for part in (raw or "").split(","):
        phone = normalise_mobile(part.strip())
        if phone:
            out.add(phone)
    return frozenset(out)


def _is_placeholder_name(name: str | None) -> bool:
    return _first_name(name) == ""


@dataclass
class CallerContext:
    """The lookup result for one call. ``phone`` is the full E.164 number --
    it goes to Chatwoot and nowhere else (never to the model, never to logs).
    """

    phone: str
    source: str
    candidates: list[CallerCandidate] = field(default_factory=list)

    @classmethod
    def from_candidates(cls, phone: str, source: str, rows: list[dict[str, Any]]) -> CallerContext:
        """Build from plain dicts (tests, and the persona fixtures)."""
        cands = [
            CallerCandidate(
                contact_id=int(r["contact_id"]),
                name=str(r.get("name") or ""),
                first_name=_first_name(r.get("name")),
                vehicles=tuple(
                    CallerVehicle(str(v.get("plate") or ""), str(v.get("model") or ""))
                    for v in r.get("vehicles") or []
                ),
                last_dealer=str(r.get("last_dealer") or ""),
                open_cases=int(r.get("open_cases") or 0),
            )
            for r in rows
        ]
        return cls(phone=phone, source=source, candidates=cands)

    @property
    def single(self) -> CallerCandidate | None:
        return self.candidates[0] if len(self.candidates) == 1 else None


@dataclass(frozen=True)
class CallerDetails:
    """What the caller told the assistant (save_caller_details)."""

    name: str = ""
    plate: str = ""
    model: str = ""
    dealer_text: str = ""

    def merged(self, newer: CallerDetails) -> CallerDetails:
        return CallerDetails(
            name=newer.name or self.name,
            plate=newer.plate or self.plate,
            model=newer.model or self.model,
            dealer_text=newer.dealer_text or self.dealer_text,
        )

    def is_empty(self) -> bool:
        return not (self.name or self.plate or self.model or self.dealer_text)


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def parse_caller_details(args: dict[str, Any]) -> CallerDetails:
    """Validate the save_caller_details tool args. The model transcribes
    speech, so a "plate" without both a letter and a digit is dropped rather
    than stored as a car."""
    plate = _plate_key(str(args.get("vehicle_registration") or ""))
    if not (re.search(r"[A-Z]", plate) and re.search(r"\d", plate)) or len(plate) > _MAX_PLATE:
        plate = ""
    return CallerDetails(
        name=_clean(args.get("name"), _MAX_NAME),
        plate=plate,
        model=_clean(args.get("vehicle_model"), _MAX_MODEL),
        dealer_text=_clean(args.get("dealer"), _MAX_DEALER_TEXT),
    )


# --- the model-facing view ------------------------------------------------------


def _plate_ending(plate: str) -> str:
    key = _plate_key(plate)
    return key[-4:] if len(key) > 4 else ""  # noqa: PLR2004 -- the last four


def _vehicle_view(v: CallerVehicle) -> dict[str, str]:
    out = {"model": v.model} if v.model else {}
    ending = _plate_ending(v.plate)
    if ending:
        out["plate_ending"] = ending
    return out


def caller_view(ctx: CallerContext | None) -> dict[str, Any]:
    """What the model may know. No phone number, no surname, no full plate."""
    if ctx is None or not ctx.candidates:
        return {"match": "none", "customers": []}
    return {
        "match": "single" if len(ctx.candidates) == 1 else "multiple_people",
        "customers": [
            {
                "first_name": c.first_name,
                "vehicles": [vv for vv in (_vehicle_view(v) for v in c.vehicles) if vv],
                "usual_dealer": dealer_display(c.last_dealer),
            }
            for c in ctx.candidates
        ],
    }


def _car_phrase(v: CallerVehicle) -> str:
    ending = _plate_ending(v.plate)
    model = v.model or "car"
    return f"{model} (plate ending {ending})" if ending else model


_FLOW = (
    "CALL FLOW: there is no menu -- the caller talks to you directly. Greet, identify "
    "the caller, help them (use kb_search), and hand off only when needed. "
    "SAFETY FIRST: if the caller reports a breakdown, an accident, smoke, a warning "
    "light while driving, or being stranded on a road, FIRST make sure they are safe "
    "(hazard lights on, move away from traffic only if it is safe, call 999 if anyone "
    "is hurt) and help or hand off straight away; collect details only afterwards and "
    "never delay help to ask for them. "
    "PRIVACY: never read out a phone number, a full plate number, an address or any "
    "other personal detail from our records. You may confirm what the caller tells "
    "you, and you may ask a confirming question using only a first name and a car "
    "model. "
)

_UNKNOWN = (
    "This caller is not in our records yet. Naturally, ONE question at a time, and at "
    "most three questions before you start helping, ask for: their name, their car "
    "registration number, and which Proton dealer or service centre they use (or the "
    "nearest one). If they decline or are in a hurry, help them anyway. "
)

_SAVE = (
    "Whenever the caller tells you their name, car registration number, car model or "
    "dealer / service centre, call save_caller_details with what you learned -- it is "
    "silent and instant, so do not mention it, and you may call it more than once. "
)


def _known_instruction(ctx: CallerContext) -> str:
    if len(ctx.candidates) > 1:
        names = ", ".join(c.first_name for c in ctx.candidates if c.first_name) or "unnamed"
        return (
            "This phone number is shared by several customers in our records (first "
            f"names: {names}). Do not guess and do NOT list these names: ask the caller "
            "for their name, and if it matches one of them treat the caller as that "
            "customer, then confirm which car the call is about. If it matches none, "
            "treat them as a new caller. "
        )
    c = ctx.candidates[0]
    parts = []
    if c.first_name:
        parts.append(f"first name {c.first_name}")
    cars = [v for v in c.vehicles if v.model or v.plate]
    if len(cars) == 1:
        parts.append(f"car {_car_phrase(cars[0])}")
    elif len(cars) > 1:
        parts.append("cars on file: " + "; ".join(_car_phrase(v) for v in cars))
    if c.last_dealer:
        parts.append(f"usual dealer {dealer_display(c.last_dealer)}")
    # No open-case count (ruling 9 Nov): the model has no business telling a
    # caller what is on file before an agent has looked at it.
    facts = ", ".join(parts) or "no details on file"
    if c.first_name and len(cars) == 1 and cars[0].model:
        confirm = (
            "In your first reply, after the greeting, confirm identity and car in ONE "
            f"sentence, e.g. 'Am I speaking with {c.first_name}? Is this about your "
            f"{cars[0].model}?'. "
        )
    elif c.first_name:
        confirm = (
            f"In your first reply confirm identity in ONE sentence ('Am I speaking with "
            f"{c.first_name}?'). "
        )
    else:
        confirm = "Ask the caller's name, then confirm the car. "
    which = (
        "They have more than one car on file: ask which car the call is about (you may "
        "mention the models, never the plates). "
        if len(cars) > 1
        else ""
    )
    if not cars:
        which = "Ask for their car registration number and model if it matters. "
    return (
        f"This caller's phone number matches a customer in our records: {facts}. "
        + confirm
        + which
        + "If the caller says this is not them, treat them as a new caller. "
    )


def build_caller_instruction(ctx: CallerContext | None, *, lookup_tool: bool) -> str:
    """The call-flow paragraph appended to the phone system instruction."""
    if ctx is None:
        if lookup_tool:
            who = (
                "We cannot see the caller's phone number. After greeting, ask for the "
                "mobile number registered with Proton and their name, then call "
                "lookup_caller with mobile_number and caller_name (the name exactly as "
                "the caller said it -- never guess or suggest one) and follow the "
                "guidance it returns. If they decline or there is no match, treat them "
                "as a new caller: " + _UNKNOWN
            )
        else:
            who = _UNKNOWN
    elif ctx.candidates:
        who = _known_instruction(ctx)
    else:
        who = _UNKNOWN
    return _FLOW + who + _SAVE


NO_MATCH_GUIDANCE = (
    "We could not confirm that number and name together. Do not say whether the "
    "number is in our records. Treat them as a new caller: " + _UNKNOWN
)


def spoken_match_vehicles(c: CallerCandidate) -> list[dict[str, str]]:
    return [vv for vv in (_vehicle_view(v) for v in c.vehicles) if vv]


def spoken_match_guidance(c: CallerCandidate) -> str:
    """`guidance` for a confirmed spoken number. Deliberately no name: the
    caller has just said theirs, and the record's version is never read back.
    """
    cars = [v for v in c.vehicles if v.model or v.plate]
    if len(cars) == 1:
        car = f"Confirm the car in one short question: is this about their {_car_phrase(cars[0])}? "
    elif cars:
        car = (
            "They have more than one car on file ("
            + "; ".join(_car_phrase(v) for v in cars)
            + "): ask which car the call is about (models only, never plates). "
        )
    else:
        car = "Ask for their car registration number and model if it matters. "
    dealer = f"Their usual dealer is {dealer_display(c.last_dealer)}. " if c.last_dealer else ""
    return (
        "The number and name match a customer. Thank them using the name THEY gave. "
        + car
        + dealer
        + "Never read out the phone number or a full plate. "
    )


# --- the service ----------------------------------------------------------------


def _attrs(obj: dict[str, Any]) -> dict[str, Any]:
    raw = obj.get("custom_attributes")
    return raw if isinstance(raw, dict) else {}


def _labels(conv: dict[str, Any]) -> list[str]:
    raw = conv.get("labels")
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _recency(conv: dict[str, Any]) -> float:
    for key in ("last_activity_at", "timestamp", "created_at"):
        v = conv.get(key)
        if isinstance(v, int | float):
            return float(v)
    return 0.0


def _vehicles(contact: dict[str, Any], convs: list[dict[str, Any]]) -> tuple[CallerVehicle, ...]:
    """Distinct cars: the contact's ``vehicle_no``/``vehicle_model`` plus each
    conversation's ``vehicle_plate``/``vehicle_model``, newest first. A
    model-only mention is dropped when a plated car of that model is known."""
    found: dict[str, CallerVehicle] = {}
    ca = _attrs(contact)
    entries = [(str(ca.get("vehicle_no") or ""), str(ca.get("vehicle_model") or ""))]
    for conv in sorted(convs, key=_recency, reverse=True):
        a = _attrs(conv)
        entries.append(
            (
                str(a.get("vehicle_plate") or a.get("vehicle_no") or ""),
                str(a.get("vehicle_model") or ""),
            )
        )
    for plate_raw, model_raw in entries:
        plate = _plate_key(plate_raw)
        model = " ".join(model_raw.split())[:_MAX_MODEL]
        if not plate and not model:
            continue
        key = plate or f"model:{model.lower()}"
        prev = found.get(key)
        if prev is None:
            found[key] = CallerVehicle(plate, model)
        elif not prev.model and model:
            found[key] = CallerVehicle(prev.plate, model)
    plated = [v for v in found.values() if v.plate]
    model_only = [v for v in found.values() if not v.plate]
    # One plate with no model + one model with no plate: the same car, told
    # in two places (contact vehicle_no, conversation vehicle_model).
    no_model = [v for v in plated if not v.model]
    if len(no_model) == 1 and len(model_only) == 1:
        merged = CallerVehicle(no_model[0].plate, model_only[0].model)
        plated = [merged if v is no_model[0] else v for v in plated]
        model_only = []
    plated_models = {v.model.lower() for v in plated if v.model}
    out = plated + [v for v in model_only if v.model.lower() not in plated_models]
    return tuple(out[:_MAX_VEHICLES])


def _last_dealer(convs: list[dict[str, Any]]) -> str:
    for conv in sorted(convs, key=_recency, reverse=True):
        for label in _labels(conv):
            m = _DEALER_LABEL.match(label)
            if m:
                return m.group(1)
        a = _attrs(conv)
        for key in ("caller_dealer", "purchased_from_dealer"):
            if a.get(key):
                return str(a[key]).strip().lower()
    return ""


def _candidate(contact: dict[str, Any], convs: list[dict[str, Any]]) -> CallerCandidate:
    ca = _attrs(contact)
    return CallerCandidate(
        contact_id=int(contact["id"]),
        name=str(contact.get("name") or ""),
        first_name=_first_name(contact.get("name")),
        vehicles=_vehicles(contact, convs),
        last_dealer=_last_dealer(convs),
        open_cases=sum(1 for c in convs if c.get("status") in _OPEN_STATUSES),
        contact_plate=_plate_key(str(ca.get("vehicle_no") or "")),
        contact_model=str(ca.get("vehicle_model") or ""),
    )


DealerSource = Callable[[], Awaitable[list[str]]]


class CallerContextService:
    """Bounded, fail-open caller lookup + save. ``chatwoot`` is a
    ``ChatwootAdapter`` (duck-typed: search_contacts,
    list_contact_conversations, update_contact, set_conversation_attributes).
    """

    def __init__(
        self,
        chatwoot: Any,
        settings: Settings,
        *,
        dealer_source: DealerSource | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chatwoot = chatwoot
        self._settings = settings
        self._dealer_source = dealer_source
        self._clock = clock
        self._dealer_cache: tuple[float, list[str]] | None = None
        self._spoken_failures: dict[str, list[float]] = {}
        self._global_failures: list[float] = []

    @property
    def settings(self) -> Settings:
        return self._settings

    def _recent(
        self, times: list[float], now: float, window: float = _SPOKEN_LOOKUP_WINDOW_SECONDS
    ) -> list[float]:
        return [t for t in times if now - t < window]

    def allow_spoken_lookup(self, phone: str) -> bool:
        """May a TOLD number be looked up now? (R39 + round 3)

        Only FAILED confirmations count: per number at most
        ``_SPOKEN_FAILURES_PER_NUMBER`` an hour and
        ``_SPOKEN_FAILURES_PER_NUMBER_DAY`` a day, and
        ``_SPOKEN_FAILURES_GLOBAL`` an hour across all numbers. A successful
        lookup costs nothing, so a real customer is only ever refused when
        someone else has burnt the budget for their number (or the global
        one) with wrong names. In-process only -- one backend container per
        tenant; a restart resets it, a second replica would double it.
        """
        now = self._clock()
        self._global_failures = self._recent(self._global_failures, now)
        if len(self._global_failures) >= _SPOKEN_FAILURES_GLOBAL:
            return False
        today = self._recent(self._spoken_failures.get(phone, []), now, _SPOKEN_DAY_SECONDS)
        self._spoken_failures[phone] = today
        if len(today) >= _SPOKEN_FAILURES_PER_NUMBER_DAY:
            return False
        return len(self._recent(today, now)) < _SPOKEN_FAILURES_PER_NUMBER

    def record_spoken_failure(self, phone: str) -> None:
        """A told number whose name did not confirm (or had no record)."""
        now = self._clock()
        self._spoken_failures.setdefault(phone, []).append(now)
        self._global_failures.append(now)
        if len(self._spoken_failures) > _SPOKEN_LOOKUP_MAP_CAP:
            for key in [
                k
                for k, v in self._spoken_failures.items()
                if not self._recent(v, now, _SPOKEN_DAY_SECONDS)
            ]:
                del self._spoken_failures[key]

    async def dealer_slugs(self) -> list[str]:
        """Configured dealer slugs (DealerStore + DEALER_EMAIL_MAP_JSON),
        cached; [] on any failure -- no list just means no dealer label."""
        now = self._clock()
        if self._dealer_cache is not None and now - self._dealer_cache[0] < _DEALER_CACHE_SECONDS:
            return list(self._dealer_cache[1])
        if self._dealer_source is None:
            return []
        try:
            slugs = await asyncio.wait_for(
                self._dealer_source(), timeout=_DEALER_FETCH_TIMEOUT_SECONDS
            )
        except Exception as e:
            _log.warning("phone_caller_dealer_list_failed", error_type=type(e).__name__)
            return []
        clean = list(dict.fromkeys(str(s).strip().lower() for s in slugs if str(s).strip()))
        self._dealer_cache = (now, clean)
        return list(clean)

    async def _fill(
        self, phone: str, contacts: list[dict[str, Any]], convs: dict[int, list[dict[str, Any]]]
    ) -> None:
        found = await self._chatwoot.search_contacts(phone)
        matched = [
            c
            for c in found
            if isinstance(c, dict)
            and c.get("id") is not None
            and _phones_match(phone, c.get("phone_number"))
        ][:_MAX_CANDIDATES]
        contacts.extend(matched)

        async def one(contact: dict[str, Any]) -> None:
            convs[int(contact["id"])] = await self._chatwoot.list_contact_conversations(
                int(contact["id"])
            )

        await asyncio.gather(*(one(c) for c in matched), return_exceptions=True)

    async def lookup(self, phone: str, source: str) -> CallerContext:
        """Never raises. On timeout the contacts already found are kept (a
        name without history beats nothing)."""
        contacts: list[dict[str, Any]] = []
        convs: dict[int, list[dict[str, Any]]] = {}
        timeout = self._settings.phone_caller_lookup_timeout_seconds
        try:
            await asyncio.wait_for(self._fill(phone, contacts, convs), timeout=timeout)
        except TimeoutError:
            _log.warning("phone_caller_lookup_timed_out", phone=mask(phone), found=len(contacts))
        except Exception as e:
            _log.warning(
                "phone_caller_lookup_failed", phone=mask(phone), error_type=type(e).__name__
            )
            contacts = []
        try:
            cands = [_candidate(c, convs.get(int(c["id"]), [])) for c in contacts]
        except Exception as e:
            _log.warning("phone_caller_lookup_parse_failed", error_type=type(e).__name__)
            cands = []
        _log.info(
            "phone_caller_lookup_done", phone=mask(phone), source=source, candidates=len(cands)
        )
        return CallerContext(phone=phone, source=source, candidates=cands)

    async def save(
        self,
        *,
        ticket_id: str,
        phone: str | None,
        source: str,
        details: CallerDetails,
        dealer_slug: str | None,
        contact_writes: bool = False,
    ) -> None:
        """Write what the caller said. Never raises; bounded by the caller.

        ``contact_writes`` (default off) allows filling the number's contact's
        EMPTY fields -- only for a number we can stand behind: network caller
        ID, an allowlisted demo number, or a spoken number whose name matched.
        Otherwise only the call's own conversation is written.
        """
        try:
            await self._save(
                ticket_id=ticket_id,
                phone=phone,
                source=source,
                details=details,
                dealer_slug=dealer_slug,
                contact_writes=contact_writes,
            )
        except Exception as e:
            _log.warning(
                "phone_caller_save_failed", ticket_id=ticket_id, error_type=type(e).__name__
            )

    async def _save(  # noqa: PLR0912 -- one branch per optional field
        self,
        *,
        ticket_id: str,
        phone: str | None,
        source: str,
        details: CallerDetails,
        dealer_slug: str | None,
        contact_writes: bool,
    ) -> None:
        attrs: dict[str, Any] = {}
        if details.name:
            attrs["caller_name"] = details.name
        if details.plate:
            attrs["vehicle_plate"] = details.plate
        if details.model:
            attrs["vehicle_model"] = details.model
        if details.dealer_text:
            attrs["caller_dealer_text"] = details.dealer_text
        if dealer_slug:
            attrs["caller_dealer"] = dealer_slug
        if phone:
            attrs["caller_phone_source"] = source
            if source != SOURCE_CALLER_ID:
                # Not proven by the network: keep it visible on the case so an
                # agent knows the number was told, not detected.
                attrs["caller_phone"] = phone
        if attrs:
            await self._chatwoot.set_conversation_attributes(ticket_id, attrs)
        if not contact_writes or not phone or not (details.name or details.plate or details.model):
            return
        found = await self._chatwoot.search_contacts(phone)
        matched = [
            c
            for c in found
            if isinstance(c, dict)
            and c.get("id") is not None
            and _phones_match(phone, c.get("phone_number"))
        ]
        if len(matched) > 1:
            # One number, several contacts: which one is calling is not
            # known, so none of them is edited.
            _log.info("phone_caller_contact_write_skipped_shared_number", phone=mask(phone))
            return
        contact = next(
            iter(matched),
            None,
        )
        if contact is None:
            return
        if source == SOURCE_WEB_CALL_AS and not str(contact.get("name") or "").startswith(
            DEMO_PREFIX
        ):
            # Ruling R34: a test-console persona call may only fill a seeded
            # [DEMO] contact, never a real customer who happens to own the
            # number.
            _log.info("phone_caller_contact_write_skipped_not_demo", phone=mask(phone))
            return
        current = _attrs(contact)
        payload: dict[str, Any] = {}
        if details.name and _is_placeholder_name(contact.get("name")):
            payload["name"] = details.name
        custom: dict[str, Any] = {}
        if details.plate and not current.get("vehicle_no"):
            custom["vehicle_no"] = details.plate
        if details.model and not current.get("vehicle_model"):
            custom["vehicle_model"] = details.model
        if custom:
            payload["custom_attributes"] = custom
        if payload:
            await self._chatwoot.update_contact(int(contact["id"]), payload)
