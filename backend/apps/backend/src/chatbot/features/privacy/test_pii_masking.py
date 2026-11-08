"""PII masking on backend-owned surfaces (Wave 2 Task 29, FIX-49).

Every test goes through the real router over HTTP, the same way the data
scope tests do (Task 28), so a surface that forgets to apply the policy fails
here rather than in a helper test.

Users: 5 = administrator (holds pii.view by seed), 7 = an agent-like role
with the surface permission but NOT pii.view, 9 = the same role plus pii.view.
"""

from __future__ import annotations

import csv
import io
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.seed import _AGENT_PERMISSIONS, PERMISSION_REGISTRY
from chatbot.features.authz.test_data_scope import FakeValidator, make_repo, session
from chatbot.features.chat.adapters.chatwoot import ConversationListing
from chatbot.features.chat.ai_activity_router import build_ai_activity_router
from chatbot.features.chat.case_fields_router import build_case_fields_router
from chatbot.features.chat.customer360_router import build_customer360_router
from chatbot.features.privacy.access import build_privacy_router
from chatbot.features.privacy.pii import MASK
from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository
from chatbot.features.rsa.rsa_router import build_rsa_router
from chatbot.platform.config import Settings, get_settings

TOKENS = {"admin": (5, False), "agent": (7, False), "viewer": (9, False), "owner": (1, False)}
PERMS = ("customer360.view", "sla.manage", "ai_activity.view", "cases.view", "cases.manage")

PHONE = "+60123456789"
IC = "900101-14-5678"
EMAIL = "siti.aminah@gmail.com"
VIN = "PL1BT3LNRGB012345"


def _settings(**overrides) -> Settings:
    base = {"rbac_enabled": True, "pii_masking_enabled": True}
    base.update(overrides)
    return get_settings().model_copy(update=base)


async def _repo(tmp_path):
    repo = await make_repo(tmp_path, "pii")
    await repo.create_role("desk", "Desk")
    await repo.create_role("desk_pii", "Desk with PII")
    for perm in PERMS:
        await repo.grant_permission("desk", perm)
        await repo.grant_permission("desk_pii", perm)
    await repo.grant_permission("desk_pii", "pii.view")
    await repo.assign_role(5, "administrator")
    await repo.assign_role(7, "agent")
    await repo.assign_role(7, "desk")
    await repo.assign_role(9, "desk_pii")
    return repo


def _client(*routers) -> TestClient:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


def _c360(settings, repo, rsa) -> TestClient:
    chatwoot = AsyncMock()
    chatwoot.search_contacts.return_value = [
        {
            "id": 11,
            "name": "Siti Aminah",
            "phone_number": PHONE,
            "email": EMAIL,
            "custom_attributes": {"ic_number": IC, "vehicle_chassis": VIN, "vehicle_no": "BMT5518"},
        }
    ]
    chatwoot.list_contact_conversations.return_value = [
        {"id": 1, "inbox_id": 3, "labels": [], "meta": {"sender": {"phone_number": PHONE}}}
    ]
    return _client(build_customer360_router(chatwoot, rsa, repo, FakeValidator(TOKENS), settings))


async def _rsa_with_incident() -> InMemoryRsaRepository:
    rsa = InMemoryRsaRepository()
    await rsa.create_incident(
        incident_date="2026-11-01",
        vehicle_no="BMT5518",
        cause="flat tyre",
        customer_phone=PHONE,
        purchased_from="kl",
        remarks=f"owner IC {IC}, alt contact {EMAIL}",
    )
    return rsa


# --- the permission ------------------------------------------------------------


def test_pii_view_is_registered_admin_only() -> None:
    assert "pii.view" in PERMISSION_REGISTRY
    assert "pii.view" not in _AGENT_PERMISSIONS


def test_flag_requires_rbac() -> None:
    with pytest.raises(ValueError, match="PII_MASKING_ENABLED requires RBAC_ENABLED"):
        Settings(pii_masking_enabled=True, rbac_enabled=False)


# --- Customer 360 --------------------------------------------------------------


