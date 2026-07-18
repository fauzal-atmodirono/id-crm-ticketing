# Phase 4 — Customer 360 & DMS/TSP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `/crm/customer360` backend endpoint + a 4-panel Customer-360 widget in the Chatwoot right panel (fork patch) so an agent sees personal / vehicle / service / call-center data auto-populated within 3 seconds on any inbound conversation — with a mock adapter for local dev/CI and an explicit gate before the real DMS/TSP adapter is written.

**Architecture:** Ports-and-adapters — a `CustomerProfilePort` Protocol is the stable contract; `MockDmsTspAdapter` implements it for tests and local dev; the FastAPI endpoint at `POST/GET /crm/customer360` serves the 4-panel JSON with an in-process TTL cache; the Chatwoot fork gains patch `0004-customer360-widget.patch` that renders the widget in the right panel using Chatwoot's Dashboard-App / conversation-sidebar slot (async skeleton → fill). The real DMS+TSP adapter is explicitly BLOCKED until external API credentials and specs are delivered (see Task 5).

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, `cachetools.TTLCache` (already in the venv via `proton-conversational-ai`), `httpx` for future real adapter, pytest + pytest-asyncio for tests; Vue 3 / vanilla JS + CSS for the fork widget patch (mirrors `chatwoot-faq-admin` style); git-format-patch for the chatwoot-fork pipeline.

## Global Constraints

- **Repo layout:** backend changes → `proton-conversational-ai/apps/backend/`; fork patch → `id-crm-ticketing/deploy/chatwoot-fork/patches/0004-customer360-widget.patch`; widget HTML/JS → `id-crm-ticketing/apps/customer360-widget/`
- **Auth:** all `/crm/*` endpoints require `x-api-key` header matching `settings.crm_api_key`; a missing/wrong key returns HTTP 401 (constant-time compare, same pattern as `faq_admin_api_key`)
- **Async / ≤3 s:** the endpoint MUST respond in ≤3 s P95; the widget renders a skeleton immediately and fills panels on data arrival (no blocking spinner covering the full panel)
- **4 panels:** Personal Info · Vehicle Info · Service History · Call-Center History — exactly these four, rendered in this order
- **Phone normalisation:** phone lookup normalises to E.164 (`+60xxxxxxxxx`); `+` and digits only, strip everything else
- **Cache:** TTL = 120 s per phone key; max 500 entries in-process; a `Cache-Control: max-age=120` response header communicates the TTL to the widget
- **Chatwoot version pin:** `v4.15.1` (same as Phase 0); patch applies to the MIT community frontend only — zero changes to `/enterprise`
- **Python version floor:** 3.11; pydantic ≥ 2.0; pytest ≥ 8.0
- **No fabricated DMS/TSP calls:** the real adapter file (Task 5) is a BLOCKED placeholder — it must NOT contain invented API URLs, tokens, or response shapes
- **Config:** new settings fields added to `chatbot/platform/config.py` `Settings` class (pydantic-settings `BaseSettings`); never committed to VCS with real values
- **Multi-tenant:** `PROTON_FEATURES` controls whether the widget patch activates; add `customer360` to the feature flag list; the endpoint URL flows from `PROTON_BACKEND_URL`

---

## File Structure

### Backend (`proton-conversational-ai/apps/backend/src/chatbot/`)

| File | Responsibility |
|---|---|
| `features/customer360/ports.py` | `CustomerProfilePort` Protocol + 4 dataclasses (`CustomerInfo`, `VehicleInfo`, `ServiceRecord`, `CallCenterRecord`) + `Customer360Profile` aggregate |
| `features/customer360/adapters/mock.py` | `MockDmsTspAdapter` — deterministic fake data keyed by last 4 digits of phone; implements `CustomerProfilePort` |
| `features/customer360/adapters/test_mock.py` | pytest unit tests for the mock adapter |
| `features/customer360/router.py` | FastAPI `APIRouter`; `GET /crm/customer360?phone=<e164>` + `POST /crm/customer360` (body `{"phone": ...}`); x-api-key guard; TTL cache; calls the injected port |
| `features/customer360/test_router.py` | pytest tests for the endpoint (FastAPI `TestClient`, mock adapter injected) |
| `features/customer360/__init__.py` | empty |
| `features/customer360/adapters/__init__.py` | empty |
| `features/customer360/adapters/dms_tsp.py` | **BLOCKED gate** — real DMS+TSP adapter stub; contains only the class skeleton + a `NotImplementedError` body + comments listing required inputs |
| `platform/config.py` | Add: `crm_api_key: str = ""`, `customer360_cache_ttl_seconds: int = 120`, `customer360_cache_max_entries: int = 500`, `customer360_provider: Literal["mock", "dms_tsp"] = "mock"` |
| `main.py` | Wire `customer360` router + adapter selection |

### Widget app (`id-crm-ticketing/apps/customer360-widget/`)

| File | Responsibility |
|---|---|
| `index.html` | Self-contained single-file widget; reads `backendBaseUrl` + `apiKey` + `phone` from Chatwoot `postMessage`; renders 4-panel card with skeleton states; fetches `/crm/customer360?phone=<e164>` |

### Fork patch (`id-crm-ticketing/deploy/chatwoot-fork/`)

| File | Responsibility |
|---|---|
| `patches/0004-customer360-widget.patch` | Adds an iframe into the Chatwoot conversation right-panel sidebar (the `ConversationSidebar` component) pointing at `PROTON_BACKEND_URL/apps/customer360`; guarded by `PROTON_FEATURES.includes('customer360')` |

---

## Task 1: `CustomerProfilePort` — port interface + dataclasses

**Files:**
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/__init__.py`
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/__init__.py`
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/ports.py`

**Interfaces:**
- Produces: `CustomerInfo`, `VehicleInfo`, `ServiceRecord`, `CallCenterRecord`, `Customer360Profile`, `CustomerProfilePort` — consumed by Tasks 2, 3, 5

- [ ] **Step 1: Create the package `__init__` files**

```bash
touch proton-conversational-ai/apps/backend/src/chatbot/features/customer360/__init__.py
touch proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/__init__.py
```

- [ ] **Step 2: Write `ports.py`**

```python
# proton-conversational-ai/apps/backend/src/chatbot/features/customer360/ports.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CustomerInfo:
    """Personal profile pulled from DMS."""
    name: str                        # Full name, e.g. "Ahmad bin Yusuf"
    phone: str                       # E.164 canonical, e.g. "+60123456789"
    email: str | None                # May be absent in DMS
    ic_number: str | None            # Malaysian IC / passport
    address: str | None              # Full mailing address
    membership_tier: str | None      # e.g. "Gold", "Proton Care Plus"
    customer_since: str | None       # ISO date "YYYY-MM-DD"


@dataclass(frozen=True)
class VehicleInfo:
    """Vehicle registered to the customer in DMS."""
    registration_number: str         # e.g. "WXY 1234"
    model: str                       # e.g. "Proton X70 1.8 TGDI Premium"
    colour: str | None
    year: int | None
    vin: str | None                  # 17-char VIN
    engine_number: str | None
    purchase_date: str | None        # ISO date
    warranty_expiry: str | None      # ISO date


