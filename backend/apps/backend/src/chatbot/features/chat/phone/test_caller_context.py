"""Caller context for AI-first phone calls (2026-11-09 brief items 1-4, 9).

All numbers are fake (+600000000xx); Chatwoot is a recording fake.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chatbot.features.chat.phone.caller_context import (
    CallerContext,
    CallerContextService,
    CallerDetails,
    build_caller_instruction,
    caller_view,
    dealer_display,
    match_dealer,
    names_contradict,
    names_match,
    normalise_mobile,
    parse_caller_details,
    parse_number_list,
    sign_caller,
    spoken_match_guidance,
    usable_caller_id,
    verify_caller,
)
from chatbot.features.contact_context.personal import PRIVACY_FLOOR
from chatbot.platform.config import Settings

PHONE = "+60000000101"


# --- number handling -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("+60000000101", "+60000000101"),
        ("60000000101", "+60000000101"),
        ("0000000101", "+60000000101"),
        ("012-345 6789", "+60123456789"),
        ("+60 12-345 6789", "+60123456789"),
        ("60123456789", "+60123456789"),
        ("0123456789", "+60123456789"),
        ("01123456789", "+601123456789"),
        ("", None),
        ("12345", None),
        ("call me maybe", None),
        (None, None),
    ],
)
def test_normalise_mobile(raw: str | None, want: str | None) -> None:
    assert normalise_mobile(raw) == want


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("+60000000101", "+60000000101"),
        ("client:proton-web-caller", None),
        ("anonymous", None),
        ("Restricted", None),
        ("sip:someone@example.com", None),
        ("", None),
        (None, None),
    ],
)
def test_usable_caller_id(raw: str | None, want: str | None) -> None:
    assert usable_caller_id(raw) == want


def test_caller_signature_binds_call_source_and_number() -> None:
    sig = sign_caller("tok", "CA1", "caller_id", PHONE)
    assert verify_caller("tok", "CA1", "caller_id", PHONE, sig)
    assert not verify_caller("tok", "CA2", "caller_id", PHONE, sig)
    assert not verify_caller("tok", "CA1", "web_call_as", PHONE, sig)
    assert not verify_caller("tok", "CA1", "caller_id", "+60000000102", sig)
    assert not verify_caller("other", "CA1", "caller_id", PHONE, sig)
    assert not verify_caller("tok", "CA1", "caller_id", PHONE, None)
    assert not verify_caller("", "CA1", "caller_id", PHONE, sig)


# --- dealer matching -------------------------------------------------------

SLUGS = ["petaling_jaya", "caroline_motor", "komang_motor", "sentul"]


@pytest.mark.parametrize(
    ("spoken", "want"),
    [
        ("Petaling Jaya", "petaling_jaya"),
        ("the Proton service centre in petaling jaya", "petaling_jaya"),
        ("Caroline Motor Sdn Bhd", "caroline_motor"),
        ("petaling_jaya", "petaling_jaya"),
        ("Shah Alam service centre", None),
        ("motor", None),  # ambiguous: two dealers share the token
        ("", None),
        # one-word slug: only an exact (compact) match, never one word of a
        # longer name
        ("Sentul", "sentul"),
        ("the Sentul service centre", "sentul"),
        ("Sentul Jaya Motors", None),
    ],
)
def test_match_dealer(spoken: str, want: str | None) -> None:
    assert match_dealer(spoken, SLUGS) == want


def test_dealer_display() -> None:
    assert dealer_display("petaling_jaya") == "Petaling Jaya"
    assert dealer_display("") == ""


def test_parse_caller_details_normalises_and_bounds() -> None:
    d = parse_caller_details(
        {
            "name": "  Ahmad   bin Ali ",
            "vehicle_registration": "dmo-1101",
            "vehicle_model": "x50",
            "dealer": "Petaling Jaya",
        }
    )
    assert d == CallerDetails(
        name="Ahmad bin Ali", plate="DMO1101", model="x50", dealer_text="Petaling Jaya"
    )
    junk = parse_caller_details({"vehicle_registration": "hello", "name": "x" * 500})
    assert junk.plate == ""  # not plate-shaped (no digit)
    assert len(junk.name) <= 80


# --- the model-facing view never carries the number or the full plate ------


def _ctx(candidates: list[dict[str, Any]], *, source: str = "caller_id") -> CallerContext:
    return CallerContext.from_candidates(PHONE, source, candidates)


AHMAD = {
    "contact_id": 11,
    "name": "Ahmad bin Ali",
    "vehicles": [{"plate": "DMO1101", "model": "Proton X50"}],
    "last_dealer": "petaling_jaya",
    "open_cases": 0,
}


def test_caller_view_has_first_name_model_and_plate_ending_only() -> None:
    view = caller_view(_ctx([AHMAD]))
    text = repr(view)
    assert view["match"] == "single"
    assert view["customers"][0]["first_name"] == "Ahmad"
    assert view["customers"][0]["vehicles"] == [{"model": "Proton X50", "plate_ending": "1101"}]
    assert view["customers"][0]["usual_dealer"] == "Petaling Jaya"
    assert "DMO1101" not in text and PHONE not in text and "bin Ali" not in text


def test_instruction_known_caller_is_greeted_personally() -> None:
    """R47: greet by first name with the car, never re-ask what we have."""
    text = build_caller_instruction(_ctx([AHMAD]), lookup_tool=False)
    assert "Ahmad" in text and "Proton X50" in text
    assert "greet them by first name" in text and "Hi Ahmad" in text
    assert "Never ask for what we already have" in text
    assert "who they are" in text  # "who am I?" gets an answer
    assert "Am I speaking" not in text
    assert PRIVACY_FLOOR in text
    assert "1101" in text  # plate ending is available to confirm against
    assert "DMO1101" not in text and PHONE not in text
    assert "save_caller_details" in text
    assert "SAFETY" in text.upper()


def test_instruction_unknown_caller_asks_at_most_three_questions() -> None:
    text = build_caller_instruction(_ctx([]), lookup_tool=False)
    assert "at most three" in text
    assert "registration" in text and "dealer" in text
    assert "lookup_caller" not in text


def test_instruction_no_number_asks_for_mobile_and_uses_lookup_tool() -> None:
    text = build_caller_instruction(None, lookup_tool=True)
    assert "lookup_caller" in text and "mobile number" in text


def test_instruction_shared_number_asks_who_without_listing() -> None:
    other = {**AHMAD, "contact_id": 12, "name": "Siti"}
    text = build_caller_instruction(_ctx([AHMAD, other]), lookup_tool=False)
    assert "ask the caller for their name" in text
    assert "do not list" in text.lower()


def test_instruction_two_cars_asks_which() -> None:
    lim = {
        **AHMAD,
        "name": "Lim",
        "vehicles": [
            {"plate": "DMO1103", "model": "Proton X70"},
            {"plate": "DMO1104", "model": "Proton Persona"},
        ],
    }
    text = build_caller_instruction(_ctx([lim]), lookup_tool=False)
    assert "which car" in text
    assert "Proton X70" in text and "Proton Persona" in text


def test_instruction_no_number_asks_for_number_and_name_together() -> None:
    text = build_caller_instruction(None, lookup_tool=True)
    assert "caller_name" in text and "mobile number" in text


def test_model_view_has_no_open_case_count() -> None:
    view = caller_view(_ctx([{**AHMAD, "open_cases": 3}]))
    assert "open_cases" not in repr(view)
    text = build_caller_instruction(_ctx([{**AHMAD, "open_cases": 3}]), lookup_tool=False)
    assert "open case" not in text


@pytest.mark.parametrize(
    ("said", "record", "want"),
    [
        ("Ahmad", "Ahmad bin Ali", True),
        ("ahmad bin ali", "Ahmad", True),
        ("Mad", "Ahmad", False),
        ("Ahmed", "Ahmad", False),
        ("Siti", "Ahmad", False),
        ("", "Ahmad", False),
        ("Ahmad", "", False),
        ("Lim", "Lim Wei", True),
        ("Li", "Lim", False),  # prefix needs 3 letters
        ("Far", "Farid", True),  # spoken is a prefix of the record's word
        ("Faridah", "Farid", False),  # no reverse prefix
        # R37: common given-name parts never match on their own
        ("Nurul", "Nurul Huda", False),
        ("Nurul Huda", "Nurul Huda", True),
        ("Nur", "Nurul Huda", False),
        ("Muhammad", "Muhammad Hafiz", False),
        ("Muhammad Hafiz", "Muhammad Hafiz", True),
        ("Mohd Hafiz", "Muhammad Hafiz", True),
        ("Siti", "Siti Aminah", False),
        ("Abdul Rahman", "Abdul Rahman", True),
        ("Kumar a/l Rajan", "Kumar a/l Rajan", True),
        # only the first two spoken words count
        ("Kamal Hafiz Ahmad", "Ahmad", False),
        ("Encik Tuan Ahmad", "Ahmad", True),  # honorifics are not counted words
    ],
)
def test_names_match(said: str, record: str, want: bool) -> None:
    assert names_match(said, record) is want


@pytest.mark.parametrize(
    "record",
    ["Ahmad bin Ali", "Siti Aminah", "Nurul Huda", "Muhammad Hafiz", "Ali", "Abdul Rahman"],
)
def test_a_string_of_common_name_parts_matches_nobody(record: str) -> None:
    assert not names_match("Muhammad Mohd Nur Siti Ahmad Abd Ali", record)


@pytest.mark.parametrize(
    ("said", "record"),
    [("Customer", "Customer"), ("Phone", "Phone caller"), ("Unknown", "Unknown"), ("x", "")],
)
def test_a_placeholder_contact_never_confirms(said: str, record: str) -> None:
    """The adapter creates "Customer" contacts WITH real phones: saying
    "Customer" must not unlock one."""
    assert not names_match(said, record)


@pytest.mark.parametrize(
    ("said", "record", "want"),
    [
        ("Encik Ahmad", "Ahmad bin Ali", True),
        ("Dato' Seri Ahmad", "Ahmad", True),  # "seri" is an honorific now
        ("Dr Lim", "Lim Wei", True),
        ("Tan Sri Ahmad", "Ahmad", True),
        ("Puan Sri Aminah", "Siti Aminah", True),
        ("Encik", "Encik Ahmad", False),  # an honorific never matches
        ("Wan", "Wan Ahmad", False),  # a lineage prefix needs the next word
        ("Wan Ahmad", "Wan Ahmad", True),
        ("Syed Ali", "Syed Hamid", False),
        ("Haji Ahmad", "Ahmad", True),
    ],
)
def test_honorifics_and_lineage_prefixes(said: str, record: str, want: bool) -> None:
    assert names_match(said, record) is want


def test_a_shorter_name_does_not_contradict() -> None:
    assert not names_contradict("Muhammad", "Muhammad Hafiz")
    assert not names_contradict("Encik", "Muhammad Hafiz")
    assert names_contradict("Siti Aminah", "Muhammad Hafiz")
    assert not names_contradict("Hafiz", "Muhammad Hafiz")


def test_demo_tag_is_not_a_first_name() -> None:
    ctx = _ctx([{**AHMAD, "name": "[DEMO] Ahmad"}])
    assert ctx.candidates[0].first_name == "Ahmad"
    assert ctx.candidates[0].name == "[DEMO] Ahmad"  # the full name is kept (ring card)
    assert caller_view(ctx)["customers"][0]["first_name"] == "Ahmad"
    assert "[DEMO]" not in build_caller_instruction(ctx, lookup_tool=False)
    assert names_match("Ahmad", "[DEMO] Ahmad")
    assert _ctx([{**AHMAD, "name": "[DEMO] "}]).candidates[0].first_name == ""
    # strict: the same "[DEMO] " startswith the call-as contact guard uses
    assert _ctx([{**AHMAD, "name": "[demo] Ahmad"}]).candidates[0].first_name == "[demo]"


def test_lookup_guidance_for_a_spoken_match_has_no_name() -> None:
    ctx = _ctx([AHMAD], source="spoken")
    text = spoken_match_guidance(ctx.candidates[0])
    assert "Ahmad" not in text and "Proton X50" in text and "1101" in text


def test_number_allowlist_parses_and_normalises() -> None:
    assert parse_number_list(" +60000000101, 0000000102 ,junk,,") == frozenset(
        {"+60000000101", "+60000000102"}
    )
    assert parse_number_list("") == frozenset()


# --- the service -----------------------------------------------------------


class _FakeChatwoot:
    def __init__(
        self,
        contacts: list[dict[str, Any]] | None = None,
        conversations: dict[int, list[dict[str, Any]]] | None = None,
        *,
        delay: float = 0.0,
        conv_delay: float = 0.0,
    ) -> None:
        self.contacts = contacts or []
        self.conversations = conversations or {}
        self.delay = delay
        self.conv_delay = conv_delay
        self.searches: list[str] = []
        self.contact_updates: list[tuple[int, dict[str, Any]]] = []
        self.conv_attrs: list[tuple[str, dict[str, Any]]] = []

    async def search_contacts(self, query: str) -> list[dict[str, Any]]:
        self.searches.append(query)
        await asyncio.sleep(self.delay)
        return list(self.contacts)

    async def list_contact_conversations(self, contact_id: int) -> list[dict[str, Any]]:
        await asyncio.sleep(self.conv_delay)
        return list(self.conversations.get(contact_id, []))

    async def update_contact(self, contact_id: int, payload: dict[str, Any]) -> bool:
        self.contact_updates.append((contact_id, payload))
        return True

    async def set_conversation_attributes(self, ticket_id: str, attributes: dict[str, Any]) -> None:
        self.conv_attrs.append((ticket_id, attributes))


def _settings(**kw: Any) -> Settings:
    return Settings(phone_caller_context_enabled=True, **kw)


def _service(cw: _FakeChatwoot, slugs: list[str] | None = None) -> CallerContextService:
    async def dealers() -> list[str]:
        return list(slugs or [])

    return CallerContextService(cw, _settings(), dealer_source=dealers)


async def test_lookup_known_caller_reads_contact_and_conversations() -> None:
    cw = _FakeChatwoot(
        contacts=[
            {
                "id": 11,
                "name": "Ahmad bin Ali",
                "phone_number": PHONE,
                "custom_attributes": {"vehicle_no": "DMO 1101"},
            },
            {"id": 99, "name": "Someone", "phone_number": "+60000000999"},
        ],
        conversations={
            11: [
                {
                    "id": 5,
                    "status": "resolved",
                    "last_activity_at": 10,
                    "labels": ["dealer_petaling_jaya"],
                    "custom_attributes": {"vehicle_model": "Proton X50"},
                },
                {
                    "id": 6,
                    "status": "open",
                    "last_activity_at": 20,
                    "labels": [],
                    "custom_attributes": {},
                },
            ]
        },
    )
    ctx = await _service(cw).lookup(PHONE, "caller_id")
    assert [c.contact_id for c in ctx.candidates] == [11]  # only the exact phone match
    c = ctx.candidates[0]
    assert c.first_name == "Ahmad"
    assert c.last_dealer == "petaling_jaya"
    assert c.open_cases == 1
    assert [(v.plate, v.model) for v in c.vehicles] == [("DMO1101", "Proton X50")]


async def test_lookup_two_cars_from_conversation_attributes() -> None:
    cw = _FakeChatwoot(
        contacts=[{"id": 13, "name": "Lim", "phone_number": "+60000000103"}],
        conversations={
            13: [
                {
                    "id": 1,
                    "status": "resolved",
                    "custom_attributes": {
                        "vehicle_plate": "DMO1103",
                        "vehicle_model": "Proton X70",
                    },
                },
                {
                    "id": 2,
                    "status": "resolved",
                    "custom_attributes": {
                        "vehicle_plate": "DMO1104",
                        "vehicle_model": "Proton Persona",
                    },
                },
                {
                    "id": 3,
                    "status": "resolved",
                    "custom_attributes": {"vehicle_model": "Proton X70"},
                },
            ]
        },
    )
    ctx = await _service(cw).lookup("+60000000103", "caller_id")
    plates = sorted(v.plate for v in ctx.candidates[0].vehicles)
    assert plates == ["DMO1103", "DMO1104"]


async def test_lookup_placeholder_name_is_not_a_first_name() -> None:
    cw = _FakeChatwoot(contacts=[{"id": 15, "name": "Customer", "phone_number": PHONE}])
    ctx = await _service(cw).lookup(PHONE, "caller_id")
    assert ctx.candidates[0].first_name == ""


async def test_lookup_is_bounded_and_fails_open() -> None:
    cw = _FakeChatwoot(contacts=[{"id": 11, "name": "Ahmad", "phone_number": PHONE}], delay=5.0)
    svc = CallerContextService(
        cw, _settings(phone_caller_lookup_timeout_seconds=0.05), dealer_source=None
    )
    loop = asyncio.get_running_loop()
    start = loop.time()
    ctx = await svc.lookup(PHONE, "caller_id")
    assert loop.time() - start < 1.0
    assert ctx.candidates == [] and ctx.phone == PHONE


async def test_lookup_keeps_contact_when_history_is_slow() -> None:
    cw = _FakeChatwoot(
        contacts=[{"id": 11, "name": "Ahmad", "phone_number": PHONE}],
        conversations={11: [{"id": 1, "status": "open"}]},
        conv_delay=5.0,
    )
    svc = CallerContextService(
        cw, _settings(phone_caller_lookup_timeout_seconds=0.1), dealer_source=None
    )
    ctx = await svc.lookup(PHONE, "caller_id")
    assert [c.first_name for c in ctx.candidates] == ["Ahmad"]


async def test_lookup_never_raises() -> None:
    class _Boom(_FakeChatwoot):
        async def search_contacts(self, query: str) -> list[dict[str, Any]]:
            raise RuntimeError("chatwoot down")

    ctx = await _service(_Boom()).lookup(PHONE, "caller_id")
    assert ctx.candidates == []


async def test_dealer_slugs_cached_and_fail_open() -> None:
    calls = 0

    async def dealers() -> list[str]:
        nonlocal calls
        calls += 1
        return ["petaling_jaya"]

    svc = CallerContextService(_FakeChatwoot(), _settings(), dealer_source=dealers)
    assert await svc.dealer_slugs() == ["petaling_jaya"]
    assert await svc.dealer_slugs() == ["petaling_jaya"]
    assert calls == 1

    async def broken() -> list[str]:
        raise RuntimeError("firestore down")

    svc2 = CallerContextService(_FakeChatwoot(), _settings(), dealer_source=broken)
    assert await svc2.dealer_slugs() == []


async def test_save_writes_conversation_attributes_and_fills_empty_contact_fields() -> None:
    cw = _FakeChatwoot(
        contacts=[
            {"id": 21, "name": "Customer", "phone_number": "+60000000105", "custom_attributes": {}}
        ]
    )
    details = CallerDetails(
        name="Hafiz", plate="DMO1106", model="Proton Saga", dealer_text="Shah Alam service centre"
    )
    await _service(cw).save(
        ticket_id="T-1",
        phone="+60000000105",
        source="caller_id",
        details=details,
        dealer_slug=None,
        contact_writes=True,
    )
    ticket, attrs = cw.conv_attrs[-1]
    assert ticket == "T-1"
    assert attrs["caller_name"] == "Hafiz"
    assert attrs["vehicle_plate"] == "DMO1106"
    assert attrs["vehicle_model"] == "Proton Saga"
    assert attrs["caller_dealer_text"] == "Shah Alam service centre"
    assert "caller_dealer" not in attrs
    cid, payload = cw.contact_updates[-1]
    assert cid == 21
    assert payload["name"] == "Hafiz"
    assert payload["custom_attributes"] == {"vehicle_no": "DMO1106", "vehicle_model": "Proton Saga"}


async def test_save_never_overwrites_a_real_contact_name_or_plate() -> None:
    cw = _FakeChatwoot(
        contacts=[
            {
                "id": 11,
                "name": "Ahmad bin Ali",
                "phone_number": PHONE,
                "custom_attributes": {"vehicle_no": "DMO1101"},
            }
        ]
    )
    details = CallerDetails(name="Siti", plate="DMO9999", model="", dealer_text="Petaling Jaya")
    await _service(cw).save(
        ticket_id="T-1",
        phone=PHONE,
        source="caller_id",
        details=details,
        dealer_slug="petaling_jaya",
        contact_writes=True,
    )
    attrs = cw.conv_attrs[-1][1]
    assert attrs["caller_dealer"] == "petaling_jaya"
    assert attrs["caller_name"] == "Siti"
    assert cw.contact_updates == []  # nothing to fill in


async def test_save_without_contact_writes_touches_only_the_conversation() -> None:
    """A spoken number nobody confirmed must never rename or re-plate the
    contact that number belongs to."""
    cw = _FakeChatwoot(
        contacts=[{"id": 21, "name": "Customer", "phone_number": PHONE, "custom_attributes": {}}]
    )
    await _service(cw).save(
        ticket_id="T-1",
        phone=PHONE,
        source="spoken",
        details=CallerDetails(name="Hafiz", plate="DMO1106"),
        dealer_slug=None,
        contact_writes=False,
    )
    assert cw.conv_attrs and cw.contact_updates == [] and cw.searches == []


async def test_save_skips_contact_writes_when_the_number_matches_several_contacts() -> None:
    cw = _FakeChatwoot(
        contacts=[
            {"id": 21, "name": "Customer", "phone_number": PHONE},
            {"id": 22, "name": "Customer", "phone_number": "60000000101"},
        ]
    )
    await _service(cw).save(
        ticket_id="T-1",
        phone=PHONE,
        source="caller_id",
        details=CallerDetails(name="Hafiz"),
        dealer_slug=None,
        contact_writes=True,
    )
    assert cw.contact_updates == []


async def test_web_call_as_only_writes_to_a_demo_contact() -> None:
    """Ruling R34: a console persona call never edits a real customer."""
    real = _FakeChatwoot(contacts=[{"id": 21, "name": "Customer", "phone_number": PHONE}])
    await _service(real).save(
        ticket_id="T-1",
        phone=PHONE,
        source="web_call_as",
        details=CallerDetails(name="Hafiz"),
        dealer_slug=None,
        contact_writes=True,
    )
    assert real.contact_updates == []
    demo = _FakeChatwoot(
        contacts=[
            {"id": 22, "name": "[DEMO] Ahmad", "phone_number": PHONE, "custom_attributes": {}}
        ]
    )
    await _service(demo).save(
        ticket_id="T-1",
        phone=PHONE,
        source="web_call_as",
        details=CallerDetails(plate="DMO1101"),
        dealer_slug=None,
        contact_writes=True,
    )
    assert demo.contact_updates == [(22, {"custom_attributes": {"vehicle_no": "DMO1101"}})]


def test_spoken_lookups_are_limited_by_failures_per_number_across_calls() -> None:
    now = [0.0]
    svc = CallerContextService(_FakeChatwoot(), _settings(), clock=lambda: now[0])
    # successful lookups never count (R39): the real customer is not locked out
    for _ in range(20):
        assert svc.allow_spoken_lookup(PHONE)
    for _ in range(5):
        svc.record_spoken_failure(PHONE)
    assert not svc.allow_spoken_lookup(PHONE)
    assert svc.allow_spoken_lookup("+60000000102")  # per number
    now[0] += 3601
    assert svc.allow_spoken_lookup(PHONE)  # the window slides


def test_spoken_lookups_have_a_daily_cap_per_number() -> None:
    now = [0.0]
    svc = CallerContextService(_FakeChatwoot(), _settings(), clock=lambda: now[0])
    for _hour in range(2):
        for _ in range(5):
            svc.record_spoken_failure(PHONE)
        now[0] += 3601
    # 10 failures today: the hourly window is clear but the day is spent
    assert not svc.allow_spoken_lookup(PHONE)
    now[0] += 24 * 3600
    assert svc.allow_spoken_lookup(PHONE)


def test_spoken_lookups_have_a_global_failure_budget() -> None:
    now = [0.0]
    svc = CallerContextService(_FakeChatwoot(), _settings(), clock=lambda: now[0])
    for i in range(100):
        svc.record_spoken_failure(f"+600000{i:05d}")
    assert not svc.allow_spoken_lookup("+60123456789")  # budget spent: nobody
    now[0] += 3601
    assert svc.allow_spoken_lookup("+60123456789")


async def test_save_records_unverified_number_on_the_conversation() -> None:
    cw = _FakeChatwoot()
    await _service(cw).save(
        ticket_id="T-1", phone=PHONE, source="spoken", details=CallerDetails(), dealer_slug=None
    )
    attrs = cw.conv_attrs[-1][1]
    assert attrs["caller_phone_source"] == "spoken"
    assert attrs["caller_phone"] == PHONE


async def test_save_never_raises() -> None:
    class _Boom(_FakeChatwoot):
        async def set_conversation_attributes(
            self, ticket_id: str, attributes: dict[str, Any]
        ) -> None:
            raise RuntimeError("down")

    await _service(_Boom()).save(
        ticket_id="T-1",
        phone=PHONE,
        source="caller_id",
        details=CallerDetails(name="A"),
        dealer_slug=None,
    )


def test_more_placeholders_never_confirm() -> None:
    from chatbot.features.chat.phone.caller_context import names_match

    for name in ("[DEMO] New caller", "New customer", "Anonymous", "Guest"):
        assert not names_match(name, name.replace("[DEMO] ", "")), name


def test_more_honorifics_are_ignored() -> None:
    from chatbot.features.chat.phone.caller_context import names_match

    assert names_match("Ahmad bin Ali", "Dato' Seri Ahmad")
    assert names_match("Ahmad bin Ali", "Prof Ahmad")
    assert names_match("Aminah Yusof", "Toh Puan Aminah")
    assert not names_match("Datuk Seri Yusof", "Dato' Seri Hafiz")


@pytest.mark.parametrize(
    "candidates",
    [
        [AHMAD],
        [
            {
                **AHMAD,
                "vehicles": [
                    {"plate": "DMO1103", "model": "Proton X70"},
                    {"plate": "DMO1104", "model": "Proton Persona"},
                ],
            }
        ],
        [{**AHMAD, "name": ""}],
        [AHMAD, {**AHMAD, "contact_id": 12, "name": "Siti Binti Example"}],
        [],
    ],
)
def test_no_instruction_carries_a_full_phone_plate_or_surname(
    candidates: list[dict[str, Any]],
) -> None:
    text = build_caller_instruction(_ctx(candidates), lookup_tool=False)
    for secret in (PHONE, "60000000101", "DMO1101", "DMO1103", "DMO1104", "bin Ali", "Example"):
        assert secret not in text
    assert PRIVACY_FLOOR in text


def test_instruction_without_first_name_asks_it_once() -> None:
    text = build_caller_instruction(_ctx([{**AHMAD, "name": ""}]), lookup_tool=False)
    assert "ask for their name once" in text and "Proton X50" in text


def test_lookup_failure_is_marked_failed_and_a_miss_is_not() -> None:
    class _Down:
        async def search_contacts(self, phone: str) -> list[dict[str, Any]]:
            raise RuntimeError("chatwoot down")

    class _Empty:
        async def search_contacts(self, phone: str) -> list[dict[str, Any]]:
            return []

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    down = asyncio.run(CallerContextService(_Down(), s).lookup(PHONE, "caller_id"))
    assert down.failed and down.candidates == []
    miss = asyncio.run(CallerContextService(_Empty(), s).lookup(PHONE, "caller_id"))
    assert not miss.failed and miss.candidates == []
