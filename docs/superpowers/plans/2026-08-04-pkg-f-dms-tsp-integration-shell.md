# Package F — DMS/TSP Integration Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give operators a real place in Settings → Integrations to configure and test a DMS/TSP connection, and give the codebase a pluggable client so the eventual real adapter is one class rather than a re-architecture.

**Architecture:** Phase 1 only — the shell. Credential storage, admin form, connection test, a narrow `DmsClient` port with null and mock implementations, and an optional `dms` block in the Customer 360 response. **We have no DMS API specification**, so anything claiming to read Proton's real vehicle data would be invented; Phase 2 is explicitly out of this plan.

**Tech Stack:** Python 3.12, FastAPI, Firestore (via the existing `PicStore`/`DealerStore` pattern), `httpx`, pytest, Vue 3 (Chatwoot fork).

**Spec:** `docs/superpowers/specs/2026-08-04-pkg-f-dms-tsp-integration-shell-design.md`

## Global Constraints

- **The credential is write-only.** It can be set and replaced, never read back. The GET endpoint omits it entirely — not masked-but-present, omitted.
- **The credential never appears** in logs, error messages, or the connection-test response.
- **Fail-open everywhere.** A DMS outage degrades Customer 360 to CRM-only data; it never 500s the page.
- The integration disabled ⇒ the Customer 360 response is **byte-identical** to today. Assert this.
- Our types are ours. `DmsCustomer` / `DmsVehicle` / `DmsServiceRecord` are defined by us; nothing outside the adapter ever sees the vendor's field names. This is what keeps Phase 2 contained.
- The UI must show a clear **"Not connected"** state. A shell mistaken for a working integration is exactly the failure that produced demo-feedback item #26.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/chat/dms_config_store.py` | Firestore-backed config record + write-only credential handling |
| `backend/.../features/chat/dms_client.py` | `DmsClient` port, `DmsCustomer`/`DmsVehicle`/`DmsServiceRecord`, null + mock implementations |
| `backend/.../features/chat/dms_admin_router.py` | `/admin/integrations/dms` CRUD + `POST .../test` |
| `backend/.../features/chat/test_dms_config_store.py` | Credential-omission tests |
| `backend/.../features/chat/test_dms_admin_router.py` | Router, permission and leak tests |
| `backend/.../features/chat/customer360_router.py` | Modify: optional `dms` block |
| `backend/.../features/authz/seed.py` | Modify: add `integration.manage` |
| `deploy/chatwoot-fork/patches/0045-dms-integration-card.patch` | The integration card and form |

---

### Task 1: Config store with a write-only credential

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/dms_config_store.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_dms_config_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) class DmsConfig: enabled: bool; provider_label: str; base_url: str; auth_type: str; extra_header_name: str; extra_header_value: str; timeout_seconds: float; retries: int`
  - `class DmsConfigStore` with `async def get() -> DmsConfig | None`, `async def get_credential() -> str | None`, `async def save(config: DmsConfig, credential: str | None) -> None`
  - `def public_dict(config: DmsConfig) -> dict` — the API-safe serialization, credential absent
  - `save(..., credential=None)` means "keep the stored credential", so an operator can edit the base URL without re-entering the secret.

- [ ] **Step 1: Write the failing tests**

```python
"""The credential must be settable and never retrievable through any public path."""

from __future__ import annotations

from chatbot.features.chat.dms_config_store import DmsConfig, DmsConfigStore, public_dict

CFG = DmsConfig(
    enabled=True,
    provider_label="Proton DMS",
    base_url="https://dms.example.com",
    auth_type="api_key_header",
    extra_header_name="X-Tenant",
    extra_header_value="proton",
    timeout_seconds=10.0,
    retries=2,
)


def test_public_dict_has_no_credential_key_at_all():
    d = public_dict(CFG)
    assert "credential" not in d
    assert "api_key" not in d
    assert "secret" not in d


def test_public_dict_does_not_contain_the_secret_value_anywhere():
    d = public_dict(CFG)
    assert "super-secret-key" not in repr(d)


async def test_saved_credential_is_retrievable_only_through_the_private_accessor(store):
    await store.save(CFG, credential="super-secret-key")
    assert await store.get_credential() == "super-secret-key"
    assert "super-secret-key" not in repr(public_dict(await store.get()))


async def test_saving_with_none_credential_preserves_the_existing_one(store):
    await store.save(CFG, credential="super-secret-key")
    await store.save(CFG, credential=None)
    assert await store.get_credential() == "super-secret-key"


async def test_config_absent_returns_none(store):
    assert await store.get() is None
```

Add a `store` fixture backed by the same in-memory Firestore double `test_pic_store.py` uses.

- [ ] **Step 2: Run and watch fail.** Expected: module not found.
- [ ] **Step 3: Implement**, following `pic_store.py` for the Firestore access pattern.
- [ ] **Step 4: Run and watch pass.** Expected: 5 PASS.
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(dms): config store with a write-only credential"
```

---

