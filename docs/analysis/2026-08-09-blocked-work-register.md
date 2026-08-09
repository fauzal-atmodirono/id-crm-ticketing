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

### 2.4 `MEDIA_DIAGNOSIS_PROMPT_ENABLED` (P7 task 8)

Code is done and unit-tested against the composed instruction string
(`build_agent_instruction` in `prompts.py`, `test_media_prompt.py`), but the
prompt has never been in front of the real model with a real photo — this
sandbox has no WhatsApp number and no real Gemini credentials, only
`GOOGLE_API_KEY=test-key` against a stubbed client. A prompt change that has
never run end to end is not a delivered feature.

**What must be sent:** a real photo of a visible vehicle fault (e.g. a dented
door, a dashboard warning light, a cracked bumper) through a real WhatsApp
inbox on a tenant with `MEDIA_DIAGNOSIS_PROMPT_ENABLED=true` **and**
`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` (§2.3 must be closed out on the
same message, since both flags gate the same photo). One follow-up message
of plain text should follow, to confirm nothing about the diagnostic
instruction leaks into a turn without media (per
`test_the_diagnosis_instruction_is_absent_when_no_media_is_attached`).

**What would count as passing:** the bot's reply names something specific it
observed in the photo (not a generic "please describe the issue"), states an
explicit confidence level, and asks at most one follow-up question — never
a checklist of several. Record the actual exchange (screenshots or the raw
WhatsApp/Chatwoot transcript) in `docs/testing/`, dated, the same way
`docs/testing/2026-08-06-escalation-email-e2e-scenario.md` records the email
E2E cases. Until that exists, `docs/testing/2026-08-09-media-diagnosis-prompt-live-check.md`
is a template awaiting that run, not a result.

### 2.5 The Malay SMS query normaliser (P7 task 6)

