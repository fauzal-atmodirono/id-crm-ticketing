# P2 — Omnichannel Escalation Delivery

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p2-omnichannel-escalation.md`
**Closes:** 7 PARTIAL requirements + 2 GAPs (B-WA-17, B-SM-09) that are the terminal step of flows this package unblocks
**Effort:** 2 weeks · **Wave:** 1 · **Blocked by:** the customer-acknowledgement wording on chat channels (assumption stated in §3.2)

---

## 1. The problem, precisely

The EM-7 two-thread escalation is, in the gap analysis's words, "the most
complete thing in the build": a customer acknowledgement, a PIC group forward, a
dealer group forward, a `Reply-To` correlation token, a reply linker that pulls
dealer and PIC replies back onto the case with an AI-drafted response, and a
once-per-escalation idempotency guard.

It is reachable only by Email-channel conversations, because of one check in
`agent/app/services/sync.py`:

```python
if (inbox or {}).get("channel_type") != "Channel::Email":
    return
```

Appendix B makes "Escalate email to Dealer/PIC per the Escalation Policy" the
**terminal step of the WhatsApp flow (B-WA-17), the Social flow (B-SM-09) and
the Email flow (B-EM-08)**. WhatsApp is 73% of weekly volume (C2 p1).

So today, an agent handling a WhatsApp complaint applies the `escalate` label,
sees it applied, and **nobody is notified**. There is no error, no warning, and
no log line an operator would recognise as a failure — the function returns
cleanly. This is the worst shape a defect can take: silent, on the busiest
channel, at the last step of the process.

Four smaller defects sit on the same code path:

- **Attachments are never sent.** `SmtpEmailSender.send` takes
  `attachments: list[Attachment]` where `Attachment = tuple[str, bytes, str]`,
  and `escalation_notifier.py` passes `attachments=[]` at lines 209, 284 and
  320 — all three call sites. §3.2.2 and §4.29 both ask for the photos and
  videos to reach the dealer. A complaint about paint damage arrives at the
  dealer as text.
- **CC is half-built.** The PIC leg has it (`cc = list(pic.cc_emails) if
  self._settings.escalation_cc_pic else []`, and `escalation_cc_pic` defaults
  `True`). The **dealer forward and the customer acknowledgement pass `cc=[]`
  unconditionally.** §4.32 asks for CC on the personnel notification; the
  customer thread must never CC anyone. So this is not "build CC" — it is
  "extend CC to the dealer leg, and keep the customer leg clean deliberately".
- **Nobody checks who is on duty.** §4.11 asks for an on-duty check *before*
  escalating. Presence filtering exists and works — in `routing/service.py`,
  for agent assignment. `escalation_notifier.py`, `escalation_router.py` and
  `pic_registry.py` contain zero references to availability. A PIC on leave is
  notified exactly as if they were at their desk.
- **The audit trail records the decision, not the delivery.** `AuditEntry`
  carries `(ticket_id, session_id, actor, from_state, to_state, at, remark)`.
  §3.2.6 names recipient, notification delivery, acknowledgement and
  `sla_status`. `escalation_notifier.py` writes **no audit entry at all** — so
  "we escalated this on the 3rd" is currently unprovable.

## 2. What this package delivers

1. Escalation that fires on **every** channel, with a channel-appropriate
   customer acknowledgement.
2. Attachments populated from the conversation's own media, within a stated
   size budget.
3. CC on the dealer forward; the customer thread stays CC-free by invariant.
4. An on-duty check before a PIC is notified, with a defined fallback.
5. Delivery recorded in the audit trail: recipient, transport, outcome.
6. Send-failure alerting (§4.39, SMTP-level).
7. A risk score on the case (§2.1.3's missing third element).

## 3. Design

### 3.1 Lifting the channel gate

The gate moves from *"is this an Email conversation"* to *"how should the
customer be acknowledged"*. The PIC and dealer legs are already channel-agnostic
— they send email to internal staff regardless of how the customer got in touch,
which is correct and unchanged.

```python
# agent/app/services/sync.py — replacing the early return

