# DMS / TSP integration — questions & concerns for Proton

**Meeting:** next available cycle after 2026-08-05
**Prepared:** 2026-08-05
**Sources:** `docs/superpowers/specs/2026-08-04-pkg-f-dms-tsp-integration-shell-design.md`
(the design spec for this package), the build itself (`dms_config_store.py`,
`dms_client.py`, `dms_admin_router.py`, `customer360_router.py`'s `dms` block,
fork patch `0045`), and the 2026-07-28 demo-feedback audit (items #4, #15,
#16).

---

## 0. What exists today, and what does not

Read this before the questions — it is the honest starting point for the
whole document.

**What we built ("the shell"):** a configuration surface under Settings →
Integrations, gated behind a new `integration.manage` permission:

- A form to store a base URL, an auth type (`bearer_token` / `basic` /
  `api_key_header`), a write-only credential, one optional extra header
  name/value pair, a timeout and a retry count.
- A "Test connection" button that issues one GET and reports
  reachable / auth-failed / timed-out / unexpected-status.
- A `DmsClient` interface (`find_customer`, `list_vehicles`,
  `list_service_history`) with two implementations: a **null client**
  (integration off → returns nothing, exactly like before this package
  existed) and a **mock client** (fixed demo records, every field tagged
  `(Demo data)`, never wired in by default).
- An optional `dms` block on the Customer 360 lookup, rendered as a clearly
  separate "DMS / TSP" section, always absent when the integration is off.

**What does not exist:** any code that talks to a real DMS or TSP system.
There is no field mapping, no API client for a real vendor, no sandbox
connection ever attempted, and no data in this system has ever come from
Proton's DMS. The "Test connection" button proves only that a GET to
whatever URL is configured returns a response — it does not confirm the URL
is a health endpoint, or that the credential format is correct beyond "the
server didn't 401/403 it." If you configure this today and enable it,
Customer 360 will show either "Not connected" or, if you also flip on our
internal mock flag, fabricated demo data clearly marked as such. **Nothing
here reads real vehicle or service data.** We built it this way deliberately
— see §1 for why — and we are raising it explicitly because a different
item from the same demo (item #26, customer-sent video) was a case of a
capability being described to Proton as working when it wasn't. We don't
want the DMS shell to become a second instance of that.

---

## 1. Why we're asking instead of guessing

Demo-feedback item #4 asked for DMS-API integration and was demoed as a
mock; item #15 (Customer 360) and item #16 (which customer identifier to
use) are the two items that most directly depend on the answers below. We
do not have Proton's DMS or TSP API documentation — no endpoints, no auth
scheme, no field names, no environment to test against — so every question
in §2 is one where a wrong guess costs a rebuild, not a config change: our
adapter design intentionally puts all vendor-specific knowledge in one
class (`DmsClient`'s eventual real implementation), and the questions below
are exactly the inputs that class needs before it can be written.

---

## 2. Questions we need answered — ordered by how much they block engineering

### Identity — the single biggest blocker

**Q1. Which key actually joins a CRM contact to a DMS customer?**
This is demo-feedback item #16, raised in the 2026-07-28 demo and still
unanswered ("we need to discuss with Rafael and team"). Options on the
table: a CIF-style customer id, phone number, or vehicle number. **No
adapter can resolve a customer without this** — it is not a detail to work
out later, it is the first thing the real client needs. Our shell's
`find_customer(phone, vehicle_no)` signature reflects what Customer 360
happens to have today, not a confirmed DMS capability.

**Q2. Does the DMS actually support lookup by phone number, by vehicle
number, or only by its own internal id?**
If lookup is only by internal id, phone/vehicle-number search requires an
extra step (e.g. list-and-filter, or a separate index) that changes the
adapter's shape and its latency budget.

**Q3. What is the canonical vehicle-number format?**
Malaysian plates vary in spacing/hyphenation and case. We need to know
whether the DMS stores them normalized (and how) or as free text, so we can
match what the CRM already stores in RSA incidents and the conversation
`vehicle_model` attribute — today those are matched by substring, which is
exactly the weakness this integration is meant to fix.

### Authentication

**Q4. Which of our three auth modes is real: a static API key in a header,
a bearer token, or HTTP Basic — or none of these?**
Our shell supports exactly those three because they cover the common REST
cases; if the real API needs mTLS, OAuth2 client-credentials with token
exchange, or IP allowlisting, that is Phase 2 work we have not started and
did not budget for.

**Q5. If it's a static key, where does it live in the request — a specific
header name, a query parameter, both?** Our shell's fallback ("anything
that isn't `bearer_token`/`basic`") sends `X-Api-Key: <credential>`, which
is a guess, not a confirmed header name.

**Q6. If it's a rotating token or requires a token-exchange step (e.g.
OAuth2), what is that flow, and does the credential we'd store change (a
long-lived client secret vs. a short-lived access token)?** This changes
whether our credential store (one write-only string) is even the right
shape, or whether the adapter needs its own token cache and refresh logic.