`nlu_normalise.py`'s `normalise()` — collapses repeated characters and
expands a fixed table of SMS-register Malay abbreviations (`brp`→`berapa`,
`nk`→`nak`, etc., sourced from P7 task 5's 56-case corpus) — is code-complete,
unit-tested against real production call payloads (`test_nlu_normalise.py`:
the text handed to the model and the text echoed back on the agent-facing
`/kb/suggest` surface are both asserted, against actual captured payloads, to
survive un-normalised), and wired into the single retrieval call site
(`MergedKnowledgeAdapter.search_kb`). It ships **disabled**
(`NORMALISE_RETRIEVAL_QUERY_ENABLED = False`, a plain module constant, not a
`Settings`/env flag — this task was explicitly scoped to add no config
surface) because its acceptance gate is literally "ship only if it
measurably improves task 5's corpus pass rate", and **that pass rate has
never been measured for real**: task 5's corpus runner only exists against a
stub keyword classifier and a stub topic embedder in this sandbox
(`CorpusReport.mode == "stub"`), because there is no real Gemini/Vertex
credential here. `test_nlu_normalise.py`'s eighth test runs the corpus
with and without the normaliser through that same stub harness and proves
the comparison mechanism works and reports both rates — it does not, and
structurally cannot, assert an improvement.

**What must happen before this flag may be flipped on any tenant:** run P7
task 5's corpus (`test_malay_sms_corpus.py`) against real Gemini/Vertex
credentials twice — once with the query fed to FAQ retrieval left raw, once
with `normalise()` applied — and confirm the FAQ-hit pass rate is measurably
better with it on. Only that comparison, run for real, is the evidence the
brief's gate asked for. Until it exists, `NORMALISE_RETRIEVAL_QUERY_ENABLED`
stays `False` — flipping it on the strength of the stub numbers this sandbox
can produce would be exactly the "plausible wrong number" this register
exists to prevent.

---

## 3. Blocked on infrastructure I cannot reach

| Item | Why | Unblocked by |
|---|---|---|
| **BigQuery `ensure_views()`** | All development ran against the mocked adapter. There are now **33 views** and the schema has grown by 13 columns. | One live run with GCP credentials. Note it **re-creates every view** — see the timezone warning below. |
| **Cloud Build for fork patch 0052** | The tier-2 manager field on the Escalation Routing page. Patch is written and verified with `git apply --check`; no image contains it. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64. **Never build this on the prod VM, never from an arm64 Mac.** |
| **`provision_case_record_fields.py`** | P3's sidebar panel needs the custom-attribute definitions to exist. No fork patch needed. | One dry-run then live run per tenant. |
| **The Power BI `.pbix` (§4.55)** | A proprietary binary only Power BI Desktop can author. `proton-crm.pbids` (the connection) and the page-by-page spec ship instead — those are the reviewable, diffable parts. | One Power BI Desktop session. **Evidence required before reporting it done:** every page rendering against a real dataset, refresh succeeding under the service account, and a screenshot of each page in `power-bi-runbook.md`. The client raised this at the 2026-07-28 demo (feedback item 5), so an unevidenced claim will be checked. |
| **Cloud Build for fork patch 0053** | P6's Workforce dashboard page. **Hand-built and never verified against a real Chatwoot checkout** — context lines were transcribed from `0045`, the hunk arithmetic is script-verified, and it applies to a *synthetic* tree, which proves internal consistency only. If some unlisted patch also touches `protonAdmin.js` / `Sidebar` / `dashboard.routes.js`, or 0045's shipped content differs from what was transcribed, 0053 needs a line-number fix-up or a regenerated diff. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64. **Do not schedule a demo of the Workforce page before this build has gone green.** |
| **Cloud Build for fork patch 0054** | P6's agent-facing **My status** page (the availability-status picker + the administrator's status-catalogue editor). Same constraint as 0053 and the same evidence: hand-built, hunk arithmetic script-generated, applies to a *synthetic* tree reconstructing its context, **never checked against a real Chatwoot checkout**. Its three anchored hunks apply ON TOP OF 0053 and transcribe 0053's own added lines as context, so if 0053 needs a line-number fix-up, 0054 needs the same one. The backend behind it (`features/routing/status_router.py`, 15 tests) is complete and needs no build — but **`build_status_router` is not yet mounted in `main.py`**, deliberately left to a wiring step because a concurrent task owned that file. Until it is mounted the endpoints return 404 and the page cannot work even after a green build. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64. **Do not promise agents the "My status" page, or demo the catalogue editor, before this build has gone green.** Until then `PRESENCE_CUSTOM_STATUSES_ENABLED` on a tenant buys an API with no UI. |
| **Upstream Chatwoot source** | This sandbox cannot reach github. | Only matters for files **upstream owns**. Files our own patches *created* can be reconstructed by replaying the patch history — that is how 0052 was authored. `CustomAttributes.vue` is upstream-owned and therefore genuinely out of reach. |

---

## 3b. P5 control items — five rows that cannot be measured

The control-item slide (C1 p48) renders **nine of fourteen** rows from real
data. The other five report `no_data` with a client-facing reason. They are
**blank on purpose**, and the distinction is the point of the package:

> **A zero would be a claim about performance; a blank is a statement about
> instrumentation.**

| Row | Why it cannot be measured | Unblocked by |
|---|---|---|
| 10 Call abandon rate | No call queue is instrumented — nothing to abandon, nothing to count | R9 (4–6 weeks) |
| 11 Average speed of answer | Queue measurement, same gap | R9 |
| 12 Service level | Queue measurement, same gap | R9 |
| 13 Calls offered | Comes from telephony queue statistics, not integrated | R9 |
| 14 Escalations to HQ | "HQ escalation" is undefined in the case model | **Q5** |

**If anyone "tidies" these to 0 before a client meeting**, the slide begins
asserting a 0% abandon rate on a platform with no call queue. That is the
single most likely way this work gets undone, and it will look like an
improvement to whoever does it.

---

