# Package D — Demo Data + Per-Case Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed 100 realistic, fully reversible demo customers into the `proton` tenant, and give operators a Cases list showing the fields Proton's own reports use.

**Architecture:** A standalone script under `deploy/scripts/`, not an application feature — it is never wired into the app, never exposed over HTTP, and never shipped in an image. Data is generated from the vocabulary in Proton's own monthly and weekly decks so Package E's reporting has something meaningful to aggregate. Every created object is stamped with a batch marker, and `purge` deletes by that marker only.

**Tech Stack:** Python 3.12, `httpx` against the Chatwoot Application API and the backend `/rsa` endpoints, pytest.

**Spec:** `docs/superpowers/specs/2026-08-04-pkg-d-demo-data-and-case-detail-design.md`

## Global Constraints

- **`purge` must never delete an object lacking the batch marker**, whatever else matches. This is the single most important behaviour in the package.
- **`seed` refuses to run without an explicit `--tenant`.** No default. A fat-fingered invocation must not be able to hit production.
- Deterministic by default (fixed seed), so a re-run produces the same names and plates and a demo script stays valid.
- Phone numbers must come from a **reserved test range** so a demo can never call or message a real person. Emails use `@example.invalid`.
- Rate-limit API calls. A burst of ~1,500 requests at a tenant's Rails app is a self-inflicted outage.
- **Rehearse on `default` before ever running against `proton`.**
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `deploy/scripts/seed_demo_data/generator.py` | Pure data generation — no I/O, fully unit-testable |
| `deploy/scripts/seed_demo_data/client.py` | Chatwoot + RSA API calls, rate limiting |
| `deploy/scripts/seed_demo_data/__main__.py` | CLI: `seed` and `purge` subcommands, confirmation prompt |
| `deploy/scripts/seed_demo_data/test_generator.py` | Distribution, uniqueness, marker tests |
| `deploy/scripts/seed_demo_data/test_purge_safety.py` | The unmarked-object guard |
| `deploy/chatwoot-fork/patches/0043-cases-list.patch` | The Cases list view |

Splitting generation from I/O is what makes the risky part testable without touching a tenant.

---

### Task 1: The data generator

**Files:**
- Create: `deploy/scripts/seed_demo_data/generator.py`
- Create: `deploy/scripts/seed_demo_data/test_generator.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) class DemoContact: name, phone, email, vehicle_no, vehicle_model, purchased_from`
  - `@dataclass(frozen=True) class DemoCase: contact_index, channel, case_type, division, concern, status, created_at, dealer, messages: list[tuple[str, str]]`
  - `def generate(count: int, batch_id: str, seed: int = 20260804) -> tuple[list[DemoContact], list[DemoCase], list[dict]]` returning contacts, cases and RSA incident payloads.
  - Task 2 consumes all three.

- [ ] **Step 1: Write the failing tests**

```python
"""Generated data must match the vocabulary and proportions in the client's own
reports, and must be safe: unique identifiers, non-routable phone numbers."""

from __future__ import annotations

import collections

from generator import generate

MODELS = {"e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "NA"}
DIVISIONS = {"Sales", "After Sales", "Apps", "Charging", "Product", "Marketing", "Others"}
CHANNELS = {"whatsapp", "phone", "email", "social"}


def test_generates_the_requested_number_of_contacts():
    contacts, _, _ = generate(count=100, batch_id="b1")
    assert len(contacts) == 100


def test_phones_and_plates_are_unique():
    contacts, _, _ = generate(count=100, batch_id="b1")
    assert len({c.phone for c in contacts}) == 100
    assert len({c.vehicle_no for c in contacts}) == 100


def test_plates_use_malaysian_format():
    contacts, _, _ = generate(count=20, batch_id="b1")
    for c in contacts:
        head, tail = c.vehicle_no.split(" ")
        assert head.isalpha() and head.isupper()
        assert tail.isdigit() and 1 <= len(tail) <= 4


def test_vocabulary_matches_the_client_reports():
    contacts, cases, _ = generate(count=100, batch_id="b1")
    assert {c.vehicle_model for c in contacts} <= MODELS
    assert {c.division for c in cases} <= DIVISIONS
    assert {c.channel for c in cases} <= CHANNELS


def test_feedback_is_rare_like_the_real_data():
    _, cases, _ = generate(count=100, batch_id="b1")
    counts = collections.Counter(c.case_type for c in cases)
    assert counts["Feedback"] < counts["Complaint"] < counts["Inquiry"]


def test_some_customers_have_more_than_one_case():
    _, cases, _ = generate(count=100, batch_id="b1")
    per_contact = collections.Counter(c.contact_index for c in cases)
    assert max(per_contact.values()) > 1


def test_generation_is_deterministic():
    a, _, _ = generate(count=30, batch_id="b1")
    b, _, _ = generate(count=30, batch_id="b1")
    assert [c.phone for c in a] == [c.phone for c in b]


def test_rsa_incidents_reuse_real_plates():
    contacts, _, incidents = generate(count=100, batch_id="b1")
    plates = {c.vehicle_no for c in contacts}
    assert incidents
    assert all(i["vehicle_no"] in plates for i in incidents)
```

