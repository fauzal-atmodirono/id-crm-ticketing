# How to test what was built on 2026-08-09 (P1 + P2 wave-1)

Everything here is **committed to `dev-yuda` and off by default**. With every
new flag unset, the platform behaves exactly as it did before — that is
asserted, not assumed. Nothing below has been enabled on a live tenant.

Branch state: `33df8ef..abb935a`, four commits. Suites: agent **389 passed**,
backend **1925 passed / 1 skipped**.

---

## 0. Before anything else — one live change I already made

I resolved and labelled **23 bounce conversations** on the proton Email inbox
(`bounce` label, status resolved). They were Gmail delivery-failure notices for
`proton.demo@demo.com`, a domain whose mail server does not answer. They were
sitting open in the agent queue and inflating the SLA backlog.

The address appears in **no** contact, user, env var, routing record or
automation rule in any of the three tenants, and the messages that triggered
them were sent by a Chatwoot container that no longer exists — so this was
residual test traffic, not a live bug. Sends had already stopped.

**Check it looks right:** open the proton Email inbox. You should see ~10 real
open conversations, not 33. Nothing was deleted; filter by the `bounce` label
to see what was tidied.

---

## 1. Run the suites (2 minutes, no infrastructure needed)

```bash
cd agent && .venv/bin/python -m pytest -q
cd backend/apps/backend && GOOGLE_API_KEY=test-key uv run pytest -q
```

`GOOGLE_API_KEY` must be set to *something* or 5 backend modules fail to
import — `google.genai.Client()` demands a key at import time. Any string works.

Then confirm the ship-dark guarantee, which is the whole safety argument:

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-key SLA_WORKING_HOURS_ENABLED=true \
  SLA_ACKNOWLEDGEMENT_ENABLED=true uv run pytest -q