### Health / connectivity

**Q7. Is there a dedicated health or ping endpoint we should probe, and
what does a healthy response look like?**
Our "Test connection" button has nothing configurable to point at — there
is no health-path field in our config today — so it GETs `config.base_url`
itself. For a correctly configured server whose root path isn't a health
endpoint, that will most likely return a 404, which our UI already labels
carefully as "unexpected response," not "failed," to avoid implying a bad
credential. We'd rather probe the real thing. If you confirm a health path,
we will add a field for it.

### Record shapes

We defined three of our own types from the vocabulary already used in the
demo deck and Customer 360's design — `DmsCustomer` (`ref`, `name`,
`phone`), `DmsVehicle` (`vehicle_no`, `model`, `purchased_from`) and
`DmsServiceRecord` (`date`, `description`, `dealer`). None of these are
confirmed against a real payload.

**Q8. Customer record — what fields actually exist, and which of `ref`
(a customer id), `name`, `phone` are guaranteed to be present vs. optional?
Is there more than one phone number per customer, and if so, which is
canonical?**

**Q9. Vehicle record — same question for `vehicle_no`, `model`,
`purchased_from` (dealer name). Is there a separate VIN/chassis number
distinct from the registration/plate number, and which one(s) do we need?
Can one DMS customer have multiple vehicles, and is there a practical upper
bound (our fan-out is currently capped at 5 vehicles per customer for
service-history lookups — is that a reasonable cap, or would it hide real
data for some customers)?**

**Q10. Service record — what date format does the API return (ISO 8601?
locale-formatted?), and is `date` the service date or a record-created
date? What does `description` actually contain — free text, or a service
code we'd need to translate? Is `dealer` always present, and is it a name,
a code, or an id we'd need to resolve separately?**

**Q11. Are there other fields on any of these three records that matter for
Customer 360 (e.g. odometer reading, next-service-due date, warranty
status) that our three types don't currently capture?** We'd rather add a
field now than ship an adapter that silently drops something an operator
needs.

### Volume, rate limits, latency

**Q12. What request volume and rate limit should we design for?**
Customer 360 is an interactive lookup — an operator is watching the page
load. Our current lookup budget is the operator-configured timeout (with a
1-second floor) covering the *entire* DMS side-trip — find-customer,
list-vehicles, and up to 5 concurrent service-history calls — as one
window, not one per call. Is that consistent with realistic DMS response
times, or does the real API sometimes take longer for full history?

**Q13. Is there a published or informal rate limit (requests/second,
requests/day) we need to respect, and is it per credential, per IP, or
per tenant?**

**Q14. Should we cache anything, and for how long?** See §3 below — our
current default is "cache nothing," but if the DMS is slow or
rate-limited, a short-TTL cache might be necessary rather than optional,
and that changes the adapter's design meaningfully.

### Environments

**Q15. Is there a sandbox or UAT environment, and can we get test
credentials for it before go-live?** We have never connected to any DMS or
TSP endpoint — sandbox access is what turns Phase 2 from "written against
documentation" into "actually verified," and we'd like it as early as
possible rather than serially with everything else.

