# Proton feedback follow-ups (Aug 6 demo) — design

**Date:** 2026-08-07
**Source:** Proton × Devoteam CRM / Email Capability call, 2026-08-06
**Tenant:** `proton`
**Commitment made on the call:** Proton gets self-serve access "early next week
(Monday or Tuesday)" to run the escalation cycle end to end, plus a written
test script.

---

## 1. What this covers

Six items from the call that are partially built or not built. Verified
against the code, not the demo:

| # | Feedback | Status today |
|---|---|---|
| 1 | "Create a group, maintain the participants, mail goes to everybody" (Fauzal/Raphael) | Partial — PIC rows have `pic_email` + `cc_emails[]`; dealer rows are single-email |
| 2 | 24h/48h escalation reminders (Raphael) | Partial — SLA engine + admin UI exist; engine off, WhatsApp-only delivery, single-inbox scope |
| 4 | Auto acknowledgement templates | Built but env-only, no UI |
| 6 | Dealer replies → agent summarizes → emails customer (Nazatul) | **Inbound leg not built** |
| 8 | Agent name visible in the list (Jinny) | Not built |
| 9 | WhatsApp voice notes (Jinny) | Built, flag off, untested |

Already correct and out of scope: AI suggestions from the knowledge base
(`/assist/*` + `/kb/suggest`), the customer never seeing the escalation trail
(EM-7 is two deliberately separate threads), Chatwoot-native macros.

---

## 2. Decisions taken

- **Dealer reply routing:** approach A — correlation token in the mail,
  reply re-ingested through the existing Email inbox. No new mailbox, no new
  credentials, no mail-routing change. Approach B (dedicated escalation
  mailbox polled by the backend) is the documented upgrade path once dealer
  volume makes the throwaway conversations annoying. Approach C (Chatwoot
  native `reply+<uuid>@` threading) is rejected outright: it attaches the
  reply to the *customer's* conversation, which is the exact email trail
  Nazatul said must never reach the customer.
- **Dealer reply handling:** private note + AI-drafted customer reply that a
  human reviews and sends. Nothing reaches the customer unprompted.
- **SLA reminder delivery:** internal email to the department PIC group +
  a private note on the conversation. WhatsApp stays as an optional extra.
  No automatic customer-facing "still working on it" mail.
- **Groups:** re-frame the existing PIC/dealer rows as named groups with
  member lists. No new entity, no migration.
- **Customer ack replies:** in scope (added during review). Same correlation
  machinery, different destination — see §3.3.

---

## 3. Phase 1 — the reply loop (demo-critical)

### 3.1 Outbound: tag the mail

`SmtpEmailSender.send` (`backend/.../features/metrics/email_sender.py`) gains
one optional `reply_to: str | None` argument — not a generic headers bag —
which sets the `Reply-To` header. Everything else about the send path is
unchanged.

New backend setting:

```
escalation_reply_to_template: str = ""   # e.g. "support+case{conv_id}@proton.example"
```

Empty by default, so with no env change today's mail is **byte-identical**.

`EscalationNotifier`:

- `_send_dealer_forward` and `_send_email` (PIC): `Reply-To` set from the
  template, and the subject gains a `[CASE-<conv_id>]` tag as the fallback
  correlation key.
- `_send_customer_ack`: `Reply-To` set from the same template, but **no
  visible subject tag** — the customer's thread stays clean.

### 3.2 Inbound: link the reply

The account webhook does not subscribe to `message_created` today — the
router has no branch and it logs as an unhandled event. Add the subscription
in Chatwoot and a matching branch in `agent/app/routers/chatwoot.py`
dispatching to a new background task, following the established
verify → dedupe → 200-fast → dispatch shape.

New module `agent/app/services/escalation_replies.py`, one entry point
`maybe_link_escalation_reply(payload)`:

1. Return unless this is an **incoming** message on a `Channel::Email` inbox.
2. Extract the case id: `case(\d+)` from `content_attributes.email.to` / `.cc`,
   falling back to `\[CASE-(\d+)\]` in the subject. No token → return silently.
