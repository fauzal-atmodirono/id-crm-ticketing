"""Deterministic demo-data generator for the seed_demo_data script (Package D).

Pure, in-memory generation only: no I/O, no network calls, no file writes.
Produces `DemoContact` / `DemoCase` dataclasses and RSA incident payload
dicts that Task 2's API client posts to a live Chatwoot tenant and the
backend's `/rsa/incidents` endpoint respectively (field names below match
`backend/apps/backend/src/chatbot/features/rsa/rsa_router.py::_IncidentRequest`
so Task 2 can POST a payload dict as-is).

Every random draw goes through the `random.Random(seed)` instance threaded
through this module — never the module-level `random.*` functions — so two
calls with the same `(count, batch_id, seed)` are byte-identical. That
determinism is what lets Task 2's purge-safety guard be tested without
touching a tenant. It is deliberately scoped to a *batch* (see `generate`):
two different batches against the same tenant must NOT collide, because
Chatwoot enforces phone/email uniqueness per account.

Vocabulary (case-type mix, channel mix, division -> concern lists) is taken
verbatim from Proton's own monthly/weekly reporting decks, per the plan at
docs/superpowers/plans/2026-08-04-pkg-d-demo-data-and-case-detail.md, so the
seeded data exercises the same aggregation buckets Package E's reports use.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeVar

T = TypeVar("T")

# --- Public data shapes -----------------------------------------------------


@dataclass(frozen=True)
class DemoContact:
    name: str
    phone: str
    email: str
    vehicle_no: str
    vehicle_model: str
    purchased_from: str


@dataclass(frozen=True)
class DemoCase:
    contact_index: int
    channel: str
    case_type: str
    division: str
    concern: str
    status: str
    created_at: datetime
    dealer: str | None
    messages: list[tuple[str, str]]


# --- Vocabulary --------------------------------------------------------------

_FIRST_NAMES = [
    "Ahmad", "Siti", "Wei Ming", "Kavitha", "Farhan", "Aisyah", "Jia Hao",
    "Nur Ain", "Rajesh", "Mei Ling", "Hafiz", "Sarah", "Kumar", "Aina",
    "Zulkifli", "Priya", "Adam", "Nurul", "Chong Wei", "Sofia",
]
_LAST_NAMES = [
    "bin Abdullah", "binti Hassan", "Tan", "Raj", "bin Ismail", "Lim",
    "a/l Muthu", "binti Yusof", "Wong", "bin Karim", "Krishnan", "Lee",
    "binti Zainal", "Ng", "bin Osman", "a/p Samy", "Ooi", "binti Rahman",
    "Teo", "bin Salleh",
]

_DEALERS = [
    "Petaling Jaya", "Shah Alam", "Puchong", "Cyberjaya", "Johor Bahru",
    "Penang", "Ipoh", "Melaka", "Kota Kinabalu", "Kuching",
]

_MODEL_WEIGHTS = [
    ("e.MAS 5", 40),
    ("e.MAS 7", 35),
    ("e.MAS 7 PHEV", 15),
    ("NA", 10),
]

_PLATE_PREFIXES = [
    "W", "B", "J", "P", "N", "M", "A", "C", "D", "R", "T", "K", "S", "V",
]

# June deck: 1024 inquiry / 770 complaint / 17 feedback.
_CASE_TYPE_WEIGHTS = [
    ("Inquiry", 1024),
    ("Complaint", 770),
    ("Feedback", 17),
]

# Weekly deck: WhatsApp ~73%, phone ~16%, email ~9%, social ~2%.
_CHANNEL_WEIGHTS = [
    ("whatsapp", 73),
    ("phone", 16),
    ("email", 9),
    ("social", 2),
]

# Sales/Charging/Apps/After Sales concern lists are verbatim from the decks.
# Product/Marketing/Others aren't broken out in the decks but are genuinely
# rare there (a few percent between them) - not zero, so they get plausible
# concerns and a low sampling weight rather than being left permanently
# empty (an empty report bucket reads as broken, not as a quiet month).
_DIVISION_CONCERNS: dict[str, list[str]] = {
    "Sales": ["Accessories", "Booking", "Delivery", "Promotion", "Trade In", "Transfer Ownership"],
    "Charging": ["Home Charging"],
    "Apps": ["Information", "Profile", "Auto Logout"],
    "After Sales": ["Body", "Spare Part", "User Manual", "Service Operation", "ADAS"],
    "Product": ["Specification", "Feature Request"],
    "Marketing": ["Campaign", "Event"],
    "Others": ["General"],
}
_DIVISION_WEIGHTS = [
    ("Sales", 28),
    ("After Sales", 37),
    ("Apps", 16),
    ("Charging", 10),
    ("Product", 4),
    ("Marketing", 3),
    ("Others", 2),
]

# The deck's division names are display strings ("After Sales"); the warehouse
# has its own canonical vocabulary, `CATEGORY_TO_DIVISION`'s *values* in
# backend/apps/backend/src/chatbot/features/metrics/mapping.py. The live writer
# (backend/.../adapters/chatwoot.py::_classification_labels) emits
# `division_<canonical.lower().replace(" ", "_")>`, and mapping.py reads that
# suffix back RAW -- so a seeded `division_after_sales` would land in its own
# bucket next to real traffic's `division_aftersales`, splitting one division in
# two on every report. Everything the seeder writes for the warehouse
# (`case_category`, the `division_*` label) goes through this map first so demo
# rows are byte-identical in vocabulary to real ones.
_CANONICAL_DIVISION = {
    "Sales": "Sales",
    "After Sales": "Aftersales",
    "Apps": "Apps",
    "Charging": "Charging",
    "Product": "Product",
    "Marketing": "Marketing",
    "Others": "Others",
}

# Share of cases escalated to a dealer. Spec §3 asks for "a minority of cases
# escalated so dealer TAT reporting has rows" -- not all of them. A case with no
# dealer gets no `dealer_<slug>` label and no `dealer_escalated_at`, so it is
# correctly absent from the dealer-turnaround view rather than diluting it.
DEALER_ESCALATION_RATE = 0.22

_STATUS_WEIGHTS = [("resolved", 55), ("open", 30), ("pending", 15)]

# How many cases a given contact ends up with; weighted so most customers
# have exactly one case but some have repeat contacts.
_CASES_PER_CONTACT_WEIGHTS: list[tuple[int, int]] = [(1, 70), (2, 22), (3, 8)]

_RSA_CAUSES = [
    "Flat battery / SOC 0%",
    "Flat tyre",
    "Accident / collision",
    "Charging fault",
    "Software fault",
    "Lockout - key/fob issue",
    "Brake system fault",
    "Motor fault",
]

_CONCERN_OPENERS = {
    "Accessories": "asking about accessories for my {model}",
    "Booking": "asking about my booking for a {model}",
    "Delivery": "asking when my {model} will be delivered",
    "Promotion": "asking about the current promotion on the {model}",
    "Trade In": "asking about trading in my old car for a {model}",
    "Transfer Ownership": "asking how to transfer ownership of my {model}",
    "Home Charging": "having trouble with my home charger for the {model}",
    "Information": "asking how to use a feature in the app",
    "Profile": "unable to update my profile in the app",
    "Auto Logout": "getting logged out of the app automatically",
    "Body": "asking about a body panel issue on my {model}",
    "Spare Part": "asking about ordering a spare part for my {model}",
    "User Manual": "asking where to find the user manual for the {model}",
    "Service Operation": "asking about booking a service appointment for my {model}",
    "ADAS": "having an issue with the ADAS system on my {model}",
    "Specification": "asking about the specifications of the {model}",
    "Feature Request": "requesting a new feature for the app",
    "Campaign": "asking about the latest marketing campaign",
    "Event": "asking about an upcoming test drive event",
    "General": "asking a general question about e.MAS",
}

_CASE_TYPE_OPENERS = {
    "Inquiry": "Hi, I'm {opener}. Can you help?",
    "Complaint": "Hi, I'm really unhappy - {opener}, and it's been a hassle.",
    "Feedback": "Hi, just some feedback: {opener}.",
}

_CASE_TYPE_REPLIES = {
    "Inquiry": (
        "Hi {first_name}, thanks for reaching out! Let me check that for you.",
        "Sure, here's what I found - happy to help further if you need anything else.",
    ),
    "Complaint": (
        "Hi {first_name}, I'm sorry to hear about this - let me look into it right away.",
        "I've raised this with the relevant team and will follow up with an update.",
    ),
    "Feedback": (
        "Hi {first_name}, thank you so much for letting us know - we really appreciate it!",
    ),
}


# --- Helpers ------------------------------------------------------------------


def canonical_division(division: str) -> str:
    """The warehouse's name for one of this module's display divisions.

    Pure and public because `client.py` needs the exact same value in two
    places (the conversation's `case_category` custom attribute and its
    `division_<slug>` label), and a test can pin it against
    `mapping.CATEGORY_TO_DIVISION` without touching a tenant. Unknown values
    pass through unchanged rather than raising -- a division the deck grows
    later should degrade to "shows up under its own name", not "the seeder
    crashes".
    """
    return _CANONICAL_DIVISION.get(division, division)


def _weighted_choice(rnd: random.Random, weighted: list[tuple[T, int]]) -> T:
    items = [item for item, _ in weighted]
    weights = [weight for _, weight in weighted]
    return rnd.choices(items, weights=weights, k=1)[0]


def _unique_phone(rnd: random.Random, used: set[str]) -> str:
    # +999 is the ITU-T E.164 reserved-for-testing country code: it is
    # permanently unassigned, so no number built on it can ever route to a
    # real subscriber - unlike a syntactically-valid-looking +60 number,
    # which is indistinguishable from a real Malaysian mobile number to
    # anything downstream (click-to-call, WhatsApp send, an automation)
    # that might act on it. Still valid E.164 (`+` + digits), so Chatwoot
    # contact validation accepts it.
    while True:
        rest = "".join(str(rnd.randrange(10)) for _ in range(9))
        phone = f"+999{rest}"
        if phone not in used:
            used.add(phone)
            return phone


def _unique_plate(rnd: random.Random, used: set[str]) -> str:
    while True:
        head = "".join(rnd.choice(_PLATE_PREFIXES) for _ in range(rnd.choice([1, 2, 3])))
        digits = rnd.randint(1, 9999)
        plate = f"{head} {digits}"
        if plate not in used:
            used.add(plate)
            return plate


def _make_contact(rnd: random.Random, index: int, used_phones: set[str], used_plates: set[str]) -> DemoContact:
    first = rnd.choice(_FIRST_NAMES)
    last = rnd.choice(_LAST_NAMES)
    name = f"[DEMO] {first} {last}"
    phone = _unique_phone(rnd, used_phones)
    slug = f"{first}.{last}".lower().replace(" ", "").replace("/", "")
    email = f"{slug}.demo{index}@example.invalid"
    vehicle_no = _unique_plate(rnd, used_plates)
    vehicle_model = _weighted_choice(rnd, _MODEL_WEIGHTS)
    purchased_from = rnd.choice(_DEALERS)
    return DemoContact(
        name=name,
        phone=phone,
        email=email,
        vehicle_no=vehicle_no,
        vehicle_model=vehicle_model,
        purchased_from=purchased_from,
    )


def _make_messages(case_type: str, concern: str, model: str, first_name: str) -> list[tuple[str, str]]:
    opener = _CONCERN_OPENERS.get(concern, f"asking about {concern.lower()}").format(model=model)
    opening_line = _CASE_TYPE_OPENERS[case_type].format(opener=opener)
    replies = _CASE_TYPE_REPLIES[case_type]
    messages: list[tuple[str, str]] = [("customer", opening_line)]
    for reply in replies:
        messages.append(("agent", reply.format(first_name=first_name)))
    return messages


def _make_case(
    rnd: random.Random,
    contact_index: int,
    contact: DemoContact,
    created_at: datetime,
) -> DemoCase:
    division = _weighted_choice(rnd, _DIVISION_WEIGHTS)
    concern = rnd.choice(_DIVISION_CONCERNS[division])
    case_type = _weighted_choice(rnd, _CASE_TYPE_WEIGHTS)
    channel = _weighted_choice(rnd, _CHANNEL_WEIGHTS)
    status = _weighted_choice(rnd, _STATUS_WEIGHTS)
    # Minority, not every case -- see DEALER_ESCALATION_RATE.
    dealer = rnd.choice(_DEALERS) if rnd.random() < DEALER_ESCALATION_RATE else None
    first_name = contact.name.removeprefix("[DEMO] ").split(" ")[0]
    messages = _make_messages(case_type, concern, contact.vehicle_model, first_name)
    return DemoCase(
        contact_index=contact_index,
        channel=channel,
        case_type=case_type,
        division=division,
        concern=concern,
        status=status,
        created_at=created_at,
        dealer=dealer,
        messages=messages,
    )


def _random_created_at(rnd: random.Random, now: datetime) -> datetime:
    # Spread over the last ~8 weeks so aging buckets and week-over-week
    # deltas in reporting are non-empty.
    seconds_back = rnd.uniform(0, 8 * 7 * 24 * 60 * 60)
    return now - timedelta(seconds=seconds_back)


def _make_rsa_incident(rnd: random.Random, contact: DemoContact, batch_id: str, now: datetime) -> dict:
    """Build an RSA incident payload with exactly the fields
    `rsa_router.py::_IncidentRequest` accepts (Task 2 POSTs this dict as-is
    to `/rsa/incidents`). The RSA table has no `custom_attributes` column to
    stamp a purge marker on like Chatwoot objects get, so the batch marker
    travels in `created_by` as `demo-seed:<batch_id>` instead - a value a
    real staff-entered row (a user identity) can never collide with, so
    Task 2's RSA purge can match on it by exact string equality.

    Timestamp fields are pre-serialized to ISO-8601 strings (not raw
    `datetime` objects) so the payload is genuinely postable as-is:
    `httpx.post(json=payload)` calls `json.dumps` with no `default=`
    handler, which raises on a bare `datetime`.
    """
    called_in = _random_created_at(rnd, now)
    towing_assigned = called_in + timedelta(minutes=rnd.randint(5, 20))
    arrived_breakdown = towing_assigned + timedelta(minutes=rnd.randint(15, 60))
    arrived_outlet = arrived_breakdown + timedelta(minutes=rnd.randint(20, 90))
    return {
        "incident_date": called_in.date().isoformat(),
        "vehicle_no": contact.vehicle_no,
        "vehicle_model": contact.vehicle_model,
        "cause": rnd.choice(_RSA_CAUSES),
        "purchased_from": contact.purchased_from,
        "breakdown_location": f"{rnd.choice(_DEALERS)} area",
        "arrived_location": rnd.choice(_DEALERS),
        "customer_called_in_time": called_in.isoformat(),
        "towing_assigned_time": towing_assigned.isoformat(),
        "time_arrived_breakdown_area": arrived_breakdown.isoformat(),
        "time_arrived_outlet": arrived_outlet.isoformat(),
        "total_km": rnd.randint(2, 80),
        "late_reason": None,
        "remarks": f"[DEMO] Seeded RSA incident for {contact.vehicle_no}.",
        "created_by": f"demo-seed:{batch_id}",
    }


# --- Public API ---------------------------------------------------------------


def generate(count: int, batch_id: str, seed: int = 20260804) -> tuple[list[DemoContact], list[DemoCase], list[dict]]:
    """Generate `count` demo contacts, their cases and a handful of RSA
    incidents that reuse real plates from those contacts.

    Deterministic **per batch**: two calls with the same `(count, batch_id,
    seed)` produce byte-identical contacts. `batch_id` is mixed into the RNG
    seed rather than only travelling in the RSA payloads, because phones,
    emails and plates are unique *per Chatwoot account*: seeding the same
    tenant twice at the default `seed` used to regenerate the identical first
    contact, whose `create_contact` then 422'd on the duplicate phone and
    killed the run. The runbook's own advice ("seed a small count first and
    watch, then go to 100") is exactly that sequence, so it has to work.
    Per-batch determinism is all the purge/backdate guards actually require --
    they key on `batch_id`, never on "the same seed twice".

    `batch_id` is additionally encoded into every RSA incident payload's
    `created_by` field (see `_make_rsa_incident`) for Task 2's purge guard;
    contact and case objects don't need it there since Task 2 stamps
    `custom_attributes.demo_seed` when it creates the corresponding Chatwoot
    objects.
    """
    # `random.Random` seeds from a str via version-2 hashing (stable across
    # runs and interpreters, unlike `hash()`), so this stays reproducible.
    rnd = random.Random(f"{seed}:{batch_id}")
    now = datetime.now(timezone.utc)

    used_phones: set[str] = set()
    used_plates: set[str] = set()
    contacts = [_make_contact(rnd, i, used_phones, used_plates) for i in range(count)]

    cases: list[DemoCase] = []
    for contact_index, contact in enumerate(contacts):
        num_cases = _weighted_choice(rnd, _CASES_PER_CONTACT_WEIGHTS)
        for _ in range(num_cases):
            created_at = _random_created_at(rnd, now)
            cases.append(_make_case(rnd, contact_index, contact, created_at))

    # RSA incidents reuse a subset of real plates (roughly 1 in 5 contacts),
    # so a vehicle-number search can surface both a conversation and an
    # incident for the same car.
    incidents = [
        _make_rsa_incident(rnd, contact, batch_id, now)
        for contact in contacts
        if rnd.random() < 0.2
    ]
    if not incidents and contacts:
        incidents = [_make_rsa_incident(rnd, contacts[0], batch_id, now)]

    return contacts, cases, incidents
