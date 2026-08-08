# P2 — Omnichannel Escalation Delivery: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `escalate` label notify somebody on every channel — not just Email — and make the notification carry the evidence, the right recipients, and a record that it happened.

**Architecture:** The PIC and dealer legs are already channel-agnostic and stay untouched. Only the *customer acknowledgement* is channel-specific, and it resolves to one of three transports. Chat acknowledgement is delivered by posting an outgoing Chatwoot message — Chatwoot's own adapter does the channel work, so the escalation notifier never learns what WhatsApp is.

**Tech Stack:** Python 3.12, FastAPI, Firestore (`PicStore`/`DealerStore`), SMTP via `SmtpEmailSender`, the Chatwoot API, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p2-omnichannel-escalation-design.md`

## Global Constraints

- **The customer acknowledgement CCs nobody, ever.** Privacy invariant, not a setting. Task 5 asserts it as a permanent regression guard.
- **The chat acknowledgement must be an outgoing message, never a private note.** This exact bug has already happened in this repo (`0aa643d`). Task 3 asserts `private=False` explicitly.
- **Never suppress an escalation.** The on-duty check adds recipients and shortens the tier-2 timer. It must not be able to stop a send.
- **A failed attachment download degrades to a note in the body**, never to a failed escalation.
- **Idempotency is inherited, not rebuilt.** The `escalation_notified_at` guard is already channel-agnostic; do not add a second guard.
- Every flag defaults off; with all of them off, behaviour is byte-identical to today (Email-only, no attachments, no CC on the dealer leg).
- Env vars go in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `agent/app/services/sync.py` | **Modify.** Replace the `Channel::Email` early return with the ack-transport resolution |
| `agent/tests/test_sync_escalation_channels.py` | **New.** Per-channel routing |
| `backend/.../features/chat/escalation_ack.py` | **New.** The three transports behind one interface |
| `backend/.../features/chat/escalation_attachments.py` | **New.** Download + budget + skip notes |
| `backend/.../features/chat/escalation_notifier.py` | **Modify.** Attachments, dealer CC, audit writes, failure signal |
| `backend/.../features/chat/pic_registry.py` | **Modify.** Optional presence filter, tier-2 manager contact |
| `backend/.../features/chat/pic_store.py` | **Modify.** `DealerRecord.cc_emails`, `PicEntry.escalation_manager_*` |
| `backend/.../features/chat/risk_score.py` | **New.** Pure weighted score |
| `backend/.../features/chat/audit.py` (or its module) | **Modify.** Four nullable fields on `AuditEntry` |
| `deploy/tenants/example.env` | **Modify.** Six settings |

---

### Task 1: Ack-transport resolution (pure)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/escalation_ack.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_ack.py`

**Interfaces:**
- Consumes: a Chatwoot `channel_type` string.
- Produces: `ack_transport(channel_type: str | None) -> Literal["email", "conversation", "none"]`. Task 2 is the only caller.

**Tests first:**

```python
def test_email_channel_resolves_to_the_email_transport():
def test_whatsapp_and_twiliosms_resolve_to_the_conversation_transport():
def test_facebook_and_instagram_resolve_to_the_conversation_transport():
def test_web_widget_and_api_resolve_to_the_conversation_transport():
def test_voice_resolves_to_none():
def test_an_unknown_channel_type_falls_back_to_the_conversation_transport():
def test_a_none_channel_type_falls_back_to_the_conversation_transport():
```

**Implementation notes:** the fallback is `conversation`, not `none`. An unknown channel almost certainly has a conversation thread; falling back to silence would reintroduce the exact defect this package exists to fix.

**Verify:** `uv run pytest src/chatbot/features/chat/test_escalation_ack.py -q`

---

### Task 2: Lift the channel gate

**Files:**
- Modify: `agent/app/services/sync.py`
- Create: `agent/tests/test_sync_escalation_channels.py`
- Modify: `agent/app/config.py`, `deploy/tenants/example.env`, `agent/tests/conftest.py`

**Interfaces:**
- Consumes: `ack_transport` (task 1), `settings.escalation_all_channels_enabled`.
- Produces: `notify_email_escalation(..., ack_transport: str)` — the backend client call gains the resolved transport. Task 3 consumes it.

