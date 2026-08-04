# Package G — Escalation Policy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Walk an escalated case through Proton's five-step dealer ladder on the SOP's working-hour timers, addressing the right roles at each step and stopping the moment the dealer responds.

**Architecture:** The ladder is **data, not code** — a policy table of steps with delays and role lists, so an operator can retune timers without an engineer editing conditionals (their step 5 is already labelled "NEW PROCESS", so it will change again). A periodic worker advances due cases **one step per sweep**. All arithmetic runs in working hours through the existing `features/metrics/business_hours.py`.

**Tech Stack:** Python 3.12, FastAPI, Firestore (`PicStore`/`DealerStore` pattern), the existing escalation notifier, pytest.

**Spec:** `docs/superpowers/specs/2026-08-04-pkg-g-escalation-policy-engine-design.md`

## Global Constraints

- **Do not build CC until the client confirms the policy** (spec §4.6, concern C1 in the questions doc). Getting it wrong leaks customer data to a dealer distribution list. Tasks 1-4 are deliberately CC-free.
- **The customer-facing acknowledgement CCs nobody, ever.** This is a privacy invariant, not a preference. Task 4 asserts it as a regression guard.
- **Idempotency is mandatory.** Each step records `sent_at` before sending; a step already stamped is never re-sent. A duplicate escalation to a Dealer Owner is worse than a late one.
- **Advance one step per sweep.** A long outage must not fire steps 3, 4 and 5 within the same minute.
- A missing contact role **skips and logs**, never raises. An incomplete contact matrix is the expected state for months.
- Ship behind `ESCALATION_POLICY_ENABLED`, default off — with it off, behaviour is exactly today's EM-7.
- Reuse `features/metrics/business_hours.py`. Do not add a second calendar.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/chat/escalation_policy.py` | The step table, role resolution, next-due calculation — pure, no I/O |
| `backend/.../features/chat/test_escalation_policy.py` | Its tests |
| `backend/.../features/chat/escalation_case_store.py` | Per-case ladder state, idempotent step stamping |
| `backend/.../features/chat/test_escalation_case_store.py` | Its tests |
| `backend/.../features/chat/escalation_scheduler.py` | The periodic sweep |
| `backend/.../features/chat/test_escalation_scheduler.py` | Its tests |
| `backend/.../features/chat/pic_store.py` | Modify: dealer record gains four contact roles |
| `backend/.../platform/config.py`, `.env.example` | The feature flag |

---

### Task 1: Extend the dealer record to four roles

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/pic_store.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/test_pic_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DealerRecord` gains `contacts: dict[str, str]` keyed by `"cre"`, `"sales_aftersales_mgr"`, `"principal"`, `"owner"`. Task 2 resolves against these exact keys.

- [ ] **Step 1: Write the failing tests**

```python
"""The dealer record grows from one address to four roles. The existing single
address must migrate to `cre`, which is closest to how it is used today, and an
absent role must be readable as absent rather than crashing a lookup."""

from __future__ import annotations

from chatbot.features.chat.pic_store import DealerRecord


def test_legacy_single_email_is_readable_as_the_cre_contact():
    rec = DealerRecord.from_dict({"slug": "ag-plentong", "email": "cre@dealer.my"})
    assert rec.contacts["cre"] == "cre@dealer.my"


def test_absent_roles_are_absent_not_empty_strings():
    rec = DealerRecord.from_dict({"slug": "ag-plentong", "email": "cre@dealer.my"})
    assert "principal" not in rec.contacts
    assert rec.contacts.get("principal") is None


def test_all_four_roles_round_trip():
    payload = {
        "slug": "ag-plentong",
        "contacts": {
            "cre": "cre@d.my",
            "sales_aftersales_mgr": "sam@d.my",
            "principal": "principal@d.my",
            "owner": "owner@d.my",
        },
    }
    rec = DealerRecord.from_dict(payload)
    assert rec.to_dict()["contacts"] == payload["contacts"]


def test_unknown_role_keys_are_dropped_not_stored():
    rec = DealerRecord.from_dict({"slug": "x", "contacts": {"cre": "a@b.my", "wizard": "z@b.my"}})
    assert set(rec.contacts) == {"cre"}
```

