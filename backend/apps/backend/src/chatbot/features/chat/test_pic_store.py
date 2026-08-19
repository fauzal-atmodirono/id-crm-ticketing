from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chatbot.features.chat.pic_store import DealerRecord, DealerStore, PicRecord, PicStore
from chatbot.platform.config import Settings


def _pic_store() -> PicStore:
    return PicStore(
        Settings(
            firestore_project_id="proj",
            firestore_database_id="db",
        )
    )


def _dealer_store() -> DealerStore:
    return DealerStore(
        Settings(
            firestore_project_id="proj",
            firestore_database_id="db",
        )
    )


def _make_snap(exists: bool, data: dict[str, Any] | None = None) -> MagicMock:
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data or {}
    return snap


# PicStore Tests


@pytest.mark.asyncio
async def test_pic_get_returns_none_when_not_found() -> None:
    snap = _make_snap(exists=False)
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.return_value = snap
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _pic_store().get("sales")
    assert result is None


@pytest.mark.asyncio
async def test_pic_get_returns_record_when_found() -> None:
    snap = _make_snap(
        exists=True,
        data={
            "department": "sales",
            "pic_name": "John Doe",
            "pic_email": "john@example.com",
            "pic_whatsapp": "+1234567890",
            "cc_emails": ["cc1@example.com", "cc2@example.com"],
        },
    )
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.return_value = snap
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _pic_store().get("sales")
    assert result == PicRecord(
        department="sales",
        pic_name="John Doe",
        pic_email="john@example.com",
        pic_whatsapp="+1234567890",
        cc_emails=["cc1@example.com", "cc2@example.com"],
    )


@pytest.mark.asyncio
async def test_pic_set_writes_document() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        MockClient.return_value.collection.return_value.document.return_value = doc
        await _pic_store().set(
            "sales",
            pic_name="Jane Doe",
            pic_email="jane@example.com",
            pic_whatsapp="+1987654321",
            cc_emails=["cc@example.com"],
        )
    doc.set.assert_called_once_with(
        {
            "department": "sales",
            "pic_name": "Jane Doe",
            "pic_email": "jane@example.com",
            "pic_whatsapp": "+1987654321",
            "cc_emails": ["cc@example.com"],
            # P2 task 7: always written, empty when the department has no
            # manager configured.
            "escalation_manager_email": "",
            "escalation_manager_whatsapp": "",
        }
    )


@pytest.mark.asyncio
async def test_pic_delete_removes_document() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        MockClient.return_value.collection.return_value.document.return_value = doc
        await _pic_store().delete("sales")
    doc.delete.assert_called_once()


@pytest.mark.asyncio
async def test_pic_list_all_returns_all_records() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        snap_a = MagicMock()
        snap_a.to_dict.return_value = {
            "department": "sales",
            "pic_name": "John Doe",
            "pic_email": "john@example.com",
            "pic_whatsapp": "+1234567890",
            "cc_emails": ["cc1@example.com"],
        }
        snap_b = MagicMock()
        snap_b.to_dict.return_value = {
            "department": "support",
            "pic_name": "Jane Doe",
            "pic_email": "jane@example.com",
            "pic_whatsapp": "+1987654321",
            "cc_emails": [],
        }
        col = MagicMock()
        col.stream.return_value = [snap_a, snap_b]
        MockClient.return_value.collection.return_value = col
        results = await _pic_store().list_all()
    assert len(results) == 2
    assert PicRecord(
        department="sales",
        pic_name="John Doe",
        pic_email="john@example.com",
        pic_whatsapp="+1234567890",
        cc_emails=["cc1@example.com"],
    ) in results
    assert PicRecord(
        department="support",
        pic_name="Jane Doe",
        pic_email="jane@example.com",
        pic_whatsapp="+1987654321",
        cc_emails=[],
    ) in results


@pytest.mark.asyncio
async def test_pic_get_on_exception_returns_none() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.side_effect = Exception("Firestore error")
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _pic_store().get("sales")
    assert result is None


@pytest.mark.asyncio
async def test_pic_set_on_exception_is_swallowed() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.set.side_effect = Exception("Firestore error")
        MockClient.return_value.collection.return_value.document.return_value = doc
        # Should not raise
        await _pic_store().set(
            "sales",
            pic_name="Jane Doe",
            pic_email="jane@example.com",
            pic_whatsapp="+1987654321",
        )


@pytest.mark.asyncio
async def test_pic_list_all_on_exception_returns_empty() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value.collection.side_effect = Exception("Firestore error")
        result = await _pic_store().list_all()
    assert result == []


@pytest.mark.asyncio
async def test_pic_delete_swallows_exception() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.delete.side_effect = Exception("Firestore error")
        MockClient.return_value.collection.return_value.document.return_value = doc
        # Should not raise
        await _pic_store().delete("sales")


# DealerStore Tests


@pytest.mark.asyncio
async def test_dealer_get_returns_none_when_not_found() -> None:
    snap = _make_snap(exists=False)
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.return_value = snap
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _dealer_store().get("acme")
    assert result is None


@pytest.mark.asyncio
async def test_dealer_get_returns_record_when_found() -> None:
    snap = _make_snap(exists=True, data={"dealer": "acme", "email": "acme@example.com"})
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.return_value = snap
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _dealer_store().get("acme")
    assert result == DealerRecord(dealer="acme", emails=["acme@example.com"])


@pytest.mark.asyncio
async def test_dealer_set_writes_document() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        MockClient.return_value.collection.return_value.document.return_value = doc
        await _dealer_store().set("acme", ["contact@acme.com"])
    doc.set.assert_called_once_with({"dealer": "acme", "emails": ["contact@acme.com"]})


