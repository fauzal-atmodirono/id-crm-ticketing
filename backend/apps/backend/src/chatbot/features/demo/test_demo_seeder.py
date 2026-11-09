"""Seed / reset / status of the [DEMO] personas against an in-memory Chatwoot.

The reset tests are the ones that matter for a live tenant: a CRM full of
real-looking records (and near misses) is reset, and every real record must
come out byte-identical with no mutating request ever naming its id.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from chatbot.features.chat.customer360_router import _phones_match
from chatbot.features.demo.conftest import INBOX, WHATSAPP_INBOX, FakeChatwoot
from chatbot.features.demo.crm import DemoCrmClient
from chatbot.features.demo.safety import DemoSafetyError
from chatbot.features.demo.seeder import DemoSeeder, load_personas
from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository
from chatbot.platform.config import Settings


def _seeder(
    settings: Settings, crm: DemoCrmClient, rsa: InMemoryRsaRepository | None = None
) -> DemoSeeder:
    return DemoSeeder(settings, crm, rsa_repo=rsa)


def _by_name(fake: FakeChatwoot, name: str) -> dict:
    [contact] = [c for c in fake.contacts.values() if c["name"] == name]
    return contact


def _convs_of(fake: FakeChatwoot, contact_id: int) -> list[dict]:
    return [c for c in fake.conversations.values() if c["contact_id"] == contact_id]


def test_personas_file_matches_task1_keys() -> None:
    personas = load_personas()
    ids = [p["id"] for p in personas]
    assert ids == ["ahmad", "nurul", "lim", "farid", "new", "unknown"]
    for p in personas:
        seed = p["crm_seed"]
        if seed is None:
            continue
        assert seed["contact"]["name"].startswith("[DEMO] ")
        assert seed["contact"]["phone_number"] == p["number"]
        assert set(seed["contact"]["custom_attributes"]) <= {"vehicle_no", "vehicle_model"}
        for conv in seed["conversations"]:
            assert {"vehicle_plate", "vehicle_model"} <= set(conv["custom_attributes"])
            assert conv["message"].startswith("[DEMO]")
            assert "escalate" not in conv["labels"]
            assert not [x for x in conv["labels"] if x.startswith("dealer_")]
            assert conv["custom_attributes"]["caller_dealer"] == "petaling_jaya"
        assert all(n.startswith("+600000001") for n in [p["number"]])


async def test_seed_creates_every_persona_history(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    rsa = InMemoryRsaRepository()
    report = await _seeder(settings, crm, rsa).seed()

    assert sorted(report["created_contacts"]) == ["ahmad", "farid", "lim", "nurul"]
    assert report["conflicts"] == []
    names = sorted(c["name"] for c in fake.contacts.values())
    assert names == ["[DEMO] Ahmad", "[DEMO] Farid", "[DEMO] Lim", "[DEMO] Nurul"]

    ahmad = _by_name(fake, "[DEMO] Ahmad")
    assert ahmad["phone_number"] == "+60000000101"
    assert ahmad["custom_attributes"]["vehicle_no"] == "DMO1101"
    assert ahmad["custom_attributes"]["vehicle_model"] == "Proton X50"
    assert ahmad["custom_attributes"]["demo_seed"] == "demo_persona"
    [a_conv] = _convs_of(fake, ahmad["id"])
    assert a_conv["status"] == "resolved"
    assert a_conv["custom_attributes"]["vehicle_plate"] == "DMO1101"

    nurul = _by_name(fake, "[DEMO] Nurul")
    [n_conv] = _convs_of(fake, nurul["id"])
    assert n_conv["status"] == "open"
    assert n_conv["custom_attributes"]["case_type"] == "Complaint"
    assert n_conv["messages"] == [
        {"content": n_conv["messages"][0]["content"], "message_type": "incoming", "private": False}
    ]

    lim = _by_name(fake, "[DEMO] Lim")
    plates = sorted(c["custom_attributes"]["vehicle_plate"] for c in _convs_of(fake, lim["id"]))
    assert plates == ["DMO1103", "DMO1104"]

    for conv in fake.conversations.values():
        assert conv["inbox_id"] == INBOX
        # R29: no dealer_* label (it drives live routing/escalation); the
        # caller lookup reads caller_dealer instead.
        assert sorted(conv["labels"]) == ["audit_test", "demo_persona"]
        assert conv["custom_attributes"]["caller_dealer"] == "petaling_jaya"
        assert conv["source_id"].startswith("demo-")
        assert conv["custom_attributes"]["demo_seed"] == "demo_persona"
        assert conv["messages"] and all(m["content"].startswith("[DEMO]") for m in conv["messages"])

    # Farid's past RSA incident, findable by Customer 360's phone match.
    [incident] = await rsa.list_incidents()
    assert _phones_match("+60000000104", incident.customer_phone)
    assert incident.vehicle_no == "DMO1105"
    assert incident.created_by == "demo_persona"
    assert incident.remarks.startswith("[DEMO]")

    # Missing demo label is created; nothing else in the account is.
    assert "demo_persona" in fake.labels
    assert [c.body["title"] for c in fake.calls if c.method == "POST" and c.path == "/labels"] == [
        "demo_persona"
    ]


async def test_seed_never_leaves_a_message_on_a_pending_conversation(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    """Chatwoot forces `pending` on create when the inbox has an agent bot,
    and the agent bot answers an incoming message on a pending conversation.
    The status must be moved off pending BEFORE the message is posted."""
    await _seeder(settings, crm).seed()
    for vid in fake.conversations:
        calls = [c for c in fake.calls if c.path.startswith(f"/conversations/{vid}/")]
        first_status = next(i for i, c in enumerate(calls) if c.path.endswith("/toggle_status"))
        first_message = next(i for i, c in enumerate(calls) if c.path.endswith("/messages"))
        assert first_status < first_message
        assert calls[first_status].body == {"status": "open"}


async def test_seed_is_idempotent(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    rsa = InMemoryRsaRepository()
    await _seeder(settings, crm, rsa).seed()
    before = (copy.deepcopy(fake.contacts), set(fake.conversations))
    report = await _seeder(settings, crm, rsa).seed()
    assert report["created_contacts"] == []
    assert set(fake.conversations) == before[1]
    assert sorted(fake.contacts) == sorted(before[0])
    assert len(await rsa.list_incidents()) == 1


async def test_seed_refuses_a_non_api_inbox(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    fake.inbox_channel = "Channel::Whatsapp"
    with pytest.raises(DemoSafetyError):
        await _seeder(settings, crm).seed()
    assert fake.mutations() == []


async def test_seed_skips_a_persona_whose_number_belongs_to_a_real_contact(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    real = fake.add_contact("Someone Real", "+60000000101", vehicle_no="ABC123")
    snapshot = copy.deepcopy(fake.contacts[real])
    report = await _seeder(settings, crm).seed()
    assert [c["persona"] for c in report["conflicts"]] == ["ahmad"]
    assert fake.contacts[real] == snapshot
    assert _convs_of(fake, real) == []
    assert real not in fake.mutated_ids()


def _populate_real_world(fake: FakeChatwoot) -> dict[str, int]:
    """Real customers and near misses the reset must never touch."""
    ids = {
        "real": fake.add_contact("Ahmad Bin Ali", "+60123000001", vehicle_no="WXY1234"),
        "near1": fake.add_contact("DEMO Lim", "+60123000002"),
        "near2": fake.add_contact("Nurul [DEMO]", "+60123000003"),
        "near3": fake.add_contact("[demo] Farid", "+60123000004"),
        "new_caller": fake.add_contact("Siti", "+60000000105"),  # made by a rehearsal call
    }
    ids["real_conv"] = fake.add_conversation(
        ids["real"], labels=["dealer_petaling_jaya", "escalate"]
    )
    ids["audit_only"] = fake.add_conversation(ids["real"], labels=["audit_test"], status="resolved")
    ids["old_label"] = fake.add_conversation(ids["near1"], labels=["demo_persona_old"])
    ids["new_call"] = fake.add_conversation(ids["new_caller"], labels=[])
    ids["labelled_unmarked"] = fake.add_conversation(ids["real"], labels=["demo_persona"])
    # deploy/scripts/seed_demo_data contact: [DEMO] name, its own batch marker.
    ids["budi"] = fake.add_contact(
        "[DEMO] Budi Santoso", "+62812000001", demo_seed="batch-1", vehicle_no="B1234XY"
    )
    ids["budi_conv"] = fake.add_conversation(
        ids["budi"], labels=["audit_test"], demo_seed="batch-1"
    )
    ids["budi_conv2"] = fake.add_conversation(ids["budi"], labels=[], status="resolved")
    # A real handset renamed by agent/app/services/demo_persona.py, on WhatsApp.
    ids["renamed"] = fake.add_contact("[DEMO] x", "+60123000009")
    ids["renamed_wa"] = fake.add_conversation(ids["renamed"], labels=[], inbox=WHATSAPP_INBOX)
    return ids


async def test_reset_never_touches_a_non_demo_record(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    rsa = InMemoryRsaRepository()
    real_rsa = await rsa.create_incident(
        incident_date="2026-09-01",
        vehicle_no="WXY1234",
        cause="Flat Tyre",
        customer_phone="+60123000001",
        created_by="staff",
        remarks="[DEMO] looks demo but staff-made",
    )
    ids = _populate_real_world(fake)
    seeder = _seeder(settings, crm, rsa)
    await seeder.seed()
    demo_contact_ids = {
        c["id"]
        for c in fake.contacts.values()
        if c["custom_attributes"].get("demo_seed") == "demo_persona"
    }
    assert len(demo_contact_ids) == 4
    # A rehearsal call on a demo persona, made by the phone code (no label).
    farid = _by_name(fake, "[DEMO] Farid")
    call_on_demo = fake.add_conversation(farid["id"], labels=[])
    real_snapshot = {
        k: copy.deepcopy(fake.contacts.get(v) or fake.conversations.get(v)) for k, v in ids.items()
    }
    fake.calls.clear()

    report = await seeder.reset()

    for key, rid in ids.items():
        now = fake.contacts.get(rid) or fake.conversations.get(rid)
        assert now == real_snapshot[key], key
        assert rid not in fake.mutated_ids(), key
    assert await rsa.get_incident(real_rsa) is not None
    # Every old demo record is gone (including the unlabelled call on a
    # demo contact), and the personas are back with fresh ids.
    assert call_on_demo not in fake.conversations
    assert not demo_contact_ids & set(fake.contacts)
    assert sorted(
        c["name"]
        for c in fake.contacts.values()
        if c["custom_attributes"].get("demo_seed") == "demo_persona"
    ) == [
        "[DEMO] Ahmad",
        "[DEMO] Farid",
        "[DEMO] Lim",
        "[DEMO] Nurul",
    ]
    assert len([r for r in await rsa.list_incidents() if r.created_by == "demo_persona"]) == 1
    assert report["removed"]["contacts"] == 4
    assert report["removed"]["conversations"] == 6
    assert report["removed"]["rsa_incidents"] == 1
    # The new caller's leftover contact is reported, not touched.
    assert [n["persona"] for n in report["not_demo_records"]] == ["new"]
    # No tenant-wide "[DEMO]" search: only persona numbers are searched.
    searches = [c.path for c in fake.calls if c.path.startswith("/contacts/search")]
    assert searches
    assert all("DEMO" not in p.upper() for p in searches)


async def test_reset_keeps_a_persona_contact_that_gained_a_real_conversation(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    seeder = _seeder(settings, crm)
    await seeder.seed()
    ahmad = _by_name(fake, "[DEMO] Ahmad")
    real_conv = fake.add_conversation(ahmad["id"], labels=[], inbox=WHATSAPP_INBOX)
    snapshot = copy.deepcopy(fake.conversations[real_conv])
    fake.calls.clear()
    report = await seeder.reset()
    assert ahmad["id"] in fake.contacts
    assert fake.conversations[real_conv] == snapshot
    assert real_conv not in fake.mutated_ids()
    assert not [
        c for c in fake.mutations() if c.method == "DELETE" and c.path == f"/contacts/{ahmad['id']}"
    ]
    assert any(str(ahmad["id"]) in e for e in report["errors"])
    assert report["removed"]["contacts"] == 3
    # Its own demo history was removed and seeded again on the kept contact.
    demo_convs = [c for c in _convs_of(fake, ahmad["id"]) if "demo_persona" in c["labels"]]
    assert len(demo_convs) == 1


async def test_reset_resolves_then_deletes_each_demo_conversation(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    seeder = _seeder(settings, crm)
    await seeder.seed()
    old = set(fake.conversations)
    fake.calls.clear()
    await seeder.reset()
    for vid in old:
        writes = [c for c in fake.mutations() if c.path.startswith(f"/conversations/{vid}")]
        assert [c.method for c in writes] == ["POST", "DELETE"]
        assert writes[0].body == {"status": "resolved"}


async def test_reset_continues_when_conversation_delete_is_not_allowed(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    """If a conversation DELETE is refused (e.g. 401), it is reported and
    the run carries on: the contact delete then removes those conversations
    (Chatwoot destroys a contact's conversations with it). Both deletes need
    an administrator token -- ContactPolicy#destroy? is admin-only too."""
    seeder = _seeder(settings, crm)
    await seeder.seed()
    fake.fail_conversation_delete = True
    report = await seeder.reset()
    assert report["errors"]
    assert len([c for c in fake.contacts.values() if c["name"].startswith("[DEMO] ")]) == 4


async def test_reset_refuses_a_non_api_inbox_before_deleting(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    await _seeder(settings, crm).seed()
    fake.inbox_channel = "Channel::Email"
    fake.calls.clear()
    with pytest.raises(DemoSafetyError):
        await _seeder(settings, crm).reset()
    assert fake.mutations() == []


async def test_status_is_read_only_and_links_to_the_crm(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    await _seeder(settings, crm).seed()
    fake.add_contact("Siti", "+60000000105")
    fake.calls.clear()
    status = await _seeder(settings, crm).status()
    assert fake.mutations() == []
    by_id = {p["id"]: p for p in status["personas"]}
    ahmad = by_id["ahmad"]
    assert ahmad["seeded"] is True
    cid = ahmad["contact"]["id"]
    assert ahmad["contact"]["url"] == f"/app/accounts/7/contacts/{cid}"
    assert ahmad["latest_conversation"]["url"].startswith("/app/accounts/7/conversations/")
    assert ahmad["customer360_url"] == "/app/accounts/7/proton/customer360?q=%2B60000000101"
    assert by_id["lim"]["conversation_count"] == 2
    assert by_id["new"]["seeded"] is False
    assert by_id["new"]["warning"]
    assert by_id["unknown"]["contact"] is None
    assert by_id["unknown"]["customer360_url"] is None
    for p in status["personas"]:
        assert p["shows"] and p["label"]


async def test_crm_base_url_prefixes_links(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    s = settings.model_copy(update={"demo_crm_base_url": "https://crm.example/"})
    await _seeder(s, crm).seed()
    status = await _seeder(s, crm).status()
    ahmad = next(p for p in status["personas"] if p["id"] == "ahmad")
    assert ahmad["contact"]["url"].startswith("https://crm.example/app/accounts/7/contacts/")


async def test_rsa_reset_rechecks_each_row(settings: Settings, crm: DemoCrmClient) -> None:
    """A row that looked demo in the listing but is not demo on the fresh
    read is never deleted."""
    rsa = InMemoryRsaRepository()
    seeder = _seeder(settings, crm, rsa)
    rid = await rsa.create_incident(
        incident_date="2026-10-12",
        vehicle_no="DMO1105",
        cause="x",
        customer_phone="+60000000104",
        created_by="demo_persona",
        remarks="[DEMO] x",
    )
    original_get = rsa.get_incident

    async def _flipped(incident_id: str) -> object:
        row = await original_get(incident_id)
        return SimpleNamespace(**{**row.__dict__, "created_by": "staff"}) if row else None

    rsa.get_incident = _flipped  # type: ignore[method-assign]
    removed, errors = await seeder._remove_demo_rsa()
    assert removed == 0
    assert errors
    assert await original_get(rid) is not None


async def test_rsa_without_phone_matches_on_plate_and_markers(
    settings: Settings, crm: DemoCrmClient
) -> None:
    """A tenant whose rsa_incidents lacks customer_phone stores it as None:
    the demo row is still found by its persona plate + both markers, a
    staff row with the same plate is not."""
    rsa = InMemoryRsaRepository()
    seeder = _seeder(settings, crm, rsa)
    ours = await rsa.create_incident(
        incident_date="2026-10-12",
        vehicle_no="DMO1105",
        cause="x",
        customer_phone=None,
        created_by="demo_persona",
        remarks="[DEMO] x",
    )
    staff = await rsa.create_incident(
        incident_date="2026-10-12",
        vehicle_no="DMO1105",
        cause="x",
        customer_phone=None,
        created_by="staff",
        remarks="[DEMO] x",
    )
    other_plate = await rsa.create_incident(
        incident_date="2026-10-12",
        vehicle_no="WXY1234",
        cause="x",
        customer_phone=None,
        created_by="demo_persona",
        remarks="[DEMO] x",
    )
    removed, _ = await seeder._remove_demo_rsa()
    assert removed == 1
    assert await rsa.get_incident(ours) is None
    assert await rsa.get_incident(staff) is not None
    assert await rsa.get_incident(other_plate) is not None


async def test_reset_then_reseed_with_async_conversation_deletes(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    """Chatwoot's DELETE /conversations/:id only enqueues a job: a deleted
    conversation 404s on GET but is still listed for a while. Reset must
    treat that 404 as "already gone" (not an error, not a reason to keep the
    contact), and the reseed must not count the just-deleted history as
    existing -- every persona gets its history back."""
    seeder = _seeder(settings, crm)
    await seeder.seed()
    ahmad = _by_name(fake, "[DEMO] Ahmad")
    fake.add_conversation(ahmad["id"], labels=[], inbox=WHATSAPP_INBOX)  # keeps Ahmad
    fake.async_delete_listings = 5  # still listed through delete_contact AND the reseed
    report = await seeder.reset()

    assert not [e for e in report["errors"] if "conversation" in e and "404" in e]
    assert report["removed"]["conversations"] == 5
    assert report["removed"]["contacts"] == 3  # Ahmad kept: a WhatsApp conversation
    assert report["seed"]["skipped_history"] == []
    assert report["seed"]["created_conversations"] == 5
    for name, count in (
        ("[DEMO] Ahmad", 1),
        ("[DEMO] Nurul", 1),
        ("[DEMO] Lim", 2),
        ("[DEMO] Farid", 1),
    ):
        contact = _by_name(fake, name)
        live = [c for c in _convs_of(fake, contact["id"]) if "demo_persona" in c["labels"]]
        assert len(live) == count, name


async def test_delete_contact_treats_a_404_conversation_as_gone(
    settings: Settings, crm: DemoCrmClient, fake: FakeChatwoot
) -> None:
    seeder = _seeder(settings, crm)
    await seeder.seed()
    farid = _by_name(fake, "[DEMO] Farid")
    [vid] = [c["id"] for c in _convs_of(fake, farid["id"])]
    # Deleted by an earlier run / another process: this client never saw the
    # DELETE, the listing is stale, and the re-read 404s.
    fake.ghosts[vid] = [fake.conversations.pop(vid), 3]
    await crm.delete_contact(farid["id"])  # stale listing + 404 on re-read: no error
    assert farid["id"] not in fake.contacts
