"""The demo-record predicates and the per-write guards in DemoCrmClient.

The seeder runs on a live tenant next to other "[DEMO]" data it does not
own (deploy/scripts/seed_demo_data contacts, handsets renamed by
agent/app/services/demo_persona.py, hand-named contacts). These prove a
write is refused -- before any request leaves -- whenever the FRESH copy of
the target is not a record this seeder owns.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chatbot.features.demo.conftest import INBOX, WHATSAPP_INBOX, FakeChatwoot
from chatbot.features.demo.crm import DemoCrmClient
from chatbot.features.demo.safety import (
    DemoSafetyError,
    is_demo_rsa,
    is_seeder_contact,
    is_seeder_conversation,
)

NUMS = ("+60000000101", "+60000000104")
MARK = {"demo_seed": "demo_persona"}


def _contact(
    name: object = "[DEMO] Ahmad", phone: str | None = "+60000000101", **attrs: object
) -> dict:
    return {"id": 1, "name": name, "phone_number": phone, "custom_attributes": {**MARK, **attrs}}


@pytest.mark.parametrize(
    "name",
    [
        "Ahmad",
        "DEMO Ahmad",
        "Ahmad [DEMO]",
        "[demo] Ahmad",
        "[DEMO]Ahmad",
        " [DEMO] Ahmad",
        "",
        None,
    ],
)
def test_near_miss_contact_names_are_not_ours(name: object) -> None:
    assert not is_seeder_contact(_contact(name), NUMS)


def test_a_contact_is_ours_only_with_name_marker_and_persona_number() -> None:
    assert is_seeder_contact(_contact(), NUMS)
    # seed_demo_data contact: [DEMO] name, but its own batch marker
    budi = _contact("[DEMO] Budi Santoso", "+60000000101", demo_seed="batch-1")
    assert not is_seeder_contact(budi, NUMS)
    # renamed real handset / hand-named contact: no marker
    assert not is_seeder_contact(
        {"id": 1, "name": "[DEMO] x", "phone_number": "+60000000101"}, NUMS
    )
    # marker + name but not a persona number
    assert not is_seeder_contact(_contact(phone="+60123000009"), NUMS)
    assert not is_seeder_contact(_contact(phone=None), NUMS)
    assert not is_seeder_contact(None, NUMS)


def test_conversation_rule() -> None:
    ours = _contact()
    real = {"name": "Ahmad", "custom_attributes": {}}

    def check(conv: dict) -> bool:
        return is_seeder_conversation(conv, inbox_id=INBOX, numbers=NUMS)

    # label AND marker
    assert check({"labels": ["demo_persona"], "custom_attributes": MARK, "meta": {"sender": real}})
    # label without marker, real sender: not ours
    assert not check(
        {"labels": ["demo_persona"], "custom_attributes": {}, "meta": {"sender": real}}
    )
    # demo inbox + strict sender (a rehearsal call)
    assert check({"inbox_id": INBOX, "labels": [], "meta": {"sender": ours}})
    # same sender but on the WhatsApp inbox: not ours
    assert not check({"inbox_id": WHATSAPP_INBOX, "labels": [], "meta": {"sender": ours}})
    for labels in ([], ["audit_test"], ["demo_persona_old"], ["Demo_Persona"]):
        assert not check(
            {
                "inbox_id": INBOX,
                "labels": labels,
                "custom_attributes": MARK,
                "meta": {"sender": real},
            }
        )
    assert not is_seeder_conversation(None, inbox_id=INBOX, numbers=NUMS)


def test_rsa_marker_needs_both_created_by_and_remarks() -> None:
    assert is_demo_rsa(SimpleNamespace(created_by="demo_persona", remarks="[DEMO] x"))
    assert not is_demo_rsa(SimpleNamespace(created_by="demo_persona", remarks="real remark"))
    assert not is_demo_rsa(SimpleNamespace(created_by="staff", remarks="[DEMO] x"))
    assert not is_demo_rsa(SimpleNamespace(created_by=None, remarks=None))


# --- guards in the client: refuse BEFORE any mutating request -----------------


async def test_delete_contact_refuses_contacts_we_do_not_own(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    ids = [
        fake.add_contact("Ahmad Real", "+60000000101"),
        fake.add_contact("[DEMO] Budi Santoso", "+60000000104", demo_seed="batch-1"),
        fake.add_contact("[DEMO] x", "+60123000009"),
    ]
    for cid in ids:
        with pytest.raises(DemoSafetyError):
            await crm.delete_contact(cid)
        assert cid in fake.contacts
    assert fake.mutations() == []


async def test_delete_contact_refuses_when_a_conversation_is_not_ours(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    cid = fake.add_persona_contact("[DEMO] Ahmad", "+60000000101")
    fake.add_conversation(cid, labels=["demo_persona"], **MARK)
    real = fake.add_conversation(cid, labels=[], inbox=WHATSAPP_INBOX)
    with pytest.raises(DemoSafetyError):
        await crm.delete_contact(cid)
    assert cid in fake.contacts and real in fake.conversations
    assert fake.mutations() == []


async def test_delete_contact_refuses_past_the_conversation_cap(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    cid = fake.add_persona_contact("[DEMO] Ahmad", "+60000000101")
    for _ in range(20):
        fake.add_conversation(cid, labels=["demo_persona"], **MARK)
    with pytest.raises(DemoSafetyError):
        await crm.delete_contact(cid)
    assert fake.mutations() == []


async def test_update_contact_refuses_and_keeps_the_marker(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    real = fake.add_contact("DEMO Ahmad", "+60000000101")
    with pytest.raises(DemoSafetyError):
        await crm.update_contact_attributes(real, {"vehicle_no": "X"})
    ours = fake.add_persona_contact("[DEMO] Farid", "+60000000104")
    with pytest.raises(DemoSafetyError):
        await crm.update_contact_attributes(ours, {"demo_seed": "other"})
    assert fake.mutations() == []


async def test_conversation_writes_refuse_conversations_we_do_not_own(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    cid = fake.add_contact("Ahmad Real", "+60123000001")
    labelled_no_marker = fake.add_conversation(cid, labels=["demo_persona"])
    old_label = fake.add_conversation(cid, labels=["audit_test", "demo_persona_old"], **MARK)
    demo = fake.add_persona_contact("[DEMO] Ahmad", "+60000000101")
    other_inbox = fake.add_conversation(demo, labels=[], inbox=WHATSAPP_INBOX)
    for vid in (labelled_no_marker, old_label, other_inbox):
        for call in (
            crm.delete_conversation(vid),
            crm.set_status(vid, "resolved"),
            crm.add_labels(vid, ["audit_test"]),
            crm.post_incoming_message(vid, "[DEMO] hi"),
        ):
            with pytest.raises(DemoSafetyError):
                await call
    assert fake.mutations() == []


async def test_create_contact_requires_name_and_persona_number(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    with pytest.raises(DemoSafetyError):
        await crm.create_contact("Ahmad", "+60000000101", {}, inbox_id=INBOX)
    with pytest.raises(DemoSafetyError):
        await crm.create_contact("[DEMO] Ahmad", "+60123000009", {}, inbox_id=INBOX)
    assert fake.mutations() == []


async def test_create_contact_stamps_the_marker(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    contact = await crm.create_contact(
        "[DEMO] Ahmad", "+60000000101", {"vehicle_no": "DMO1101"}, inbox_id=INBOX
    )
    assert fake.contacts[contact["id"]]["custom_attributes"]["demo_seed"] == "demo_persona"


async def test_create_conversation_rules(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    real = fake.add_contact("Ahmad Real", "+60123000001")
    unmarked = fake.add_contact("[DEMO] Ahmad", "+60000000101")
    demo = fake.add_persona_contact("[DEMO] Farid", "+60000000104")
    for cid, source, inbox in (
        (real, "demo-x-1", INBOX),
        (unmarked, "demo-x-1", INBOX),
        (demo, "whatsapp-60000000104", INBOX),
        (demo, "demo-x-1", WHATSAPP_INBOX),
    ):
        with pytest.raises(DemoSafetyError):
            await crm.create_conversation(
                cid, inbox_id=inbox, source_id=source, custom_attributes={}
            )
    assert fake.mutations() == []
    conv = await crm.create_conversation(
        demo, inbox_id=INBOX, source_id="demo-farid-1", custom_attributes={}
    )
    assert fake.conversations[conv["id"]]["custom_attributes"]["demo_seed"] == "demo_persona"


async def test_message_text_must_be_demo(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    demo = fake.add_persona_contact("[DEMO] Ahmad", "+60000000101")
    vid = fake.add_conversation(demo, labels=["demo_persona"], **MARK)
    with pytest.raises(DemoSafetyError):
        await crm.post_incoming_message(vid, "Hello")
    assert fake.mutations() == []


async def test_labels_never_add_escalate_or_dealer_and_keep_existing(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    demo = fake.add_persona_contact("[DEMO] Nurul", "+60000000101")
    vid = fake.add_conversation(demo, labels=["demo_persona", "csat_5"], **MARK)
    for bad in (["escalate"], ["dealer_petaling_jaya"], ["audit_test", "dealer_x"]):
        with pytest.raises(DemoSafetyError):
            await crm.add_labels(vid, bad)
    assert fake.mutations() == []
    await crm.add_labels(vid, ["audit_test"])
    assert fake.conversations[vid]["labels"] == ["demo_persona", "csat_5", "audit_test"]


async def test_create_label_only_demo_labels(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    with pytest.raises(DemoSafetyError):
        await crm.create_label("vip")
    assert fake.mutations() == []


async def test_stale_search_result_cannot_steer_a_delete(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    """The id came from an earlier search, but the record at that id is now
    a real customer: the fresh read decides, and it refuses."""
    cid = fake.add_persona_contact("[DEMO] Ahmad", "+60000000101")
    fake.contacts[cid]["name"] = "Real Customer"
    with pytest.raises(DemoSafetyError):
        await crm.delete_contact(cid)
    assert cid in fake.contacts
