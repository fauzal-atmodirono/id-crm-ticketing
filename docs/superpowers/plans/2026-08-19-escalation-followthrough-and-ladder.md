# Escalation Follow-Through and Dealer Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining PARTIAL items in Proton's email process flow — fix what a live 2026-08-19 run showed the customer, measure the human follow-through after a dealer replies, and walk an escalated case up the SOP's five-step dealer ladder on working-hour timers.

**Architecture:** Three additive layers on the shipped EM-7 flow. Ladder state lives in Chatwoot conversation custom attributes (not a new store) so it shares one source of truth with `escalation_notified_at` / `escalation_replied_at` and gets idempotency by stamping before sending. The ladder itself is a **data table** with a JSON override, swept by a periodic worker modelled on `start_sla_scheduler`. Everything ships behind a flag that is off by default; the ladder additionally defaults to dry-run.

**Tech Stack:** Python 3.12, FastAPI, APScheduler, Firestore (`PicStore`/`DealerStore`), pytest (`asyncio_mode=auto`), respx; Vue 2 for the Chatwoot fork patches.

**Spec:** `docs/superpowers/specs/2026-08-19-escalation-followthrough-and-ladder-design.md`

## Global Constraints

- **Never print, echo, log or commit a credential.** Tenant env values are handed to the operator as commands; they are not applied from this repo.
- **The customer acknowledgement CCs nobody, ever.** Assert it as a regression test in every task that touches the notifier.
- **Every new flag defaults off** and, with it off, both suites must be green and behaviour byte-identical to `fe3eb46`.
- **`ESCALATION_POLICY_DRY_RUN` defaults on** whenever the ladder is enabled. A step must not be able to send mail on the first deploy.
- **Advance one ladder step per sweep.** A long outage must never fire steps 3, 4 and 5 in the same minute.
- **Stamp before send.** A step already stamped is never re-sent; a duplicate escalation to a Dealer Owner is worse than a late one.
- **A missing contact role skips and logs, never raises,** and a CC role is never promoted to TO.
- Every new env var appears in **both** `backend/apps/backend/.env.example` (or `agent/app/config.py`) **and** `deploy/tenants/example.env`.
- Backend tests: `cd backend/apps/backend && uv run pytest src/chatbot/features/<area> -q`. Agent tests: `cd agent && pytest`.
- Work on branch `dev-yuda`. **Never merge to `main`.**

---

## File Structure

| File | Responsibility |
|---|---|
| `agent/app/services/sync.py` | Modify: send a separate clean customer subject alongside the descriptive internal title |
| `backend/.../features/chat/escalation_notifier.py` | Modify: `customer_subject`, ack threading headers, per-step reminder sends |
| `backend/.../features/chat/escalation_router.py` | Modify: accept `customer_subject` / `in_reply_to` on `/escalation/notify` |
| `backend/.../features/metrics/email_sender.py` | Modify: optional `in_reply_to` / `references` headers |
| `backend/.../features/chat/customer_update.py` | **Create:** pure customer-update-clock computation |
| `backend/.../features/chat/escalation_policy.py` | **Create:** the five steps as data; role resolution; next-due arithmetic |
| `backend/.../features/chat/escalation_ladder.py` | **Create:** the sweep + scheduler |
| `backend/.../features/chat/pic_store.py` | Modify: `DealerRecord.contacts` role map, `ProtonNetRecord` |
| `backend/.../features/tasks/deadline.py` | Modify: `customer_update_*` and `attend_after_iso` fields |
| `backend/.../features/tasks/tasks_router.py` | Modify: expose the new fields |
| `backend/.../platform/config.py` | Modify: the new flags |
| `agent/app/services/escalation_replies.py` | Modify: auto-reply suppression, unknown-sender surfacing |
| `deploy/chatwoot-fork/patches/0070-*.patch` | **Create:** Send-to-customer action + dealer role fields + attend-after column |
| `deploy/tenants/example.env` | Modify: every new var, documented |
| `docs/testing/2026-08-19-aftersales-escalation-runbook.md` | **Create:** the scenario runbook |

---

