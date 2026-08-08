# P12 — Screen-Pop Customer 360

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p12-screen-pop-customer360.md`
**Closes:** 3 PARTIAL requirements + 2 GAPs (4.44, 4.45) whose blocker is the pop, not the data
**Effort:** 2 weeks · **Wave:** 3
**Open question:** Q4 (is there a DMS/TSP API spec and sandbox?)

---

## 1. The problem, precisely

**Customer 360 exists as a search page with one text box.** Patch `0041` adds a
sidebar page where an operator types a phone or vehicle number and gets a result:
the Chatwoot contact, their conversations across channels, RSA incidents, and an
optional `dms` block that reports `status:"unreachable"` when absent and
`mock:true` when demo data is on.

The requirement is a card that **pops automatically**:

- §3.3.2 "Automated Pop-Up Card" — GAP. No Dashboard App, no conversation-open
  trigger, no screen-pop.
- §4.44 "Auto pop-up card" — GAP.
- §4.45 "Card pops automatically after a customer calls" — GAP. Explicitly
  acknowledged as unbuilt in `docs/proposals/build_proton_proposal_v5.py:1002`.
- §4.46 four sections (personal / vehicle / service / call-centre) — PARTIAL: the
  sections render, but vehicle and service carry demo data only.

An agent answering a call today has to notice the number, switch to another page,
retype it, and search — during the first ten seconds of a conversation with a
customer who is already talking. The data is there; the moment it is needed is
the one moment it is not shown.

**The data behind two of the four sections does not exist**, and no amount of UI
work changes that: `dms_client.py` ships `NullDmsClient` (what every tenant runs)
and `MockDmsClient` (every field stamped `"(Demo data)"`). There is no TSP client
at all — "TSP" is a label on the DMS card. Its own docstring says *"Phase 1 only
— there is no DMS API specification, so nothing here claims to read a real
vendor's data."*

## 2. The design decision this package turns on

**Ship the pop now; be explicit about the empty sections.**

The alternative — wait for the DMS — means §3.3.2, §4.44 and §4.45 stay GAP for
the 6–10 weeks R11 needs after a spec arrives, when the *pop mechanism* is two
weeks and the two sections that do work (personal details and call-centre
history) are the ones an agent uses most on an inbound call.

The risk in shipping early is that a card with two populated sections and two
demo-stamped ones **looks** like a working DMS integration in a demo. So the
design makes the distinction structural rather than cosmetic: an unavailable
section renders an explicit "Not connected — vehicle data requires the DMS
integration" state, and **`MockDmsClient` cannot be enabled on a tenant whose
name is not a sandbox** (see §3.4). A demo-data card shown to a client without
that label is how a proposal ends up claiming an integration that does not exist —
which the gap analysis found had already happened 17 times in the vendor
response.

## 3. Design

### 3.1 The pop mechanism

A **Chatwoot Dashboard App** scoped to the conversation, which Chatwoot renders
in the conversation sidebar and passes the conversation context to. It appears
when the agent opens the conversation — which for an inbound call is the moment
the call connects.

The existing standalone search page stays. It answers a different question
("look up this customer") and operators use it.

Two triggers:

| Trigger | Behaviour |
|---|---|
| Agent opens a conversation | Card renders for that conversation's contact |
| Inbound call rings (P11's bridge) | Conversation is created and auto-opened for the answering agent |

The second is what §4.45 asks for and it depends on P6's assignment: the call is
assigned, the assigned agent's client auto-focuses the conversation. Where
assignment is off (`routing_enabled` defaults false), the card still pops on
open — degraded, not broken.

### 3.2 Identity resolution

`customer360_router.py::_PHONE_RE` already matches a caller number to a Chatwoot
contact, and §4.42 is MET on that basis.

Two improvements the pop needs:

- **Number normalisation.** `+60123456789`, `0123456789` and `60123456789` are
  one customer. Twilio delivers E.164; Chatwoot contacts are entered by humans.
  Without normalisation the pop misses on a customer who exists.
- **A miss is a first-class state**, not an error. An unrecognised caller shows
  "New caller — no prior record", with a create-contact action. That is a common
  and expected case, and rendering it as a failure teaches agents to ignore the
  card.

### 3.3 The four sections (§4.46)

| Section | Source | State today |
|---|---|---|
| Personal | Chatwoot contact | **Real** |
| Vehicle | DMS | **Not connected** |
| Service history | DMS | **Not connected** |
| Call-centre history | Chatwoot conversations + RSA incidents | **Real** |

Each section renders independently and **asynchronously**, which §4.47 asks for
in its "async loading" clause. A slow or absent DMS must not delay the two
sections that are real — the card is useless if it arrives after the agent has
already asked the customer for their name.

Per-section states: `loading` / `ready` / `empty` / `not_connected` / `demo`.
Five states rather than three, because "we have no vehicle on file for this
customer" and "we are not connected to the system that would know" are different
answers, and the agent will say different things to the customer.

**"Insured Name / Vehicle Owner's Name"** (§3.3.2) is not a field anywhere in the
system. It is a DMS field. The section reserves the slot and shows it as
unavailable rather than silently omitting it — an omitted field reads as "this
customer has no insurer".

### 3.4 Demo-data containment

`MockDmsClient` stamps every field `"(Demo data)"`, which is good practice and
insufficient on a screenshot or a projector at ten feet.

- A card containing any mock section renders a **card-level banner**, not only
  per-field suffixes.
- `DMS_MOCK_ENABLED` refuses to activate unless the tenant name matches a
  sandbox pattern, and logs a warning at startup on any other tenant.

The second is the load-bearing one. Per-field labels are a convention someone can
crop out; a startup refusal is not.

### 3.5 PIC from vehicle number (§4.31)

The requirement is to identify the PIC from information solicited from the caller
— dealer, vehicle number. Today PIC comes from a manually applied `dept_*` label
and dealer from a `dealer_*` label. **Nothing derives a PIC from a vehicle
number**, and nothing can without the DMS.

What P12 delivers is the half that does not need it: when P3's
`purchased_from_dealer` is populated on the case, the card surfaces the dealer's
escalation contacts, so an agent escalating has the routing in front of them.

**Vehicle-number → PIC derivation is documented as blocked on Q4**, not
approximated. A PIC derived from a guessed dealer is a complaint sent to the
wrong organisation.

### 3.6 §4.47's latency budget

§4.47 asks for `≤3 s` synchronisation and async loading. Today there is no
latency budget, no measurement, and — with no real DMS — nothing to measure.

P12 adds the measurement and the budget for the sections that exist: each
section's fetch is timed, the card shows a per-section timing in a debug mode, and
a section exceeding `CUSTOMER360_SECTION_TIMEOUT_MS` (default 3000) renders as
`timed_out` rather than hanging.

That makes §4.47 assessable for the real sections and honestly unassessable for
the DMS ones, which is the true position.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| A demo-data card is read as a working DMS integration | Card-level banner; `DMS_MOCK_ENABLED` refuses to start outside a sandbox tenant |
| A slow DMS delays the whole card | Per-section async render with per-section timeout |
| Number-format mismatch makes the pop miss known customers | E.164 normalisation on both sides |
| An unrecognised caller looks like a failure | "New caller" is a designed state with a create action |
| Omitted unavailable fields read as "customer has none" | Slots reserved and marked unavailable |
| A PIC guessed from a partial match misroutes a complaint | Vehicle→PIC derivation not built; documented as blocked on Q4 |
| The card pops for the wrong conversation | Dashboard App is conversation-scoped by construction; asserted by test |

## 5. Testing

- **Resolution** (`test_customer360_identity.py`): the three number formats
  resolve to one contact; an unknown number returns the `new_caller` state;
  vehicle-number lookup unchanged.
- **Sections** (`test_customer360_sections.py`): each renders independently; a
  DMS timeout does not delay personal or call-centre; five states distinguished;
  `not_connected` ≠ `empty`.
- **Demo containment** (`test_dms_mock_guard.py`): mock refuses outside a
  sandbox tenant; the banner renders when any section is mock.
- **Pop** (fork tests): card scoped to the open conversation; does not leak
  another conversation's contact; renders with routing disabled.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `CUSTOMER360_SCREENPOP_ENABLED` | `false` | Off = today's standalone search page only |
| `CUSTOMER360_SECTION_TIMEOUT_MS` | `3000` | Per-section budget |
| `DMS_MOCK_ENABLED` | `false` | Off; refuses to activate outside a sandbox tenant |

## 7. Requirements closed

4.31 (the dealer-contact half), 4.43, 4.46 — plus **3.3.2, 4.44 and 4.45**, which
are GAP today because the pop does not exist rather than because the data does
not.

**Stated limits:** the vehicle and service sections render `not_connected` until
R11 delivers a real DMS adapter (blocked on Q4). §4.47's `≤3 s` is measured and
enforced for the sections that exist and is unassessable for the DMS ones.
Vehicle-number → PIC derivation is not built.
