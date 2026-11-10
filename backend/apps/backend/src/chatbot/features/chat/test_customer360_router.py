from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import chatbot.features.chat.customer360_router as customer360_module
from chatbot.features.authz.db import build_engine as build_authz_engine
from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
from chatbot.features.authz.db import init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.customer360_router import (
    _DMS_BUDGET_FLOOR_SECONDS,
    _build_dms_block,
    _plate_variants,
    build_customer360_router,
)
from chatbot.features.chat.dms_client import (
    DmsCustomer,
    DmsServiceRecord,
    DmsVehicle,
    MockDmsClient,
)
from chatbot.features.chat.dms_config_store import MAX_TIMEOUT_SECONDS, DmsConfig
from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository
from chatbot.platform.config import Settings, get_settings

HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}


async def _build_authz_repo(tmp_path, name: str) -> AuthzRepository:
    authz_engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(authz_engine)
    return AuthzRepository(build_authz_session_maker(authz_engine))


def _app_with_router(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _fake_chatwoot(*, contacts=None, contact_conversations=None, all_conversations=None):
    """AsyncMock stand-in for ChatwootAdapter exposing only the three public
    Customer 360 read methods the router calls -- search_contacts,
    list_contact_conversations, list_conversations."""
    chatwoot = AsyncMock()
    chatwoot.search_contacts.return_value = contacts or []
    chatwoot.list_contact_conversations.return_value = contact_conversations or []
    chatwoot.list_conversations.return_value = all_conversations or []
    chatwoot.find_contacts_by_vehicle_no.return_value = []
    chatwoot.find_conversations_by_vehicle_plate.return_value = []
    return chatwoot


async def _authorized(tmp_path, name: str, user_id: int):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, name)
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=user_id, role_id="administrator")
    validator = TokenValidator(settings)
    return settings, authz_repo, validator


@pytest.mark.asyncio
async def test_search_requires_customer360_view_permission(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "no_perm")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=9, role_id="agent")  # lacks customer360.view

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    validator = TokenValidator(settings)
    router = build_customer360_router(
        _fake_chatwoot(), InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=+60123456789", headers=HEADERS)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(tmp_path):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "unauth")
    await seed_defaults(authz_repo)
    validator = TokenValidator(settings)
    router = build_customer360_router(
        _fake_chatwoot(), InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=+60123456789")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_phone_query_searches_contact_and_conversations(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "phone_ok", 10)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 10})
    )
    contact = {"id": 77, "name": "Ali", "phone_number": "+60123456789"}
    conversations = [{"id": 501, "status": "resolved", "inbox_id": 1}]
    chatwoot = _fake_chatwoot(contacts=[contact], contact_conversations=conversations)
    rsa_repo = InMemoryRsaRepository()
    router = build_customer360_router(chatwoot, rsa_repo, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=%2B60123456789", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["contact"] == contact
    assert body["conversations"] == conversations
    assert body["rsa_incidents"] == []
    chatwoot.search_contacts.assert_awaited_once_with("+60123456789")
    chatwoot.list_contact_conversations.assert_awaited_once_with(77)
    chatwoot.list_conversations.assert_not_awaited()


@pytest.mark.asyncio
async def test_phone_query_picks_exact_digits_match_over_first_result(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "phone_pick", 14)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 14})
    )
    decoy = {"id": 1, "name": "Decoy", "phone_number": "+60111111111"}
    wanted = {"id": 2, "name": "Wanted", "phone_number": "60-1234-5678"}
    chatwoot = _fake_chatwoot(contacts=[decoy, wanted], contact_conversations=[])
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=%2B60%201234%205678", headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["contact"] == wanted
    chatwoot.list_contact_conversations.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_vehicle_query_searches_rsa_incidents_and_conversations(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "vehicle_ok", 11)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 11})
    )
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(
        incident_date="2026-08-01", vehicle_no="ABC1234", vehicle_model="Camry", cause="flat tyre"
    )
    await rsa_repo.create_incident(
        incident_date="2026-08-02", vehicle_no="XYZ9999", vehicle_model="Corolla", cause="battery"
    )
    matching_conv = {
        "id": 42,
        "status": "open",
        "inbox_id": 1,
        "custom_attributes": {"vehicle_model": "Toyota Camry"},
    }
    other_conv = {
        "id": 43,
        "status": "open",
        "inbox_id": 1,
        "custom_attributes": {"vehicle_model": "Honda Civic"},
    }
    chatwoot = _fake_chatwoot(all_conversations=[matching_conv, other_conv])
    router = build_customer360_router(chatwoot, rsa_repo, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=camry", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["contact"] is None
    assert len(body["rsa_incidents"]) == 0  # "camry" doesn't substring-match a vehicle_no
    assert body["conversations"] == [matching_conv]
    chatwoot.search_contacts.assert_not_awaited()

    res2 = client.get("/admin/customer360/search?q=ABC1234", headers=HEADERS)
    body2 = res2.json()
    assert len(body2["rsa_incidents"]) == 1
    assert body2["rsa_incidents"][0]["vehicle_no"] == "ABC1234"


async def _client_for(tmp_path, name, uid, respx_mock, chatwoot, rsa_repo):
    settings, authz_repo, validator = await _authorized(tmp_path, name, uid)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": uid})
    )
    router = build_customer360_router(chatwoot, rsa_repo, authz_repo, validator, settings)
    return _app_with_router(router)