3. Classify the sender:
   - matches a known escalation contact (PIC or dealer) → **internal reply**
   - matches the linked conversation's contact email → **customer reply**
   - neither, or the check cannot be made → skip and log.
4. Strip the quoted trail (`On … wrote:`, `-----Original Message-----`,
   leading `>` blocks) before anything downstream sees it.
5. Apply the destination action (§3.3).
6. Resolve the throwaway conversation and label it `escalation_reply` so it
   drops out of the agent queue.

Sender verification needs the routing table. New lightweight backend endpoint
`GET /escalation/contacts` returns the PIC + dealer address set; the agent
calls it through `ProtonConfigClient`. An unreachable backend means the
membership check cannot be made, so the reply is **not** linked — skipping is
the fail-open behavior here, and it is what stops a customer from injecting a
private note by guessing a conversation id.

### 3.3 Destination actions

**Internal reply (dealer or PIC):**

- Private note on conversation `#N`: `Reply from <name> <email>:` + stripped body.
- Label `dealer_replied`; stamp `dealer_replied_at` first-write-wins, mirroring
  `maybe_stamp_dealer_escalation`'s existing `dealer_escalated_at` treatment.
- Call the backend's existing `/assist/summarize` with the stripped reply plus
  conversation context, and post the result as a second private note prefixed
  `Suggested customer reply (draft — review before sending)`. Gated by
  `escalation_reply_draft_enabled`.

**Customer reply (reply to the acknowledgement):**

- Posted as a **public incoming** message on `#N`, so it reads as the
  customer's own message rather than an agent note.
- This requires an optional `message_type` argument on
  `ChatwootClient.create_message`, which posts outgoing-only today.
- Known and intended side effect: an incoming message reopens the conversation
  and can wake the agent-bot. That is the correct behavior for a customer
  reply.

### 3.4 Idempotency

Two layers, both already in the codebase. `claim_delivery` kills duplicate
webhook deliveries on the `X-Chatwoot-Delivery` key. The first-write-wins
attribute stamp guards a replay arriving under a different delivery id.

### 3.5 Groups

- `DealerRecord.email: str` → `emails: list[str]`. The reader accepts **both**
  shapes, so existing Firestore documents and `DEALER_EMAIL_MAP_JSON` values
  (string or list) keep working with no migration; the writer emits `emails`.
- `build_dealer_email_map` accepts a string or a list per key.
- `_send_dealer_forward` sends to every member.
- `pic_admin_router.py` dealer PUT accepts `emails: list[str]`, still accepting
  `email` for compatibility.
- PIC rows already carry `pic_email` + `cc_emails[]` — no model change.
- Fork patch: re-label the Escalation Routing page in the customer's
  vocabulary ("Group name" / "Members") and give the dealer form the same
  comma-separated members field, reusing the `splitEmails` helper that patch
  `0039` already ships.

### 3.6 New agent settings (all default-off)

```
escalation_reply_linking_enabled: bool = False
escalation_reply_draft_enabled:   bool = False
```

Backend: `escalation_reply_to_template` (§3.1). Every one must be added to
`deploy/tenants/example.env` and, where required at import time,
`agent/tests/conftest.py`.

---

## 4. Phase 2 — the timers

Three changes, in dependency order.

**Scope.** `fetch_conversations` hard-filters to the single
`settings.chatwoot_inbox_id`. With the SLA engine switched on as-is, **email
conversations are never scanned** unless that one env var happens to point at
the Email inbox. A new `sla_inbox_ids` setting (comma-separated; empty = all
inboxes) replaces the single-inbox filter. Without this, feedback #2 cannot be
satisfied for email at all.

**Delivery.** `_build_pic_alert` composes an email leg and a Chatwoot
private-note leg alongside the existing WhatsApp ping. Recipients resolve
through `PicRegistry.lookup` on the conversation's `dept_<slug>` label, reusing
the same routing table operators already edit, falling back to the global
`sla_pic_whatsapp` when no department is present. The alert callback does not
receive per-conversation context today (its docstring says so explicitly); the
cleanest change is to pass the conversation's labels through from
`scan_conversations`, which already has them.