### Task 1: A clean subject on the customer acknowledgement

The customer ack currently reuses the internal escalation title, which is the first 100 characters of the customer's own email cut mid-word. The internal legs keep that title — it is genuinely useful for triage — so the fix is a second, separate subject.

**Files:**
- Modify: `agent/app/services/sync.py` (`_maybe_notify_escalation`), `agent/app/clients/proton.py` (`notify_email_escalation`)
- Modify: `backend/.../features/chat/escalation_router.py` (`_NotifyIn`), `backend/.../features/chat/escalation_notifier.py` (`notify_escalation`, `_send_customer_ack`)
- Test: `agent/tests/test_sync_escalation.py`, `backend/.../features/chat/test_escalation_notifier.py`

**Interfaces:**
- Produces: `_NotifyIn.customer_subject: str | None`; `EscalationNotifier.notify_escalation(..., customer_subject: str | None = None)`. Absent → today's `f"Update on your case: {title}"`, so a pre-upgrade agent service is unaffected.
- Consumes: nothing.

- [ ] **Step 1: Write the failing backend test**

```python
async def test_customer_ack_subject_never_carries_the_message_body():
    notifier, sender = _notifier()
    await notifier.notify_escalation(
        conv_id="42",
        title="Hi, I bought an e.MAS 7 from Proton e.MAS Petaling Jaya last month, plate VAB 3271. The home charger",
        body="…",
        department="aftersales",
        dealer=None,
        customer_email="cust@example.com",
        customer_subject="Update on your case (#42)",
    )
    ack = _leg(sender, to="cust@example.com")
    assert ack.subject == "Update on your case (#42)"
    assert "e.MAS 7" not in ack.subject
    assert ack.cc == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_notifier.py -k customer_ack_subject -q`
Expected: FAIL — `notify_escalation() got an unexpected keyword argument 'customer_subject'`

- [ ] **Step 3: Thread the parameter through the notifier**

`notify_escalation` takes `customer_subject: str | None = None` and passes it to `_send_customer_ack`, which uses it when set and falls back to `f"Update on your case: {title}"` when not.

- [ ] **Step 4: Add the internal-legs regression test**

```python
async def test_internal_legs_keep_the_descriptive_title():
    # the PIC leg's subject still contains the case text and the [CASE-n] tag
```

- [ ] **Step 5: Add `customer_subject` to `_NotifyIn` and pass it to the notifier**

- [ ] **Step 6: Agent side — build and send the clean subject**

In `_maybe_notify_escalation`, alongside the existing `title`, build `customer_subject = f"Update on your case (#{conversation_id})"` and pass it through `notify_email_escalation`.

- [ ] **Step 7: Agent test**

```python
async def test_escalation_posts_a_clean_customer_subject(respx_mock):
    # asserts the JSON body carries customer_subject == "Update on your case (#42)"
    # and that title still carries the message text
```

- [ ] **Step 8: Run both suites**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat -q` and `cd agent && pytest`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "fix(escalation): give the customer acknowledgement its own clean subject"
```

---

### Task 2: The customer-update clock (pure computation)

When a dealer or PIC replies, `escalation_replied_at` is stamped and nothing measures how long the customer then waits. This task builds only the arithmetic; Task 3 wires it in.

**Files:**
- Create: `backend/.../features/chat/customer_update.py`
- Create: `backend/.../features/chat/test_customer_update.py`
- Modify: `backend/.../platform/config.py`

**Interfaces:**
- Produces:
  ```python
  CUSTOMER_UPDATE_DUE_STATE = "CUSTOMER_UPDATE_DUE"
  CUSTOMER_UPDATE_WARNING_STATE = "CUSTOMER_UPDATE_WARNING"

  @dataclass(frozen=True)
  class CustomerUpdateClock:
      due_at: datetime | None
      remaining_seconds: float | None
      breached: bool
      warning_due: bool

  def compute_customer_update_clock(
      conv: dict[str, Any], settings: Settings, now: datetime,
      *, inbox: dict[str, Any] | None = None,
  ) -> CustomerUpdateClock
  ```