### Task 2: The client port, null and mock implementations

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/dms_client.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_dms_client.py`

**Interfaces:**
- Consumes: `DmsConfig` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class DmsCustomer: ref: str; name: str; phone: str | None`
  - `@dataclass(frozen=True) class DmsVehicle: vehicle_no: str; model: str; purchased_from: str | None`
  - `@dataclass(frozen=True) class DmsServiceRecord: date: str; description: str; dealer: str | None`
  - `class DmsClient(Protocol)` with `find_customer(*, phone, vehicle_no)`, `list_vehicles(customer_ref)`, `list_service_history(vehicle_no)`
  - `class NullDmsClient` — returns `None` / `[]` for everything
  - `class MockDmsClient` — plausible fixed records for demos
  - `async def probe(config: DmsConfig, credential: str, client: httpx.AsyncClient) -> ProbeResult` where `ProbeResult` has `status: str` in `{"reachable", "auth_failed", "timeout", "unexpected_status"}` and a sanitised `message: str`
  - Task 3 and Task 4 both consume these names.

- [ ] **Step 1: Write the failing tests** — covering each `probe` status mapping (200, 401/403, timeout, 500), that the credential never appears in `ProbeResult.message`, that `NullDmsClient` returns empties, and that `MockDmsClient` returns records with our field names.
- [ ] **Step 2: Run, implement, re-run until green.**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat(dms): client port with null and mock implementations"
```

---

### Task 3: Admin router

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/dms_admin_router.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_dms_admin_router.py`
- Modify: `backend/apps/backend/src/chatbot/features/authz/seed.py`
- Modify: `backend/apps/backend/src/chatbot/main.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `GET/PUT /admin/integrations/dms` and `POST /admin/integrations/dms/test`, all behind `require_permission("integration.manage")`.

- [ ] **Step 1: Write the failing tests**

Cover: GET without the permission returns 403; GET returns config with **no credential key**; PUT saves; PUT without a credential keeps the stored one; the test endpoint maps each probe outcome to a response; a `base_url` that is not `https://` is rejected with 400; and — most importantly — **the credential appears in no response body from any endpoint**, asserted by scanning the serialized response.

- [ ] **Step 2: Run and watch fail**, then implement following `pic_admin_router.py`, register `integration.manage` in `seed.py`, mount in `main.py`, then re-run until green.
- [ ] **Step 3: Commit**

```bash
git commit -m "feat(dms): /admin/integrations/dms CRUD and connection test"
```

---

### Task 4: Optional DMS block on Customer 360

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/customer360_router.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/test_customer360_router.py`

**Interfaces:**
- Consumes: `DmsClient` (Task 2), `DmsConfigStore` (Task 1).
- Produces: the search response gains an optional `dms` key alongside `contact`, `conversations`, `rsa_incidents`.

- [ ] **Step 1: Write the failing tests**

The important one first:

```python
async def test_response_is_unchanged_when_the_integration_is_disabled(client_disabled):
    body = (await client_disabled.get("/admin/customer360/search?q=0123456789")).json()
    assert set(body) == {"contact", "conversations", "rsa_incidents"}
```

Then: with the mock client enabled the `dms` block appears; and a client that raises still returns all three CRM blocks with `dms` absent or null — fail-open, asserted rather than assumed.

- [ ] **Step 2: Run, implement, re-run until green.**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat(customer360): optional DMS block, fail-open and off by default"
```

---

### Task 5: The integration card and form

**Files:**
- Create: `deploy/chatwoot-fork/patches/0045-dms-integration-card.patch`

- [ ] **Step 1:** Author against upstream `v4.15.1` — same clone-and-apply procedure as Package B Task 6.
- [ ] **Step 2:** Add a **DMS / TSP** card to the Integrations list routing to a config form: enabled, provider label, base URL, auth type, credential, one extra header pair, timeout, retries.
- [ ] **Step 3:** Render the credential as `••••` with a **Replace** action; never populate the field from the API, because the API does not return it.
- [ ] **Step 4:** Add a **Test connection** button surfacing the four probe outcomes in plain language.
- [ ] **Step 5:** Show an unmistakable **"Not connected"** state when disabled, and label mock-client data as mock wherever it renders in Customer 360.
- [ ] **Step 6:** Verify the patch applies from a clean clone, then commit.

---

### Task 6: Send Proton the Phase 2 questions

Phase 2 cannot start without these, and asking early is the only thing that shortens the critical path.

- [ ] **Step 1:** Request, in writing: API documentation (endpoints, auth, pagination, error codes, rate limits); a sandbox plus test credentials; the **identifier decision** — which key joins a CRM contact to a DMS customer (CIF-style id, phone, or vehicle number), which is demo-feedback item #16 and still unanswered; the data-protection position on what may leave the DMS and whether we may cache it; and whether the integration is read-only.
- [ ] **Step 2:** Add them to `docs/analysis/2026-08-05-email-channel-questions-for-proton.md` or a sibling document so they are asked in the same meeting cycle rather than serially.
- [ ] **Step 3:** Note in the spec that the identifier decision blocks entity resolution entirely — no adapter can match a customer without it.

---

## Out of scope

Any real DMS/TSP call; TSP telematics streaming (live vehicle location and state is a different problem needing its own spec); writing back to the DMS; and a generic third-party integration framework. This is one card for one purpose.