**Q16. Does the sandbox return realistic-shaped data (real field
population, not all-nulls), so we can validate the record-shape questions
above against real responses rather than documentation alone?**

### TSP

**Q17. Is TSP (Telematics Service Provider) the same system as the DMS, a
second and separate integration, or out of scope for now?** Everything we
have built and every question above is DMS-shaped — a dealer/service
record system. We have made no assumptions about telematics data (vehicle
location, live status, driving data) and have built nothing toward it. If
TSP is in scope, it is very likely a second integration with its own
auth, its own record shapes, and (per our design spec) its own spec —
telematics streaming is a different problem from a DMS record lookup.

**Q18. Is the integration read-only, or does anything ever need to write
back to the DMS/TSP?** Our shell assumes read-only. If a write path is
needed (e.g. logging a service performed through the CRM back into the
DMS), that is new scope, not an extension of what exists.

---

## 3. Two decisions we made on our side that need your confirmation, not just ours

### D1. `extra_header_value` is stored and returned in plain text

Every other secret in this shell (the credential) is write-only: set it,
replace it, never read it back. The optional extra header pair
(`extra_header_name`/`extra_header_value`) — meant for the tenant/partner
id these APIs often want alongside the main credential — does **not** get
that treatment. `public_dict()` (`dms_config_store.py`) returns it in full
on every GET, and the admin form (fork patch `0045`) displays it as an
editable plain-text field with an inline warning that it is "not secret
storage." This was fine for a tenant id, but if your API needs a *second*
real secret in a custom header (for example, a bearer token in a
non-standard header name alongside an API key), our current schema will
expose it to anyone who can open the Integrations page. **Does the real
auth scheme need a second secret value anywhere?** If yes, we need to widen
the write-only treatment to cover it before this ships for real — that is
a schema change, not a config change, so we would rather know now.

### D2. Retention and PII — what we cache and log today, for you to confirm is correct

**Our current position: we cache nothing, and we log no record content.**
Concretely, verified against the code:

- No lookup result (customer, vehicle, or service record) is ever written
  to our database or any persistent cache. Every Customer 360 request that
  reaches the DMS block re-fetches live; nothing survives past the HTTP
  response.
- Our logging is deliberately minimal: a failed lookup logs the exception
  *type* only (`error_type=type(exc).__name__`), never the exception
  message or any DMS response body; the connection-test probe logs only a
  status and the configured base URL, never the credential or any header
  value.
- The credential itself is write-only end to end — never in a GET
  response, never in a log line, never in an error message, and a
  dedicated FastAPI exception handler strips it from the one place FastAPI
  would otherwise echo it (a malformed-request validation error).

**Confirm this is the right position**, and answer: once real customer
data (name, phone, vehicle, service history) starts flowing back through
this integration, is "display it, cache nothing, log nothing beyond error
type" sufficient for your data-protection requirements, or is there a
retention period we're expected to support (e.g. for audit purposes), or
categories of data (e.g. anything tied to warranty/finance) we should
exclude from Customer 360 entirely?

---

## 4. The ask list — what we need Proton to hand over

Ordered the same as §2:

1. The identifier decision (Q1) — the customer join key. This is the single
   highest-priority item; nothing else in Phase 2 can start without it.
2. API documentation: endpoints, auth scheme, pagination, error codes, rate
   limits (Q2–Q6, Q12–Q13).
3. Confirmation of (or a real) health/ping endpoint (Q7).
4. Field-level documentation (or a sample payload) for customer, vehicle,
   and service-history records, including date formats and which fields
   are optional (Q8–Q11).
5. A sandbox environment and test credentials (Q15–Q16).
6. Whether TSP is in scope as a second integration (Q17), and whether any
   write-back is required (Q18).
7. A yes/no on D1 (does a second secret need to live in a header) and
   confirmation of our D2 retention/logging position.

Items 1 and 5 are on the critical path — no adapter code can be written
against a real API without the identifier decision, and none of it can be
verified without sandbox access.
