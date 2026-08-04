"""NullDmsClient must return nothing; MockDmsClient must return records
shaped as OUR types; probe() must classify every reachable/auth/timeout/
error outcome without ever leaking the credential into its message.
"""

from __future__ import annotations

import base64
from collections.abc import Callable

import httpx
import pytest

from chatbot.features.chat.dms_client import (
    DmsClient,
    DmsCustomer,
    DmsServiceRecord,
    DmsVehicle,
    MockDmsClient,
    NullDmsClient,
    ProbeResult,
    probe,
)
from chatbot.features.chat.dms_config_store import DmsConfig

CREDENTIAL = "super-secret-token-xyz"

CFG = DmsConfig(
    enabled=True,
    provider_label="Proton DMS",
    base_url="https://dms.example.com/health",
    auth_type="api_key_header",
    extra_header_name="X-Tenant",
    extra_header_value="proton",
    timeout_seconds=5.0,
    retries=2,
)


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- NullDmsClient -----------------------------------------------------


async def test_null_client_find_customer_returns_none() -> None:
    client: DmsClient = NullDmsClient()
    assert await client.find_customer(phone="0123456789", vehicle_no=None) is None


async def test_null_client_list_vehicles_returns_empty_list() -> None:
    client: DmsClient = NullDmsClient()
    assert await client.list_vehicles("any-ref") == []


async def test_null_client_list_service_history_returns_empty_list() -> None:
    client: DmsClient = NullDmsClient()
    assert await client.list_service_history("B1234ABC") == []


# --- MockDmsClient -------------------------------------------------------


async def test_mock_client_find_customer_returns_our_dataclass_shape() -> None:
    client: DmsClient = MockDmsClient()
    customer = await client.find_customer(phone="0123456789", vehicle_no=None)
    assert isinstance(customer, DmsCustomer)
    assert customer.ref and customer.name and customer.phone
    # our field names only -- nothing vendor-shaped leaks through
    assert {"ref", "name", "phone"} == set(customer.__dataclass_fields__)


async def test_mock_client_find_customer_returns_none_with_no_identifiers() -> None:
    client: DmsClient = MockDmsClient()
    assert await client.find_customer(phone=None, vehicle_no=None) is None


async def test_mock_client_list_vehicles_returns_our_dataclass_shape() -> None:
    client: DmsClient = MockDmsClient()
    vehicles = await client.list_vehicles("DEMO-CUST-001")
    assert vehicles
    assert all(isinstance(v, DmsVehicle) for v in vehicles)
    assert {"vehicle_no", "model", "purchased_from"} == set(vehicles[0].__dataclass_fields__)


async def test_mock_client_list_service_history_returns_our_dataclass_shape() -> None:
    client: DmsClient = MockDmsClient()
    records = await client.list_service_history("B1234ABC")
    assert records
    assert all(isinstance(r, DmsServiceRecord) for r in records)
    assert {"date", "description", "dealer"} == set(records[0].__dataclass_fields__)


async def test_mock_client_records_are_visibly_marked_as_demo_data() -> None:
    """A shell mistaken for a live integration is the failure this package
    must avoid -- every record must be unmistakably fake at a glance.
    """
    client = MockDmsClient()
    customer = await client.find_customer(phone="0123456789", vehicle_no=None)
    vehicles = await client.list_vehicles("DEMO-CUST-001")
    history = await client.list_service_history("B1234ABC")

    assert customer is not None
    assert "Demo" in customer.name
    assert all("Demo" in v.model for v in vehicles)
    assert all("Demo" in r.description for r in history)


# --- probe() status mapping ------------------------------------------------


async def test_probe_maps_200_to_reachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200, json={"ok": True})

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert result.status == "reachable"


