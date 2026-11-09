"""The demo-record predicates and the per-write guards in DemoCrmClient.

The seeder runs on a live tenant, so these prove a write is refused --
before any request leaves -- whenever the FRESH copy of the target record is
not a demo record, including near-miss names and labels.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chatbot.features.demo.conftest import FakeChatwoot
from chatbot.features.demo.crm import DemoCrmClient
from chatbot.features.demo.safety import (
    DemoSafetyError,
    is_demo_contact,
    is_demo_conversation,
    is_demo_rsa,
)


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
def test_near_miss_contact_names_are_not_demo(name: object) -> None:
    assert not is_demo_contact({"id": 1, "name": name})


def test_demo_contact_name() -> None:
    assert is_demo_contact({"id": 1, "name": "[DEMO] Ahmad"})
    assert not is_demo_contact(None)
    assert not is_demo_contact("[DEMO] Ahmad")


def test_conversation_markers() -> None:
    real_sender = {"meta": {"sender": {"name": "Ahmad"}}}
    assert is_demo_conversation({**real_sender, "labels": ["demo_persona"]})
    assert is_demo_conversation({"labels": [], "meta": {"sender": {"name": "[DEMO] Lim"}}})
    for labels in (
        [],
        ["audit_test"],
        ["demo_persona_old"],
        ["Demo_Persona"],
        ["dealer_petaling_jaya"],
    ):
        assert not is_demo_conversation({**real_sender, "labels": labels})
    assert not is_demo_conversation({"labels": "demo_persona"})  # not a list
    assert not is_demo_conversation(None)


def test_rsa_marker_needs_both_created_by_and_remarks() -> None:
    assert is_demo_rsa(SimpleNamespace(created_by="demo_persona", remarks="[DEMO] x"))
    assert not is_demo_rsa(SimpleNamespace(created_by="demo_persona", remarks="real remark"))
    assert not is_demo_rsa(SimpleNamespace(created_by="staff", remarks="[DEMO] x"))
    assert not is_demo_rsa(SimpleNamespace(created_by=None, remarks=None))


# --- guards in the client: refuse BEFORE any mutating request -----------------


async def test_delete_contact_refuses_real_contact(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    cid = fake.add_contact("Ahmad Real", "+60123000001")
    with pytest.raises(DemoSafetyError):
        await crm.delete_contact(cid)
    assert cid in fake.contacts
    assert fake.mutations() == []


async def test_update_contact_refuses_real_contact(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    cid = fake.add_contact("DEMO Ahmad", "+60123000001")
    with pytest.raises(DemoSafetyError):
        await crm.update_contact_attributes(cid, {"vehicle_no": "X"})
    assert fake.mutations() == []


async def test_conversation_writes_refuse_real_conversation(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    cid = fake.add_contact("Ahmad Real", "+60123000001")
    vid = fake.add_conversation(cid, labels=["audit_test", "demo_persona_old"])
    for call in (
        crm.delete_conversation(vid),
        crm.set_status(vid, "resolved"),
        crm.add_labels(vid, ["demo_persona"]),
        crm.post_incoming_message(vid, "[DEMO] hi"),
    ):
        with pytest.raises(DemoSafetyError):
            await call
    assert vid in fake.conversations
    assert fake.conversations[vid]["labels"] == ["audit_test", "demo_persona_old"]
    assert fake.mutations() == []


async def test_create_contact_requires_demo_name(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    with pytest.raises(DemoSafetyError):
        await crm.create_contact("Ahmad", "+60000000101", {}, inbox_id=42)
    assert fake.mutations() == []


async def test_create_conversation_requires_demo_contact_and_source(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    real = fake.add_contact("Ahmad Real", "+60123000001")
    demo = fake.add_contact("[DEMO] Ahmad", "+60000000101")
    with pytest.raises(DemoSafetyError):
        await crm.create_conversation(
            real, inbox_id=42, source_id="demo-ahmad-1", custom_attributes={}
        )
    with pytest.raises(DemoSafetyError):
        await crm.create_conversation(
            demo, inbox_id=42, source_id="whatsapp-60000000101", custom_attributes={}
        )
    assert fake.mutations() == []


async def test_message_text_must_be_demo(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    demo = fake.add_contact("[DEMO] Ahmad", "+60000000101")
    vid = fake.add_conversation(demo, labels=["demo_persona"])
    with pytest.raises(DemoSafetyError):
        await crm.post_incoming_message(vid, "Hello")
    assert fake.mutations() == []


async def test_labels_never_add_escalate_and_keep_existing(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    demo = fake.add_contact("[DEMO] Nurul", "+60000000102")
    vid = fake.add_conversation(demo, labels=["demo_persona", "csat_5"])
    with pytest.raises(DemoSafetyError):
        await crm.add_labels(vid, ["escalate"])
    await crm.add_labels(vid, ["dealer_petaling_jaya"])
    assert fake.conversations[vid]["labels"] == ["demo_persona", "csat_5", "dealer_petaling_jaya"]


async def test_create_label_only_demo_labels(crm: DemoCrmClient, fake: FakeChatwoot) -> None:
    with pytest.raises(DemoSafetyError):
        await crm.create_label("vip")
    assert fake.mutations() == []


async def test_stale_search_result_cannot_steer_a_delete(
    crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    """The id came from an earlier [DEMO] search, but the record at that id
    is now a real customer: the fresh read decides, and it refuses."""
    cid = fake.add_contact("[DEMO] Ahmad", "+60000000101")
    fake.contacts[cid]["name"] = "Real Customer"  # id reused / renamed since the search
    with pytest.raises(DemoSafetyError):
        await crm.delete_contact(cid)
    assert cid in fake.contacts