**Tests first:**

```python
async def test_with_the_flag_off_a_whatsapp_escalation_still_notifies_nobody():
async def test_with_the_flag_off_an_email_escalation_behaves_exactly_as_today():
async def test_with_the_flag_on_a_whatsapp_escalation_reaches_the_backend():
async def test_with_the_flag_on_a_voice_escalation_reaches_the_backend_with_transport_none():
async def test_the_once_per_escalation_guard_holds_on_a_whatsapp_conversation():
async def test_the_guard_is_re_armed_when_the_escalate_label_is_removed_on_any_channel():
async def test_an_inbox_fetch_failure_is_logged_and_skips_without_raising():
```

**Implementation notes:**
- The first two tests are the "ship dark" guarantee — write them first and keep them.
- The existing stamp-guard comment block in `_maybe_notify_email_escalation` explains *why* it stamps after a confirmed send. Preserve that comment; the reasoning is unchanged and it is the kind of comment CLAUDE.md asks to keep.
- Rename `_maybe_notify_email_escalation` → `_maybe_notify_escalation`. The old name becomes actively misleading once the gate is gone.

**Verify:** `cd agent && pytest tests/test_sync_escalation_channels.py -q && pytest -q`

---

### Task 3: The conversation ack transport

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_chat_ack.py`
- Modify: the tenant settings registry (new key `escalation_ack_chat_template`)

**Interfaces:**
- Consumes: `ack_transport` from task 2, the Chatwoot messages API, `get_effective_value` for the template.
- Produces: `_send_chat_ack(conv_id, *, title)`; `notify_email_channel_escalation` renamed `notify_escalation` with an `ack_transport` parameter.

**Tests first:**

```python
async def test_the_chat_ack_posts_an_outgoing_message_not_a_private_note():
async def test_the_chat_ack_posts_to_the_right_conversation():
async def test_the_chat_ack_uses_the_operator_edited_template_when_present():
async def test_the_chat_ack_falls_back_to_the_env_default_when_the_store_errors():
async def test_the_chat_ack_never_sets_cc_or_any_email_field():
async def test_transport_none_sends_no_customer_ack_but_still_sends_pic_and_dealer():
async def test_transport_email_is_byte_identical_to_the_previous_implementation():
async def test_a_chatwoot_422_is_logged_and_does_not_abort_the_pic_and_dealer_legs():
```

**The first test is the one that matters.** `private=False` and `message_type="outgoing"` must both be asserted on the request body, not merely that a POST happened. Commit `0aa643d` in this repo degraded a customer-facing escalation reply to a private note on a 422 — the customer silently received nothing. Assert the payload.

**Verify:** `uv run pytest src/chatbot/features/chat/test_escalation_chat_ack.py -q`

---

### Task 4: Attachments

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/escalation_attachments.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_attachments.py`
- Modify: `escalation_notifier.py` (the PIC and dealer legs only)

**Interfaces:**
- Consumes: the Chatwoot messages API (`attachments[].data_url`), `settings.escalation_attachment_budget_bytes`.
- Produces: `collect(conv_id, *, budget_bytes, allowed) -> tuple[list[Attachment], list[str]]` where `Attachment = tuple[str, bytes, str]` — the type `SmtpEmailSender.send` already takes.

**Tests first:**

```python
async def test_attachments_within_budget_are_all_returned():
async def test_newest_attachments_are_preferred_when_the_budget_is_exceeded():
async def test_skipped_attachments_are_described_not_silently_dropped():
async def test_a_download_failure_yields_a_skip_note_and_does_not_raise():
async def test_a_disallowed_mimetype_is_skipped_with_a_reason():
async def test_a_conversation_with_no_attachments_returns_empty_lists():
async def test_the_customer_ack_never_receives_attachments():
async def test_the_skip_notes_are_appended_to_the_pic_and_dealer_email_body():
async def test_the_flag_off_returns_empty_lists_without_any_http_call():
```

**Implementation notes:** the last test matters for cost — with the flag off there must be no attachment fetch at all, not a fetch whose result is discarded.

**Verify:** `uv run pytest src/chatbot/features/chat/test_escalation_attachments.py -q`

---

