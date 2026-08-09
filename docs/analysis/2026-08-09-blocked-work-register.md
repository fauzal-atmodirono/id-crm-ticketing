# Blocked work register — what I cannot finish, and what would unblock it

**As of 2026-08-09**, branch `dev-yuda`. Companion to the test guide at
`docs/testing/2026-08-09-p1-p2-test-guide.md`.

This lists work that is **blocked on something other than engineering effort**:
a client answer, a credential, a running system, or a decision that is not
mine. It deliberately does *not* list "not built yet" — packages P5–P14 are
simply pending, and pending is not blocked.

Each item says who can unblock it and what changes when they do. If an item
here is quietly dropped rather than resolved, something ships that looks
finished and is not.

---

## 1. Blocked on a client answer

These produce a **truthful gap** today. None of them is a bug, and none should
be "fixed" by guessing — a plausible wrong number on a client slide is worse
than a visible zero, because nobody audits a number that looks reasonable.

| # | Blocked | Question | What ships until it is answered |
|---|---|---|---|
| Q5 | `escalated_to = 'hq'` | What counts as an HQ escalation? Nothing in the case model distinguishes one. | `escalated_to` offers **`dealer` and `none` only**. The validator rejects `hq` by name and says why; the provisioning script offers no `hq` option. **C1-07's HQ column will report zero** — it must be captioned "not yet classified", never read as "no HQ escalations happened". |
| Q6 | A dedicated bounce mailbox | Do you want DSNs delivered somewhere other than the tenant's own Email inbox? | **Downgraded from blocker to optimisation.** Bounce handling is built and live: Gmail returns the DSN to the envelope sender, which *is* the tenant's inbox, so no separate mailbox is required. A dedicated one would only stop DSNs touching the agent queue at all, which matters at volume. |
| Q8 | The C2 297-vs-264 discrepancy | Which of the two figures is authoritative? | P3 does **not** fix this. It avoids creating a second instance: `v_concern_pivot` buckets a null `case_detail` as `Unspecified` rather than filtering it out, so the pivot always reconciles with the headline count. |
| Q3 | Case-field ownership | Which fields are agent-entered vs system-derived? | All ten P3 fields are agent-entered. `REPORT_COVERAGE_DISCLOSURE` (default ON) captions any block grouped by one with its actual coverage. |
| Q10 | Licensing / §7 | Commercial. | R19 not attempted. |
| Q4 | Real DMS/TSP endpoints | | R11 not attempted; the integration shell exists. |
| Q7 | PII masking scope | | R16 not attempted. |

**The most urgent item in the whole programme is not engineering:** the
already-drafted vendor response marks **≥17 unbuilt requirements as "Fully
Out-of-the-Box"**. That needs reconciling before any clarification meeting.
It is row 6 of P14's risk register and it is not getting less true with time.

---

## 2. Blocked on something only the account owner can do

### 2.1 The bounce sender is outside this VM — **still open**

Delivery-failure notices for `proton.demo@demo.com` and `pic@emas.proton.com`
were still arriving on 2026-08-09 (five in 30 minutes; 60 total on the inbox).

**Ruled out**, across all three tenants: no user, no contact, no active
automation rule, no campaign, zero outgoing Chatwoot messages. No
`proton-backend` Cloud Run service. No local Docker. The originals' ActionMailer
Message-ID hosts (`c43dbee7b0fb`, `e417feed08dd`) match **no container on this
VM**, running or stopped — and `Config.Hostname` is fixed at create time, so
that is not a restart artifact.

The DSNs are addressed to `devotech29@gmail.com`, so the originals were **sent
from that Gmail account** by a Chatwoot instance elsewhere.

**Mitigated, not solved.** Deployed: a transport-level blocklist
(`EMAIL_BLOCKED_RECIPIENTS`) so this platform can never mail either address —
verified against the live backend — plus bounce handling so returning DSNs stop
becoming live cases. The automation rule targeting `pic@emas.proton.com` was
deleted outright rather than left disabled.