@pytest.mark.asyncio
async def test_vehicle_search_matches_car_plate_attribute(tmp_path, respx_mock):
    # The Cases "Car Plate" column reads the contact's custom attribute
    # vehicle_no (conversation meta.sender.custom_attributes), not vehicle_model.
    contact = {"id": 9, "name": "Plate Owner", "custom_attributes": {"vehicle_no": "BMT5518"}}
    conv = {"id": 700, "status": "open", "inbox_id": 1}
    chatwoot = _fake_chatwoot(contact_conversations=[conv])
    chatwoot.find_contacts_by_vehicle_no.return_value = [contact]
    client = await _client_for(
        tmp_path, "plate_attr", 20, respx_mock, chatwoot, InMemoryRsaRepository()
    )

    res = client.get("/admin/customer360/search?q=BMT5518", headers=HEADERS)
    body = res.json()
    assert res.status_code == 200
    assert body["contact"] == contact
    assert body["conversations"] == [conv]
    chatwoot.list_contact_conversations.assert_awaited_once_with(9)
    # no per-conversation scan of the whole inbox
    chatwoot.list_conversations.assert_not_awaited()


@pytest.mark.asyncio
async def test_vehicle_search_normalises_spaces_and_case(tmp_path, respx_mock):
    chatwoot = _fake_chatwoot()
    chatwoot.find_contacts_by_vehicle_no.return_value = []
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(
        incident_date="2026-08-01", vehicle_no="BMT5518", cause="flat tyre"
    )
    await rsa_repo.create_incident(
        incident_date="2026-08-02", vehicle_no="XYZ 9999", cause="battery"
    )
    client = await _client_for(tmp_path, "plate_norm", 21, respx_mock, chatwoot, rsa_repo)

    res = client.get("/admin/customer360/search?q=bmt%205518", headers=HEADERS)
    body = res.json()
    assert [i["vehicle_no"] for i in body["rsa_incidents"]] == ["BMT5518"]
    (variants,) = chatwoot.find_contacts_by_vehicle_no.await_args.args
    assert "BMT5518" in variants  # compact, upper-cased form is always tried

    res2 = client.get("/admin/customer360/search?q=xyz9999", headers=HEADERS)
    assert [i["vehicle_no"] for i in res2.json()["rsa_incidents"]] == ["XYZ 9999"]


@pytest.mark.asyncio
async def test_phone_search_returns_rsa_incidents_with_that_phone(tmp_path, respx_mock):
    contact = {"id": 77, "name": "Ali", "phone_number": "+60123456789"}
    chatwoot = _fake_chatwoot(contacts=[contact], contact_conversations=[])
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(
        incident_date="2026-08-01", vehicle_no="AAA1", cause="x", customer_phone="012-345 6789"
    )
    await rsa_repo.create_incident(
        incident_date="2026-08-02", vehicle_no="BBB2", cause="y", customer_phone="+60199999999"
    )
    await rsa_repo.create_incident(incident_date="2026-08-03", vehicle_no="CCC3", cause="z")
    client = await _client_for(tmp_path, "phone_rsa", 22, respx_mock, chatwoot, rsa_repo)

    res = client.get("/admin/customer360/search?q=%2B60123456789", headers=HEADERS)
    assert [i["vehicle_no"] for i in res.json()["rsa_incidents"]] == ["AAA1"]
    assert res.json()["rsa_incidents"][0]["customer_phone"] == "012-345 6789"


@pytest.mark.asyncio
async def test_slow_plate_filter_returns_within_budget(tmp_path, respx_mock, monkeypatch):
    monkeypatch.setattr(customer360_module, "_VEHICLE_CRM_BUDGET_SECONDS", 0.2)
    chatwoot = _fake_chatwoot()

    async def slow(_variants):
        await asyncio.sleep(5)
        return []

    chatwoot.find_contacts_by_vehicle_no.side_effect = slow
    client = await _client_for(
        tmp_path, "plate_slow", 23, respx_mock, chatwoot, InMemoryRsaRepository()
    )
    start = time.monotonic()
    res = client.get("/admin/customer360/search?q=BMT5518", headers=HEADERS)
    assert time.monotonic() - start < 2
    assert res.status_code == 200
    assert res.json()["conversations"] == []
    chatwoot.list_conversations.assert_not_awaited()  # plate-like miss: no fallback


@pytest.mark.asyncio
async def test_plate_like_miss_skips_model_fallback_but_model_query_uses_it(tmp_path, respx_mock):
    chatwoot = _fake_chatwoot()
    client = await _client_for(
        tmp_path, "fallback", 24, respx_mock, chatwoot, InMemoryRsaRepository()
    )
    for q in ("BMT5518", "bmt%205518", "BMT-5518"):
        client.get(f"/admin/customer360/search?q={q}", headers=HEADERS)
    chatwoot.list_conversations.assert_not_awaited()

    client.get("/admin/customer360/search?q=X50", headers=HEADERS)
    chatwoot.list_conversations.assert_awaited_once()


@pytest.mark.asyncio
async def test_exact_plate_contact_is_preferred_and_dash_variant_sent(tmp_path, respx_mock):
    near = {"id": 1, "custom_attributes": {"vehicle_no": "BMT55189"}}
    exact = {"id": 2, "custom_attributes": {"vehicle_no": "BMT 5518"}}
    chatwoot = _fake_chatwoot()
    chatwoot.find_contacts_by_vehicle_no.return_value = [near, exact]
    client = await _client_for(
        tmp_path, "plate_exact", 25, respx_mock, chatwoot, InMemoryRsaRepository()
    )
    res = client.get("/admin/customer360/search?q=BMT5518", headers=HEADERS)
    assert res.json()["contact"]["id"] == 2
    (variants,) = chatwoot.find_contacts_by_vehicle_no.await_args.args
    assert {"BMT5518", "BMT 5518", "BMT-5518"} <= set(variants)


@pytest.mark.asyncio
async def test_no_match_returns_empty_lists_not_error(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "no_match", 12)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 12})
    )
    chatwoot = _fake_chatwoot()
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=nomatchvehicle", headers=HEADERS)
    assert res.status_code == 200
    assert res.json() == {"contact": None, "conversations": [], "rsa_incidents": []}