- [ ] **Step 2: Run and watch fail.** Expected: `DealerRecord` has no `contacts`.
- [ ] **Step 3: Implement**, keeping the legacy `email` field readable so no data migration is required and existing tenants keep working.
- [ ] **Step 4: Run the full chat suite.** Expected: all PASS, including existing dealer-lookup tests.
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(escalation): dealer record gains CRE, manager, principal and owner roles"
```

---

### Task 2: The policy table and role resolution

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/escalation_policy.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_policy.py`

**Interfaces:**
- Consumes: `DealerRecord.contacts` (Task 1), `features/metrics/business_hours.py`.
- Produces:
  - `@dataclass(frozen=True) class EscalationStep: step_no: int; delay_working_hours: float; to_roles: tuple[str, ...]; cc_roles: tuple[str, ...]; channel: str`
  - `DEFAULT_POLICY: tuple[EscalationStep, ...]` — the five steps from the SOP
  - `def resolve_recipients(step, dealer, pronet) -> tuple[list[str], list[str]]` returning `(to, cc)`, skipping absent roles
  - `def next_due_at(step, from_time, calendar) -> datetime` in working hours
  - Task 3 consumes all of these.

- [ ] **Step 1: Write the failing tests**

Cover, at minimum:

- the default policy has five steps with delays `0`, `2`, `4`, `4`, `8` and step 5's channel is `"phone"`;
- step 1 resolves TO `cre` + `sales_aftersales_mgr`; step 3 TO `principal`; step 4 TO `owner`;
- a dealer missing `principal` yields a **shorter** TO list rather than an exception or an empty-string recipient;
- `next_due_at` for a case escalated Friday 16:00 with a 4-working-hour delay lands on the **next working day**, not Saturday morning;
- a public holiday is skipped;
- `resolve_recipients` returns `cc` as an **empty list** while the CC policy is unconfirmed, so nothing can be sent to a CC list by accident before Task 6.

Write that last test explicitly. It is the mechanism that enforces the "do not build CC yet" constraint rather than relying on discipline.

- [ ] **Step 2: Run and watch fail**, implement, re-run until green.
- [ ] **Step 3: Commit**

```bash
git commit -m "feat(escalation): five-step policy table with working-hour scheduling"
```

---

### Task 3: Per-case ladder state

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/escalation_case_store.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_case_store.py`

**Interfaces:**
- Consumes: `EscalationStep` (Task 2).
- Produces:
  - `@dataclass class EscalationCase: conversation_id: str; dealer_slug: str; current_step: int; step_sent_at: dict[int, datetime]; acknowledged_at: datetime | None; next_due_at: datetime | None`
  - `async def open_case(...) -> EscalationCase`
  - `async def due_cases(now) -> list[EscalationCase]`
  - `async def mark_step_sent(conversation_id, step_no, at) -> bool` — returns `False` if already stamped
  - `async def acknowledge(conversation_id, at) -> None`

- [ ] **Step 1: Write the failing tests** — covering: a second `mark_step_sent` for the same step returns `False` and does not overwrite the timestamp; an acknowledged case never appears in `due_cases`; a case whose next-due is in the future never appears; and `open_case` for an already-open conversation returns the existing case rather than duplicating it.
- [ ] **Step 2: Run, implement, re-run until green.**
- [ ] **Step 3: Commit**

```bash
git commit -m "feat(escalation): idempotent per-case ladder state"
```

---

### Task 4: The scheduler

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/escalation_scheduler.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_scheduler.py`
- Modify: `config.py`, `.env.example`