- Consumes: `features/metrics/business_hours.working_minutes_between`.

- [ ] **Step 1: Write the failing tests**

```python
def test_clock_starts_at_the_reply_not_at_creation():
    conv = _conv(created_at=_epoch("09:00"), replied_at="13:00")
    clock = compute_customer_update_clock(conv, _settings(hours=4), _at("14:00"))
    assert clock.due_at == _at("17:00") and not clock.breached

def test_no_reply_means_no_clock():
    assert compute_customer_update_clock(_conv(), _settings(), _now()).due_at is None

def test_a_private_note_does_not_clear_the_clock():
    # last_non_activity_message is private -> still due

def test_an_outgoing_public_message_after_the_reply_clears_it():
    # -> due_at is None

def test_warning_fires_at_half_time():
    # 4h window, 2h elapsed -> warning_due is True, breached is False

def test_disabled_flag_yields_an_empty_clock():
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_customer_update.py -q`
Expected: FAIL — module does not exist

- [ ] **Step 3: Add the settings**

```python
escalation_customer_update_enabled: bool = False
escalation_customer_update_hours: float = 4.0
```

- [ ] **Step 4: Implement `customer_update.py`**

Reads `custom_attributes.escalation_replied_at`; the window is `escalation_customer_update_hours`, measured in working hours when the caller passes an `inbox` and working hours are on for it, wall-clock otherwise. "Cleared" means an **outgoing, non-private** message exists with `created_at` later than the reply stamp.

- [ ] **Step 5: Run the tests**

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(escalation): customer-update clock arithmetic"
```

---

### Task 3: Wire the clock into the SLA scan and My-Tasks

**Files:**
- Modify: `backend/.../features/chat/sla.py` (`scan_conversations`), `backend/.../features/tasks/deadline.py`, `backend/.../features/tasks/tasks_router.py`
- Test: `backend/.../features/chat/test_customer_update_wiring.py`, `backend/.../features/tasks/test_deadline.py`

**Interfaces:**
- Consumes: `compute_customer_update_clock`, `CUSTOMER_UPDATE_DUE_STATE` from Task 2.
- Produces: `TaskItem.customer_update_at_iso: str | None`, `TaskItem.customer_update_remaining_seconds: float | None`; JSON keys `customerUpdateAtIso`, `customerUpdateRemainingSeconds`.

- [ ] **Step 1: Write the failing wiring test**

```python
async def test_scan_fires_a_customer_update_breach_once():
    # a conv replied 5 working hours ago with no outgoing public message
    # -> one CUSTOMER_UPDATE_DUE audit entry; a second scan fires nothing
```

- [ ] **Step 2: Write the failing deadline test**

```python
def test_customer_update_never_sets_breach_type():
    item = compute_deadlines(_replied_conv(), _settings(), _now())
    assert item.customer_update_at_iso is not None
    assert item.breach_type is None   # an overdue customer update is not an SLA breach
```

- [ ] **Step 3: Run both and watch them fail**

- [ ] **Step 4: Implement**

In `scan_conversations`, after the existing tier-2 block, compute the clock and `_fire` a `CUSTOMER_UPDATE_DUE` entry, deduped through the same prior-states read as every other breach. In `compute_deadlines`, add the two fields on their own — never folded into `resolution_deadline_iso`, exactly as `follow_up_at` is kept separate. Expose them in `tasks_router`.

- [ ] **Step 5: Run the suites**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat src/chatbot/features/tasks -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(escalation): measure the customer update owed after a dealer reply"
```

---

### Task 4: Verify, then thread the acknowledgement onto the customer's thread

**Verification gate:** before writing any code, confirm Chatwoot populates `source_id` with the RFC Message-ID on inbound email messages. If it does not, **stop, delete this task, and record the finding in the spec's §3.4** — do not fake threading with a synthesised id.

**Files:**
- Modify: `backend/.../features/metrics/email_sender.py`, `backend/.../features/chat/escalation_notifier.py`, `backend/.../features/chat/escalation_router.py`, `agent/app/services/sync.py`
- Test: `backend/.../features/metrics/test_email_sender.py`, `backend/.../features/chat/test_escalation_notifier.py`