CHANNEL_ACK = {
    "Channel::Email":        "email",       # today's behaviour, unchanged
    "Channel::TwilioSms":    "conversation",
    "Channel::Whatsapp":     "conversation",
    "Channel::FacebookPage": "conversation",
    "Channel::Instagram":    "conversation",
    "Channel::WebWidget":    "conversation",
    "Channel::Api":          "conversation",
    "Channel::Voice":        "none",
}
```

Three acknowledgement transports, not one:

| Transport | Used for | Mechanism |
|---|---|---|
| `email` | Email inboxes | Today's `_send_customer_ack` — untouched |
| `conversation` | WhatsApp, Social, Web, API | **Post an outgoing message on the Chatwoot conversation** |
| `none` | Voice | No customer-facing ack; PIC/dealer legs still fire |

**The `conversation` transport is the important design choice, and it is
deliberately the boring one.** The alternative — calling Twilio directly from
the escalation notifier — would need per-channel credentials, per-channel
formatting, a second delivery-failure path, and it would put the customer's
acknowledgement *outside* the conversation transcript. Posting an outgoing
message to the Chatwoot conversation instead means Chatwoot's own channel
adapter delivers it over whatever channel the customer used, the message appears
in the transcript where the agent and every report can see it, and there is
exactly one delivery path to debug. The escalation notifier does not need to
know what WhatsApp is.

Voice gets `none` rather than a guess. A phone caller has no thread to
acknowledge into; if `PHONE_ESCALATION_SMS_ACK` is later wanted it is an
addition, not a rework.

### 3.2 The acknowledgement text (open question)

The email acknowledgement is an operator-editable template resolved through
`_resolve_ack_template` (tenant store → env default). The chat acknowledgement
needs its own text: an email body reads wrong in a WhatsApp bubble, and
Appendix B does not specify one.

**Assumption, stated so it is cheap to correct:** a separate
`escalation_ack_chat_template` key in the same tenant settings store, defaulting
to a short single-paragraph version of the email text. It appears on the same
Knowledge Settings admin page as the other lifecycle messages (patch `0049`
already renders that surface), so changing it is an operator action, not a
deploy.

This is the one item in P2 worth a client question before the wording is
finalised — the mechanism does not change either way.

### 3.3 Attachments

The customer's photos and videos live on Chatwoot messages as
`attachments[].data_url`. The notifier needs bytes.

```python
# features/chat/escalation_attachments.py  (new)

async def collect(
    conv_id: str, *, budget_bytes: int, allowed: set[str]
) -> tuple[list[Attachment], list[str]]:
    """Download conversation attachments for forwarding.

    Returns (attachments, skipped_descriptions). Never raises: a download
    failure yields a skip note, because an escalation that reaches the dealer
    without a photo is far better than one that does not reach them at all.
    """
