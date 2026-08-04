"""NullDmsClient must return nothing; MockDmsClient must return records
shaped as OUR types; probe() must classify every reachable/auth/timeout/
error outcome without ever leaking the credential into its message.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest
from structlog.testing import capture_logs

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
from chatbot.features.chat.dms_config_store import MAX_TIMEOUT_SECONDS, DmsConfig

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


async def test_probe_clamps_a_stored_timeout_above_the_ceiling() -> None:
    """`DmsConfigBody` rejects an out-of-range `timeout_seconds` on write,
    but that constraint post-dates the field: a document saved before it
    existed (or written by anything that bypasses the admin API) can still
    hold e.g. 600. `customer360_router.py` already clamps on its own read
    path for this reason; `probe()` is the OTHER path that reads
    `config.timeout_seconds` -- the admin "Test connection" button, which a
    human is directly waiting on -- and must clamp too, or a stale document
    hangs that button for up to ten minutes instead of ~30s.
    """
    seen_timeout: float | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_timeout
        seen_timeout = request.extensions["timeout"]["read"]
        return httpx.Response(200)

    unclamped_cfg = replace(CFG, timeout_seconds=600.0)
    await probe(unclamped_cfg, CREDENTIAL, _client(handler))

    assert seen_timeout == MAX_TIMEOUT_SECONDS


async def test_probe_timeout_message_reports_the_clamped_value() -> None:
    """The timeout error message must describe the timeout `probe()` actually
    used, not the raw (possibly unclamped) stored value -- otherwise a
    document holding 600 would report "did not respond within 600s" for a
    request that, per the clamp, could only ever have waited 30s.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    unclamped_cfg = replace(CFG, timeout_seconds=600.0)
    result = await probe(unclamped_cfg, CREDENTIAL, _client(handler))

    assert f"{MAX_TIMEOUT_SECONDS:g}s" in result.message
    assert "600" not in result.message


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


async def test_probe_maps_unsupported_protocol_to_unexpected_status() -> None:
    """Structurally covered by the `httpx.HTTPError` branch already
    (`httpx.UnsupportedProtocol` subclasses it), but pinned explicitly so a
    future refactor of that branch can't silently drop this case.
    """
    cfg = DmsConfig(
        enabled=True,
        provider_label="Proton DMS",
        base_url="dms.example.com/health",  # no scheme -> UnsupportedProtocol
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


async def test_probe_reports_a_redirect_as_an_unexpected_status() -> None:
    """`probe()` pins `follow_redirects=False` on its own request, so a 302
    is simply an unexpected status -- it is never chased. (This test formerly
    pinned the `httpx.TooManyRedirects` -> `unexpected_status` mapping;
    that exception is no longer reachable from `probe()` at all, and it was
    only ever handled by the generic `httpx.HTTPError` branch anyway.)
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://dms.example.com/health"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, max_redirects=1
    ) as client:
        result = await probe(CFG, CREDENTIAL, client)

    assert result.status == "unexpected_status"
    assert CREDENTIAL not in result.message
    assert len(requests) == 1


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


async def test_probe_never_logs_the_credential() -> None:
    """The brief's constraint is "any log", a stronger requirement than just
    ProbeResult.message -- a future edit adding `credential=credential` to
    the `_log.info("dms_probe_result", ...)` call would pass every other
    test here. Assert directly against captured structlog records.
    """

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(200)

    with capture_logs() as captured:
        await probe(CFG, CREDENTIAL, _client(handler))

    assert captured
    for record in captured:
        assert CREDENTIAL not in repr(record)


async def test_probe_message_has_no_credential_even_with_redirects_enabled() -> None:
    """The injected `httpx.AsyncClient` is constructed outside this module
    (Task 3 owns the real one); our sanitisation must not silently depend on
    it being built with `follow_redirects=False`. Simulate a 302 to a
    different host and confirm the message still never contains the
    credential or extra_header_value, regardless of what httpx itself does
    with the auth header across that redirect.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "dms.example.com":
            return httpx.Response(302, headers={"Location": "https://attacker.example.com/steal"})
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        result = await probe(CFG, CREDENTIAL, client)

    assert CREDENTIAL not in result.message
    assert CFG.extra_header_value not in result.message


@pytest.mark.parametrize(
    "auth_type", ["api_key_header", "bearer_token", "basic", "", "something_unknown"]
)
async def test_probe_never_sends_the_credential_to_a_redirect_target(auth_type: str) -> None:
    """The credential must never leave the host the operator configured.

    The test above only proved the credential is absent from the returned
    MESSAGE -- it never checked whether the credential was SENT to the
    redirect target, which is the actual attack. Verified against the
    installed httpx (0.28.1): `Client._redirect_headers` drops
    `Authorization` when the redirect crosses origins, but leaves custom
    headers alone -- so under `api_key_header` (the default fall-through)
    `X-Api-Key: <credential>` and any `extra_header_*` pair WOULD be replayed
    to the attacker's host if redirects were followed. `base_url` is
    operator-supplied and points at a third party, so this is reachable
    without compromising anything of ours.

    Parametrized across every `auth_type` because the leak's severity
    depends on which header the credential lands in, and the safe-looking
    `Authorization` cases must not be what makes this pass.
    """
    cfg = replace(CFG, auth_type=auth_type)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "dms.example.com":
            return httpx.Response(302, headers={"Location": "https://attacker.example.com/steal"})
        return httpx.Response(200)

    # follow_redirects=True at the CLIENT level: probe() must override it
    # per-request, not rely on the caller having configured it safely.
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        await probe(cfg, CREDENTIAL, client)

    assert [r.url.host for r in seen] == ["dms.example.com"], (
        "probe() followed the redirect; the second host is not ours to trust"
    )
    for request in seen:
        if request.url.host != "dms.example.com":
            joined = "\n".join(f"{k}: {v}" for k, v in request.headers.items())
            assert CREDENTIAL not in joined
            assert cfg.extra_header_value not in joined


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


async def test_extra_header_matching_the_auth_header_name_does_not_overwrite_the_credential() -> (
    None
):
    """If an operator sets extra_header_name to the same header carrying the
    credential (here, case-differently as "Authorization" vs "authorization"),
    the extra pair must not silently replace the credential's header value --
    that would send an unauthenticated request while looking, to the
    operator, like nothing changed.
    """
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    cfg = DmsConfig(
        enabled=True,
        provider_label="Proton DMS",
        base_url="https://dms.example.com/health",
        auth_type="bearer_token",
        extra_header_name="Authorization",
        extra_header_value="not-the-credential",
        timeout_seconds=5.0,
        retries=0,
    )
    await probe(cfg, CREDENTIAL, _client(handler))
    assert captured["authorization"] == f"Bearer {CREDENTIAL}"