@pytest.mark.asyncio
async def test_dealer_delete_removes_document() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        MockClient.return_value.collection.return_value.document.return_value = doc
        await _dealer_store().delete("acme")
    doc.delete.assert_called_once()


@pytest.mark.asyncio
async def test_dealer_list_all_returns_all_records() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        snap_a = MagicMock()
        snap_a.to_dict.return_value = {"dealer": "acme", "email": "acme@example.com"}
        snap_b = MagicMock()
        snap_b.to_dict.return_value = {"dealer": "techcorp", "email": "contact@techcorp.com"}
        col = MagicMock()
        col.stream.return_value = [snap_a, snap_b]
        MockClient.return_value.collection.return_value = col
        results = await _dealer_store().list_all()
    assert len(results) == 2
    assert DealerRecord(dealer="acme", emails=["acme@example.com"]) in results
    assert DealerRecord(dealer="techcorp", emails=["contact@techcorp.com"]) in results


@pytest.mark.asyncio
async def test_dealer_get_on_exception_returns_none() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.get.side_effect = Exception("Firestore error")
        MockClient.return_value.collection.return_value.document.return_value = doc
        result = await _dealer_store().get("acme")
    assert result is None


@pytest.mark.asyncio
async def test_dealer_set_on_exception_is_swallowed() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.set.side_effect = Exception("Firestore error")
        MockClient.return_value.collection.return_value.document.return_value = doc
        # Should not raise
        await _dealer_store().set("acme", "contact@acme.com")


@pytest.mark.asyncio
async def test_dealer_list_all_on_exception_returns_empty() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value.collection.side_effect = Exception("Firestore error")
        result = await _dealer_store().list_all()
    assert result == []


@pytest.mark.asyncio
async def test_dealer_delete_swallows_exception() -> None:
    with patch(
        "chatbot.features.chat.pic_store.firestore.Client", autospec=True
    ) as MockClient:
        doc = MagicMock()
        doc.delete.side_effect = Exception("Firestore error")
        MockClient.return_value.collection.return_value.document.return_value = doc
        # Should not raise
        await _dealer_store().delete("acme")


# ---------------------------------------------------------------------------
# The escalation ladder's four dealer roles (2026-08-19)
# ---------------------------------------------------------------------------

from chatbot.features.chat.pic_store import (  # noqa: E402
    DEALER_ROLES,
    ProtonNetRecord,
    _dealer_record_from_dict,
)


def test_a_legacy_group_record_reads_its_first_address_as_the_cre() -> None:
    """Every dealer record written before the ladder holds one group that was
    used exactly as the CRE is used now: first contact on escalation. Reading
    it that way keeps the live tenant working with no migration."""
    rec = _dealer_record_from_dict({"dealer": "kl", "emails": ["desk@kl.my", "b@kl.my"]}, "kl")

    assert rec.contact("cre") == "desk@kl.my"
    assert rec.emails == ["desk@kl.my", "b@kl.my"]  # the group itself is untouched


def test_the_senior_roles_never_fall_back_to_the_group() -> None:
    """Mailing a Dealer Owner an address that was only ever meant for the
    service desk is worse than skipping the step."""
    rec = _dealer_record_from_dict({"dealer": "kl", "emails": ["desk@kl.my"]}, "kl")

    assert rec.contact("principal") == ""
    assert rec.contact("owner") == ""
    assert rec.contact("sales_aftersales_mgr") == ""


def test_an_explicit_contact_wins_over_the_legacy_group() -> None:
    rec = _dealer_record_from_dict(
        {"dealer": "kl", "emails": ["desk@kl.my"], "contacts": {"cre": "cre@kl.my"}}, "kl"
    )

    assert rec.contact("cre") == "cre@kl.my"


def test_the_full_role_map_round_trips_with_the_region() -> None:
    rec = _dealer_record_from_dict(
        {
            "dealer": "kl",
            "emails": [],
            "region": "central",
            "contacts": {
                "cre": "cre@kl.my",
                "sales_aftersales_mgr": "sam@kl.my",
                "principal": "dp@kl.my",
                "owner": "owner@kl.my",
            },
        },
        "kl",
    )

    assert [rec.contact(r) for r in DEALER_ROLES] == [
        "cre@kl.my",
        "sam@kl.my",
        "dp@kl.my",
        "owner@kl.my",
    ]
    assert rec.region == "central"


def test_an_unknown_role_key_is_dropped_rather_than_stored() -> None:
    """A typo'd role would otherwise sit in Firestore looking configured
    while no step ever reads it."""
    rec = _dealer_record_from_dict(
        {"dealer": "kl", "contacts": {"principle": "typo@kl.my"}}, "kl"
    )

    assert rec.contacts == {}
    assert rec.contact("principal") == ""


def test_an_unknown_role_lookup_returns_empty_and_does_not_raise() -> None:
    rec = _dealer_record_from_dict({"dealer": "kl"}, "kl")

    assert rec.contact("nobody") == ""


def test_a_record_with_no_contacts_at_all_is_still_usable() -> None:
    rec = _dealer_record_from_dict({}, "kl")

    assert rec.dealer == "kl"
    assert all(rec.contact(role) == "" for role in DEALER_ROLES)


def test_pronet_record_reads_its_two_roles() -> None:
    rec = ProtonNetRecord(region="central", area_regional_mgr="arm@proton.my", hod="hod@proton.my")

    assert rec.contact("area_regional_mgr") == "arm@proton.my"
    assert rec.contact("hod") == "hod@proton.my"
    assert rec.contact("owner") == ""  # a dealer role is not a PRO-NET role