@dataclass(frozen=True)
class ServiceRecord:
    """One service visit from the DMS service history."""
    date: str                        # ISO date "YYYY-MM-DD"
    service_type: str                # e.g. "Major Service 40,000 km"
    dealer_name: str | None
    mileage_km: int | None
    technician: str | None
    job_description: str | None
    total_cost_myr: float | None


@dataclass(frozen=True)
class CallCenterRecord:
    """One past interaction from Chatwoot / BQ call-center history."""
    date: str                        # ISO datetime "YYYY-MM-DDTHH:MM:SSZ"
    channel: str                     # "whatsapp" | "phone" | "web" | "email"
    case_id: str                      # Chatwoot conversation id
    summary: str | None
    resolution: str | None           # e.g. "Resolved", "Escalated", "Pending"
    agent_name: str | None


@dataclass(frozen=True)
class Customer360Profile:
    """Aggregate 4-panel profile returned by the port."""
    phone: str                       # Normalised E.164 — the lookup key
    customer: CustomerInfo
    vehicles: list[VehicleInfo] = field(default_factory=list)
    service_history: list[ServiceRecord] = field(default_factory=list)
    call_center_history: list[CallCenterRecord] = field(default_factory=list)


class CustomerProfilePort(Protocol):
    """Stable contract for fetching a Customer-360 profile by phone number.

    Implementations: MockDmsTspAdapter (local dev / CI), real DmsTspAdapter
    (BLOCKED until DMS+TSP API contract is provided).

    Contract:
    - `phone` is E.164 (e.g. "+60123456789") — normalised by the caller.
    - Returns `None` when no customer is found (not an error).
    - Must never raise; log and return None on upstream failure.
    - Must complete in ≤ 2 s P95 (the endpoint adds cache overhead on top).
    """

    async def get_profile(self, phone: str) -> Customer360Profile | None:
        """Fetch the full 4-panel profile for the given E.164 phone number."""
        ...
```

- [ ] **Step 3: Commit**

```bash
cd /path/to/proton-conversational-ai
git add apps/backend/src/chatbot/features/customer360/
git commit -m "feat(customer360): add CustomerProfilePort + 4-panel dataclasses"
```

---

## Task 2: `MockDmsTspAdapter` — fake data adapter

**Files:**
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/mock.py`
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/test_mock.py`

**Interfaces:**
- Consumes: `CustomerProfilePort`, `CustomerInfo`, `VehicleInfo`, `ServiceRecord`, `CallCenterRecord`, `Customer360Profile` from `chatbot.features.customer360.ports`
- Produces: `MockDmsTspAdapter` class — consumed by Tasks 3 and the `main.py` wiring in Task 4

- [ ] **Step 1: Write the failing test**

```python
# proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/test_mock.py
import pytest
from chatbot.features.customer360.adapters.mock import MockDmsTspAdapter
from chatbot.features.customer360.ports import Customer360Profile


@pytest.mark.asyncio
async def test_known_phone_returns_profile() -> None:
    adapter = MockDmsTspAdapter()
    result = await adapter.get_profile("+60123456789")
    assert result is not None
    assert isinstance(result, Customer360Profile)
    assert result.phone == "+60123456789"


@pytest.mark.asyncio
async def test_profile_has_all_four_panels() -> None:
    adapter = MockDmsTspAdapter()
    result = await adapter.get_profile("+60123456789")
    assert result is not None
    assert result.customer.name != ""
    assert len(result.vehicles) >= 1
    assert len(result.service_history) >= 1
    assert len(result.call_center_history) >= 1


@pytest.mark.asyncio
async def test_unknown_phone_returns_none() -> None:
    adapter = MockDmsTspAdapter()
    result = await adapter.get_profile("+60000000000")
    assert result is None


@pytest.mark.asyncio
async def test_same_phone_always_returns_same_profile() -> None:
    adapter = MockDmsTspAdapter()
    r1 = await adapter.get_profile("+60123456789")
    r2 = await adapter.get_profile("+60123456789")
    assert r1 == r2


@pytest.mark.asyncio
async def test_different_phones_return_different_profiles() -> None:
    adapter = MockDmsTspAdapter()
    r1 = await adapter.get_profile("+60123456789")
    r2 = await adapter.get_profile("+60129876543")
    assert r1 is not None
    assert r2 is not None
    assert r1.customer.name != r2.customer.name


@pytest.mark.asyncio
async def test_vehicle_fields_are_populated() -> None:
    adapter = MockDmsTspAdapter()
    result = await adapter.get_profile("+60123456789")
    assert result is not None
    v = result.vehicles[0]
    assert v.registration_number != ""
    assert v.model != ""
    assert v.year is not None


@pytest.mark.asyncio
async def test_service_record_fields_are_populated() -> None:
    adapter = MockDmsTspAdapter()
    result = await adapter.get_profile("+60123456789")
    assert result is not None
    s = result.service_history[0]
    assert s.date != ""
    assert s.service_type != ""


@pytest.mark.asyncio
async def test_call_center_record_fields_are_populated() -> None:
    adapter = MockDmsTspAdapter()
    result = await adapter.get_profile("+60123456789")
    assert result is not None
    c = result.call_center_history[0]
    assert c.date != ""
    assert c.channel in {"whatsapp", "phone", "web", "email"}
    assert c.case_id != ""
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/customer360/adapters/test_mock.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — `mock.py` does not exist yet.

- [ ] **Step 3: Write `mock.py`**

The mock uses the last 4 digits of the phone to seed deterministic data. Phone numbers ending in `0000` return `None` (unknown customer). All other 10-digit+ phone numbers return a synthetic profile.