**Only the account owner can close it:** Google Account → Security → **App
passwords**, revoke anything that is not this VM. Gmail → Sent, search
`to:proton.demo@demo.com`, names the sender. Worth doing promptly — sustained
failures to a dead domain get a Gmail account rate-limited, and that would take
every real escalation down with it.

### 2.2 Live E2E test cases TC-01…TC-07, TC-10

Every one ends in "your test mailbox receives X". I can drive the entire server
side; I cannot read the mailbox. You chose *your own mailboxes only* for the
run. **The execution log in `docs/testing/2026-08-06-escalation-email-e2e-scenario.md`
is still empty** — TC-08 and TC-09 were proven live in an earlier session; the
rest have never been formally run.

### 2.3 `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED`

Set to `true` on proton and **never tested**. Needs a real WhatsApp voice note
or image sent to the tenant.

---

## 3. Blocked on infrastructure I cannot reach

| Item | Why | Unblocked by |
|---|---|---|
| **BigQuery `ensure_views()`** | All development ran against the mocked adapter. There are now **31 views** and the schema has grown by 12 columns. | One live run with GCP credentials. Note it **re-creates every view** — see the timezone warning below. |
| **Cloud Build for fork patch 0052** | The tier-2 manager field on the Escalation Routing page. Patch is written and verified with `git apply --check`; no image contains it. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64. **Never build this on the prod VM, never from an arm64 Mac.** |
| **`provision_case_record_fields.py`** | P3's sidebar panel needs the custom-attribute definitions to exist. No fork patch needed. | One dry-run then live run per tenant. |
| **Upstream Chatwoot source** | This sandbox cannot reach github. | Only matters for files **upstream owns**. Files our own patches *created* can be reconstructed by replaying the patch history — that is how 0052 was authored. `CustomAttributes.vue` is upstream-owned and therefore genuinely out of reach. |

---

## 4. Deliberately not attempted

Recorded so they are not mistaken for oversights.

- **R9 call queue** — 4–6 weeks. Blocks 6 of the 14 monthly control items.
- **R17 multi-zone HA** — **99.9% uptime and P1<2h are not supportable on one
  GCE VM.** That is a commercial conversation, not an engineering task.
- **§4.63 telephony half** — depends on R9.
- **§2.1.1 procurement, §2.1.2** — not achievable as written.
- **B-EM-01 mailbox provisioning, B-SM-05 Meta verification** — third-party.
- **Appendix B Malay wording** — Appendix B is **English-only**. The plan
  assumed bilingual; no Malay was invented, because inventing customer-facing
  copy in a language the client did not approve is not a gap I get to close.
  PROTON must supply it.

---

## 5. Carries a warning rather than a block

Not blocked — but they will surprise someone if they land unannounced.

**`REPORTING_TIMEZONE`** — switching it **re-buckets every historical figure on
every dashboard** the next time `ensure_views()` runs. Totals do not change;
cases slide between adjacent days, weeks and months. That is why it reads as
"close but not quite" rather than obviously broken. The default (UTC) is the
*identity transform* — byte-identical DDL — so nothing moves until someone
decides. **Run `scripts/compare-reporting-timezone.py` first and keep the
output**: it is the evidence that Monday's movement was expected.

**`v_dealer_escalation` keys on `dealer_escalated_at`, not `created_at`.** A
case created in May and escalated in June is a **June** row, so this view's
monthly total deliberately does not sum to that month's case count. Someone
will file that as a bug. It is asserted as a named test so the answer is
findable.

**The flags-on test run.** `deploy/scripts/check-suites-both-flag-states.sh`
runs both suites with every feature flag forced ON. That run has already caught
two defects the flags-off run could not — the on-path is code nobody exercises
until a tenant opts in. **Every new default-off flag must be added to
`FLAGS_ON`**, or its on-path is untested.