async def test_ic_number_masked_for_agent(tmp_path) -> None:
    repo = await _repo(tmp_path)
    client = _c360(_settings(), repo, await _rsa_with_incident())

    body = client.get(f"/admin/customer360/search?q={PHONE}", headers=session("agent")).json()
    contact = body["contact"]
    assert contact["custom_attributes"]["ic_number"] == "******-**-5678"
    assert contact["phone_number"] == f"+60{MASK * 5}6789"
    assert contact["email"] == f"s{MASK * 3}@gmail.com"
    assert contact["custom_attributes"]["vehicle_chassis"] == f"{MASK * 13}2345"
    assert contact["custom_attributes"]["vehicle_no"] == "BMT5518"  # plates are not masked
    assert contact["name"] == "Siti Aminah"  # names are not on the rule list
    assert body["conversations"][0]["meta"]["sender"]["phone_number"] == f"+60{MASK * 5}6789"
    incident = body["rsa_incidents"][0]
    assert incident["customer_phone"] == f"+60{MASK * 5}6789"
    assert IC not in incident["remarks"] and EMAIL not in incident["remarks"]
    assert (
        IC not in client.get(f"/admin/customer360/search?q={PHONE}", headers=session("agent")).text
    )


async def test_unmasked_for_pii_view(tmp_path) -> None:
    repo = await _repo(tmp_path)
    client = _c360(_settings(), repo, await _rsa_with_incident())

    for who in ("admin", "viewer", "owner"):  # administrator, pii.view role, platform owner
        body = client.get(f"/admin/customer360/search?q={PHONE}", headers=session(who)).json()
        assert body["contact"]["custom_attributes"]["ic_number"] == IC, who
        assert body["contact"]["phone_number"] == PHONE, who
        assert body["rsa_incidents"][0]["customer_phone"] == PHONE, who


async def test_flag_off_unchanged(tmp_path) -> None:
    repo = await _repo(tmp_path)
    rsa = await _rsa_with_incident()
    off = _settings(pii_masking_enabled=False)
    url = f"/admin/customer360/search?q={PHONE}"

    agent_off = _c360(off, repo, rsa).get(url, headers=session("agent"))
    admin_on = _c360(_settings(), repo, rsa).get(url, headers=session("admin"))
    # Flag off, a user without pii.view gets exactly what an unmasked user gets.
    assert agent_off.content == admin_on.content
    assert agent_off.json()["contact"]["custom_attributes"]["ic_number"] == IC

    rsa_off = _client(build_rsa_router(rsa, off, repo, FakeValidator(TOKENS)))
    rsa_admin = _client(build_rsa_router(rsa, _settings(), repo, FakeValidator(TOKENS)))
    for path in ("/rsa/incidents", "/rsa/incidents/export"):
        assert (
            rsa_off.get(path, headers=session("agent")).content
            == rsa_admin.get(path, headers=session("admin")).content
        )

    privacy = _client(build_privacy_router(off, repo=repo, validator=FakeValidator(TOKENS)))
    assert privacy.get("/privacy/me", headers=session("agent")).json() == {
        "masking_enabled": False,
        "masked": False,
    }


async def test_missing_or_bad_session_is_masked(tmp_path) -> None:
    """Fail closed: a caller the policy cannot identify sees masked data."""
    repo = await _repo(tmp_path)
    privacy = _client(build_privacy_router(_settings(), repo=repo, validator=FakeValidator(TOKENS)))
    assert privacy.get("/privacy/me").json() == {"masking_enabled": True, "masked": True}
    assert privacy.get("/privacy/me", headers=session("nobody")).json()["masked"] is True
    assert privacy.get("/privacy/me", headers=session("agent")).json()["masked"] is True
    assert privacy.get("/privacy/me", headers=session("viewer")).json()["masked"] is False

    class _Broken:
        async def permissions_for_user(self, _uid):
            raise RuntimeError("rbac db down")

    broken = _client(
        build_privacy_router(_settings(), repo=_Broken(), validator=FakeValidator(TOKENS))
    )
    assert broken.get("/privacy/me", headers=session("viewer")).json()["masked"] is True


# --- RSA incident log and CSV export -------------------------------------------


async def test_rsa_list_and_get_masked(tmp_path) -> None:
    repo = await _repo(tmp_path)
    rsa = await _rsa_with_incident()
    client = _client(build_rsa_router(rsa, _settings(), repo, FakeValidator(TOKENS)))

    row = client.get("/rsa/incidents", headers=session("agent")).json()["incidents"][0]
    assert row["customer_phone"] == f"+60{MASK * 5}6789"
    assert row["vehicle_no"] == "BMT5518"
    one = client.get(f"/rsa/incidents/{row['id']}", headers=session("agent")).json()
    assert one["customer_phone"] == f"+60{MASK * 5}6789"
    raw = client.get(f"/rsa/incidents/{row['id']}", headers=session("viewer")).json()
    assert raw["customer_phone"] == PHONE


