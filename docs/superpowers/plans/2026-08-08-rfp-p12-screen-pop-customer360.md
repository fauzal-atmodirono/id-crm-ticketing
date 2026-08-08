# P12 — Screen-Pop Customer 360: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the customer's record in front of the agent at the moment the call connects — with the two sections that are real clearly separated from the two that are waiting on a DMS that does not exist yet.

**Architecture:** A conversation-scoped Chatwoot Dashboard App, with each of the four sections fetched and rendered independently so an absent DMS cannot delay the sections that work. The existing standalone search page stays — it answers a different question.

**Tech Stack:** Chatwoot Dashboard App + fork patch, FastAPI, the existing `customer360_router.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p12-screen-pop-customer360-design.md`

## Global Constraints

- **A demo-data card must be unmistakable at ten feet.** Per-field `"(Demo data)"` suffixes are not enough on a projector or a cropped screenshot. Card-level banner **and** a startup guard.
- **`not_connected` is not `empty`.** "We have no vehicle on file" and "we are not connected to the system that would know" are different answers and the agent says different things to the customer.
- **A slow or absent DMS must never delay the personal and call-centre sections.** The card is useless if it arrives after the agent has already asked for the customer's name.
- **An unrecognised caller is a designed state, not an error.** It is common and expected.
- **Do not derive a PIC from a vehicle number.** It cannot be done without the DMS, and a guessed PIC sends a complaint to the wrong organisation. Blocked on Q4.
- Env vars in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/chat/customer360_router.py` | **Modify.** Per-section endpoints, states, timings |
| `backend/.../features/chat/phone_number.py` | **New.** E.164 normalisation |
| `backend/.../features/chat/dms_client.py` | **Modify.** Sandbox guard on the mock |
| `deploy/chatwoot-fork/patches/00NN-customer360-screenpop.patch` | **New.** The Dashboard App |
| `deploy/tenants/example.env` | **Modify.** Three settings |

---

### Task 1: Phone-number normalisation

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone_number.py`
- Create: its test file

**Interfaces:**
- Consumes: a raw number from Twilio (E.164) or a Chatwoot contact (human-entered).
- Produces: `normalise(raw, *, default_region="MY") -> str | None` in E.164.

**Tests first:**

```python
def test_plus_60123456789_normalises_to_e164():
def test_0123456789_normalises_to_the_same_e164():
def test_60123456789_normalises_to_the_same_e164():
def test_spaces_and_dashes_are_ignored():
def test_an_unparseable_string_returns_none_rather_than_a_guess():
def test_a_non_malaysian_number_with_a_country_code_is_preserved():
def test_normalisation_is_idempotent():
```

**Test five matters:** returning a mangled best-effort number would resolve to
the wrong contact and show one customer's history to another. `None` is the safe
answer, and it produces the `new_caller` state.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_phone_number.py -q`

---

### Task 2: Per-section endpoints and states

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/customer360_router.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_customer360_sections.py`

**Interfaces:**
- Consumes: the Chatwoot contact API, conversations, `rsa_incidents`, `dms_client`.
- Produces: `GET /admin/customer360/conversation/{id}` returning four sections, each with a state ∈ `loading|ready|empty|not_connected|demo|timed_out` and a fetch duration.

**Tests first:**

```python
async def test_the_personal_section_renders_from_the_chatwoot_contact():
async def test_the_call_centre_section_renders_conversations_and_rsa_incidents():
async def test_the_vehicle_section_reports_not_connected_with_a_null_dms_client():
async def test_the_service_section_reports_not_connected_with_a_null_dms_client():
async def test_not_connected_is_distinguishable_from_empty():
async def test_a_dms_timeout_renders_timed_out_and_does_not_delay_the_other_sections():
async def test_each_section_reports_its_fetch_duration():
async def test_a_section_exceeding_the_timeout_does_not_block_the_response():
async def test_an_unknown_contact_returns_the_new_caller_state():
async def test_the_insured_name_slot_is_present_and_marked_unavailable():
```

**Test six is the design's central behaviour** — assert it with a DMS stub that
sleeps past the timeout, and assert the personal section still returned promptly.

Test ten: an omitted field reads as "this customer has no insurer". Reserve the
slot.

**Verify:** `uv run pytest src/chatbot/features/chat/test_customer360_sections.py -q`

---