**Interfaces:**
- Consumes: Tasks 2 and 3, plus the existing escalation notifier for sending.
- Produces: `async def sweep(now) -> SweepReport` with `advanced: int`, `skipped: int`, `dry_run: bool`.

- [ ] **Step 1: Add the settings**

```python
    escalation_policy_enabled: bool = False
    escalation_policy_dry_run: bool = True
```

Note `dry_run` defaults to **true**: enabling the feature must not start emailing dealers on the same change.

- [ ] **Step 2: Write the failing tests**

The critical ones:

```python
async def test_a_long_outage_advances_only_one_step(store, clock):
    # Case due at step 3; the sweep runs 30 hours late.
    report = await sweep(now=clock.plus_hours(30))
    assert report.advanced == 1
```

plus: an acknowledged case is skipped; a case with no due time is skipped; re-running the same sweep sends nothing twice; `dry_run=True` produces a full report and **zero sends**; a send failure leaves the step unstamped so it retries; and with `escalation_policy_enabled=False` the sweep is a no-op.

- [ ] **Step 3: Run, implement, re-run until green.**
- [ ] **Step 4: Assert the privacy invariant**

Add a test that the customer-facing acknowledgement produced anywhere in this flow has **zero CC recipients**. This is a permanent regression guard on the constraint at the top of this plan.

- [ ] **Step 5: Run the full backend suite and commit**

```bash
.venv/bin/pytest src/ -q
git commit -m "feat(escalation): one-step-per-sweep scheduler with dry-run default"
```

---

### Task 5: Dry-run for a full working week

**This is a task, not a formality.** A ladder that emails dealer owners on wrong timers damages a real business relationship, and that is not recoverable with a hotfix.

- [ ] **Step 1:** Deploy with `ESCALATION_POLICY_ENABLED=true` and `ESCALATION_POLICY_DRY_RUN=true` on `proton`.
- [ ] **Step 2:** Every day for five working days, review what would have been sent: recipient, step, timestamp, and the case it belongs to.
- [ ] **Step 3:** Check specifically for weekend and public-holiday misfires, cases advancing while the dealer had already replied, and any step resolving to an empty recipient list.
- [ ] **Step 4:** Only after a clean week, and with the client's agreement, set `ESCALATION_POLICY_DRY_RUN=false`.

---

## Blocked — needs the client meeting first

These cannot be planned without answers; writing steps for them now would be invention.

| Blocked work | Needs |
|---|---|
| **Task 6 — CC lists** | Confirmation of concern **C1**: customer leg CCs nobody, internal legs CC the SOP roles. Until then `resolve_recipients` returns an empty CC list by design (Task 2). |
| **Task 7 — acknowledgement detection** | **Q8**: does any reply into the thread count, only a reply from a known dealer address, or a manual agent action? Whatever the rule, the design must reject auto-replies (match `Auto-Submitted` / `X-Autoreply` headers), surface unknown senders to an agent rather than ignoring them, and allow a manual override — the automatic rule will sometimes be wrong. |
| **Task 8 — PRO-NET internal contacts** | **Q7**: are Area & Regional Managers per-region, i.e. does the correct CC depend on which dealer the case went to? |
| **Task 9 — working-hour definition** | **Q9**: do the weekend operating hours in Proton's own auto-reply text count toward the 2h/4h/8h clocks, and whose public-holiday calendar — national or per state? |
| **Task 10 — telephone step** | **Q10/Q11**: what the CRM does at step 5, and whether it records non-compliance. Design intent is an agent task carrying the number, the case context and the 1-hour deadline, reusing the existing `chatwoot-my-tasks` surface rather than inventing a new one. |
| **Dealer contact matrix data** | **Q6**: four addresses per dealer. Tasks 1-4 are buildable without it, but nothing can be tested end to end until it arrives. |

All six are listed in `docs/analysis/2026-08-05-email-channel-questions-for-proton.md`. Tasks 1-4 can start immediately; they are structure, not content.