**Interfaces:**
- Produces: `SmtpEmailSender.send(..., in_reply_to: str | None = None)` — sets both `In-Reply-To` and `References`; `_NotifyIn.in_reply_to: str | None`.

- [ ] **Step 1: Verify the field**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command='sudo docker exec platform-infra-postgres-1 psql -U postgres -d chatwoot_proton -A -F"|" -c "select id, source_id from messages where inbox_id=4 and message_type=0 order by id desc limit 5;"'
```
Expected: `source_id` values shaped like `<CAF…@mail.gmail.com>`. Empty column → delete this task.

- [ ] **Step 2: Write the failing sender test**

```python
def test_send_sets_in_reply_to_and_references():
    sender.send(to=["c@x"], cc=[], subject="s", body="b", attachments=[],
                in_reply_to="<abc@mail.gmail.com>")
    msg = _sent(sender)
    assert msg["In-Reply-To"] == "<abc@mail.gmail.com>"
    assert msg["References"] == "<abc@mail.gmail.com>"

def test_send_without_in_reply_to_sets_neither_header():
```

- [ ] **Step 3: Run and watch it fail**

- [ ] **Step 4: Implement the header on the sender, plumb `in_reply_to` through notify → ack only**

The PIC and dealer legs must NOT get it: they are new threads to different people and threading them onto the customer's mail would be wrong.

- [ ] **Step 5: Agent side — read the first inbound message's `source_id` and send it**

- [ ] **Step 6: Run both suites, then commit**

```bash
git add -A && git commit -m "fix(escalation): thread the customer acknowledgement onto their own email"
```

---

### Task 5: Dealer contacts gain the SOP's four roles

**Files:**
- Modify: `backend/.../features/chat/pic_store.py`
- Test: `backend/.../features/chat/test_pic_store.py`

**Interfaces:**
- Produces:
  ```python
  DEALER_ROLES = ("cre", "sales_aftersales_mgr", "principal", "owner")

  @dataclass(frozen=True)
  class DealerRecord:
      dealer: str
      emails: list[str]          # retained
      cc_emails: list[str]       # retained
      contacts: dict[str, str]   # role -> email, may be partial
      region: str = ""

      def contact(self, role: str) -> str: ...   # "" when unset

  @dataclass(frozen=True)
  class ProtonNetRecord:
      region: str
      area_regional_mgr: str = ""
      hod: str = ""
  ```
- Consumes: nothing.

- [ ] **Step 1: Write the failing migration test**

```python
def test_a_legacy_group_record_resolves_cre_from_the_first_email():
    rec = dealer_from_dict({"dealer": "kl", "emails": ["cre@kl.my", "x@kl.my"]}, "kl")
    assert rec.contact("cre") == "cre@kl.my"
    assert rec.contact("principal") == ""
    assert rec.emails == ["cre@kl.my", "x@kl.my"]   # the group is not lost

def test_an_explicit_contacts_map_wins_over_the_legacy_list():

def test_an_unknown_role_returns_empty_string_and_does_not_raise():
```

- [ ] **Step 2: Run and watch it fail**

- [ ] **Step 3: Implement `contacts`, `region`, `contact()`, `ProtonNetRecord`, and the read-side migration**

Write-side keeps accepting both shapes, as `_dealer_from_dict` already does for `email` vs `emails`.

- [ ] **Step 4: Run tests, commit**

```bash
git add -A && git commit -m "feat(escalation): dealer records carry the SOP's four contact roles"
```

---

### Task 6: The ladder as data

**Files:**
- Create: `backend/.../features/chat/escalation_policy.py`, `backend/.../features/chat/test_escalation_policy.py`
- Modify: `backend/.../platform/config.py`

**Interfaces:**
- Consumes: `DealerRecord`, `ProtonNetRecord`, `DEALER_ROLES` (Task 5).
- Produces:
  ```python
  @dataclass(frozen=True)
  class EscalationStep:
      step_no: int
      delay_working_hours: float
      to_roles: tuple[str, ...]
      cc_roles: tuple[str, ...]
      template: str
      channel: str              # "email" | "phone"

  DEFAULT_STEPS: tuple[EscalationStep, ...]

  def load_steps(raw_json: str) -> tuple[EscalationStep, ...]
  def resolve_recipients(step, dealer, pronet) -> tuple[list[str], list[str]]  # (to, cc)
  def due_step(steps, elapsed_working_hours, current_step) -> EscalationStep | None
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_default_table_matches_the_sop():
    assert [s.delay_working_hours for s in DEFAULT_STEPS] == [0.0, 2.0, 4.0, 8.0, 8.0]
    assert DEFAULT_STEPS[2].to_roles == ("principal",)
    assert DEFAULT_STEPS[3].to_roles == ("owner",)
    assert DEFAULT_STEPS[4].channel == "phone"