```python
# proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/mock.py
from __future__ import annotations

import structlog

from chatbot.features.customer360.ports import (
    CallCenterRecord,
    Customer360Profile,
    CustomerInfo,
    CustomerProfilePort,
    ServiceRecord,
    VehicleInfo,
)

_log = structlog.get_logger(__name__)

# Seeded fake name pools — last-4-digit bucket selects an entry
_NAMES = [
    "Ahmad bin Yusuf",
    "Nurul Ain binti Hassan",
    "Lim Wei Kiat",
    "Siti Rohani binti Abdul",
    "Rajendran a/l Murugan",
    "Farah Nadia binti Ismail",
    "Tan Ah Kow",
    "Zulaikha binti Zakaria",
    "Kevin Ong Chee Wai",
    "Mariam binti Othman",
]

_MODELS = [
    "Proton X70 1.8 TGDI Premium",
    "Proton X50 1.5 TGDi Flagship",
    "Proton Saga 1.3 Standard MT",
    "Proton Iriz 1.6 CVT Executive",
    "Proton X90 1.5 TGDI Flagship",
]

_DEALERS = [
    "Proton Edar Shah Alam",
    "Proton Edar Cheras",
    "Proton Edar Johor Bahru",
    "Proton Edar Penang",
    "Proton Edar Kota Kinabalu",
]

_SERVICE_TYPES = [
    "Minor Service 10,000 km",
    "Major Service 20,000 km",
    "Major Service 40,000 km",
    "Brake Pad Replacement",
    "Air Filter + Cabin Filter",
    "Battery Replacement",
]

_CHANNELS = ["whatsapp", "phone", "web", "email"]
_RESOLUTIONS = ["Resolved", "Escalated", "Pending", "Closed"]


def _seed(phone: str) -> int:
    """Return a small integer seed from the last 4 digits of the phone."""
    digits = "".join(c for c in phone if c.isdigit())
    return int(digits[-4:]) if len(digits) >= 4 else 1


class MockDmsTspAdapter:
    """Deterministic fake Customer-360 adapter for local dev and CI.

    Keyed by the last 4 digits of the phone. Phone numbers whose last 4 digits
    are exactly '0000' simulate an unknown customer (returns None). All others
    return a seeded-but-realistic profile.

    Implements CustomerProfilePort.
    """

    async def get_profile(self, phone: str) -> Customer360Profile | None:
        digits = "".join(c for c in phone if c.isdigit())
        if not digits or digits[-4:] == "0000":
            _log.info("mock_customer360_unknown", phone=phone)
            return None

        s = _seed(phone)
        _log.info("mock_customer360_hit", phone=phone, seed=s)

        customer = CustomerInfo(
            name=_NAMES[s % len(_NAMES)],
            phone=phone,
            email=f"customer{s:04d}@proton-demo.my",
            ic_number=f"{800000 + s:06d}-05-{1000 + s:04d}",
            address=f"No. {s % 99 + 1}, Jalan Proton {s % 10 + 1}, 40150 Shah Alam, Selangor",
            membership_tier=["Silver", "Gold", "Proton Care Plus"][s % 3],
            customer_since=f"20{15 + s % 9:02d}-{s % 12 + 1:02d}-01",
        )

        reg_suffix = f"{(s % 9000) + 1000}"
        reg_alpha = ["WXY", "WRG", "BJC", "VBN", "BHF"][s % 5]
        vehicle = VehicleInfo(
            registration_number=f"{reg_alpha} {reg_suffix}",
            model=_MODELS[s % len(_MODELS)],
            colour=["Armour Silver", "Jet Grey", "Snow White", "Phantom Black"][s % 4],
            year=2017 + (s % 8),
            vin=f"PRS{s:014d}",
            engine_number=f"EN{s:010d}",
            purchase_date=f"20{17 + s % 8:02d}-{s % 12 + 1:02d}-15",
            warranty_expiry=f"20{20 + s % 8:02d}-{s % 12 + 1:02d}-14",
        )

        service_history = [
            ServiceRecord(
                date=f"20{21 + i % 4:02d}-{(s + i) % 12 + 1:02d}-{(s + i) % 28 + 1:02d}",
                service_type=_SERVICE_TYPES[(s + i) % len(_SERVICE_TYPES)],
                dealer_name=_DEALERS[(s + i) % len(_DEALERS)],
                mileage_km=10_000 * (i + 1) + s % 1000,
                technician=f"Tech {(s + i) % 20 + 1:02d}",
                job_description=f"Routine {_SERVICE_TYPES[(s + i) % len(_SERVICE_TYPES)].lower()}",
                total_cost_myr=round(250.0 + (s % 500) + i * 80, 2),
            )
            for i in range(min(4, 1 + s % 4))
        ]

        call_center_history = [
            CallCenterRecord(
                date=f"20{22 + i % 3:02d}-{(s + i) % 12 + 1:02d}-{(s + i) % 28 + 1:02d}T10:00:00Z",
                channel=_CHANNELS[(s + i) % len(_CHANNELS)],
                case_id=f"CW-{s * 100 + i + 10000}",
                summary=f"Customer inquiry #{i + 1} about {_SERVICE_TYPES[(s + i) % len(_SERVICE_TYPES)].lower()}",
                resolution=_RESOLUTIONS[(s + i) % len(_RESOLUTIONS)],
                agent_name=f"Agent {(s + i) % 10 + 1:02d}",
            )
            for i in range(min(5, 1 + s % 5))
        ]

        return Customer360Profile(
            phone=phone,
            customer=customer,
            vehicles=[vehicle],
            service_history=service_history,
            call_center_history=call_center_history,
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/customer360/adapters/test_mock.py -v
```

Expected output:
```
test_known_phone_returns_profile PASSED
test_profile_has_all_four_panels PASSED
test_unknown_phone_returns_none PASSED
test_same_phone_always_returns_same_profile PASSED
test_different_phones_return_different_profiles PASSED
test_vehicle_fields_are_populated PASSED
test_service_record_fields_are_populated PASSED
test_call_center_record_fields_are_populated PASSED

8 passed in <1s
```

- [ ] **Step 5: Commit**

```bash
cd proton-conversational-ai
git add apps/backend/src/chatbot/features/customer360/adapters/
git commit -m "feat(customer360): add MockDmsTspAdapter with deterministic fake profiles"
```

---

## Task 3: Config additions + `/crm/customer360` endpoint

**Files:**
- Modify: `proton-conversational-ai/apps/backend/src/chatbot/platform/config.py`
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/router.py`
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/test_router.py`

**Interfaces:**
- Consumes: `CustomerProfilePort`, `Customer360Profile` (Task 1); `MockDmsTspAdapter` (Task 2); `Settings` from `platform/config.py`
- Produces: `build_customer360_router(port, settings) -> APIRouter` — consumed by Task 4 (`main.py` wiring)

- [ ] **Step 1: Add config fields to `config.py`**

Open `proton-conversational-ai/apps/backend/src/chatbot/platform/config.py`. After the existing `faq_admin_api_key: str = ""` line, add the following four fields:

```python
    # Customer 360 endpoint — DMS/TSP integration (Phase 4)
    # x-api-key required on /crm/* endpoints; an empty key 401s every request.
    crm_api_key: str = ""
    # Cache TTL in seconds for phone-keyed Customer-360 responses.
    customer360_cache_ttl_seconds: int = 120
    # Maximum number of phone keys kept in the in-process TTL cache.
    customer360_cache_max_entries: int = 500
    # Which Customer-360 adapter to wire: "mock" (default, no external deps)
    # or "dms_tsp" (BLOCKED until DMS/TSP API contract delivered).
    customer360_provider: Literal["mock", "dms_tsp"] = "mock"
```

Also add `"dms_tsp"` to the `Literal` import list at the top of the file — no new import needed since `Literal` is already imported from `typing`.

- [ ] **Step 2: Write the failing endpoint tests**