### Task 5: CC on the dealer leg, and the customer-ack invariant

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/pic_store.py` (`DealerRecord.cc_emails`)
- Modify: `escalation_notifier.py` (`_send_dealer_forward`)
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_cc.py`

**Interfaces:**
- Consumes: `settings.escalation_cc_dealer`.
- Produces: `DealerRecord.cc_emails: list[str]`, defaulting empty; existing stored records read unchanged.

**Tests first:**

```python
async def test_a_legacy_dealer_record_without_cc_emails_still_loads():
async def test_the_dealer_forward_sends_no_cc_when_the_flag_is_off():
async def test_the_dealer_forward_sends_cc_when_the_flag_is_on_and_cc_emails_exist():
async def test_the_pic_leg_cc_behaviour_is_unchanged():
async def test_the_customer_ack_cc_is_empty_with_every_flag_combination():   # invariant
async def test_a_cc_address_equal_to_the_customer_address_is_dropped_from_the_dealer_cc():
```

**The fifth test iterates every combination of the six P2 flags** and asserts `cc=[]` on the customer leg in all of them. It is a permanent guard, not a one-off check.

The sixth test prevents a subtle leak: if a dealer's CC list happens to contain the customer's own address, the dealer forward (which contains the full transcript) would reach the customer. Drop it and log.

**Verify:** `uv run pytest src/chatbot/features/chat/test_escalation_cc.py -q`

---

### Task 6: On-duty check

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/pic_registry.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_presence.py`

**Interfaces:**
- Consumes: `routing/presence.py::PresenceFetcher` (reuse — do not read Chatwoot availability a second way).
- Produces: `PicRegistry.lookup(department, *, presence: PresenceFetcher | None)` returning the resolved contacts plus an `all_offline: bool` that task 7 reads for the shortened tier-2 timer.

**Tests first:**

```python
async def test_an_online_pic_is_notified_normally():
async def test_an_offline_pic_with_an_online_colleague_notifies_both():
async def test_an_entirely_offline_department_is_still_notified():
async def test_an_entirely_offline_department_sets_all_offline_true():
async def test_a_presence_fetch_failure_falls_back_to_notifying_everyone():
async def test_the_flag_off_skips_presence_entirely_and_makes_no_api_call():
async def test_the_check_can_never_return_an_empty_recipient_list():
```

**The last test is the safety property**: whatever the presence data says, `lookup` must never reduce the recipient list to nothing. An unescalated complaint is the failure mode this whole package exists to eliminate.

**Verify:** `uv run pytest src/chatbot/features/chat/test_escalation_presence.py -q`

---

### Task 7: Tier-2 reaches someone different

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/pic_store.py` (`PicEntry.escalation_manager_email`, `escalation_manager_whatsapp`)
- Modify: `backend/apps/backend/src/chatbot/features/chat/sla.py` (the `TIER2_ESCALATION` recipient resolution)
- Modify: the Escalation Routing admin page (fork patch, new patch number after `0050`)

**Interfaces:**
- Consumes: `all_offline` from task 6, `settings.escalation_tier2_hours`.
- Produces: tier-2 alerts addressed to the manager contact when configured.

**Tests first:**

```python
async def test_tier2_goes_to_the_manager_contact_when_configured():
async def test_tier2_falls_back_to_the_original_group_when_unconfigured():
async def test_the_unconfigured_fallback_logs_a_warning_naming_the_department():
async def test_an_all_offline_department_shortens_the_tier2_timer():
async def test_a_legacy_pic_entry_without_manager_fields_still_loads():
```

**Verify:** `uv run pytest src/chatbot/features/chat/ -q`

---

### Task 8: Delivery in the audit trail