def test_a_missing_role_is_skipped_not_promoted_from_cc():
    to, cc = resolve_recipients(DEFAULT_STEPS[2], _dealer(principal=""), _pronet())
    assert to == []          # skip
    assert "owner@x" not in to   # a CC never becomes a TO

def test_due_step_returns_only_the_next_rung():
    assert due_step(DEFAULT_STEPS, 12.0, current_step=2).step_no == 3   # not 5

def test_malformed_override_json_falls_back_to_the_default_table():
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement the module and add `escalation_policy_steps_json: str = ""`**

- [ ] **Step 4: Run tests, commit**

```bash
git add -A && git commit -m "feat(escalation): the five-step dealer ladder as a policy table"
```

---

### Task 7: The sweep

**Files:**
- Create: `backend/.../features/chat/escalation_ladder.py`, `backend/.../features/chat/test_escalation_ladder.py`
- Modify: `backend/.../platform/config.py`, `backend/.../platform/server.py`

**Interfaces:**
- Consumes: Task 6's `load_steps` / `due_step` / `resolve_recipients`; `features/metrics/sync.fetch_conversations`; `features/metrics/business_hours.working_minutes_between`.
- Produces:
  ```python
  LADDER_STEP_ATTR = "escalation_step"
  def step_sent_attr(step_no: int) -> str        # "escalation_step3_sent_at"
  async def sweep_ladder(conversations, *, settings, dealer_store, pronet_store,
                         notifier, chatwoot_request, audit, now) -> list[dict]
  def start_ladder_scheduler(settings, ...) -> Any | None
  ```

- [ ] **Step 1: Write the failing tests**

```python
async def test_one_step_per_sweep_even_after_a_long_outage():
    conv = _escalated(step=2, notified_hours_ago=40)
    fired = await sweep_ladder([conv], **_deps())
    assert [f["step_no"] for f in fired] == [3]

async def test_a_stamped_step_is_never_resent():
async def test_an_acknowledged_case_halts_the_ladder():
    # escalation_replied_at set -> nothing fires
async def test_a_resolved_case_halts_the_ladder():
async def test_the_stamp_is_written_before_the_send():
    # ordering assertion on the mock call log
async def test_friday_afternoon_does_not_fire_on_saturday():
async def test_dry_run_stamps_nothing_and_sends_nothing():
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Add the flags**

```python
escalation_policy_enabled: bool = False
escalation_policy_dry_run: bool = True
escalation_policy_scan_interval_seconds: int = 300
```

- [ ] **Step 4: Implement `sweep_ladder`**

Filter to conversations carrying `escalate` and `escalation_notified_at`; skip acknowledged / replied / resolved; compute elapsed working hours from the step-1 send; `due_step`; stamp; send. Dry run logs `escalation_ladder_dry_run` with step, TO, CC and the due time, and returns before both the stamp and the send.

- [ ] **Step 5: Implement `start_ladder_scheduler` mirroring `start_sla_scheduler`, wire it into the server lifespan behind the flag**

- [ ] **Step 6: Run tests, commit**

```bash
git add -A && git commit -m "feat(escalation): periodic ladder sweep, one step per pass"
```

---

### Task 8: Reminder sends and the step-5 phone task

**Files:**
- Modify: `backend/.../features/chat/escalation_notifier.py`
- Test: `backend/.../features/chat/test_escalation_reminders.py`

**Interfaces:**
- Consumes: `EscalationStep` (Task 6).
- Produces: `EscalationNotifier.send_ladder_step(conv_id, step, to, cc, title, body) -> tuple[bool, str]`; `EscalationNotifier.raise_phone_task(conv_id, step, contact_name, contact_number, deadline) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_first_reminder_says_what_it_is_and_carries_the_case_tag():
    ok, _ = await notifier.send_ladder_step("42", DEFAULT_STEPS[2], ["dp@kl"], ["owner@kl"], "t", "b")
    msg = _last(sender)
    assert "1ST REMINDER" in msg.subject and "[CASE-42]" in msg.subject
    assert msg.to == ["dp@kl"] and msg.cc == ["owner@kl"]