## 3c. P6 workforce dashboard — one column that cannot be measured

Same rule as §3b, one column instead of five rows:

| Column | Why it cannot be measured | Unblocked by |
|---|---|---|
| Cases closed today | No helper date-filters "resolved today". The only conversation pager in the codebase is a synchronous, unfiltered, full-history one built for a nightly batch job; calling it on every ~30 s dashboard poll would get slower as history grows. Reported as `null` with a `cases_closed_today_caveat`, never `0`. | A "resolved since" incremental query (new plumbing, not requested by any package) |

Two scope statements that belong here rather than only in prose, because both
read as more than they are:

- **The 1-hour unavailability alert's WIP list is scoped to `SLA_INBOX_IDS`** —
  the same inboxes the SLA engine watches, **not** account-wide. A tenant
  routing agent chats through an out-of-scope inbox has those open cases
  invisible to the alert. It is a prompt to review, not an audit.
- **Requirement 4.69's average-handling-time half is not delivered.** P6 ships
  the After-Call-Work *state*; AHT needs the same call-queue instrumentation
  (R9) that blanks four control items above. Do not let the ACW state be
  reported as closing 4.69.

## 3d. P6 follow-up date — built, and deliberately invisible

Not blocked on anyone outside this repo, recorded here so it is not mistaken
for shipped: the per-ticket follow-up date exists end to end in the backend and
the `agent` service (and is asserted never to appear as an SLA breach), but
there is **no Chatwoot UI for it**. The field needs P3's conversation-panel
patch, which is not part of P6. `FOLLOW_UP_DATE_ENABLED` keeps it invisible
until that patch exists — so the flag being off is the honest state, not an
oversight.

## 3e. P6 absence alerts — the two statuses that deliberately never alert

Recorded because both look like bugs and neither is, and "fixing" either would
produce an alert storm that gets the whole feature switched off:

- **Chatwoot's native `busy` never counts as an absence.** Busy means an agent
  is working — mid-conversation, or on a long call. A 10-minute alert on Busy
  fires at every agent handling anything substantial.
- **Chatwoot's native `offline` never counts as an absence either.** Offline is
  an agent stating they are off shift; the design derives the availability
  history from exactly these transitions. Alerting on it pages an
  administrator ten minutes after every agent logs off in the evening, and
  again an hour later, every night, per agent.

The alerts fire for the away-from-desk statuses an agent picks — Lunch, Break,
Toilet, Prayer — which is what §4.13/4.14 ask for. Both native values *do* now
resolve to catalogue entries (before the C1 fix they resolved to "no
information", which is what made the alerts unreachable in production); they
resolve to entries with `counts_as_unavailable=False`. Named tests pin all of
this: `test_a_native_status_from_the_poller_resolves_and_alerts_about_nothing`
and `test_offline_is_catalogued_and_does_not_count_as_unavailable`.

**A consequence worth stating for operators:** a tenant that enables
`PRESENCE_THRESHOLD_ALERTS_ENABLED` but never enables
`PRESENCE_CUSTOM_STATUSES_ENABLED` will correctly see **no alerts at all** —
it has no way for an agent to record an absence in the first place.

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

P6 added its seven flags **and P5's two, which had been omitted**. Adding them
immediately caught a third class of defect the flags-off run cannot see: three
tests asserting "this flag defaults to false" by constructing `Settings()`
without clearing the environment first. `_env_file=None` does not stop
pydantic-settings reading `os.environ`, so on the flags-ON run those tests were
asserting the exact opposite of their own names — passing while proving nothing.
**A new default-asserting test must clear its variable from the environment**,
or adding its flag to `FLAGS_ON` turns the test into a lie rather than a check.
`ROUTING_ENABLED` is deliberately *not* in `FLAGS_ON`: it is a Phase-5 switch,
and the one P6 component gated on it (the assignment sweeper) has its on-path
covered by `features/routing/test_p6_wiring.py` instead.