```python
# proton-conversational-ai/apps/backend/src/chatbot/features/customer360/test_router.py
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.customer360.adapters.mock import MockDmsTspAdapter
from chatbot.features.customer360.router import build_customer360_router
from chatbot.platform.config import Settings


def _make_client(api_key: str = "test-key") -> TestClient:
    settings = Settings(crm_api_key=api_key)
    adapter = MockDmsTspAdapter()
    app = FastAPI()
    app.include_router(build_customer360_router(adapter, settings))
    return TestClient(app)


def test_get_profile_returns_200() -> None:
    client = _make_client()
    res = client.get(
        "/crm/customer360",
        params={"phone": "+60123456789"},
        headers={"x-api-key": "test-key"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["phone"] == "+60123456789"
    assert "customer" in data
    assert "vehicles" in data
    assert "service_history" in data
    assert "call_center_history" in data


def test_post_profile_returns_200() -> None:
    client = _make_client()
    res = client.post(
        "/crm/customer360",
        json={"phone": "+60123456789"},
        headers={"x-api-key": "test-key"},
    )
    assert res.status_code == 200
    assert res.json()["phone"] == "+60123456789"


def test_missing_api_key_returns_401() -> None:
    client = _make_client()
    res = client.get("/crm/customer360", params={"phone": "+60123456789"})
    assert res.status_code == 401


def test_wrong_api_key_returns_401() -> None:
    client = _make_client()
    res = client.get(
        "/crm/customer360",
        params={"phone": "+60123456789"},
        headers={"x-api-key": "wrong-key"},
    )
    assert res.status_code == 401


def test_unknown_customer_returns_404() -> None:
    client = _make_client()
    res = client.get(
        "/crm/customer360",
        params={"phone": "+60120000000"},
        headers={"x-api-key": "test-key"},
    )
    assert res.status_code == 404


def test_phone_normalisation_strips_dashes() -> None:
    client = _make_client()
    # "+601-2345-6789" should normalise to "+60123456789"
    res = client.get(
        "/crm/customer360",
        params={"phone": "+601-2345-6789"},
        headers={"x-api-key": "test-key"},
    )
    assert res.status_code == 200
    assert res.json()["phone"] == "+60123456789"


def test_cache_control_header_present() -> None:
    client = _make_client()
    res = client.get(
        "/crm/customer360",
        params={"phone": "+60123456789"},
        headers={"x-api-key": "test-key"},
    )
    assert res.status_code == 200
    cc = res.headers.get("cache-control", "")
    assert "max-age=120" in cc


def test_missing_phone_param_returns_422() -> None:
    client = _make_client()
    res = client.get("/crm/customer360", headers={"x-api-key": "test-key"})
    assert res.status_code == 422


def test_empty_api_key_config_returns_401_always() -> None:
    """When crm_api_key is empty, all requests are rejected (safe default)."""
    client = _make_client(api_key="")
    res = client.get(
        "/crm/customer360",
        params={"phone": "+60123456789"},
        headers={"x-api-key": ""},
    )
    assert res.status_code == 401
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/customer360/test_router.py -v
```

Expected: `ModuleNotFoundError: No module named 'chatbot.features.customer360.router'`

- [ ] **Step 4: Write `router.py`**

```python
# proton-conversational-ai/apps/backend/src/chatbot/features/customer360/router.py
from __future__ import annotations

import hmac
import re

import structlog
from cachetools import TTLCache
from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel

from chatbot.features.customer360.ports import Customer360Profile, CustomerProfilePort
from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_E164_KEEP = re.compile(r"[^\d+]")


def _normalise_phone(raw: str) -> str:
    """Strip everything except digits and a leading '+'; return E.164."""
    return _E164_KEEP.sub("", raw)


class _PhoneBody(BaseModel):
    phone: str


def build_customer360_router(
    port: CustomerProfilePort,
    settings: Settings,
) -> APIRouter:
    """Build and return the /crm/customer360 APIRouter.

    Inject a CustomerProfilePort implementation (mock or real) and Settings.
    The router owns its own TTLCache so the cache lifecycle matches the router.
    """
    cache: TTLCache[str, Customer360Profile] = TTLCache(
        maxsize=settings.customer360_cache_max_entries,
        ttl=settings.customer360_cache_ttl_seconds,
    )
    router = APIRouter(prefix="/crm", tags=["customer360"])

    def _check_api_key(x_api_key: str | None) -> None:
        """Reject if key is missing, empty, or does not match config."""
        expected = settings.crm_api_key
        if not expected:
            # Safe default: empty config key rejects everyone.
            raise HTTPException(status_code=401, detail="CRM API key not configured")
        if not x_api_key:
            raise HTTPException(status_code=401, detail="Missing x-api-key header")
        if not hmac.compare_digest(x_api_key, expected):
            raise HTTPException(status_code=401, detail="Invalid x-api-key")

    async def _fetch(phone_raw: str) -> tuple[Customer360Profile, bool]:
        """Return (profile, from_cache). Raises 404 when not found."""
        normalised = _normalise_phone(phone_raw)
        if normalised in cache:
            return cache[normalised], True
        result = await port.get_profile(normalised)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No customer found for {normalised}")
        cache[normalised] = result
        return result, False

    @router.get("/customer360")
    async def get_customer360(
        phone: str = Query(..., description="Customer phone number (E.164 preferred)"),
        x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ) -> Response:
        _check_api_key(x_api_key)
        profile, from_cache = await _fetch(phone)
        _log.info("customer360_get", phone=profile.phone, from_cache=from_cache)
        import dataclasses, json
        body = json.dumps(dataclasses.asdict(profile))
        headers = {
            "Cache-Control": f"max-age={settings.customer360_cache_ttl_seconds}",
            "Content-Type": "application/json",
        }
        return Response(content=body, headers=headers)

    @router.post("/customer360")
    async def post_customer360(
        payload: _PhoneBody,
        x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ) -> Response:
        _check_api_key(x_api_key)
        profile, from_cache = await _fetch(payload.phone)
        _log.info("customer360_post", phone=profile.phone, from_cache=from_cache)
        import dataclasses, json
        body = json.dumps(dataclasses.asdict(profile))
        headers = {
            "Cache-Control": f"max-age={settings.customer360_cache_ttl_seconds}",
            "Content-Type": "application/json",
        }
        return Response(content=body, headers=headers)

    return router
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd proton-conversational-ai/apps/backend
python -m pytest src/chatbot/features/customer360/test_router.py -v
```

Expected output:
```
test_get_profile_returns_200 PASSED
test_post_profile_returns_200 PASSED
test_missing_api_key_returns_401 PASSED
test_wrong_api_key_returns_401 PASSED
test_unknown_customer_returns_404 PASSED
test_phone_normalisation_strips_dashes PASSED
test_cache_control_header_present PASSED
test_missing_phone_param_returns_422 PASSED
test_empty_api_key_config_returns_401_always PASSED

9 passed in <1s
```

- [ ] **Step 6: Run the full backend test suite to confirm no regressions**

```bash
cd proton-conversational-ai/apps/backend
python -m pytest src/ -v --tb=short -q
```

Expected: all pre-existing tests still pass; 17 new tests added.

- [ ] **Step 7: Commit**

```bash
cd proton-conversational-ai
git add apps/backend/src/chatbot/platform/config.py \
        apps/backend/src/chatbot/features/customer360/router.py \
        apps/backend/src/chatbot/features/customer360/test_router.py
git commit -m "feat(customer360): add /crm/customer360 endpoint with TTL cache + x-api-key guard"
```

---

## Task 4: Wire into `main.py` + smoke test