That last test is the one that makes a vehicle-number search return **both** a conversation and an RSA incident — the exact path feedback #15 asked for.

- [ ] **Step 2: Run and watch fail**

Run: `cd deploy/scripts/seed_demo_data && python -m pytest test_generator.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Use `random.Random(seed)` — never the module-level `random`, or determinism breaks when anything else draws. Draw the case-type mix from the June deck's proportions (1024 inquiry / 770 complaint / 17 feedback), the channel mix from the weekly deck (WhatsApp ~73%, phone ~16%, email ~9%, social ~2%), and concerns per division from the decks (Sales → Accessories, Booking, Delivery, Promotion, Trade In, Transfer Ownership; Charging → Home Charging; Apps → Information, Profile, Auto Logout; After Sales → Body, Spare Part, User Manual, Service Operation, ADAS). Spread `created_at` over the last ~8 weeks so aging buckets and week-over-week deltas are non-empty. Prefix every name with `[DEMO]`. Include `batch_id` on every generated record.

- [ ] **Step 4: Run and watch pass**

Run: `python -m pytest test_generator.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/scripts/seed_demo_data/generator.py deploy/scripts/seed_demo_data/test_generator.py
git commit -m "feat(seed): demo data generator matching the client report vocabulary"
```

---

### Task 2: API client with the purge safety guard

**Files:**
- Create: `deploy/scripts/seed_demo_data/client.py`
- Create: `deploy/scripts/seed_demo_data/test_purge_safety.py`

**Interfaces:**
- Consumes: the dataclasses from Task 1.
- Produces:
  - `async def create_contact(contact: DemoContact, batch_id: str) -> int`
  - `async def create_case(case: DemoCase, contact_id: int, batch_id: str) -> int`
  - `async def create_rsa_incident(payload: dict) -> int`
  - `async def purge(batch_id: str) -> PurgeReport`
  - `def selectable_for_purge(objects: list[dict], batch_id: str) -> list[dict]` — pure, so the guard is testable without a tenant.

- [ ] **Step 1: Write the failing tests**

```python
"""The purge guard. This is the highest-consequence code in the package: it runs
against a tenant a client will see."""

from __future__ import annotations

from client import selectable_for_purge

BATCH = "seed-2026-08-04-a"


def test_selects_only_objects_carrying_the_batch_marker():
    objects = [
        {"id": 1, "custom_attributes": {"demo_seed": BATCH}},
        {"id": 2, "custom_attributes": {}},
        {"id": 3, "custom_attributes": {"demo_seed": "some-other-batch"}},
        {"id": 4},
        {"id": 5, "custom_attributes": {"demo_seed": None}},
    ]
    assert [o["id"] for o in selectable_for_purge(objects, BATCH)] == [1]


def test_empty_batch_id_selects_nothing():
    objects = [{"id": 1, "custom_attributes": {"demo_seed": BATCH}}]
    assert selectable_for_purge(objects, "") == []


def test_real_customer_data_is_never_selected():
    objects = [{"id": 99, "name": "A Real Customer", "custom_attributes": {"vehicle_no": "WXY 1234"}}]
    assert selectable_for_purge(objects, BATCH) == []