```

Rules, all of them chosen to keep a failure from becoming a non-delivery:

- **Total budget**, not per-file: `ESCALATION_ATTACHMENT_BUDGET_BYTES`, default
  **10 MiB**, chosen to sit under typical corporate mailbox limits (and well
  under the 14 MiB video budget `WHATSAPP_VIDEO_MAX_BYTES` already uses).
- **Newest first** until the budget is exhausted. A complaint's most recent
  photo is the one being discussed.
- **Anything skipped is named in the email body** — "2 attachments omitted
  (size): IMG_4021.mp4, IMG_4022.mp4" — with the case link. Silent omission of
  evidence is a worse failure than a bounced email.
- **The customer acknowledgement never carries attachments.** It is an
  acknowledgement, not a forward.

### 3.4 CC

- **Dealer forward:** gains `cc` from a new `cc_emails` list on `DealerRecord`,
  mirroring the field `PicEntry` already has, behind
  `escalation_cc_dealer` (default `False` — see below).
- **PIC email:** unchanged; already correct.
- **Customer acknowledgement:** `cc=[]`, permanently, asserted by a regression
  test. **This is a privacy invariant, not a configuration option.** CCing a
  dealer distribution list on a message to a customer leaks the customer's
  identity and complaint to a group they never consented to.

`escalation_cc_dealer` defaults to `False` while `escalation_cc_pic` defaults to
`True`, and the asymmetry is intentional: the PIC CC list is internal PRO-NET
staff, while a dealer CC list reaches an external organisation. Package G's spec
made the same call for the same reason ("getting it wrong leaks customer data to
a dealer distribution list"). The default flips only when the client confirms
the distribution.

### 3.5 On-duty check (§4.11)

`PicRegistry.lookup()` gains an optional presence filter, reusing
`routing/presence.py::PresenceFetcher` rather than reading Chatwoot availability
a second way.

The policy question is what to do when the PIC is offline, and "don't send" is
the wrong answer — an unescalated complaint is worse than one sent to someone
who reads it in the morning. So:

| PIC state | Behaviour |
|---|---|
| Online | Notify normally |
| Offline / busy, department has another online member | Notify the online member, **and** the original PIC; record both recipients |
| Offline, nobody in the department online | Notify anyway, and **mark the case for tier-2 escalation on a shortened timer** |

The third row is the useful one: the on-duty check's value is not suppressing a
notification, it is knowing early that this escalation is likely to sit
unread — which is exactly the condition tier 2 exists for.

### 3.6 Tier 2 must reach someone different (§4.36)

Today `TIER2_ESCALATION` re-alerts the same PIC group unless a distinct
recipient happens to be configured. "Escalation triggers automatic reminder of
the higher-level responsible person" is not satisfied by reminding the same
person.

`PicEntry` gains `escalation_manager_email` / `escalation_manager_whatsapp`. When
unset, tier 2 still fires to the original group — but **logs a warning naming
the department**, and the Escalation Routing admin page (patch `0039`) shows an
unmissable "no tier-2 contact configured" state. An unconfigurable gap becomes a
visible, operator-fixable one.

### 3.7 Delivery in the audit trail (§3.2.6)

`AuditEntry` gains four optional fields — `recipients: list[str]`,
`transport: str`, `delivery_status: str`, `sla_status: str | None` — all
nullable so every existing entry still reads.

`escalation_notifier` writes one entry per leg (`customer_ack`, `pic_email`,
`dealer_forward`), each recording who it went to, over what, and whether the
send succeeded. This is what turns "we escalated this on the 3rd" from an
assertion into a record, and it is the evidence base §3.2.6 asks for.

### 3.8 Send-failure alerting (§4.39)

`SmtpEmailSender.send` swallows and logs all errors by design. That design is
right for the send path and wrong for the operator, who currently has no
surface showing that escalation mail is failing.

P2 adds an operator-visible failure signal: a private note on the conversation
("Escalation email to dealer X failed to send") plus a counter the health
endpoint exposes. **Bounce/DSN handling is out of scope** — it needs a bounce
mailbox, which is client question Q6. The requirement is met at the SMTP level
and the bounce half is stated as pending, rather than claimed.

### 3.9 Risk score (§2.1.3)

§2.1.3 names three things: an escalation matrix (built), resolution timelines
(built), and **risk scoring (does not exist — no field, no score, no model)**.

Deliberately the smallest honest thing: a computed `risk_score` (0–100) on the
case, from signals that already exist — case type (complaint > inquiry),
sentiment (P7's classifier, when present; the complaint label until then), SLA
proximity, reopen count, and escalation depth. Rules-based, in one pure module,
with weights in config so an operator can retune without a deploy.

It is deliberately **not** a model. There is no labelled outcome data to train
one on, and a scored-but-unvalidated model in an escalation path is worse than
an explainable weighted sum that an operator can argue with.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Lifting the gate floods PICs with WhatsApp escalations that were previously silent | Ship behind `ESCALATION_ALL_CHANNELS_ENABLED`, default off; the plan's rollout task enables it per-inbox first |
| The chat acknowledgement posts as a *private note* instead of reaching the customer | Explicit test asserting `private=False` and `message_type=outgoing`; this exact bug already occurred once in this codebase (commit `0aa643d` degraded a customer escalation reply to a private note on 422) |
| A 14 MB video attachment bounces the whole escalation email | Total budget, newest-first, skipped items named in the body |
| The customer ack CCs a dealer list | Permanent `cc=[]` invariant with a regression test |
| Duplicate escalation on a channel where `conversation_updated` fires more often | The existing `escalation_notified_at` guard is channel-agnostic and already covers this |
| The on-duty check suppresses a genuine escalation | It never suppresses — it only adds recipients and shortens the tier-2 timer |

## 5. Testing

- **Gate** (`test_sync_escalation_channels.py`): each channel type routes to the
  right ack transport; voice sends no ack but still fires PIC/dealer; unknown
  channel falls back to `conversation`.
- **Chat ack** (`test_escalation_chat_ack.py`): posts outgoing not private;
  reaches the conversation; template resolved from the tenant store; never CCs.
- **Attachments** (`test_escalation_attachments.py`): budget respected;
  newest-first; skipped items named; a download failure degrades to a note.
- **CC** (`test_escalation_cc.py`): dealer CC behind its flag; customer ack
  `cc=[]` always; PIC CC unchanged.
- **Presence** (`test_escalation_presence.py`): the three-row policy table.
- **Audit** (`test_escalation_audit.py`): one entry per leg, recipients
  recorded, historical entries still read.
- **Idempotency**: the existing once-per-escalation tests re-run against every
  channel.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `ESCALATION_ALL_CHANNELS_ENABLED` | `false` | Off = Email-only, exactly as today |
| `ESCALATION_ATTACHMENTS_ENABLED` | `false` | Off = `attachments=[]`, as today |
| `ESCALATION_ATTACHMENT_BUDGET_BYTES` | `10485760` | Total across all attachments |
| `ESCALATION_CC_DEALER` | `false` | Off = dealer forward has no CC |
| `ESCALATION_ONDUTY_CHECK_ENABLED` | `false` | Off = PIC notified regardless of presence |
| `ESCALATION_RISK_SCORING_ENABLED` | `false` | Off = no `risk_score` written |

## 7. Requirements closed

2.1.3, 3.2.2, 3.2.6, 4.11, 4.29, 4.32, 4.36 — plus **B-WA-17** and **B-SM-09**,
which are GAP today and are the terminal step of the two highest-volume flows in
Appendix B. B-SM-09's channel remains blocked on Meta verification; the
escalation mechanism behind it will be in place and tested.