async def test_the_phone_step_sends_no_mail_and_raises_a_task():
    await notifier.raise_phone_task("42", DEFAULT_STEPS[4], "Dealer Principal", "+60…", _deadline())
    assert sender.sent == []
    note = _last_private_note(chatwoot)
    assert "+60" in note and "1 hour" in note
    assert _custom_attrs(chatwoot)["follow_up_at"] == _deadline().isoformat()
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement both methods**

Reminder bodies name the step, what response is required, and the elapsed working hours. The step-5 note names the Daily Complaint Clause and the 1-hour window.

- [ ] **Step 4: Run tests, commit**

```bash
git add -A && git commit -m "feat(escalation): reminder templates and the step-5 phone task"
```

---

### Task 9: Acknowledgement detection hardening

**Files:**
- Modify: `agent/app/services/escalation_replies.py`
- Test: `agent/tests/test_escalation_replies.py`

**Interfaces:**
- Produces: `is_auto_reply(message: dict) -> bool`; the `escalation_acknowledged_at` attribute an agent can set by hand.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_auto_submitted_reply_is_not_an_acknowledgement():
    assert is_auto_reply({"content_attributes": {"email": {"auto_submitted": "auto-replied"}}})

def test_an_x_autoreply_header_counts_as_an_auto_reply():

async def test_an_auto_reply_is_noted_but_does_not_stamp_the_ack(respx_mock):
    # private note posted; escalation_replied_at NOT set; ladder keeps climbing

async def test_an_unknown_sender_is_surfaced_as_a_note_not_dropped(respx_mock):
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

`is_auto_reply` checks `Auto-Submitted` (anything but `no`), `X-Autoreply`, and `X-Autorespond` in the message's email metadata. An auto-reply still gets a note (an agent should see the dealer is away) but never stamps `escalation_replied_at`, records an acknowledgement, or posts a draft. An unknown sender posts a note naming the address and why it was not linked.

- [ ] **Step 4: Run `cd agent && pytest`, commit**

```bash
git add -A && git commit -m "fix(escalation): an out-of-office is not an acknowledgement"
```

---

### Task 10: Surface `attend_after` in My-Tasks (B-EM-04)

`sync.maybe_stamp_business_hours` already writes `attend_after` from `next_working_instant`; nothing shows it.

**Files:**
- Modify: `backend/.../features/tasks/deadline.py`, `backend/.../features/tasks/tasks_router.py`
- Test: `backend/.../features/tasks/test_deadline.py`

**Interfaces:**
- Produces: `TaskItem.attend_after_iso: str | None`; JSON key `attendAfterIso`.

- [ ] **Step 1: Write the failing test**

```python
def test_attend_after_is_surfaced_and_never_sets_breach_type():
    item = compute_deadlines(_conv(attend_after="2026-08-20T09:00:00+08:00"), _settings(), _now())
    assert item.attend_after_iso == "2026-08-20T09:00:00+08:00"
    assert item.breach_type is None

def test_a_malformed_attend_after_is_ignored():
```

- [ ] **Step 2: Run and watch it fail**

- [ ] **Step 3: Implement, mirroring `_follow_up_at_from_conv`'s parse-and-ignore-garbage discipline**

- [ ] **Step 4: Run tests, commit**

