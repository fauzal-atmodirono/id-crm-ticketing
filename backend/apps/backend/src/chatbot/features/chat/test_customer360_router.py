from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine as build_authz_engine
from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
from chatbot.features.authz.db import init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.features.chat.customer360_router import (
    _DMS_BUDGET_FLOOR_SECONDS,
    _build_dms_block,
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
from chatbot.platform.config import get_settings

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
        dms_client=MockDmsClient(),  # even wired, must never be consulted while disabled
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
        dms_client=MockDmsClient(),
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
        dms_client=MockDmsClient(),
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
        dms_client=MockDmsClient(),
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
        MockDmsClient(),
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