### Task 3: Demo-data containment

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/dms_client.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_dms_mock_guard.py`

**Interfaces:**
- Consumes: `DMS_MOCK_ENABLED`, the tenant name.
- Produces: a startup refusal outside sandbox tenants; a `demo` state on any mock-sourced section.

**Tests first:**

```python
def test_the_mock_client_activates_on_a_sandbox_tenant():
def test_the_mock_client_refuses_to_activate_on_a_non_sandbox_tenant():
def test_the_refusal_logs_a_warning_naming_the_tenant():
def test_a_mock_sourced_section_reports_the_demo_state():
async def test_a_card_with_any_demo_section_sets_a_card_level_banner_flag():
def test_the_existing_per_field_demo_data_suffix_is_retained():
```

**Test two is the load-bearing guard.** Per-field labels are a convention someone
can crop out of a screenshot; a startup refusal is not. This is the mechanism
that prevents a demo card being read as a working integration — the failure mode
the gap analysis found had already occurred repeatedly in the vendor response.

**Verify:** `uv run pytest src/chatbot/features/chat/test_dms_mock_guard.py -q`

---

### Task 4: The Dashboard App

**Files:**
- Create: `deploy/chatwoot-fork/patches/00NN-customer360-screenpop.patch`

**Interfaces:**
- Consumes: task 2's endpoint, the Chatwoot conversation context.
- Produces: a conversation-scoped sidebar card rendering the four sections asynchronously.

**Tests first:**

```python
def test_the_patch_applies_cleanly_onto_the_pinned_upstream_ref():
def test_the_card_renders_for_the_open_conversation_only():
def test_the_card_never_shows_another_conversations_contact():
def test_each_section_renders_as_it_arrives_rather_than_waiting_for_all_four():
def test_a_not_connected_section_renders_its_explanatory_state():
def test_the_demo_banner_renders_when_any_section_is_demo():
def test_the_new_caller_state_offers_a_create_contact_action():
def test_the_standalone_search_page_still_works_unchanged():
```

**Test three is a privacy property.** A card scoped to the wrong conversation
shows one customer's history to an agent handling another — assert it explicitly
rather than relying on the framework.

Test eight: the search page answers a different question and operators use it.

**Fork-patch note:** reconstruct from patch `0041`'s structure (the existing
Customer 360 page) — closest analogue and it already talks to this router. Build
via Cloud Build for `amd64`; never on the prod VM, never from an arm64 Mac.

**Verify:** patch applies; manual verification on a scratch tenant with
screenshots of all five section states.

---

### Task 5: Auto-focus on inbound call

**Files:**
- Modify: the patch from task 4
- Modify: P11's call-connect path

**Interfaces:**
- Consumes: the conversation created on call connect, P6's assignment.
- Produces: the assigned agent's client focuses that conversation.

**Tests first:**

```python
async def test_an_inbound_call_creates_a_conversation_and_focuses_it_for_the_assigned_agent():
async def test_no_other_agents_client_is_focused():
async def test_with_routing_disabled_the_card_still_pops_when_an_agent_opens_the_conversation():
async def test_an_agent_already_typing_in_another_conversation_is_not_interrupted():
async def test_the_flag_off_creates_the_conversation_without_focusing():
```

**Test four is a real usability trap:** stealing focus from an agent mid-reply to
another customer is worse than making them click. Queue the pop as a prominent
notification instead — P9's toast — and let them take it.

**Verify:** manual, on a scratch tenant, with a real inbound call if P11 task 9 is available.

---

### Task 6: Dealer escalation contacts on the card (§4.31, partial)

**Files:**
- Modify: `customer360_router.py`
- Create: its test

**Tests first:**

```python
async def test_a_case_with_purchased_from_dealer_surfaces_that_dealers_contacts():
async def test_a_case_without_it_shows_no_dealer_contacts_rather_than_a_guess():
async def test_the_contacts_come_from_dealer_store_not_from_env():
async def test_no_pic_is_derived_from_a_vehicle_number():        # deliberate non-feature
```

**Test four asserts a non-feature deliberately**, so a future contributor who
"helpfully" adds vehicle→PIC inference has to read why it is blocked on Q4. A PIC
derived from a guessed dealer sends a complaint to the wrong organisation.

Depends on P3 for `purchased_from_dealer`.

**Verify:** `uv run pytest src/chatbot/features/chat/test_customer360_dealer.py -q`

---

### Task 7: Flags, env, docs

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`
- Modify: `README.md`

**Tests first:**

```python
def test_the_three_settings_are_present_in_example_env():
def test_customer360_section_timeout_defaults_to_3000():
def test_dms_mock_enabled_defaults_to_false():
def test_the_service_starts_with_none_of_them_set():
```

**Docs note (the deliverable):**

> The Customer 360 card shows **four sections. Two are live** — personal details
> and call-centre history, both from CRM data. **Two report "Not connected"** —
> vehicle and service history, which require a DMS/TSP integration that does not
> exist: there is no API specification and no sandbox (client question Q4).
>
> "Not connected" is deliberately distinct from "no records". The first means the
> system that would know is not integrated; the second means it was asked and had
> nothing.
>
> `DMS_MOCK_ENABLED` populates those sections with demo data and **refuses to
> activate outside a sandbox tenant**. A card containing demo data shows a
> card-level banner. Do not screenshot a demo card for client material.

**Verify:** suite green with flags off, then on.

---

## Definition of done

- [ ] All three flags off → suite green, behaviour identical to `d85f0d4`.
- [ ] The card pops on conversation open and renders four sections asynchronously.
- [ ] A DMS timeout provably does not delay the personal or call-centre sections.
- [ ] `not_connected`, `empty`, `demo` and `timed_out` visually distinct; screenshots recorded.
- [ ] The mock client refuses to start outside a sandbox tenant.
- [ ] Three phone-number formats resolve to one contact; an unparseable number yields `new_caller`, never a wrong match.
- [ ] The card never shows another conversation's contact.
- [ ] An agent mid-reply is not focus-stolen.
- [ ] No vehicle-number → PIC derivation exists anywhere.
- [ ] Nothing merged to `main`.
