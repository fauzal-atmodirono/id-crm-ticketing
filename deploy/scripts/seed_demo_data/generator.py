"""Deterministic demo-data generator for the seed_demo_data script (Package D).

Pure, in-memory generation only: no I/O, no network calls, no file writes.
Produces `DemoContact` / `DemoCase` dataclasses and RSA incident payload
dicts that Task 2's API client posts to a live Chatwoot tenant and the
backend's `/rsa/incidents` endpoint respectively (field names below match
`backend/apps/backend/src/chatbot/features/rsa/rsa_router.py::_IncidentRequest`
so Task 2 can POST a payload dict as-is).

Every random draw goes through the `random.Random(seed)` instance threaded
through this module — never the module-level `random.*` functions — so two
calls with the same `(count, seed)` are byte-identical. That determinism is
what lets Task 2's purge-safety guard be tested without touching a tenant.

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
    dealer: str
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

# Only divisions the decks give a concern breakdown for are sampled; the
# other DIVISIONS values (Product, Marketing, Others) are left for a future
# generator update once the decks document concerns for them.
_DIVISION_CONCERNS: dict[str, list[str]] = {
    "Sales": ["Accessories", "Booking", "Delivery", "Promotion", "Trade In", "Transfer Ownership"],
    "Charging": ["Home Charging"],
    "Apps": ["Information", "Profile", "Auto Logout"],
    "After Sales": ["Body", "Spare Part", "User Manual", "Service Operation", "ADAS"],
}
_DIVISION_WEIGHTS = [
    ("Sales", 30),
    ("After Sales", 40),
    ("Apps", 18),
    ("Charging", 12),
]

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


def _weighted_choice(rnd: random.Random, weighted: list[tuple[T, int]]) -> T:
    items = [item for item, _ in weighted]
    weights = [weight for _, weight in weighted]
    return rnd.choices(items, weights=weights, k=1)[0]


def _unique_phone(rnd: random.Random, used: set[str]) -> str:
    # +60 1X-XXXXXXX Malaysian mobile format. Purely synthetic: this script
    # never dials or sends to these numbers, only stores them as demo
    # contact records.
    while True:
        prefix = rnd.choice(["10", "11", "12", "13", "14", "16", "17", "18", "19"])
        rest = "".join(str(rnd.randrange(10)) for _ in range(7))
        phone = f"+60{prefix}{rest}"
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
    email = f"{slug}.demo{index}@example.com"
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
    dealer = rnd.choice(_DEALERS)
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
        "customer_called_in_time": called_in,
        "towing_assigned_time": towing_assigned,
        "time_arrived_breakdown_area": arrived_breakdown,
        "time_arrived_outlet": arrived_outlet,
        "total_km": rnd.randint(2, 80),
        "late_reason": None,
        "remarks": f"[DEMO] Seeded RSA incident for {contact.vehicle_no}.",
        "created_by": "seed_demo_data",
        "batch_id": batch_id,
    }


# --- Public API ---------------------------------------------------------------


def generate(count: int, batch_id: str, seed: int = 20260804) -> tuple[list[DemoContact], list[DemoCase], list[dict]]:
    """Generate `count` demo contacts, their cases and a handful of RSA
    incidents that reuse real plates from those contacts.

    Deterministic for a given (count, batch_id-independent) seed: two calls
    with the same seed produce byte-identical contacts. `batch_id` is
    stamped on every RSA incident payload for Task 2's purge guard; contact
    and case objects don't need it here since Task 2 stamps
    `custom_attributes.demo_seed` when it creates the corresponding Chatwoot
    objects.
    """
    rnd = random.Random(seed)
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
