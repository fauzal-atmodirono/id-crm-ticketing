"""DMS/TSP integration shell: our own record types, the port other code
depends on, two implementations (a null default, a mock for demos), and a
connection probe for the admin "Test connection" button.

Phase 1 only -- there is no DMS API specification, so nothing here claims to
read a real vendor's data. `DmsCustomer`/`DmsVehicle`/`DmsServiceRecord` are
OUR shapes; a Phase 2 adapter's whole job is mapping one real vendor's
response onto these three types, so nothing else in the codebase (Customer
360, the UI) ever needs to learn the vendor's field names. `NullDmsClient` is
what every tenant runs today: integration disabled/unconfigured -> every
caller works exactly as it did before this package existed (`None`/`[]`,
never an error). `MockDmsClient` exists only for demos, is never wired in by
default, and every record it returns carries an explicit "(Demo data)"
marker in its own string fields -- there is no separate "is_mock" field on
our frozen record types, so the marker has to live in the data itself, or a
caller that renders records verbatim could mistake a demo for a live
integration (see the package design doc's demo-feedback item #26).

`probe()` is the "Test connection" button's backend: a single GET against the
operator-configured `base_url`. There is no separate health-path field on
`DmsConfig` and no documented health endpoint to guess at, so `base_url`
itself -- whatever the operator points it at -- is the only endpoint we have.
It never raises: DMS reachability is advisory, never a 500, and its
`ProbeResult.message` is sanitised -- built only from the base URL, the
provider label, and an HTTP status code, never the credential, a header
value, or raw exception text that could echo request internals.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

from chatbot.features.chat.dms_config_store import DmsConfig

_log = structlog.get_logger(__name__)

_HTTP_OK = 200
_HTTP_AUTH_FAILURE_STATUSES = (401, 403)


@dataclass(frozen=True)
class DmsCustomer:
    ref: str
    name: str
    phone: str | None


@dataclass(frozen=True)
class DmsVehicle:
    vehicle_no: str
    model: str
    purchased_from: str | None


@dataclass(frozen=True)
class DmsServiceRecord:
    date: str
    description: str
    dealer: str | None


class DmsClient(Protocol):
    """Narrow port: exactly what Customer 360 (Task 4) needs, nothing a
    hypothetical vendor's API shape would leak through. Every implementation
    must fail open -- a DMS outage degrades to "no data", never raises out to
    the caller.
    """

    async def find_customer(
        self, *, phone: str | None, vehicle_no: str | None
    ) -> DmsCustomer | None: ...

    async def list_vehicles(self, customer_ref: str) -> list[DmsVehicle]: ...

    async def list_service_history(self, vehicle_no: str) -> list[DmsServiceRecord]: ...


class NullDmsClient:
    """What every tenant runs with the integration disabled (or never
    configured). Every method returns the same "nothing here" value a caller
    would have gotten before this package existed -- no DMS block, no error.
    """

    async def find_customer(
        self,
        *,
        phone: str | None,  # noqa: ARG002
        vehicle_no: str | None,  # noqa: ARG002
    ) -> DmsCustomer | None:
        return None

    async def list_vehicles(self, customer_ref: str) -> list[DmsVehicle]:  # noqa: ARG002
        return []

    async def list_service_history(
        self,
        vehicle_no: str,  # noqa: ARG002
    ) -> list[DmsServiceRecord]:
        return []


class MockDmsClient:
    """Fixed, plausible-looking records for demos. Never wired in by any
    default config -- a caller has to deliberately construct this class.
    Every string field carries a "(Demo data)" marker so a UI rendering a
    record verbatim cannot present it as a live DMS response; our frozen
    record types have no separate "source"/"is_mock" field to flag this
    structurally, so the marker lives in the values themselves. Callers
    (e.g. the Customer 360 response) should still label the block as mock
    independently wherever it's assembled -- this is a second layer, not a
    substitute for that.
    """

    _CUSTOMER = DmsCustomer(
        ref="DEMO-CUST-001",
        name="Budi Santoso (Demo data)",
        phone="+628123456789",
    )
    _VEHICLES = (
        DmsVehicle(
            vehicle_no="B1234ABC",
            model="Proton X50 (Demo data)",
            purchased_from="Demo Dealer Jakarta",
        ),
    )
    _HISTORY = (
        DmsServiceRecord(
            date="2026-05-12",
            description="Scheduled service, 20,000 km (Demo data)",
            dealer="Demo Dealer Jakarta",
        ),
        DmsServiceRecord(
            date="2026-01-08",
            description="Brake pad replacement (Demo data)",
            dealer="Demo Dealer Bandung",
        ),
    )

    async def find_customer(
        self, *, phone: str | None, vehicle_no: str | None
    ) -> DmsCustomer | None:
        if not phone and not vehicle_no:
            return None
        return self._CUSTOMER

    async def list_vehicles(self, customer_ref: str) -> list[DmsVehicle]:  # noqa: ARG002
        return list(self._VEHICLES)

    async def list_service_history(
        self,
        vehicle_no: str,  # noqa: ARG002
    ) -> list[DmsServiceRecord]:
        return list(self._HISTORY)


@dataclass(frozen=True)
class ProbeResult:
    status: str
    message: str


def _auth_headers(config: DmsConfig, credential: str) -> dict[str, str]:
    """Build the request headers for one probe attempt.

    `extra_header_name`/`extra_header_value` is always sent, when both are
    set, as an ADDITIONAL header -- per the design doc, "for the
    tenant/partner id these APIs usually want" -- it never carries the
    credential. `auth_type` alone decides where `credential` goes:
      - "bearer_token"    -> ``Authorization: Bearer <credential>``
      - "basic"           -> ``Authorization: Basic <base64(credential)>``
      - "api_key_header", or anything else -> ``X-Api-Key: <credential>``,
        since `DmsConfig` has no field naming a custom API-key header (see
        this package's report for the design finding on this gap).
    """
    headers: dict[str, str] = {}
    if config.auth_type == "bearer_token":
        headers["Authorization"] = f"Bearer {credential}"
    elif config.auth_type == "basic":
        encoded = base64.b64encode(credential.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"
    else:
        headers["X-Api-Key"] = credential

    if config.extra_header_name and config.extra_header_value:
        headers[config.extra_header_name] = config.extra_header_value

    return headers


async def probe(config: DmsConfig, credential: str, client: httpx.AsyncClient) -> ProbeResult:
    """Issue one GET against `config.base_url` and classify the outcome.

    Never raises. `credential` is used only to build request headers; it is
    never interpolated into the returned message, logged, or allowed to
    surface via a propagated exception.
    """
    headers = _auth_headers(config, credential)
    label = config.provider_label or config.base_url

    try:
        response = await client.get(
            config.base_url, headers=headers, timeout=config.timeout_seconds
        )
    except httpx.TimeoutException:
        result = ProbeResult(
            status="timeout",
            message=f"{label} did not respond within {config.timeout_seconds:g}s.",
        )
    except httpx.HTTPError:
        result = ProbeResult(
            status="unexpected_status",
            message=f"Could not reach {label}: connection failed.",
        )
    except Exception:
        # Catches anything a malformed operator-supplied base_url/header can
        # raise that isn't an httpx.HTTPError -- e.g. httpx.InvalidURL, which
        # (unlike httpx.UnsupportedProtocol) does NOT subclass HTTPError.
        # A DMS outage/misconfiguration must degrade, never raise -- see the
        # package's fail-open invariant -- so this is the deliberate last
        # resort, not a swallowed bug.
        result = ProbeResult(
            status="unexpected_status",
            message=f"Could not reach {label}: request failed.",
        )
    else:
        if response.status_code == _HTTP_OK:
            result = ProbeResult(status="reachable", message=f"{label} is reachable (HTTP 200).")
        elif response.status_code in _HTTP_AUTH_FAILURE_STATUSES:
            result = ProbeResult(
                status="auth_failed",
                message=f"{label} rejected the credential (HTTP {response.status_code}).",
            )
        else:
            result = ProbeResult(
                status="unexpected_status",
                message=f"{label} responded with HTTP {response.status_code}.",
            )

    _log.info("dms_probe_result", status=result.status, base_url=config.base_url)
    return result
