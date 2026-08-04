# Package F — DMS / TSP integration surface in Settings → Integrations

**Date:** 2026-08-04
**Covers demo-feedback items:** #4 (custom AI tools / DMS-API integration, demoed as a mock) and the DMS half of #15 (Customer 360)
**Effort:** medium for the shell. Unknown for the real integration — **we have no API specification.**

---

## 1. Goal

Give operators a real place to configure a DMS/TSP connection — the
Integrations settings page Chatwoot already ships ("Chatwoot integrates with
multiple tools and services… Explore the list below to configure your favorite
apps") — with a custom form for base URL and credentials, a connection test,
and a pluggable client the rest of the platform can call.

## 2. The constraint that shapes this entire spec

**We do not have Proton's DMS or TSP API documentation.** No endpoints, no auth
scheme, no field names, no environment to test against. Anything built now that
claims to *read Proton's vehicle data* would be invented.

So this package deliberately splits in two:

- **Phase 1 (buildable today):** the integration *shell* — credential storage,
  admin form, connection test, a `DmsClient` interface, and a mock
  implementation. Nothing in it depends on knowing their API.
- **Phase 2 (blocked):** the real adapter — field mapping, entity resolution,
  caching, error semantics. Starts when Proton supplies docs and a sandbox.

Building Phase 1 first is not busywork: it is what lets Phase 2 be a single
adapter class instead of a re-architecture, and it converts an unanswerable ask
into a visible, demoable "connect your DMS here" surface.

## 3. Design — Phase 1

### 3.1 Where it lives

Chatwoot's Integrations page lists integration cards. The fork adds a **DMS /
TSP** card that routes to a configuration form. This follows the exact shape of
the two admin surfaces already shipped — Escalation Routing (patch `0039`) and
Customer 360 (patch `0041`): a backend CRUD router behind `require_permission`,
plus a fork page that talks to it via `protonAdmin.js`.

New permission: `integration.manage`, added to `features/authz/seed.py`
alongside `escalation.manage` and `customer360.view`.

### 3.2 What the form holds

Deliberately generic, because we're configuring an unknown REST API:

| Field | Notes |
|---|---|
| Enabled | Master switch; off means nothing calls out |
| Provider label | Free text — "Proton DMS", "TSP", whatever they call it |
| Base URL | `https://` enforced |
| Auth type | `api_key_header` \| `bearer_token` \| `basic` — covers the realistic cases without guessing theirs |
| Credential | The secret itself |
| Extra header name/value | One optional pair, for the tenant/partner id these APIs usually want |
| Timeout, retries | Sane defaults, editable |

Storage follows `PicStore` / `DealerStore` (Firestore-backed, per tenant).

### 3.3 Secret handling — the part to get right

- The credential is **write-only from the UI**: it can be set and replaced,
  never read back. The form shows `••••` and a "Replace" action.
- The GET endpoint returns configuration **with the credential omitted**, never
  masked-but-present.
- Credentials never appear in logs, error messages, or the connection-test
  response. The test returns a status and a sanitised message only.
- If the platform gains a secret manager later this moves; until then, document
  that the credential sits in the tenant's Firestore.

### 3.4 Connection test

A "Test connection" button calls a backend endpoint that issues one request to
a configurable health/probe path and reports reachable / auth-failed /
timeout / unexpected-status. This is the only honest thing we can verify
without their API, and it's genuinely useful on day one of Phase 2.

### 3.5 The client interface

A narrow port with exactly the operations Customer 360 would want:

```
class DmsClient(Protocol):
    async def find_customer(self, *, phone: str | None,
                            vehicle_no: str | None) -> DmsCustomer | None
    async def list_vehicles(self, customer_ref: str) -> list[DmsVehicle]
    async def list_service_history(self, vehicle_no: str) -> list[DmsServiceRecord]
```

Two implementations ship in Phase 1: a **null client** (integration disabled →
returns nothing, so every caller works unchanged) and a **mock client** behind
a flag, returning plausible records for demos. Both are fail-open: a DMS
outage must degrade Customer 360 to CRM-only data, never 500 the page.

`DmsCustomer` / `DmsVehicle` / `DmsServiceRecord` are **our** types, not
theirs. Phase 2 maps their payload into these; nothing outside the adapter ever
sees their field names. That is what keeps Phase 2 contained.

### 3.6 How Customer 360 consumes it

`customer360_router.py`'s search gains an optional fourth block alongside
`contact`, `conversations`, `rsa_incidents`: a `dms` block, populated only when
the integration is enabled. The UI renders it as a clearly separate "From DMS"
section, so nobody mistakes CRM data for dealer-system data. Absent DMS, the
page looks exactly as it does today.

This is also where the vehicle-lookup weakness noted in Package B finally
closes properly: a DMS lookup by plate returns the owner, which is the real
answer to feedback #15 that neither RSA incidents nor `vehicle_model`
substring matching can give.

## 4. Design — Phase 2 (what we'll need from Proton)

Blocked pending, and worth sending as an explicit list:

1. API documentation — endpoints, auth, pagination, error codes, rate limits.
2. A sandbox environment plus test credentials.
3. The identifier decision from feedback item #16 — which key joins a CRM
   contact to a DMS customer (CIF-style id, phone, or vehicle number). **This
   is still unanswered and is the single biggest open question**; the integration
   cannot resolve identities without it.
4. Data-protection position: what customer data may leave the DMS, whether we
   may cache it, and for how long.
5. Whether the integration is read-only (assumed) or must write back.

Design decisions deferred to Phase 2 for good reason: caching/TTL, whether
lookups are synchronous on page load or pre-fetched, reconciliation when DMS
and CRM disagree, and what "custom AI tools" means concretely — most likely a
Gemini function-calling tool wrapping `DmsClient` so the bot can answer "when
is my service due", which is a natural follow-on once the client exists.

## 5. Testing

- CRUD + permission enforcement on the config router.
- The credential never appears in any GET response — asserted explicitly.
- Connection test maps each failure mode to the right status without leaking
  the credential.
- Null client → Customer 360 response is byte-identical to today.
- Mock client → the `dms` block renders, and a client exception still returns
  the CRM blocks (fail-open, asserted).

## 6. Risks

- **The shell can be mistaken for a working integration.** The card must show a
  clear "Not connected" state, and any demo using the mock client must say it's
  a mock. Feedback item #26 is a live example of what happens when a mock gets
  described as working.
- **Our generic auth model may not fit theirs** (mTLS, OAuth client credentials,
  IP allowlisting are all plausible for an automotive DMS). Phase 1 covers the
  common cases; treat a mismatch as expected Phase 2 work, not a design failure.
- **Credential in Firestore** is adequate, not ideal. Note it and revisit.

## 7. Out of scope

- Any real DMS/TSP call (Phase 2).
- TSP telematics streaming — live vehicle location/state is a different problem
  from a DMS record lookup and would need its own spec.
- Writing back to the DMS.
- Generic third-party integration framework. This is one card for one purpose.

## 8. Definition of done

An operator can configure and test a DMS connection from Settings →
Integrations, the credential is never readable back, Customer 360 shows a DMS
section when enabled and is unchanged when not, and the five Phase 2 questions
in §4 have been sent to Proton in writing.