```

Both states must be green. They are as of this commit.

---

## 2. P1 — the working-hours SLA clock

**What it fixes:** SLA *reporting* measured working hours; SLA *enforcement*
measured the wall clock. The two halves contradicted each other, and they would
have done so in front of PRO-NET.

### The one sentence that matters before you enable it

> With `SLA_WORKING_HOURS_ENABLED` on, a target of "2 hours" means **2 working
> hours**. Your existing targets do not need changing — they were always meant
> as working-hours targets. A case arriving 18:00 Friday breaches a 2-hour
> target **Monday morning**, not Friday evening.

### Enable on proton

Edit `deploy/tenants/proton.env`, then rebuild:

```bash
SLA_WORKING_HOURS_ENABLED=true
```

**Prerequisite that is easy to miss:** inbox 4 must have real working hours
configured in Chatwoot (Settings → Inboxes → Business Hours). An inbox with
working hours *disabled* falls back to calendar minutes and behaves identically
flag-on or flag-off — so if nothing changes, check that first.

Appendix B's hours, which the inbox config should match:
Mon–Fri **08:30–17:30**, Sat/Sun/public holidays **09:00–17:00**.

### What you should observe

- **Breach volume drops, and breaches arrive later.** Nothing should ever
  breach *sooner* than before. If breach volume is unchanged, the inbox has no
  working-hours config.
- Roll back by setting it to `false`. The off path is byte-identical.

### The after-hours reporting

Two new BigQuery views (`v_volume_after_hours`,
`v_first_response_by_hours_split`) and `GET /metrics/after-hours`. These need
`ensure_views()` to run against real BigQuery — **not yet done**, all
development was against the mocked adapter.

Three buckets, never two: `in_hours` / `after_hours` / `unknown`. Every row
synced before today has no intake stamp and is `unknown` — that is deliberate.
Counting unmeasured history as after-hours would invent an out-of-hours problem
you may not have.

To start populating the flag, set `BUSINESS_HOURS_STAMP_ENABLED=true` (agent).
It stamps at intake and never recomputes.

### The acknowledgement event

Two flags, and **neither does anything alone** — the first reads a signal the
second writes:

| Flag | Service | Effect |
|---|---|---|
| `ESCALATION_REPLY_ACKNOWLEDGEMENT_ENABLED` | agent | writes `ACKNOWLEDGED` when a PIC/dealer reply is linked |
| `SLA_ACKNOWLEDGEMENT_ENABLED` | backend | lets that satisfy the first-response SLA |

**Test:** with both on, escalate an Email case, reply to the escalation mail
from an allowlisted PIC address, then check `GET /admin/audit` (permission
`audit.view`) for an `ACKNOWLEDGED` row on that ticket. The case should stop
firing `SLA_BREACH_NO_RESPONSE` but must **still** fire
`SLA_BREACH_UNRESOLVED` if left unresolved — acknowledging stops the ack clock
and nothing else. That distinction is the requirement.

---

## 3. P2 — escalation on every channel

**What it fixes, and it is the worst bug in this batch:** applying the
`escalate` label to a **WhatsApp, Web or Phone** case notified **nobody**.
`sync.py` returned early unless the inbox was `Channel::Email`. The label stuck,
the operator assumed it had worked, and the customer's complaint reached no one.
Silently. It has been that way the whole time.

### Enable

```bash
ESCALATION_ALL_CHANNELS_ENABLED=true                       # agent
ESCALATION_ACK_CHAT_TEMPLATE=We have escalated your case and will follow up shortly.   # backend
```

The template is **customer-facing** and goes out as a normal outgoing reply.
Blank means post nothing — emptying it is an opt-out, not a request to send an
empty message.

### Test it (this is the one worth doing by hand)

1. Send a WhatsApp message into the proton WhatsApp inbox.
2. Apply the `escalate` label, plus a `dept_*` label that has a PIC
   (`dept_sales`, `dept_engineer`, `dept_pre_sales`, `dept_aftersales`,
   `dept_cs`, `dept_technical` all resolve today).
3. Expect **three** things:
   - the PIC receives the escalation email (as before),
   - the **customer sees your ack in the WhatsApp thread** — as a normal
     reply, *not* a private note,
   - `escalation_notified_at` is stamped on the conversation.

**Watch specifically for the note-vs-reply distinction.** Commit `0aa643d`
shipped exactly that bug on the reply path: a private note left the
conversation looking handled while the customer received nothing. The test
suite now asserts `private=False` and `message_type=outgoing` on the payload,
but confirm it with your own eyes on a real thread once.

A **voice** escalation deliberately sends no written ack — the caller was
already spoken to — while the PIC and dealer legs still fire.

### Dealer CC

```bash
ESCALATION_CC_DEALER=false   # default; set true to enable
```

Defaults to *false*, unlike `ESCALATION_CC_PIC` which defaults true: the dealer
forward carries the full transcript to an address outside the company.

Two guards you can verify: a dealer CC entry equal to the customer's own
address is always dropped (flag on or off), and the customer acknowledgement
CCs nobody under any flag combination.

---

## 4. Deploy when you're ready

No Chatwoot image rebuild needed — nothing here touches the fork.

```bash
git archive HEAD agent backend deploy | gzip > /tmp/src.tgz
gcloud compute scp /tmp/src.tgz crm-ticketing:/tmp/ --zone asia-southeast2-a
# on the VM: extract to /tmp/newsrc, rsync agent/ and backend/, then
docker compose -p proton -f docker-compose.tenant.yml \
  --env-file tenants/proton.env up -d --build backend agent
```

**No database migration this time.** P1's columns are BigQuery-side only.

---

## 5. Still outstanding, and honestly so

- **The E2E execution log** (`docs/testing/2026-08-06-escalation-email-e2e-scenario.md`)
  still has no "Execution log" section. TC-08 and TC-09 were proven live in a
  previous session; TC-01…TC-07 and TC-10 have never been formally run. I did
  not run them because each ends in "your test mailbox receives X" and I cannot
  read your Gmail. You chose *your own mailboxes only* — when you're ready,
  I can drive the server side and you confirm receipt.
- **The plus-addressing spike doc** was never written up. The finding is
  recorded in project memory and TC-08 proved the whole chain live; only the
  document is missing.
- **`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true`** is set on proton and has
  never been tested.
- **BigQuery `ensure_views()`** has never been run against real GCP.
- **P2 tasks 4, 6–11** and **packages P3–P14** are not built. See the session
  summary for the complexity breakdown.