**Files:**
- Modify: the `AuditEntry` dataclass and its Firestore adapter
- Modify: `escalation_notifier.py` — one entry per leg
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_audit.py`

**Interfaces:**
- Consumes: the existing `AuditLogPort`.
- Produces: `AuditEntry.recipients: list[str] | None`, `.transport: str | None`, `.delivery_status: str | None`, `.sla_status: str | None`. All nullable.

**Tests first:**

```python
async def test_every_existing_audit_entry_still_deserialises():
async def test_a_successful_pic_send_records_recipients_and_transport():
async def test_a_failed_send_records_delivery_status_failed():
async def test_all_three_legs_produce_three_distinct_entries():
async def test_the_customer_ack_entry_does_not_record_the_dealer_recipients():
async def test_the_audit_write_failing_does_not_abort_the_send():
```

**The last test states the priority order plainly:** recording the escalation matters less than making it. An audit-store outage must not stop a complaint reaching a dealer.

**Verify:** `uv run pytest src/chatbot/features/chat/test_escalation_audit.py -q`

---

### Task 9: Send-failure signal (§4.39, SMTP level)

**Files:**
- Modify: `escalation_notifier.py`
- Modify: `backend/.../platform/health.py` (or the equivalent health surface)
- Create: `backend/apps/backend/src/chatbot/features/chat/test_escalation_failure_signal.py`

**Interfaces:**
- Consumes: the `delivery_status` from task 8.
- Produces: a private note on the conversation and a counter on the health endpoint.

**Tests first:**

```python
async def test_a_send_failure_posts_a_private_note_naming_the_recipient():
async def test_a_successful_send_posts_no_note():
async def test_the_failure_note_is_private_and_never_reaches_the_customer():
async def test_repeated_failures_increment_the_health_counter():
async def test_the_note_write_failing_is_swallowed():
```

**Scope note to carry into the docs:** this closes the *SMTP send-failure* half of §4.39. Bounce and invalid-recipient DSN handling needs a bounce mailbox — client question Q6 — and must be reported as pending, not claimed.

**Verify:** `uv run pytest src/chatbot/features/chat/test_escalation_failure_signal.py -q`

---

### Task 10: Risk score (§2.1.3)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/risk_score.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_risk_score.py`

**Interfaces:**
- Consumes: case type, complaint label (sentiment when P7 lands), SLA proximity, reopen count, escalation depth. Pure — no I/O.
- Produces: `score(signals: RiskSignals) -> int` in 0–100, plus `RISK_WEIGHTS` read from config.

**Tests first:**

```python
def test_a_fresh_inquiry_scores_low():
def test_a_reopened_complaint_near_its_sla_deadline_scores_high():
def test_the_score_is_clamped_to_0_100():
def test_missing_signals_degrade_gracefully_rather_than_raising():
def test_weights_are_configurable_without_a_code_change():
def test_the_score_is_deterministic_for_identical_signals():
def test_the_contribution_of_each_signal_is_reportable():   # explainability
```

**The last test is the design commitment.** This is a weighted sum precisely so an operator can be told *why* a case scored 82. Do not replace it with a model; there is no labelled outcome data to train one on.

**Verify:** `uv run pytest src/chatbot/features/chat/test_risk_score.py -q`

---

### Task 11: Flags, env, rollout note

**Files:**
- Modify: `deploy/tenants/example.env`, both `config.py` files, `agent/tests/conftest.py`
- Modify: `README.md`

**Tests first:**

```python
def test_all_six_settings_are_present_in_example_env():
def test_all_six_settings_default_off_or_to_the_documented_value():
def test_both_services_start_with_none_of_the_new_vars_set():
```

**Rollout note (the deliverable):**

> `ESCALATION_ALL_CHANNELS_ENABLED` changes who gets email. Before switching it
> on for a tenant, confirm the PIC and dealer contact matrix is current — with
> the gate lifted, WhatsApp escalations (73% of volume) start reaching the same
> addresses that previously only received Email-channel escalations. Enable on
> one inbox first and watch a full working day.

**Verify:** both suites green with all flags off, then all flags on.

---

## Definition of done

- [ ] All six flags off → both suites green, behaviour identical to `d85f0d4`.
- [ ] All six flags on → both suites green.
- [ ] A WhatsApp `escalate` label demonstrably notifies the PIC and the dealer, and acknowledges the customer **in the WhatsApp thread**.
- [ ] The customer-ack `cc=[]` invariant test passes across every flag combination.
- [ ] The on-duty check provably cannot empty the recipient list.
- [ ] Three audit entries per escalation, with recipients and transport.
- [ ] §4.39 documented as SMTP-level only, bounce handling pending Q6.
- [ ] Nothing merged to `main`.