```

- [ ] **Step 2: Run and watch fail.** Expected: module not found.

- [ ] **Step 3: Implement**

```python
def selectable_for_purge(objects: list[dict], batch_id: str) -> list[dict]:
    """Objects carrying exactly this batch marker, and nothing else.

    Deliberately strict: an empty batch_id selects nothing, and a missing or
    null marker is never a match. Purge runs against a live tenant, so the
    failure mode of deleting too little is recoverable and deleting too much
    is not.
    """
    if not batch_id:
        return []
    return [o for o in objects if (o.get("custom_attributes") or {}).get("demo_seed") == batch_id]
```

Write `create_*` to stamp `custom_attributes.demo_seed = batch_id` on contacts, conversations and RSA incidents alike, and to sleep briefly between calls.

- [ ] **Step 4: Run and watch pass.** Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(seed): API client with a strict purge marker guard"
```

---

### Task 3: The CLI

**Files:**
- Create: `deploy/scripts/seed_demo_data/__main__.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `python -m seed_demo_data seed --tenant proton --count 100` and `... purge --tenant proton --batch <id>`.

- [ ] **Step 1:** Require `--tenant` with no default; exit non-zero if absent.
- [ ] **Step 2:** Print a dry-run summary — counts by type, the batch id, the target tenant and base URL — and require a typed confirmation of the tenant name before writing anything.
- [ ] **Step 3:** Print the batch id prominently on completion. Without it, purge cannot be targeted.
- [ ] **Step 4:** Support `--dry-run` that prints and exits without any write.
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(seed): CLI with mandatory tenant and confirmation"
```

---

### Task 4: Prove it is safe on `default` before touching `proton`

- [ ] **Step 1:** Confirm seeded conversations cannot trigger side effects. Create them in a state the bot ignores (not `pending` on a bot-enabled inbox), and run with `EMAIL_ESCALATION_ENABLED=false`. **100 accidental escalation emails to a real address is the obvious way this goes wrong.**
- [ ] **Step 2:** `seed --tenant default --count 5`. Verify in the UI.
- [ ] **Step 3:** Confirm no AI replies, no outbound email, no WhatsApp sends were generated:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='sudo docker logs default-agent --since 10m 2>&1 | grep -viE "healthz" | tail -20'
```

Expected: no chat-turn or lifecycle activity for the seeded conversations.

- [ ] **Step 4:** `purge --tenant default --batch <id>`. Verify zero remain and that pre-existing `default` data is untouched — count conversations before and after.
- [ ] **Step 5:** Only now run `seed --tenant proton --count 100`.

---

### Task 5: The Cases list view

**Files:**
- Create: `deploy/chatwoot-fork/patches/0043-cases-list.patch`

**Interfaces:**
- Consumes: existing conversation data; no new backend storage.
- Produces: a Cases table Package E reuses rather than building a second one.

- [ ] **Step 1:** Author against upstream `v4.15.1` — the SPA source is not in this checkout, so follow the same clone-and-apply procedure as Package B Task 6.
- [ ] **Step 2:** Build one filterable table with the columns Proton's WIP tables use: Case ID, Division, Concern, Purchased From, Escalated To, Car Plate, Aging (days), Status. Filters: division, case type, status, channel, dealer.
- [ ] **Step 3:** Render blanks for legacy cases lacking `vehicle_no` / `purchased_from` rather than breaking — most existing conversations will not have them.
- [ ] **Step 4:** Verify the patch applies from a clean clone alongside the whole stack.
- [ ] **Step 5: Commit**

```bash
git add deploy/chatwoot-fork/patches/0043-cases-list.patch
git commit -m "feat(chatwoot-fork): Cases list with the client's WIP-table columns"
```

---

### Task 6: Decide the reporting exclusion policy — before seeding proton

**This is a decision task, and it must happen before Task 4 Step 5.**

- [ ] **Step 1:** Package E's metrics sync will ingest seeded rows and inflate every report. Choose one: accept it for the demo window and purge before any real reporting, **or** exclude `demo_seed`-marked conversations in `features/metrics/sync.py`.
- [ ] **Step 2:** Record the decision in the spec's §6 Risks section with its rationale.
- [ ] **Step 3:** If exclusion is chosen, implement it with a test asserting a marked conversation never reaches the warehouse.
- [ ] **Step 4:** Tell Proton the data is synthetic **before** any demo that uses it. The `[DEMO]` name prefix helps but is not a substitute for saying so.