@pytest.mark.parametrize("status_code", [401, 403])
async def test_probe_maps_401_and_403_to_auth_failed(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(status_code)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert result.status == "auth_failed"


async def test_probe_maps_timeout_to_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert result.status == "timeout"


async def test_probe_maps_500_to_unexpected_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert result.status == "unexpected_status"


async def test_probe_maps_404_to_unexpected_status_not_auth_failed() -> None:
    """A wrong-URL 404 must not be reported as an auth failure -- an
    operator chasing an auth problem on a merely-mistyped base_url would be
    looking in the wrong place entirely.
    """

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(404)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert result.status == "unexpected_status"
    assert result.status != "auth_failed"


async def test_probe_maps_connection_error_to_unexpected_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert result.status == "unexpected_status"


async def test_probe_never_raises_on_a_malformed_base_url() -> None:
    """`httpx.InvalidURL` (e.g. from an unmatched IPv6 bracket) does NOT
    subclass `httpx.HTTPError`, unlike most httpx request errors -- a naive
    `except httpx.HTTPError` alone would let this one propagate out of
    probe(), breaking the "never raises" invariant. This never reaches the
    handler at all (URL parsing fails first), so no transport is needed.
    """
    cfg = DmsConfig(
        enabled=True,
        provider_label="Proton DMS",
        base_url="https://[::1",
        auth_type="api_key_header",
        extra_header_name="",
        extra_header_value="",
        timeout_seconds=5.0,
        retries=0,
    )
    async with httpx.AsyncClient() as client:
        result = await probe(cfg, CREDENTIAL, client)

    assert result.status == "unexpected_status"
    assert CREDENTIAL not in result.message


# --- probe() message sanitisation ------------------------------------------


@pytest.mark.parametrize("status_code", [200, 401, 500, 404])
async def test_probe_message_never_contains_the_credential(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(status_code)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert CREDENTIAL not in result.message


async def test_probe_timeout_message_never_contains_the_credential() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert CREDENTIAL not in result.message


async def test_probe_extra_header_value_never_leaks_into_the_message() -> None:
    """extra_header_value is a second value the operator supplies; it must
    not end up in the probe message either, even though it is not treated
    as secret by DmsConfig/public_dict.
    """

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(500)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert CFG.extra_header_value not in result.message


async def test_probe_result_is_a_probe_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200)

    result = await probe(CFG, CREDENTIAL, _client(handler))
    assert isinstance(result, ProbeResult)
    assert result.status in {"reachable", "auth_failed", "timeout", "unexpected_status"}


# --- probe() header construction (auth_type mapping) -----------------------


async def test_probe_sends_bearer_token_when_configured() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    cfg = DmsConfig(
        enabled=True,
        provider_label="Proton DMS",
        base_url="https://dms.example.com/health",
        auth_type="bearer_token",
        extra_header_name="",
        extra_header_value="",
        timeout_seconds=5.0,
        retries=0,
    )
    await probe(cfg, CREDENTIAL, _client(handler))
    assert captured["authorization"] == f"Bearer {CREDENTIAL}"


async def test_probe_sends_basic_auth_when_configured() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    cfg = DmsConfig(
        enabled=True,
        provider_label="Proton DMS",
        base_url="https://dms.example.com/health",
        auth_type="basic",
        extra_header_name="",
        extra_header_value="",
        timeout_seconds=5.0,
        retries=0,
    )
    await probe(cfg, CREDENTIAL, _client(handler))
    expected = base64.b64encode(CREDENTIAL.encode("utf-8")).decode("ascii")
    assert captured["authorization"] == f"Basic {expected}"


async def test_probe_sends_api_key_header_by_default() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    cfg = DmsConfig(
        enabled=True,
        provider_label="Proton DMS",
        base_url="https://dms.example.com/health",
        auth_type="api_key_header",
        extra_header_name="",
        extra_header_value="",
        timeout_seconds=5.0,
        retries=0,
    )
    await probe(cfg, CREDENTIAL, _client(handler))
    assert captured["x-api-key"] == CREDENTIAL


async def test_probe_sends_the_extra_header_pair_when_set() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    await probe(CFG, CREDENTIAL, _client(handler))
    assert captured["x-tenant"] == "proton"