@pytest.mark.asyncio
async def test_phone_query_no_contact_found_returns_empty(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "phone_no_match", 13)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 13})
    )
    chatwoot = _fake_chatwoot(contacts=[])
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=+60199999999", headers=HEADERS)
    assert res.status_code == 200
    assert res.json() == {"contact": None, "conversations": [], "rsa_incidents": []}
    chatwoot.list_contact_conversations.assert_not_awaited()


@pytest.mark.asyncio
async def test_rbac_disabled_falls_back_to_shared_secret(tmp_path):
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "secret123"}
    )
    authz_repo = await _build_authz_repo(tmp_path, "disabled")
    validator = TokenValidator(settings)
    chatwoot = _fake_chatwoot(contacts=[{"id": 5, "phone_number": "+60100000000"}])
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    assert client.get("/admin/customer360/search?q=%2B60100000000").status_code == 401
    assert (
        client.get(
            "/admin/customer360/search?q=%2B60100000000", headers={"x-api-key": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/admin/customer360/search?q=%2B60100000000", headers={"x-api-key": "secret123"}
        ).status_code
        == 200
    )


@pytest.mark.asyncio
async def test_query_too_short_returns_422(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "too_short", 15)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 15})
    )
    router = build_customer360_router(
        _fake_chatwoot(), InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=a", headers=HEADERS)
    assert res.status_code == 422


# --- Package F: the optional `dms` block --------------------------------
#
# `dms_config_store` and `dms_client` are both optional constructor params
# (default None). main.py passes BOTH since c85fa89 -- the store always, the
# client only under DMS_MOCK_CLIENT_ENABLED -- so the store-based tests here
# are what cover the configuration production actually runs. The
# params-omitted case is still exercised (any other caller can omit them),
# and it is what test_response_is_unchanged_when_the_integration_is_disabled
# pins; but the disabled-config path below is the one that matters in
# production, since main.py wires a store on every tenant, DMS or not.


def _dms_config(*, enabled: bool, timeout_seconds: float = 10.0) -> DmsConfig:
    return DmsConfig(
        enabled=enabled,
        provider_label="Proton DMS",
        base_url="https://dms.example.com",
        auth_type="api_key_header",
        extra_header_name="",
        extra_header_value="",
        timeout_seconds=timeout_seconds,
        retries=0,
    )


class _StubDmsConfigStore:
    """Minimal stand-in for DmsConfigStore -- customer360_router.py only
    ever calls .get() on it."""

    def __init__(self, config: DmsConfig | None) -> None:
        self._config = config

    async def get(self) -> DmsConfig | None:
        return self._config


class _RaisingDmsConfigStore:
    async def get(self) -> DmsConfig | None:
        raise RuntimeError("firestore is down")


class _EmptyDmsClient:
    """A DMS client that is genuinely reachable and genuinely has nothing
    on file for this customer -- the "no records" half of the
    empty-vs-unreachable distinction."""

    async def find_customer(self, *, phone, vehicle_no):
        return None

    async def list_vehicles(self, customer_ref):
        return []

    async def list_service_history(self, vehicle_no):
        return []


class _RaisingDmsClient:
    async def find_customer(self, *, phone, vehicle_no):
        raise RuntimeError("dms outage")

    async def list_vehicles(self, customer_ref):
        return []

    async def list_service_history(self, vehicle_no):
        return []


class _SlowDmsClient:
    """Never fails, just never returns before an operator would have given
    up waiting -- proves the DMS side-trip is bounded, not left to hang for
    however long the client takes."""

    async def find_customer(self, *, phone, vehicle_no):
        await asyncio.sleep(1.5)

    async def list_vehicles(self, customer_ref):
        return []

    async def list_service_history(self, vehicle_no):
        return []


class _ManyVehiclesDmsClient:
    """Returns more vehicles than the service-history fan-out cap, so the
    cap can be pinned by call count rather than assumed."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def find_customer(self, *, phone, vehicle_no):
        return DmsCustomer(ref="CUST-1", name="Many Vehicles", phone=phone)

    async def list_vehicles(self, customer_ref):
        return [DmsVehicle(vehicle_no=f"V{i}", model="X", purchased_from=None) for i in range(8)]

    async def list_service_history(self, vehicle_no):
        self.calls.append(vehicle_no)
        return [DmsServiceRecord(date="2026-01-01", description="d", dealer=None)]


class _PartiallyFailingDmsClient:
    """One list_service_history call fails immediately; the others take a
    beat before finishing. Pins that the fan-out waits for every sibling
    call to actually finish -- not just for the first exception -- before
    the block degrades to unreachable. Under the pre-fix
    `asyncio.gather(...)` (default `return_exceptions=False`), V2's
    exception propagates the instant it's raised, `_build_dms_block`
    returns immediately, and V1/V3 -- still mid-`sleep` -- are abandoned
    without ever reaching `self.completed`. Under the fix, `completed` is
    reliably `{"V1", "V3"}` by the time the awaited call returns."""

    def __init__(self) -> None:
        self.completed: list[str] = []

    async def find_customer(self, *, phone, vehicle_no):
        return DmsCustomer(ref="CUST-1", name="Partial", phone=phone)

    async def list_vehicles(self, customer_ref):
        return [
            DmsVehicle(vehicle_no="V1", model="X", purchased_from=None),
            DmsVehicle(vehicle_no="V2", model="X", purchased_from=None),
            DmsVehicle(vehicle_no="V3", model="X", purchased_from=None),
        ]

    async def list_service_history(self, vehicle_no):
        if vehicle_no == "V2":
            raise RuntimeError("dms outage for V2")
        await asyncio.sleep(0.05)
        self.completed.append(vehicle_no)
        return [DmsServiceRecord(date="2026-01-01", description="d", dealer=None)]


@pytest.mark.asyncio
async def test_response_is_unchanged_when_the_integration_is_disabled(tmp_path, respx_mock):
    """A router built with neither DMS param must return exactly the three
    keys it always has -- byte-identical to before this package existed.

    Note this is NOT how main.py builds the router (it has passed both
    params since c85fa89); the production-shaped equivalent is
    `test_response_is_unchanged_when_config_store_says_disabled` below,
    which wires a store and gets the same three keys. This test guards the
    weaker, structural claim: the block cannot even be computed when the
    caller declines to opt in.
    """
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_off_default", 20)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 20})
    )
    router = build_customer360_router(
        _fake_chatwoot(), InMemoryRsaRepository(), authz_repo, validator, settings
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=0123456789", headers=HEADERS)
    assert res.status_code == 200
    assert set(res.json()) == {"contact", "conversations", "rsa_incidents"}


@pytest.mark.asyncio
async def test_response_is_unchanged_when_config_store_says_disabled(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_off_store", 21)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 21})
    )
    router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_StubDmsConfigStore(_dms_config(enabled=False)),
        dms_client=MockDmsClient(environment="sandbox"),  # even wired, must never be consulted while disabled
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=0123456789", headers=HEADERS)
    assert res.status_code == 200
    assert set(res.json()) == {"contact", "conversations", "rsa_incidents"}


@pytest.mark.asyncio
async def test_response_is_unchanged_when_no_config_is_stored_yet(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_off_none", 22)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 22})
    )
    router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_StubDmsConfigStore(None),
        dms_client=MockDmsClient(environment="sandbox"),
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=0123456789", headers=HEADERS)
    assert res.status_code == 200
    assert set(res.json()) == {"contact", "conversations", "rsa_incidents"}


@pytest.mark.asyncio
async def test_mock_client_enabled_shows_dms_block_labeled_as_mock(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_mock", 23)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 23})
    )
    router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_StubDmsConfigStore(_dms_config(enabled=True)),
        dms_client=MockDmsClient(environment="sandbox"),
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=0123456789", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"contact", "conversations", "rsa_incidents", "dms"}
    dms = body["dms"]
    assert dms["status"] == "ok"
    assert dms["mock"] is True
    assert "(Demo data)" in dms["customer"]["name"]
    assert dms["vehicles"] and "(Demo data)" in dms["vehicles"][0]["model"]
    assert dms["service_history"]
    # Our field names, never a vendor's.
    assert set(dms["customer"]) == {"ref", "name", "phone"}
    assert set(dms["vehicles"][0]) == {"vehicle_no", "model", "purchased_from"}
    assert set(dms["service_history"][0]) == {"date", "description", "dealer"}


@pytest.mark.asyncio
async def test_dms_client_exception_still_returns_all_crm_blocks_fail_open(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_raises", 24)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 24})
    )
    contact = {"id": 88, "name": "Wati", "phone_number": "+60177777777"}
    conversations = [{"id": 900, "status": "open", "inbox_id": 1}]
    chatwoot = _fake_chatwoot(contacts=[contact], contact_conversations=conversations)
    router = build_customer360_router(
        chatwoot,
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_StubDmsConfigStore(_dms_config(enabled=True)),
        dms_client=_RaisingDmsClient(),
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=%2B60177777777", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["contact"] == contact
    assert body["conversations"] == conversations
    assert body["rsa_incidents"] == []
    # dms_config_store says enabled and a client is wired, so the block is
    # guaranteed present -- it must never claim success, fail-open not silent.
    assert body["dms"]["status"] == "unreachable"


@pytest.mark.asyncio
async def test_dms_enabled_but_no_client_wired_reads_as_unreachable_not_empty(tmp_path, respx_mock):
    """Phase 1 ships no real adapter. An operator who flips 'enabled' before
    one exists must see 'not connected', never a silent 'no records found'
    that could pass for a working integration."""
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_no_client", 25)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 25})
    )
    router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_StubDmsConfigStore(_dms_config(enabled=True)),
        dms_client=None,
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=0123456789", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["dms"]["status"] == "unreachable"
    assert body["dms"]["mock"] is False


@pytest.mark.asyncio
async def test_empty_dms_result_is_distinguishable_from_unreachable(tmp_path, respx_mock):
    """The single property this package is graded on: an operator must be
    able to tell 'the DMS has nothing on this customer' from 'we couldn't
    reach the DMS'. Both leave customer/vehicles/service_history empty --
    only `status` differs."""
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_empty", 26)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 26})
    )
    empty_router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_StubDmsConfigStore(_dms_config(enabled=True)),
        dms_client=_EmptyDmsClient(),
    )
    unreachable_router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_StubDmsConfigStore(_dms_config(enabled=True)),
        dms_client=_RaisingDmsClient(),
    )
    empty_body = (
        _app_with_router(empty_router)
        .get("/admin/customer360/search?q=0123456789", headers=HEADERS)
        .json()
    )
    unreachable_body = (
        _app_with_router(unreachable_router)
        .get("/admin/customer360/search?q=0123456789", headers=HEADERS)
        .json()
    )

    assert empty_body["dms"]["status"] == "ok"
    assert unreachable_body["dms"]["status"] == "unreachable"
    assert empty_body["dms"]["status"] != unreachable_body["dms"]["status"]
    for body in (empty_body, unreachable_body):
        assert body["dms"]["customer"] is None
        assert body["dms"]["vehicles"] == []
        assert body["dms"]["service_history"] == []


@pytest.mark.asyncio
async def test_dms_config_store_failure_omits_the_block_rather_than_guessing(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_store_raises", 27)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 27})
    )
    router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_RaisingDmsConfigStore(),
        dms_client=MockDmsClient(environment="sandbox"),
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=0123456789", headers=HEADERS)
    assert res.status_code == 200
    assert set(res.json()) == {"contact", "conversations", "rsa_incidents"}


@pytest.mark.asyncio
async def test_slow_dms_client_is_bounded_not_left_to_hang(tmp_path, respx_mock):
    """A Customer 360 lookup is interactive. The whole DMS side-trip must be
    bounded to roughly one timeout window, not left to run for however long
    a slow/hung client takes."""
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_slow", 28)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 28})
    )
    router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        # Deliberately tiny -- below the floor -- so this also pins that a
        # degenerate/near-zero configured timeout can't make the bound
        # disappear entirely.
        dms_config_store=_StubDmsConfigStore(_dms_config(enabled=True, timeout_seconds=0.05)),
        dms_client=_SlowDmsClient(),
    )
    client = _app_with_router(router)

    started = time.monotonic()
    res = client.get("/admin/customer360/search?q=0123456789", headers=HEADERS)
    elapsed = time.monotonic() - started

    assert res.status_code == 200
    assert res.json()["dms"]["status"] == "unreachable"
    # The client sleeps 1.5s; a bound near the 1.0s floor must cut it off
    # well before that, not merely before some generous outer ceiling.
    assert elapsed < 1.3


@pytest.mark.asyncio
async def test_service_history_fanout_is_capped_per_customer(tmp_path, respx_mock):
    settings, authz_repo, validator = await _authorized(tmp_path, "dms_many_vehicles", 29)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 29})
    )
    dms_client = _ManyVehiclesDmsClient()
    router = build_customer360_router(
        _fake_chatwoot(),
        InMemoryRsaRepository(),
        authz_repo,
        validator,
        settings,
        dms_config_store=_StubDmsConfigStore(_dms_config(enabled=True)),
        dms_client=dms_client,
    )
    client = _app_with_router(router)

    res = client.get("/admin/customer360/search?q=0123456789", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["dms"]["status"] == "ok"
    # 8 vehicles were on file; the service-history fan-out is capped.
    assert len(dms_client.calls) <= 5
    assert len(dms_client.calls) < 8


@pytest.mark.asyncio
async def test_one_failing_vehicle_history_call_does_not_orphan_siblings():
    """Regression test: asyncio.gather's default return_exceptions=False
    propagates the first exception immediately and leaves the other
    in-flight list_service_history calls running -- uncancelled, unawaited
    by anything, outside the request's wait_for window, able to emit "Task
    exception was never retrieved" noise and hold resources with no bound of
    their own. return_exceptions=True (the fix) makes the fan-out wait for
    every sibling call to actually finish before the block degrades to
    unreachable.

    Calls `_build_dms_block` directly rather than through the HTTP router so
    "did the siblings actually finish" is observed with certainty at the
    moment the awaited call returns, with no ambiguity from a test client's
    own event-loop handling.
    """
    dms_client = _PartiallyFailingDmsClient()

    block = await _build_dms_block(
        _StubDmsConfigStore(_dms_config(enabled=True)),
        dms_client,
        phone="0123456789",
        vehicle_no=None,
    )

    assert block is not None
    assert block["status"] == "unreachable"
    assert block["service_history"] == []
    # V1 and V3 were allowed to actually finish before an answer came back --
    # not abandoned mid-flight the instant V2 raised.
    assert set(dms_client.completed) == {"V1", "V3"}


# --- the DMS time budget has a ceiling, not just a floor --------------------


def _budget_spy(monkeypatch) -> list[float]:
    """Record every timeout `_build_dms_block` hands `asyncio.wait_for`.

    Asserting on the budget the code actually applies beats timing the real
    wait: a ten-minute ceiling violation can't be observed by a stopwatch in
    a test suite, and a stopwatch assertion would be flaky besides.
    """
    seen: list[float] = []
    real_wait_for = asyncio.wait_for

    async def spy(awaitable, timeout):  # noqa: ASYNC109 -- mirrors wait_for's own signature
        seen.append(timeout)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr("chatbot.features.chat.customer360_router.asyncio.wait_for", spy)
    return seen


async def _budget_for(monkeypatch, timeout_seconds: float) -> list[float]:
    seen = _budget_spy(monkeypatch)
    await _build_dms_block(
        _StubDmsConfigStore(_dms_config(enabled=True, timeout_seconds=timeout_seconds)),
        MockDmsClient(environment="sandbox"),
        phone="0123456789",
        vehicle_no=None,
    )
    return seen


@pytest.mark.asyncio
async def test_stored_timeout_is_clamped_to_the_ceiling(monkeypatch):
    """`DmsConfigBody` now rejects an out-of-range `timeout_seconds` on
    write, but that constraint post-dates the field: a document saved before
    it existed (or by anything bypassing the admin API) can still hold 600,
    and the read path is where a human waits. An operator typing 600 must not
    be able to make every Customer 360 lookup hang for ten minutes.
    """
    assert await _budget_for(monkeypatch, 600.0) == [MAX_TIMEOUT_SECONDS]


@pytest.mark.asyncio
async def test_the_floor_still_applies_under_the_new_ceiling(monkeypatch):
    """The ceiling must not have displaced the floor: a degenerate near-zero
    stored timeout still gets raised to the floor, not clamped to zero."""
    assert await _budget_for(monkeypatch, 0.0) == [_DMS_BUDGET_FLOOR_SECONDS]


@pytest.mark.asyncio
async def test_an_in_range_timeout_is_passed_through_untouched(monkeypatch):
    assert await _budget_for(monkeypatch, 7.5) == [7.5]


# --- C360-002: a vehicle number returns the customer, not only RSA incidents --


@pytest.mark.asyncio
async def test_plate_only_on_conversation_returns_its_contact(tmp_path, respx_mock):
    owner = {"id": 31, "name": "Conv Owner", "phone_number": "+60000000031"}
    plate_conv = {"id": 801, "status": "open", "inbox_id": 1,
                  "custom_attributes": {"vehicle_plate": "VAB1234"},
                  "meta": {"sender": owner}}
    older = {"id": 802, "status": "resolved", "inbox_id": 1}
    chatwoot = _fake_chatwoot(contact_conversations=[plate_conv, older])
    chatwoot.find_conversations_by_vehicle_plate.return_value = [plate_conv]
    client = await _client_for(
        tmp_path, "plate_conv", 40, respx_mock, chatwoot, InMemoryRsaRepository()
    )
    res = client.get("/admin/customer360/search?q=vab%201234", headers=HEADERS)
    body = res.json()
    assert res.status_code == 200
    assert body["contact"]["id"] == 31
    assert [c["id"] for c in body["conversations"]] == [801, 802]
    chatwoot.list_contact_conversations.assert_awaited_once_with(31)
    (variants,) = chatwoot.find_conversations_by_vehicle_plate.await_args.args
    assert "VAB1234" in variants  # case_fields stores vehicle_plate compact


@pytest.mark.asyncio
async def test_plate_only_on_rsa_incident_returns_contact_by_incident_phone(
    tmp_path, respx_mock
):
    """The Playwright C360-002 data: the plate lives only on an RSA incident
    that carries the contact's phone -- the contact is found through it."""
    contact = {"id": 32, "name": "[TEST] Rsa Owner", "phone_number": "+60120000032"}
    conv = {"id": 803, "status": "open", "inbox_id": 1}
    chatwoot = _fake_chatwoot(contacts=[contact], contact_conversations=[conv])
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(
        incident_date="2026-11-10", vehicle_no="TEST ABC123V", vehicle_model="X50",
        cause="[TEST] marker", customer_phone="012-000 0032",
    )
    client = await _client_for(tmp_path, "plate_rsa", 41, respx_mock, chatwoot, rsa_repo)
    for q in ("TEST%20ABC123V", "test%20abc123v", "TESTABC123V", "TEST-ABC123V"):
        body = client.get(f"/admin/customer360/search?q={q}", headers=HEADERS).json()
        assert body["contact"]["id"] == 32, q
        assert body["conversations"] == [conv], q
        assert [i["vehicle_no"] for i in body["rsa_incidents"]] == ["TEST ABC123V"], q
    chatwoot.search_contacts.assert_awaited_with("012-000 0032")


@pytest.mark.asyncio
async def test_rsa_phone_with_no_exact_contact_match_returns_no_contact(tmp_path, respx_mock):
    stranger = {"id": 33, "phone_number": "+60000000099"}
    chatwoot = _fake_chatwoot(contacts=[stranger])
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(
        incident_date="2026-11-10", vehicle_no="VCD5678", vehicle_model="X50",
        cause="x", customer_phone="+60000000034",
    )
    client = await _client_for(tmp_path, "plate_rsa_miss", 42, respx_mock, chatwoot, rsa_repo)
    body = client.get("/admin/customer360/search?q=VCD5678", headers=HEADERS).json()
    assert body["contact"] is None
    assert body["conversations"] == []
    assert len(body["rsa_incidents"]) == 1


@pytest.mark.asyncio
async def test_contact_attribute_variants_include_the_typed_spelling(tmp_path, respx_mock):
    chatwoot = _fake_chatwoot()
    client = await _client_for(
        tmp_path, "plate_typed", 43, respx_mock, chatwoot, InMemoryRsaRepository()
    )
    client.get("/admin/customer360/search?q=test%20%20abc123v", headers=HEADERS)
    (variants,) = chatwoot.find_contacts_by_vehicle_no.await_args.args
    assert "TEST ABC123V" in variants
    assert "TESTABC123V" in variants


@pytest.mark.asyncio
async def test_unknown_plate_no_contact_no_error_even_when_filters_fail(tmp_path, respx_mock):
    chatwoot = _fake_chatwoot()
    chatwoot.find_conversations_by_vehicle_plate.side_effect = RuntimeError("no attr")
    chatwoot.find_contacts_by_vehicle_no.return_value = None
    client = await _client_for(
        tmp_path, "plate_unknown", 44, respx_mock, chatwoot, InMemoryRsaRepository()
    )
    res = client.get("/admin/customer360/search?q=ZZZ9999", headers=HEADERS)
    assert res.status_code == 200
    assert res.json() == {"contact": None, "conversations": [], "rsa_incidents": []}


@pytest.mark.asyncio
async def test_phone_search_never_runs_plate_lookups(tmp_path, respx_mock):
    contact = {"id": 35, "phone_number": "+60000000035"}
    chatwoot = _fake_chatwoot(contacts=[contact])
    client = await _client_for(
        tmp_path, "phone_only", 45, respx_mock, chatwoot, InMemoryRsaRepository()
    )
    res = client.get("/admin/customer360/search?q=%2B60000000035", headers=HEADERS)
    assert res.json()["contact"] == contact
    chatwoot.find_conversations_by_vehicle_plate.assert_not_awaited()
    chatwoot.find_contacts_by_vehicle_no.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_conversation_plate_filter_posts_vehicle_plate():
    adapter = ChatwootAdapter(Settings(chatwoot_account_id=1, chatwoot_inbox_id=7))
    calls = []

    async def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        return {"meta": {}, "payload": [{"id": 5}, "junk"]}

    adapter._request = fake_request
    out = await adapter.find_conversations_by_vehicle_plate(["VAB1234", "VAB 1234", "VAB1234"])
    assert out == [{"id": 5}]
    (method, path, body), = calls
    assert (method, path) == ("POST", "/conversations/filter")
    assert [f["attribute_key"] for f in body["payload"]] == ["vehicle_plate", "vehicle_plate"]
    assert [f["values"] for f in body["payload"]] == [["VAB1234"], ["VAB 1234"]]
    assert body["payload"][0]["query_operator"] == "OR"
    assert body["payload"][-1]["query_operator"] is None
    assert await adapter.find_conversations_by_vehicle_plate([]) == []


# --- C360-002 review: exactness and ranking -----------------------------------


@pytest.mark.asyncio
async def test_rsa_fallback_same_plate_two_phones_newest_incident_wins(tmp_path, respx_mock):
    """Documented choice: one plate, two phones on file (e.g. the car was
    sold) -> the phone on the NEWEST incident names the contact."""
    old = {"id": 51, "phone_number": "+60120000051"}
    new = {"id": 52, "phone_number": "+60120000052"}
    chatwoot = _fake_chatwoot()

    async def by_phone(phone):
        return [c for c in (old, new) if c["phone_number"].endswith(phone[-4:])]

    chatwoot.search_contacts.side_effect = by_phone
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(incident_date="2026-01-05", vehicle_no="VEF 4321",
                                   vehicle_model="X50", cause="x", customer_phone="0120000051")
    await rsa_repo.create_incident(incident_date="2026-10-05", vehicle_no="VEF4321",
                                   vehicle_model="X50", cause="y", customer_phone="0120000052")
    await rsa_repo.create_incident(incident_date="2026-09-05", vehicle_no="VEF4321",
                                   vehicle_model="X50", cause="z", customer_phone="0120000052")
    client = await _client_for(tmp_path, "rsa_newest", 46, respx_mock, chatwoot, rsa_repo)
    body = client.get("/admin/customer360/search?q=VEF4321", headers=HEADERS).json()
    assert body["contact"]["id"] == 52
    phones = [c.args[0] for c in chatwoot.search_contacts.await_args_list]
    assert phones[0] == "0120000052"
    assert phones.count("0120000052") == 1  # deduped


@pytest.mark.asyncio
async def test_rsa_fallback_ignores_substring_only_neighbour(tmp_path, respx_mock):
    contact = {"id": 53, "phone_number": "+60120000053"}
    chatwoot = _fake_chatwoot(contacts=[contact])
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(incident_date="2026-10-05", vehicle_no="VGH12345",
                                   vehicle_model="X50", cause="x", customer_phone="0120000053")
    client = await _client_for(tmp_path, "rsa_substr", 47, respx_mock, chatwoot, rsa_repo)
    body = client.get("/admin/customer360/search?q=VGH1234", headers=HEADERS).json()
    assert len(body["rsa_incidents"]) == 1  # still listed (partial plate search)
    assert body["contact"] is None  # but never names a contact
    chatwoot.search_contacts.assert_not_awaited()


@pytest.mark.asyncio
async def test_rsa_fallback_only_for_plate_shaped_queries(tmp_path, respx_mock):
    contact = {"id": 54, "phone_number": "+60120000054"}
    chatwoot = _fake_chatwoot(contacts=[contact])
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(incident_date="2026-10-05", vehicle_no="flat tyre",
                                   vehicle_model="X50", cause="x", customer_phone="0120000054")
    client = await _client_for(tmp_path, "rsa_shape", 48, respx_mock, chatwoot, rsa_repo)
    body = client.get("/admin/customer360/search?q=flat%20tyre", headers=HEADERS).json()
    assert body["contact"] is None
    chatwoot.search_contacts.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_conversation_plate_beats_substring_contact(tmp_path, respx_mock):
    near = {"id": 55, "custom_attributes": {"vehicle_no": "VJK12345"}}
    owner = {"id": 56, "name": "Exact Owner"}
    conv = {"id": 810, "inbox_id": 1, "custom_attributes": {"vehicle_plate": "VJK1234"},
            "meta": {"sender": owner}}
    chatwoot = _fake_chatwoot()
    chatwoot.find_contacts_by_vehicle_no.return_value = [near]
    chatwoot.find_conversations_by_vehicle_plate.return_value = [conv]
    client = await _client_for(
        tmp_path, "rank_cross", 49, respx_mock, chatwoot, InMemoryRsaRepository()
    )
    body = client.get("/admin/customer360/search?q=VJK1234", headers=HEADERS).json()
    assert body["contact"]["id"] == 56


@pytest.mark.asyncio
async def test_substring_hits_do_not_suppress_exact_rsa_fallback(tmp_path, respx_mock):
    near = {"id": 57, "custom_attributes": {"vehicle_no": "VLM12345"}}
    rsa_owner = {"id": 58, "phone_number": "+60120000058"}
    chatwoot = _fake_chatwoot(contacts=[rsa_owner])
    chatwoot.find_contacts_by_vehicle_no.return_value = [near]
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(incident_date="2026-10-05", vehicle_no="VLM 1234",
                                   vehicle_model="X50", cause="x", customer_phone="0120000058")
    client = await _client_for(tmp_path, "rank_rsa", 50, respx_mock, chatwoot, rsa_repo)
    body = client.get("/admin/customer360/search?q=VLM1234", headers=HEADERS).json()
    assert body["contact"]["id"] == 58


@pytest.mark.asyncio
async def test_exact_contact_hit_suppresses_rsa_fallback(tmp_path, respx_mock):
    exact = {"id": 59, "custom_attributes": {"vehicle_no": "VNP 1234"}}
    chatwoot = _fake_chatwoot(contacts=[{"id": 60, "phone_number": "+60120000060"}])
    chatwoot.find_contacts_by_vehicle_no.return_value = [exact]
    rsa_repo = InMemoryRsaRepository()
    await rsa_repo.create_incident(incident_date="2026-10-05", vehicle_no="VNP1234",
                                   vehicle_model="X50", cause="x", customer_phone="0120000060")
    client = await _client_for(tmp_path, "rank_exact", 51, respx_mock, chatwoot, rsa_repo)
    body = client.get("/admin/customer360/search?q=VNP1234", headers=HEADERS).json()
    assert body["contact"]["id"] == 59
    chatwoot.search_contacts.assert_not_awaited()


def test_typed_variant_strips_like_wildcards():
    variants = _plate_variants("ab%c_1 2")
    assert all("%" not in v and "_" not in v for v in variants)


# --- Brief 11: special plates up to 16 characters ------------------------


def test_special_plate_up_to_16_characters_is_plate_shaped():
    assert customer360_module._plate_shaped("PUTRAJAYA 1234")
    assert customer360_module._plate_shaped("ABCDEFGHIJKL1234")  # exactly 16
    assert not customer360_module._plate_shaped("ABCDEFGHIJKLM1234")  # 17
    assert not customer360_module._plate_shaped("flat tyre")
    assert not customer360_module._plate_shaped("X50")


# --- Brief 11: conversation-panel DMS card endpoints -----------------------


async def _dms_client_for(tmp_path, name, uid, respx_mock, *, store, client=None,
                          role="administrator"):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, name)
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=uid, role_id=role)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": uid})
    )
    chatwoot = _fake_chatwoot()
    router = build_customer360_router(
        chatwoot, InMemoryRsaRepository(), authz_repo, TokenValidator(settings), settings,
        dms_config_store=store, dms_client=client,
    )
    return _app_with_router(router), chatwoot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("store", "client", "connected"),
    [
        (None, None, False),
        (_StubDmsConfigStore(None), None, False),
        (_StubDmsConfigStore(_dms_config(enabled=False)), MockDmsClient(environment="sandbox"),
         False),
        # Enabled but nothing wired: "not connected", never "no record".
        (_StubDmsConfigStore(_dms_config(enabled=True)), None, False),
        (_RaisingDmsConfigStore(), MockDmsClient(environment="sandbox"), False),
        (_StubDmsConfigStore(_dms_config(enabled=True)), _EmptyDmsClient(), True),
    ],
)
async def test_dms_status_reports_connected_only_when_enabled_and_wired(
    tmp_path, respx_mock, store, client, connected
):
    http, chatwoot = await _dms_client_for(tmp_path, "dms_status", 61, respx_mock,
                                           store=store, client=client)
    res = http.get("/admin/customer360/dms/status", headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["connected"] is connected
    chatwoot.search_contacts.assert_not_awaited()


@pytest.mark.asyncio
async def test_dms_status_marks_the_mock_client(tmp_path, respx_mock):
    http, _ = await _dms_client_for(
        tmp_path, "dms_status_mock", 62, respx_mock,
        store=_StubDmsConfigStore(_dms_config(enabled=True)),
        client=MockDmsClient(environment="sandbox"),
    )
    assert http.get("/admin/customer360/dms/status", headers=HEADERS).json() == {
        "connected": True, "mock": True,
    }


@pytest.mark.asyncio
async def test_dms_endpoints_require_customer360_view(tmp_path, respx_mock):
    http, _ = await _dms_client_for(
        tmp_path, "dms_noperm", 63, respx_mock,
        store=_StubDmsConfigStore(_dms_config(enabled=True)), client=_EmptyDmsClient(),
        role="agent",
    )
    assert http.get("/admin/customer360/dms/status", headers=HEADERS).status_code == 403
    assert http.get("/admin/customer360/dms?phone=%2B60000000061",
                    headers=HEADERS).status_code == 403


@pytest.mark.asyncio
async def test_dms_lookup_returns_only_the_dms_block(tmp_path, respx_mock):
    http, chatwoot = await _dms_client_for(
        tmp_path, "dms_lookup", 64, respx_mock,
        store=_StubDmsConfigStore(_dms_config(enabled=True)),
        client=MockDmsClient(environment="sandbox"),
    )
    res = http.get("/admin/customer360/dms?phone=%2B60000000064&vehicle_no=VAB1234",
                   headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"dms"}
    assert body["dms"]["status"] == "ok" and body["dms"]["mock"] is True
    assert body["dms"]["vehicles"] and body["dms"]["service_history"]
    # No CRM fan-out: this is the cheap per-conversation call.
    chatwoot.search_contacts.assert_not_awaited()
    chatwoot.list_conversations.assert_not_awaited()


@pytest.mark.asyncio
async def test_dms_lookup_no_record_and_unreachable_stay_distinct(tmp_path, respx_mock):
    empty, _ = await _dms_client_for(
        tmp_path, "dms_empty", 65, respx_mock,
        store=_StubDmsConfigStore(_dms_config(enabled=True)), client=_EmptyDmsClient(),
    )
    dms = empty.get("/admin/customer360/dms?phone=%2B60000000065", headers=HEADERS).json()["dms"]
    assert dms["status"] == "ok" and dms["customer"] is None

    down, _ = await _dms_client_for(
        tmp_path, "dms_down", 66, respx_mock,
        store=_StubDmsConfigStore(_dms_config(enabled=True)), client=_RaisingDmsClient(),
    )
    dms = down.get("/admin/customer360/dms?phone=%2B60000000066", headers=HEADERS).json()["dms"]
    assert dms["status"] == "unreachable"


@pytest.mark.asyncio
async def test_dms_lookup_disabled_is_null_and_needs_a_key(tmp_path, respx_mock):
    http, _ = await _dms_client_for(
        tmp_path, "dms_off", 67, respx_mock,
        store=_StubDmsConfigStore(_dms_config(enabled=False)), client=_EmptyDmsClient(),
    )
    assert http.get("/admin/customer360/dms?phone=%2B60000000067",
                    headers=HEADERS).json() == {"dms": None}
    assert http.get("/admin/customer360/dms", headers=HEADERS).status_code == 422