**Configurability.** Surface `escalation_tier2_hours` and the warning-window
minutes on the existing SLA Policies admin page (patch `0025`), since Raphael
asked for those thresholds by name.

New settings `sla_alert_email_enabled` / `sla_alert_note_enabled` default off.
`SLA_ENGINE_ENABLED=true` for the proton tenant is a deploy step, not a code
change.

---

## 5. Phase 3 — the small asks

**Assignee name (#8).** Frontend only. An "Agent" column on the custom Cases
list (patch `0043`) sourced from `meta.assignee.name`, plus the assignee name
rendered as text on the inbox conversation card instead of avatar-only. Keep
the upstream card touch minimal so future rebases stay cheap.

**Voice notes (#9).** Expected to be zero code: flip
`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED`, sanity-check the per-turn byte budget,
send a real voice note, confirm Gemini transcribes it and Chatwoot renders the
player for the agent. Anything broken becomes its own task rather than being
pre-specced blind.

**Templates (#4).** `email_autoack_template` (agent) and
`email_escalation_ack_template` (backend) move behind the tenant-settings store
with env as fallback, like every other operator-editable value, and get two
textareas on the Knowledge Settings messages tab. The agent side rides the
`get_assistant_messages` fetch `lifecycle.py` already makes rather than opening
a second config path.

---

## 6. Phase 0 — spikes, before any code

Both later phases rest on unverified assumptions. Do these first.

1. **Mail round-trip.** Send a tagged mail through the Gmail relay. Confirm
   plus-addressing survives and that the `message_created` webhook actually
   carries `content_attributes.email`. Nothing in this repo reads that field
   today.
2. **Run the existing E2E script** (`docs/testing/2026-08-06-escalation-email-e2e-scenario.md`,
   TC-01 … TC-06). Those outbound legs have never been executed. Building a
   reply loop on an untested forward leg is how Monday goes wrong.

---

## 7. Verification

Agent-side work is unit-tested with `pytest` + `respx` per the existing suite —
no postgres, no live Chatwoot, no Gemini. Cases: token in `to`, token only in
subject, no token, unknown sender, backend unreachable, duplicate delivery,
internal vs customer sender classification, and trail-stripping fixtures taken
from a real Gmail reply and a real Outlook reply. Backend changes get tests
alongside their modules.

The E2E script then grows three cases, and that extended script is also the
deliverable Nazatul asked for ("you just give us the script"):

- **TC-07** — dealer reply links back to `#N` and produces a draft summary.
- **TC-08** — customer replies to the acknowledgement and lands on `#N` as an
  incoming message.
- **TC-09** — an SLA threshold fires an email and a conversation note.

---

## 8. Rollout

Every new setting defaults off, so `default` and `wahchan` tenants stay
byte-identical until someone opts in. `agent` and `backend` build on the VM and
are cheap to iterate. The three fork patches (groups re-frame, assignee column,
template textareas) each need an `amd64` Cloud Build, so they batch into **one**
image build at the end of Phase 3 — never three, never on the VM, never from a
local Mac.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Plus-addressing stripped by the mail provider | `[CASE-<id>]` subject tag fallback; if both fail, fall back to approach B and Phase 1 slips past Tuesday — Phase 0 surfaces this today, not Monday night |
| `content_attributes.email` absent from the webhook payload | Phase 0 spike; subject parsing from message content as a degraded fallback |
| Guessed case id used to inject a note | Sender allowlist via `GET /escalation/contacts`; unverifiable sender → skip |
| Throwaway reply conversations clutter the Email inbox | Auto-resolve + `escalation_reply` label; approach B removes them entirely later |
| Outbound legs still unproven | Phase 0 runs TC-01…TC-06 before Phase 1 starts |
| Chatwoot image built on the wrong architecture | Cloud Build `amd64` only, per `CLAUDE.md` |

---

## 10. Out of scope

A generic Groups entity (rejected in favour of re-framing existing rows), a
dealer-facing portal, and Chatwoot-native macro authoring — that capability
already exists and is empty by design.