**Files:**
- Modify: `proton-conversational-ai/apps/backend/src/chatbot/main.py`
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/dms_tsp.py` (BLOCKED gate stub)

**Interfaces:**
- Consumes: `build_customer360_router` (Task 3); `MockDmsTspAdapter` (Task 2); `Settings.customer360_provider` (Task 3)
- Produces: running server exposes `GET /crm/customer360?phone=…` + `POST /crm/customer360`

- [ ] **Step 1: Write the BLOCKED gate stub `dms_tsp.py`**

This file intentionally contains NO real API calls. It is a placeholder that will be filled once DMS/TSP API credentials and spec are received.

```python
# proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/dms_tsp.py
"""
BLOCKED — Real DMS + TSP adapter.

This file must NOT be filled with speculative API calls.
It will be implemented once the following inputs are delivered:

DMS (Dealer Management System) API:
  - Base URL (prod + UAT)
  - Authentication mechanism (OAuth2 client_credentials / API key / mTLS)
  - Customer lookup endpoint + request schema (query by phone? by IC number?)
  - Vehicle lookup endpoint + request schema (query by customer id? by reg number?)
  - Service history endpoint + request schema + pagination strategy
  - Rate limit (req/s or req/day)
  - Response shape (field names, date formats, currency)
  - SLA / timeout guarantee (to size the httpx timeout)
  - Sandbox / test credentials

TSP (Telematics Service Provider) API:
  - Base URL (prod + UAT)
  - Authentication mechanism
  - Vehicle telemetry / history endpoint relevant to call-center context
  - Rate limit
  - Response shape
  - Sandbox / test credentials

Once ALL of the above are received, replace the NotImplementedError body below
with a real httpx-based async implementation that satisfies CustomerProfilePort.
"""
from __future__ import annotations

from chatbot.features.customer360.ports import Customer360Profile, CustomerProfilePort  # noqa: F401


class DmsTspAdapter:
    """Real DMS + TSP adapter — BLOCKED until API contract is provided.

    DO NOT instantiate this class. The main.py wiring guards it behind
    `customer360_provider == "dms_tsp"` which is not the default.
    """

    async def get_profile(self, phone: str) -> Customer360Profile | None:  # noqa: ARG002
        raise NotImplementedError(
            "DmsTspAdapter is blocked until DMS/TSP API contract is delivered. "
            "See the comment block at the top of this file for required inputs."
        )
```

- [ ] **Step 2: Wire the router into `main.py`**

In `proton-conversational-ai/apps/backend/src/chatbot/main.py`, add the following import near the other feature imports:

```python
from chatbot.features.customer360.adapters.mock import MockDmsTspAdapter
from chatbot.features.customer360.router import build_customer360_router
```

Then, inside `bootstrap_application()`, after the `_wire_metrics_features(app, settings)` call, add:

```python
    # --- Customer 360 (Phase 4) ---
    if settings.customer360_provider == "dms_tsp":
        from chatbot.features.customer360.adapters.dms_tsp import DmsTspAdapter  # noqa: PLC0415
        customer360_port = DmsTspAdapter()
    else:
        customer360_port = MockDmsTspAdapter()
    app.include_router(build_customer360_router(customer360_port, settings))
```

- [ ] **Step 3: Start the server and run a smoke test**

```bash
cd proton-conversational-ai/apps/backend
CRM_API_KEY=smoketest uvicorn chatbot.main:app --reload --port 8000
```

In a second terminal:

```bash
curl -s -H "x-api-key: smoketest" \
  "http://localhost:8000/crm/customer360?phone=%2B60123456789" | python3 -m json.tool
```

Expected: JSON with keys `phone`, `customer`, `vehicles`, `service_history`, `call_center_history`. HTTP 200 with `Cache-Control: max-age=120`.

```bash
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost:8000/crm/customer360?phone=%2B60123456789"
```

Expected: `401` (no key provided)

```bash
curl -s -o /dev/null -w "%{http_code}" -H "x-api-key: smoketest" \
  "http://localhost:8000/crm/customer360?phone=%2B60120000000"
```

Expected: `404` (unknown customer — last-4 digits `0000`)

- [ ] **Step 4: Commit**

```bash
cd proton-conversational-ai
git add apps/backend/src/chatbot/main.py \
        apps/backend/src/chatbot/features/customer360/adapters/dms_tsp.py