async def test_csv_export_masked(tmp_path) -> None:
    repo = await _repo(tmp_path)
    rsa = await _rsa_with_incident()
    client = _client(build_rsa_router(rsa, _settings(), repo, FakeValidator(TOKENS)))

    res = client.get("/rsa/incidents/export", headers=session("agent"))
    assert res.status_code == 200
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert rows[0]["customer_phone"] == f"+60{MASK * 5}6789"
    assert rows[0]["remarks"] == f"owner IC ******-**-5678, alt contact s{MASK * 3}@gmail.com"
    assert rows[0]["vehicle_no"] == "BMT5518"
    assert PHONE not in res.text and IC not in res.text

    full = client.get("/rsa/incidents/export", headers=session("viewer"))
    assert list(csv.DictReader(io.StringIO(full.text)))[0]["customer_phone"] == PHONE


async def test_masked_value_cannot_be_written_back(tmp_path) -> None:
    """A masked value round-tripped from a masked page must never overwrite
    the real one."""
    repo = await _repo(tmp_path)
    rsa = await _rsa_with_incident()
    client = _client(build_rsa_router(rsa, _settings(), repo, FakeValidator(TOKENS)))
    row = client.get("/rsa/incidents", headers=session("agent")).json()["incidents"][0]

    res = client.patch(
        f"/rsa/incidents/{row['id']}",
        json={"customer_phone": row["customer_phone"]},
        headers=session("agent"),
    )
    assert res.status_code == 400
    assert (await rsa.get_incident(row["id"])).customer_phone == PHONE
    ok = client.patch(
        f"/rsa/incidents/{row['id']}", json={"cause": "battery"}, headers=session("agent")
    )
    assert ok.status_code == 200


# --- AI activity previews ---------------------------------------------------------


async def test_ai_activity_preview_masked(tmp_path) -> None:
    repo = await _repo(tmp_path)
    chatwoot = AsyncMock()

    async def by_status(status, **_kw):
        if status != "pending":
            return ConversationListing([])
        return ConversationListing(
            [
                {
                    "id": 1,
                    "inbox_id": 3,
                    "status": "pending",
                    "labels": [],
                    "meta": {"sender": {"name": "Siti"}},
                    "last_non_activity_message": {
                        "content": f"my IC is {IC} and phone {PHONE}",
                        "message_type": 0,
                        "created_at": 1_700_000_000,
                    },
                }
            ]
        )

    chatwoot.list_conversations_by_status.side_effect = by_status
    chatwoot.is_ai_paused.return_value = False
    client = _client(build_ai_activity_router(chatwoot, repo, FakeValidator(TOKENS), _settings()))

    masked = client.get("/admin/ai-activity", headers=session("agent")).json()
    preview = masked["conversations"][0]["last_action"]["preview"]
    assert preview == f"my IC is ******-**-5678 and phone +60{MASK * 5}6789"
    # Same cache, next caller has pii.view: the cached body was never masked in place.
    raw = client.get("/admin/ai-activity", headers=session("viewer")).json()
    assert raw["conversations"][0]["last_action"]["preview"] == f"my IC is {IC} and phone {PHONE}"


# --- Case fields --------------------------------------------------------------------


async def test_case_fields_masked_and_masked_write_refused(tmp_path) -> None:
    repo = await _repo(tmp_path)
    conv = {
        "id": 1,
        "inbox_id": 3,
        "labels": [],
        "custom_attributes": {
            "vehicle_chassis": VIN,
            "vehicle_plate": "BMT5518",
            "case_detail": f"customer email {EMAIL}",
        },
    }

    async def _read(_cid):
        return conv

    merge = AsyncMock()
    client = _client(
        build_case_fields_router(
            _read, merge, repo, FakeValidator(TOKENS), _settings(case_fields_enabled=True)
        )
    )
    fields = {
        f["name"]: f["value"]
        for f in client.get("/cases/1/fields", headers=session("agent")).json()["fields"]
    }
    assert fields["vehicle_chassis"] == f"{MASK * 13}2345"
    assert fields["vehicle_plate"] == "BMT5518"
    assert fields["case_detail"] == f"customer email s{MASK * 3}@gmail.com"
    raw = {
        f["name"]: f["value"]
        for f in client.get("/cases/1/fields", headers=session("viewer")).json()["fields"]
    }
    assert raw["vehicle_chassis"] == VIN

    res = client.patch(
        "/cases/1/fields",
        json={"fields": {"vehicle_chassis": fields["vehicle_chassis"]}},
        headers=session("agent"),
    )
    assert res.status_code == 400
    merge.assert_not_called()