```bash
git add -A && git commit -m "feat(tasks): surface the next-business-hour promise in My-Tasks"
```

---

### Task 11: Fork patch 0070 — one-click send, dealer roles, attend-after

**Files:**
- Create: `deploy/chatwoot-fork/patches/0070-escalation-followthrough.patch`

Three UI changes in one patch, since the Dockerfile globs `patches/*.patch`:
1. A **Send to customer** action on private notes whose body starts with `Suggested customer reply` — loads the draft into the reply composer. It does **not** send; the agent presses send.
2. The Escalation Routing dealer editor gains the four role fields plus region.
3. My-Tasks shows `attendAfterIso` and `customerUpdateAtIso` as their own columns.

- [ ] **Step 1: Read `0039` and `0046` to match the existing Escalation Routing markup**
- [ ] **Step 2: Write the patch**
- [ ] **Step 3: Verify it applies**

```bash
cd deploy/chatwoot-fork && git apply --check patches/0070-escalation-followthrough.patch
```
Expected: no output (per `docs/.../project_chatwoot-fork-patch-network-restriction`, reconstruct from existing patch content — this sandbox cannot clone upstream)

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(fork): send-to-customer action, dealer roles, attend-after column"
```

---

### Task 12: Env, documentation, and the gap-analysis correction

**Files:**
- Modify: `deploy/tenants/example.env`, `README.md`, `docs/analysis/2026-08-08-rfp-2026_028-gap-analysis.md`

- [ ] **Step 1: Document every new var in `example.env`** with its default and a one-line why

```
ESCALATION_CUSTOMER_UPDATE_ENABLED=false
ESCALATION_CUSTOMER_UPDATE_HOURS=4
ESCALATION_POLICY_ENABLED=false
ESCALATION_POLICY_DRY_RUN=true
ESCALATION_POLICY_SCAN_INTERVAL_SECONDS=300
# ESCALATION_POLICY_STEPS_JSON=   # override the SOP table; empty = the default five steps
```

- [ ] **Step 2: Correct B-EM-06 in the gap analysis** — P6 Task 8 shipped explicit-agent reassignment; the PARTIAL is stale. Re-verify with `uv run pytest src/chatbot/features/routing/test_routing_reassign.py -q` and cite it.

- [ ] **Step 3: Run both full suites with every new flag off, then with every new flag on**

Run: `cd backend/apps/backend && uv run pytest -q` then `cd agent && pytest`
Expected: green both ways

- [ ] **Step 4: Commit**

---

### Task 13: The After Sales escalation runbook

**Files:**
- Create: `docs/testing/2026-08-19-aftersales-escalation-runbook.md`

Walks the whole path from the After Sales label, with what to do, what to watch, and what counts as a pass at each rung: intake and auto-ack → `dept_aftersales` + `dealer_<slug>` + `escalate` → the three emails → the PIC reply, the note, the draft, the one-click send → the customer-update clock → steps 3, 4 and 5 with a fast-clock config for testing → what to check in the sidebar and in `docker logs` at each stage. Ends with the operator env commands to switch each piece on, dry-run first.

- [ ] **Step 1: Write it**
- [ ] **Step 2: Commit**

---

## Self-Review

**Spec coverage:** §3.1→T1, §3.2→T2+T3, §3.3→T11, §3.4→T4, §4.4→T5, §4.3→T6, §4.2/4.5→T7, §4.6→T8, ack detection→T9, §5 B-EM-04→T10, §5 B-EM-06→T12 step 2 (verify-only), §4.7 rollout→T7+T12, §6 testing→distributed, runbook→T13. No gaps.

**Type consistency:** `contact(role)` is used in T5 and T6; `DEFAULT_STEPS` indices are 0-based throughout (`DEFAULT_STEPS[2]` is step 3); `escalation_replied_at` is the attribute in T2, T7 and T9; `step_sent_attr(3)` yields `escalation_step3_sent_at` in T7 and the runbook.

**Ordering:** T4 has a hard verification gate that can delete it. T5 precedes T6 precedes T7. T11 needs T2, T5 and T10's field names to already exist.