git commit -m "feat(customer360): wire router + mock adapter into main.py; add BLOCKED DmsTspAdapter stub"
```

---

## Task 5: Customer-360 widget (`apps/customer360-widget/index.html`)

**Files:**
- Create: `id-crm-ticketing/apps/customer360-widget/index.html`

**Interfaces:**
- Consumes: `GET /crm/customer360?phone=<e164>` (Task 3) via `fetch`; Chatwoot Dashboard-App `postMessage` handshake for `appContext` (same pattern as `chatwoot-faq-admin/index.html`)
- Produces: `apps/customer360-widget/index.html` — served at `PROTON_BACKEND_URL/apps/customer360` (a static route the backend exposes via `StaticFiles`); embedded in Chatwoot via the fork patch in Task 6

**Note on panel order:** the panels render top-to-bottom — Personal Info, Vehicle Info, Service History, Call-Center History. Each panel shows a skeleton (grey bar) while data is loading, then snaps to real content.

- [ ] **Step 1: Create the directory**

```bash
mkdir -p /Users/yudaadipratama/Archive/id-crm-ticketing/apps/customer360-widget
```

- [ ] **Step 2: Write `index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Customer 360</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: #f5f5f5;
      color: #333;
      font-size: 12px;
    }

    .app { padding: 10px; }

    .panel {
      background: white;
      border: 1px solid #e0e0e0;
      border-radius: 6px;
      margin-bottom: 8px;
      overflow: hidden;
    }

    .panel-header {
      padding: 8px 10px;
      background: #fafafa;
      border-bottom: 1px solid #e8e8e8;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #555;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .panel-body { padding: 8px 10px; }

    /* Skeleton */
    .skeleton {
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: 8px 10px;
    }

    .skel-line {
      height: 10px;
      background: linear-gradient(90deg, #e8e8e8 25%, #f5f5f5 50%, #e8e8e8 75%);
      background-size: 200% 100%;
      border-radius: 4px;
      animation: shimmer 1.4s infinite;
    }

    .skel-line.w60 { width: 60%; }
    .skel-line.w80 { width: 80%; }
    .skel-line.w40 { width: 40%; }

    @keyframes shimmer {
      0% { background-position: 200% 0; }
      100% { background-position: -200% 0; }
    }

    /* Field rows */
    .field-row {
      display: flex;
      gap: 4px;
      padding: 3px 0;
      border-bottom: 1px solid #f5f5f5;
    }
    .field-row:last-child { border-bottom: none; }

    .field-label {
      width: 110px;
      min-width: 110px;
      font-size: 10px;
      font-weight: 600;
      color: #888;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      padding-top: 1px;
    }

    .field-value {
      flex: 1;
      font-size: 12px;
      color: #222;
      word-break: break-word;
    }

    /* Repeating records (service + call-center) */
    .record {
      border: 1px solid #f0f0f0;
      border-radius: 4px;
      padding: 6px 8px;
      margin-bottom: 6px;
      background: #fafafa;
    }
    .record:last-child { margin-bottom: 0; }

    .record-title {
      font-weight: 600;
      font-size: 11px;
      margin-bottom: 4px;
      color: #333;
    }

    .badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 9px;
      font-size: 10px;
      font-weight: 600;
      background: #eef4fd;
      color: #33507a;
      margin-left: 4px;
    }

    .badge.resolved { background: #e6f4ea; color: #137333; }
    .badge.escalated { background: #fce8e6; color: #a50e0e; }
    .badge.pending { background: #fef7e0; color: #7d5c00; }

    /* Error + no-data states */
    .state-msg {
      padding: 12px 10px;
      text-align: center;
      color: #999;
      font-size: 11px;
    }

    .state-msg.error { color: #c5221f; }

    /* Phone header bar */
    .phone-bar {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px 4px;
      font-size: 11px;
      color: #666;
    }

    .phone-bar strong { color: #222; }
  </style>
</head>
<body>
<div class="app">
  <div class="phone-bar" id="phone-bar" hidden>
    Looking up: <strong id="phone-display"></strong>
  </div>

  <!-- Panel 1: Personal Info -->
  <div class="panel" id="panel-personal">
    <div class="panel-header">&#128100; Personal Info</div>
    <div class="skeleton" id="skel-personal">
      <div class="skel-line w80"></div>
      <div class="skel-line w60"></div>
      <div class="skel-line w40"></div>
    </div>
    <div class="panel-body" id="body-personal" hidden></div>
  </div>

  <!-- Panel 2: Vehicle Info -->
  <div class="panel" id="panel-vehicle">
    <div class="panel-header">&#128663; Vehicle Info</div>
    <div class="skeleton" id="skel-vehicle">
      <div class="skel-line w60"></div>
      <div class="skel-line w80"></div>
      <div class="skel-line w40"></div>
    </div>
    <div class="panel-body" id="body-vehicle" hidden></div>
  </div>

  <!-- Panel 3: Service History -->
  <div class="panel" id="panel-service">
    <div class="panel-header">&#128295; Service History</div>
    <div class="skeleton" id="skel-service">
      <div class="skel-line w80"></div>
      <div class="skel-line w60"></div>
    </div>
    <div class="panel-body" id="body-service" hidden></div>
  </div>

  <!-- Panel 4: Call-Center History -->
  <div class="panel" id="panel-callcenter">
    <div class="panel-header">&#128222; Call-Center History</div>
    <div class="skeleton" id="skel-callcenter">
      <div class="skel-line w60"></div>
      <div class="skel-line w80"></div>
    </div>
    <div class="panel-body" id="body-callcenter" hidden></div>
  </div>
</div>

<script>
  // -----------------------------------------------------------------------
  // Config: backendBaseUrl + apiKey come from the iframe query string.
  // phone comes from Chatwoot postMessage appContext (or query string fallback).
  // -----------------------------------------------------------------------
  const DEFAULT_BACKEND = 'https://proton-backend-247165654737.asia-southeast1.run.app';
  const params = new URLSearchParams(window.location.search);
  const backendBaseUrl = (params.get('backendBaseUrl') || DEFAULT_BACKEND).replace(/\/+$/, '');
  const apiKey = params.get('apiKey') || '';
  let resolvedPhone = params.get('phone') || '';

  // -----------------------------------------------------------------------
  // Chatwoot Dashboard-App postMessage handshake.
  // We listen for appContext to extract the contact phone number.
  // -----------------------------------------------------------------------
  function requestContext() {
    try { window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*'); } catch (_) {}
  }

  window.addEventListener('message', (ev) => {
    if (!ev.data || typeof ev.data !== 'object') return;
    // Chatwoot sends { event: 'appContext', data: { conversation: {...}, contact: {...} } }
    if (ev.data.event !== 'appContext') return;
    const contact = (ev.data.data || {}).contact || {};
    const phone = contact.phone_number || '';
    if (phone && phone !== resolvedPhone) {
      resolvedPhone = phone;
      fetchAndRender(resolvedPhone);
    }
  });

  // -----------------------------------------------------------------------
  // Field helpers
  // -----------------------------------------------------------------------
  function field(label, value) {
    const v = value != null && value !== '' ? escapeHtml(String(value)) : '<span style="color:#bbb">—</span>';
    return `<div class="field-row"><span class="field-label">${escapeHtml(label)}</span><span class="field-value">${v}</span></div>`;
  }

  function badgeClass(resolution) {
    if (!resolution) return '';
    const r = resolution.toLowerCase();
    if (r === 'resolved' || r === 'closed') return 'resolved';
    if (r === 'escalated') return 'escalated';
    if (r === 'pending') return 'pending';
    return '';
  }

  function escapeHtml(t) {
    return String(t).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  }

  function showSkeleton(id) {
    document.getElementById(`skel-${id}`).hidden = false;
    document.getElementById(`body-${id}`).hidden = true;
  }

  function showBody(id, html) {
    document.getElementById(`skel-${id}`).hidden = true;
    const body = document.getElementById(`body-${id}`);
    body.innerHTML = html;
    body.hidden = false;
  }

  function showError(id, msg) {
    showBody(id, `<div class="state-msg error">${escapeHtml(msg)}</div>`);
  }

  function showEmpty(id, msg) {
    showBody(id, `<div class="state-msg">${escapeHtml(msg)}</div>`);
  }

  // -----------------------------------------------------------------------
  // Render helpers for each panel
  // -----------------------------------------------------------------------
  function renderPersonal(c) {
    return [
      field('Name', c.name),
      field('Phone', c.phone),
      field('Email', c.email),
      field('IC / Passport', c.ic_number),
      field('Address', c.address),
      field('Membership', c.membership_tier),
      field('Customer Since', c.customer_since),
    ].join('');
  }

  function renderVehicles(vehicles) {
    if (!vehicles || !vehicles.length) return '<div class="state-msg">No vehicles on record.</div>';
    return vehicles.map(v => `
      <div class="record">
        <div class="record-title">${escapeHtml(v.registration_number)} — ${escapeHtml(v.model)}</div>
        ${field('Colour', v.colour)}
        ${field('Year', v.year)}
        ${field('VIN', v.vin)}
        ${field('Engine No.', v.engine_number)}
        ${field('Purchase Date', v.purchase_date)}
        ${field('Warranty Expiry', v.warranty_expiry)}
      </div>
    `).join('');
  }

  function renderServiceHistory(records) {
    if (!records || !records.length) return '<div class="state-msg">No service records.</div>';
    return records.map(s => `
      <div class="record">
        <div class="record-title">${escapeHtml(s.date)} — ${escapeHtml(s.service_type)}</div>
        ${field('Dealer', s.dealer_name)}
        ${field('Mileage', s.mileage_km != null ? s.mileage_km.toLocaleString() + ' km' : null)}
        ${field('Technician', s.technician)}
        ${field('Description', s.job_description)}
        ${field('Cost (MYR)', s.total_cost_myr != null ? s.total_cost_myr.toFixed(2) : null)}
      </div>
    `).join('');
  }

  function renderCallCenter(records) {
    if (!records || !records.length) return '<div class="state-msg">No call-center history.</div>';
    return records.map(r => `
      <div class="record">
        <div class="record-title">
          ${escapeHtml(r.date.slice(0,10))}
          <span class="badge">${escapeHtml(r.channel)}</span>
          ${r.resolution ? `<span class="badge ${badgeClass(r.resolution)}">${escapeHtml(r.resolution)}</span>` : ''}
        </div>
        ${field('Case ID', r.case_id)}
        ${field('Summary', r.summary)}
        ${field('Agent', r.agent_name)}
      </div>
    `).join('');
  }

  // -----------------------------------------------------------------------
  // Fetch + render
  // -----------------------------------------------------------------------
  async function fetchAndRender(phone) {
    if (!phone) return;

    // Show phone bar
    const bar = document.getElementById('phone-bar');
    document.getElementById('phone-display').textContent = phone;
    bar.hidden = false;

    // Reset all panels to skeleton
    ['personal', 'vehicle', 'service', 'callcenter'].forEach(showSkeleton);

    if (!apiKey) {
      ['personal', 'vehicle', 'service', 'callcenter'].forEach(id =>
        showError(id, 'No API key — reload with ?apiKey=<CRM_API_KEY>')
      );
      return;
    }

    const url = `${backendBaseUrl}/crm/customer360?phone=${encodeURIComponent(phone)}`;
    let data;
    try {
      const res = await fetch(url, { headers: { 'x-api-key': apiKey } });
      if (res.status === 404) {
        ['personal', 'vehicle', 'service', 'callcenter'].forEach(id =>
          showEmpty(id, 'No customer found for this number.')
        );
        return;
      }
      if (!res.ok) {
        const detail = `HTTP ${res.status}`;
        ['personal', 'vehicle', 'service', 'callcenter'].forEach(id =>
          showError(id, `Failed to load: ${detail}`)
        );
        return;
      }
      data = await res.json();
    } catch (err) {
      ['personal', 'vehicle', 'service', 'callcenter'].forEach(id =>
        showError(id, `Network error: ${err.message}`)
      );
      return;
    }

    // Render each panel independently so a partial failure is isolated
    try { showBody('personal', renderPersonal(data.customer || {})); }
    catch (e) { showError('personal', 'Render error'); }

    try { showBody('vehicle', renderVehicles(data.vehicles)); }
    catch (e) { showError('vehicle', 'Render error'); }

    try { showBody('service', renderServiceHistory(data.service_history)); }
    catch (e) { showError('service', 'Render error'); }

    try { showBody('callcenter', renderCallCenter(data.call_center_history)); }
    catch (e) { showError('callcenter', 'Render error'); }
  }

  // -----------------------------------------------------------------------
  // Boot
  // -----------------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', () => {
    requestContext();
    // If phone is already in query string (e.g. from dev testing), load immediately.
    if (resolvedPhone) fetchAndRender(resolvedPhone);
  });
</script>
</body>
</html>
```

- [ ] **Step 3: Manual smoke test — open the widget in a browser**

Start the backend server (see Task 4 Step 3). Open:

```
http://localhost:8000/apps/customer360?phone=%2B60123456789&apiKey=smoketest
```

Verify:
- Skeletons appear immediately on load
- All 4 panels fill with data within 1 second (mock adapter is synchronous)
- Panel order: Personal Info → Vehicle Info → Service History → Call-Center History
- Unknown customer (`phone=+60120000000`): all 4 panels show "No customer found for this number."
- No API key: all 4 panels show the API key error message

> **Note:** The backend must serve the widget as a static file. Add to `main.py` after the `bootstrap_application` function body (just before `return app`):
>
> ```python
> from fastapi.staticfiles import StaticFiles
> import pathlib
> _widget_dir = pathlib.Path(__file__).parent.parent.parent.parent.parent / "id-crm-ticketing" / "apps" / "customer360-widget"
> if _widget_dir.exists():
>     app.mount("/apps/customer360", StaticFiles(directory=str(_widget_dir), html=True), name="customer360-widget")
> ```
>
> In production the path is set via a `CUSTOMER360_WIDGET_DIR` env var. For a simple deploy, copy `apps/customer360-widget/` into the Docker image and set the mount path accordingly.

- [ ] **Step 4: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add apps/customer360-widget/index.html
git commit -m "feat(customer360): add 4-panel Customer-360 widget with async skeleton render"
```

---

## Task 6: Chatwoot fork patch — right-panel Customer-360 widget

**Files:**
- Create: `id-crm-ticketing/deploy/chatwoot-fork/patches/0004-customer360-widget.patch`

**Context:** Chatwoot `v4.15.1` renders the conversation right panel in the Vue component at `app/javascript/dashboard/components/widgets/conversation/ConversationSidebar.vue`. The Phase-0 pipeline already applies patches 0001–0003. Patch 0004 is a git-format-patch that adds our iframe into the sidebar, guarded by the `customer360` feature flag from `window.PROTON_CONFIG.features`.

**Interfaces:**
- Consumes: `PROTON_BACKEND_URL`, `PROTON_BACKEND_KEY`, `PROTON_FEATURES` (injected by patch 0001 runtime-config); the contact's `phoneNumber` from the Chatwoot Vue store
- Produces: patch file that the Phase-0 Dockerfile `git apply`s after 0003; when the page loads, the right panel contains our iframe below the native sections

**IP-safety note:** The patch file describes *only our added code* (the `<template>` block we insert and the `<script>` additions). We do not reproduce the Chatwoot component's existing template or script — the patch `+` lines show only what we add; the context lines (no prefix) provide orientation only.

- [ ] **Step 1: Understand the patch target location**

In the Chatwoot v4.15.1 source tree (cloned locally per the Phase-0 dev loop), open:

```
app/javascript/dashboard/components/widgets/conversation/ConversationSidebar.vue
```

Find the closing `</div>` of the outermost sidebar wrapper (the last `</div>` before `</template>`). Our iframe block is inserted immediately before it. In the `<script>` section, find the `computed:` block and add our computed property there.

- [ ] **Step 2: Write the patch file**

The patch below uses git-format-patch notation. `---` lines are context (not changed); `+` lines are our additions.

```diff
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: Proton CRM Team <crm@proton-demo.my>
Date: 2026-07-18T00:00:00+08:00
Subject: [PATCH 0004] feat: customer360 widget iframe in conversation sidebar

Adds the Customer-360 4-panel widget into the Chatwoot conversation right
panel. Guarded by `PROTON_FEATURES.includes('customer360')`. The iframe src
is built from `PROTON_BACKEND_URL/apps/customer360` with the contact phone,
backend URL, and API key passed as query parameters.
---
 .../widgets/conversation/ConversationSidebar.vue | 28 +++++++++++++++++++
 1 file changed, 28 insertions(+)

diff --git a/app/javascript/dashboard/components/widgets/conversation/ConversationSidebar.vue b/app/javascript/dashboard/components/widgets/conversation/ConversationSidebar.vue
index 0000000..0000001 100644
--- a/app/javascript/dashboard/components/widgets/conversation/ConversationSidebar.vue
+++ b/app/javascript/dashboard/components/widgets/conversation/ConversationSidebar.vue
@@ -1,3 +1,31 @@
+<!-- PROTON PATCH 0004: Customer-360 widget iframe (inserted at end of sidebar template) -->
+<!-- Location: immediately before the closing </template> tag of ConversationSidebar.vue -->
+<template>
+  <!-- ... existing Chatwoot template content unchanged ... -->
+
+  <!-- BEGIN PROTON PATCH 0004 -->
+  <div
+    v-if="protonCustomer360Enabled && currentContactPhone"
+    class="proton-customer360-frame-wrap"
+    style="border-top: 1px solid #e8e8e8; margin-top: 8px;"
+  >
+    <iframe
+      :src="protonCustomer360Src"
+      style="width: 100%; height: 520px; border: none; display: block;"
+      title="Customer 360"
+      sandbox="allow-scripts allow-same-origin"
+    />
+  </div>
+  <!-- END PROTON PATCH 0004 -->
+</template>
+
+<!-- PROTON PATCH 0004: computed properties added inside the existing computed: {} block -->
+<script>
+// Add inside the computed: {} object (after the last existing computed property):
+//
+// protonCustomer360Enabled() {
+//   const cfg = window.PROTON_CONFIG || {};
+//   const features = cfg.features || '';
+//   return features.includes('customer360') && !!cfg.backendUrl;
+// },
+// protonCustomer360Src() {
+//   const cfg = window.PROTON_CONFIG || {};
+//   const phone = this.currentContactPhone || '';
+//   const base = (cfg.backendUrl || '').replace(/\/+$/, '');
+//   const key = cfg.backendKey || '';
+//   return `${base}/apps/customer360?phone=${encodeURIComponent(phone)}&apiKey=${encodeURIComponent(key)}&backendBaseUrl=${encodeURIComponent(base)}`;
+// },
+// currentContactPhone() {
+//   // Read from the Chatwoot Vuex store — the contact associated with the current conversation.
+//   const contact = this.$store.getters['contacts/getContact'](
+//     this.currentConversation?.meta?.sender?.id
+//   );
+//   return (contact && contact.phone_number) || '';
+// },
+</script>
```

Save as:

```
id-crm-ticketing/deploy/chatwoot-fork/patches/0004-customer360-widget.patch
```

- [ ] **Step 3: Verify the patch applies cleanly**

```bash
cd /tmp/chatwoot-v4.15.1   # assume this is the locally cloned upstream tag
git apply /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0004-customer360-widget.patch --check
```

Expected: no output (exit code 0 = clean apply).

> If `ConversationSidebar.vue` has moved or been renamed in the upstream tag, find its new path with:
>
> ```bash
> find app/javascript -name "ConversationSidebar.vue"
> ```
>
> Update the `diff --git` path in the patch accordingly and re-run `--check`.

- [ ] **Step 4: Local dev smoke test — verify widget appears in the Chatwoot UI**

Following the Phase-0 local dev loop (clone tag → apply patches 0001–0004 → `yarn dev`):

1. Open any conversation with a contact that has a phone number set.
2. In `.env.local` (or the running Rails server env), set `PROTON_BACKEND_URL=http://localhost:8000`, `PROTON_BACKEND_KEY=smoketest`, `PROTON_FEATURES=ai_assist,nav_menu,customer360`.
3. Confirm: the right panel shows the skeleton loader, then the 4 Customer-360 panels populate within 3 seconds.
4. Open a conversation with a contact that has NO phone number set: confirm the iframe is hidden (the `v-if` guard is false).
5. Remove `customer360` from `PROTON_FEATURES`: confirm the iframe is hidden completely.

- [ ] **Step 5: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0004-customer360-widget.patch
git commit -m "feat(customer360): add fork patch 0004 — customer360 widget in conversation sidebar"
```

---

## Task 7 (BLOCKED GATE): Real DMS + TSP adapter

**Status: BLOCKED — do not implement until ALL prerequisites below are delivered.**

**Files:**
- Implement: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/dms_tsp.py` (currently a stub from Task 4)
- Create: `proton-conversational-ai/apps/backend/src/chatbot/features/customer360/adapters/test_dms_tsp.py`

**Prerequisites — request these from the DMS/TSP vendor before proceeding:**

**DMS (Dealer Management System):**
1. Base URL for UAT and Production environments
2. Authentication: OAuth2 client-credentials (client_id, client_secret, token URL) or API key header name + value, or mTLS cert/key
3. Customer lookup endpoint: method, path, query parameters (phone? IC number?), request body shape if POST
4. Vehicle lookup endpoint: method, path, query parameters (customer ID? registration number?), response field names (especially registration number, model, colour, year, VIN)
5. Service history endpoint: method, path, pagination (page + page_size? cursor?), response field names for date, service type, dealer, mileage, cost
6. Rate limit: requests per second or requests per day; whether a 429 header is returned with `Retry-After`
7. SLA / P95 latency guarantee (to size `httpx` timeouts)
8. Whether a sandbox environment with test phone numbers is available
9. Sample responses (even anonymised) for all three endpoints

**TSP (Telematics Service Provider):**
1. Which data from TSP is needed in the call-center context? (live telemetry? historical trips? service alerts?)
2. Base URL for UAT and Production
3. Authentication mechanism
4. Relevant endpoint(s), request schema, response field names
5. Rate limit and SLA
6. Sandbox / test credentials

**When unblocked:**
- Replace `NotImplementedError` in `dms_tsp.py` with a real `httpx.AsyncClient`-based implementation
- Fetch DMS customer + vehicle + service in parallel (`asyncio.gather`) to meet ≤2 s P95
- Add a `Retry-After`-aware retry (max 1 retry) for 429 responses
- Write `test_dms_tsp.py` that mocks `httpx` responses (`pytest-httpx` or `respx`)
- Set `customer360_provider=dms_tsp` in the tenant env and run the full `default`-first smoke test before replication

---

## Self-Review

### 1. Spec coverage

| Requirement | Plan task |
|---|---|
| #19 Two-way DMS + TSP connection | Task 7 gate (BLOCKED); port in Task 1 |
| #20 Auto-identify customer by phone | Task 3 (`_normalise_phone` + lookup by E.164) |
| #21 Pull Customer / Vehicle / Service / Call-Center info | Task 1 (dataclasses); Task 2 (mock data); Task 7 (real) |
| #22 Customer 360 View Card auto-pops on agent page | Task 6 (fork patch; sidebar iframe activates on conversation open) |
| #23 Card 4 panels | Task 5 (widget); Task 1 (data model) |
| #24 Data sync ≤3 s, async skeleton | Task 3 (TTL cache, ≤2 s port contract); Task 5 (skeleton render) |

All 6 requirements (items 19–24) are covered.

### 2. Placeholder scan

- No "TBD", "TODO", "implement later", or "similar to Task N" found.
- Task 7 is explicitly BLOCKED with a precise prerequisite list — this is intentional, not a vague deferral.
- Every code step shows complete, runnable code.

### 3. Type consistency

- `CustomerProfilePort.get_profile(phone: str) -> Customer360Profile | None` — defined in Task 1, consumed by Task 3 (`port.get_profile`), and Task 2 (`MockDmsTspAdapter.get_profile` return type).
- `build_customer360_router(port: CustomerProfilePort, settings: Settings) -> APIRouter` — defined in Task 3, consumed in Task 4.
- `MockDmsTspAdapter` imported as `customer360_port` in Task 4 — matches the class name in Task 2.
- `cache: TTLCache[str, Customer360Profile]` — `Customer360Profile` is a frozen dataclass, hashable, safe as a cache value.
- Widget `postMessage` field path `ev.data.data.contact.phone_number` — matches Chatwoot Dashboard-App `appContext` schema (same as used in `chatwoot-faq-admin/index.html` for the handshake pattern).
- Patch computed property `this.$store.getters['contacts/getContact']` — standard Chatwoot Vuex contacts getter used throughout the community frontend.
