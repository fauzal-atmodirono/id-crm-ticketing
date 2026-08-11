# Risk register

**Programme:** PROTON e.MAS CRM enhancement (RFP 2026_028)
**Closes:** §6.1 (QA & risk management), the risk half
**Companion:** `qa-plan.md` · `../../analysis/2026-08-09-blocked-work-register.md`

---

## How to read this register, and why it is unusually blunt

A risk register earns its keep entirely in the rows nobody wants to write. The
rows below are drawn from the engineering record — the per-package ledgers under
`.superpowers/sdd/`, the blocked-work register, and the commit history — rather
than from a workshop, so several of them name things this delivery does not yet
do. That is deliberate. **Every one of these is already true; writing it down
changes only whether the client learns it from us or from their own reviewer.**

Two conventions:

- **Likelihood and impact are stated as High/Medium/Low with a reason.** A
  register that scores risks 1–25 without saying why invites arguing about the
  arithmetic instead of the risk.
- **Owner** names the *kind* of person who can close the row, not an individual —
  a steering committee has not been named yet (see R-GOV-1). Where a row can only
  be closed by the client, it says so.

**R1–R6 are the six rows the programme design named in advance** so they could
not be quietly dropped. R7 onward are the rows the engineering work produced.

---

## R1 — A single VM cannot meet 99.9% uptime or a 2-hour P1 response

| | |
|---|---|
| **Likelihood** | High — this is an architectural certainty, not a probability |
| **Impact** | High — it is a contractual commitment |
| **Status** | **Open. Not an engineering task.** |
| **Owner** | Commercial / account |

The entire platform — Caddy, every tenant's Chatwoot, Rails, Sidekiq, the `agent`
and `backend` services, Postgres and Mailpit — runs on **one Google Compute
Engine VM**. There is no second zone, no failover, no load balancer in front of a
second instance. A zone outage, a host maintenance event, a full disk or a bad
deploy is a total outage of every tenant simultaneously.

99.9% availability permits roughly 43 minutes of downtime per month. A single VM
with a single Docker daemon cannot be *engineered* to that number, and no amount
of monitoring changes it: monitoring shortens the outage, it does not prevent it.
A 2-hour P1 response likewise assumes a staffed on-call rotation that has not
been costed.

**What would close it:** R17, multi-zone HA — a real programme of work
(load-balanced Chatwoot, replicated Postgres, per-tenant isolation across zones),
not a configuration change. **This is a commercial conversation about either the
price or the SLA, and it should happen before the SLA is signed rather than after
the first outage.**

**Do not accept a mitigation that reads "improved monitoring and backups".**
Backups bound data loss; they do not bound downtime.

---

## R2 — Every upstream Chatwoot security release requires rebasing 58 fork patches

| | |
|---|---|
| **Likelihood** | High — upstream ships security releases on its own schedule |
| **Impact** | Medium — cost and delay, not data loss |
| **Status** | **Open. Standing cost, needs pricing.** |
| **Owner** | Delivery / commercial |

The Chatwoot SPA is forked. Customisation is delivered as **58 patch files**
(`deploy/chatwoot-fork/patches/0001-*` through `0059-*`; `0042` is absent) that
are `git apply`-ed onto upstream at image-build time. Every upstream version bump
— including one taken purely for a CVE — means re-applying all 58, fixing
whatever no longer applies, rebuilding, and re-verifying the UI.

**Correction to the programme design, which said 49.** The count was 49 when the
design was written and is 58 now; nine patches were added by this programme
alone. **The number grows with every feature**, so the rebase cost is not a fixed
overhead — it rises monotonically for the life of the product. That is the more
important half of this row.

**What would close it:** nothing closes it while the fork exists. It can be
*reduced* — upstreaming what upstream would accept, replacing patches with
supported extension points where any exist, and P13's rebase tooling to automate
the mechanical part. It must be **priced as recurring effort in the support
contract**, because it will otherwise be absorbed silently until a security
release is deferred for cost reasons. A deferred security patch on a
customer-facing CRM is the real risk in this row.

---

## R3 — The DMS/TSP integration has no API specification, and eight requirements depend on it

| | |
|---|---|
| **Likelihood** | High — already the case |
| **Impact** | High — eight requirements cannot be delivered |
| **Status** | **Blocked on the client (open question Q4).** |
| **Owner** | PROTON |

No endpoint, no API specification, no sandbox, no credentials. What exists is an
**integration shell**: a configuration store, an admin page, a connection test,
and a `not_connected` state that is honest about being unreachable.

The shell's behaviour is deliberately built so that an unconfigured DMS reads as
**"we could not ask"** rather than as **"this customer has no vehicles"** — an
empty success response would be worse than an error, because a service advisor
would act on it. A demo mock client exists but is behind an explicit opt-in
(`DMS_MOCK_CLIENT_ENABLED`) and is never the default, precisely so a demo cannot
be mistaken for an integration.

**What would close it:** PROTON supplying the DMS/TSP API specification and a
sandbox. Until then these eight requirements are GAP, and **no demo, slide or
status report may show DMS data**, because the only DMS data this platform can
produce is fabricated.

---

## R4 — Meta Business verification blocks the social channels entirely

| | |
|---|---|
| **Likelihood** | Medium — it is a process, and processes complete |
| **Impact** | Medium — two channels, no workaround |
| **Status** | **Blocked on the client. Needs a date.** |
| **Owner** | PROTON |

Facebook and Instagram inboxes **cannot be created at all** until Meta Business
verification completes for PROTON's business account. This is not a technical
gap: the code path is Chatwoot's own and is unmodified. There is no engineering
work to schedule and nothing to test, because no inbox can exist.

**What would close it:** PROTON completing Meta Business verification. **The
specific ask is a target date**, because the social channels' SIT cases, training
content and acceptance criteria all queue behind it, and a dependency with no date
silently becomes the reason a milestone slips.

---

## R5 — There is no call queue, so 6 of the 14 monthly control items cannot be measured

| | |
|---|---|
| **Likelihood** | High — already the case |
| **Impact** | High — it is a reporting commitment in Appendix C1 |
| **Status** | **Open. Deliberately not attempted (R9).** |
| **Owner** | Delivery / commercial |

Five rows of the fourteen-row control-item slide report `no_data` with a
client-facing reason, and a sixth (average handling time, §4.69) is unmeasurable
for the same underlying cause:

| Row | Why |
|---|---|
| Call abandon rate | No call queue is instrumented — nothing to abandon, nothing to count |
| Average speed of answer | Queue measurement, same gap |
| Service level | Queue measurement, same gap |
| Calls offered | Comes from telephony queue statistics, not integrated |
| Escalations to HQ | "HQ escalation" is undefined in the case model (open question Q5) |
| Average handling time | Needs the same queue instrumentation |

**The row that matters operationally is not in the table above.** These figures
render as **blank, never as `0`**, and that distinction is load-bearing:

> A zero is a claim about performance. A blank is a statement about
> instrumentation.

**The single most likely way this work gets undone** is somebody "tidying" the
blanks to `0` before a client meeting — at which point the slide begins asserting
a 0% call-abandon rate on a platform with no call queue, and it will look like an
improvement to whoever does it. If that happens, the platform starts making a
measurable claim it cannot support.

**What would close it:** R9, call-queue instrumentation — estimated 4–6 weeks and
a separate programme. Until then the client should agree that six control items
are reported as not-instrumented.

---

## R6 — The vendor response marks at least 17 unbuilt requirements as "Fully Out-of-the-Box"

| | |
|---|---|
| **Likelihood** | High — the document exists and says this today |
| **Impact** | High — commercial exposure and loss of credibility on everything else |
| **Status** | **OPEN AND URGENT. Not an engineering task.** |
| **Owner** | Bid / commercial, before the next clarification meeting |

The already-drafted vendor response classifies **at least 17 requirements that
are not built as "Fully Out-of-the-Box"**.

This is the most urgent item in the entire analysis and it is the only row in
this register with no engineering component whatsoever. It cannot be closed by
building the features: several of them are blocked on the client (R3, R4) or on a
separate programme (R5), and the response would still be wrong about their
status.

**It is in this register because this is the artefact read by the people who can
act on it.** The engineering record has said so for months in documents that the
commercial team does not read.

**What must happen:** a line-by-line reconciliation of the response's
capability claims against the delivered state, **before the next clarification
meeting**, and a corrected response. The corrected version will be a weaker
document. It will also be one that survives the client's own technical review,
which the current one will not — the client's reviewers have already queried one
demo claim (the Power BI artefact, 2026-07-28 feedback item 5), so the assumption
that nobody checks is already disproved.

**This row must not be softened, deferred to a later revision, or reworded into
"documentation alignment".** It is a statement that a commercial document is
inaccurate in the client's favour to read and our disfavour to defend.

---

## R7 — Eight fork patches have never been applied to a real Chatwoot checkout

| | |
|---|---|
| **Likelihood** | High that at least one needs rework; low that all eight are clean |
| **Impact** | Medium — each is a UI feature that does not exist until its build succeeds |
| **Status** | **Open. Blocked on build infrastructure, not on effort.** |
| **Owner** | Engineering, needs a Cloud Build run |

Patches **0052 through 0059** — eight of them — have never been applied to a real
Chatwoot source tree and no built image contains any of them. The development
sandbox has no network access to clone upstream, so each was authored against a
*synthetic* reconstruction of its context and verified with `git apply --check`
against that reconstruction. **That proves internal consistency and nothing about
upstream.**

| Patch | Feature | Verification quality |
|---|---|---|
| `0052` | Escalation tier-2 manager contact | `git apply --check` against a reconstruction |
| `0053` | P6 Workforce dashboard page | **Weakest.** Context lines transcribed from `0045` from memory of its content |
| `0054` | P6 agent "My status" page | **Stacks on `0053`** and transcribes 0053's own added lines as context |
| `0055` | P7 Translate button | Anchored on `0002`'s already-merged content, transcribed verbatim |
| `0056` | P7 FAQ composer strip | **Stacks on `0002` + `0055`.** Its arithmetic broke once already (80 vs 107 added lines) after a hand edit |
| `0057` | P9 inbound alerts + preferences page | **Best.** 4 of 6 files are new; hunks generated by real `git diff`; tests execute the shipped JavaScript in Node |
| `0058` | Feature-flag unification | Hunks generated by `git diff`; tests render the shipped ERB through Ruby's own `erb` |
| `0059` | Roles & Permissions redesign | Pre-image reconstructed by replaying `0027`→`0028`→`0051`; the SFC compiles under `@vue/compiler-sfc` |

**The stacking is the real risk, not the individual patches.** `0054` sits on
`0053` and transcribes its added lines as context, so **a line-number fix-up to
`0053` cascades into `0054`**. `0056` sits on both `0002` and `0055`, so a fix to
either forces one there. `0053` is both the weakest-verified and the lowest in a
stack — the worst combination available.

`0057` deliberately anchors *below* `0053`/`0054`'s insertions rather than on
their content, so a content change to those two cannot break it; only their line
counts shift, which `git apply` absorbs as an offset. That is the pattern the
others should have used.

**Two upstream APIs inside `0057` are outright guesses**, because the sandbox
cannot read upstream: the Vuex conversation-list getter names, and the response
shape of `GET /api/v1/accounts/:id/conversations`. Both fail toward a **visible
degraded indicator** rather than toward silence, so a wrong guess is noticeable
and fixable by editing one array — but both must be confirmed on the first real
build.

**What must happen, in order:** `gcloud builds submit deploy/chatwoot-fork/` —
**off-VM and for `amd64`**; a local arm64 Mac build will produce an image the VM
cannot pull, and this heavy Vite build must never run on the 16 GB production VM.
Then the manual UI verification each patch's own definition of done requires.

**Until each build has gone green, the feature in front of it does not exist.**
Specifically: do not schedule a demo of the Workforce dashboard, the "My status"
page, the Translate button, the FAQ composer strip, the inbound-alert toast, or
the redesigned Roles & Permissions page. `PRESENCE_CUSTOM_STATUSES_ENABLED` on a
tenant today buys **an API with no UI**.

**`0059` is additionally undocumented scope** — it is the current HEAD, is
recorded in no package ledger and in no design document, and nobody has agreed it.

---

## R8 — The AI quality baselines are unmeasured, and the stub numbers look excellent

| | |
|---|---|
| **Likelihood** | High that the stub figures get quoted if they are not guarded |
| **Impact** | High — an AI acceptance threshold agreed against a fabricated baseline |
| **Status** | **Open. Blocked on real Gemini/Vertex credentials.** |
| **Owner** | Engineering, needs credentials; then the client, to agree thresholds |

Two measurement exercises are code-complete, green, and **have never produced a
real number**, because the development environment has no Gemini or Vertex
credential (`GOOGLE_API_KEY=test-key`, every model client stubbed):

- **The AI calibration baseline** — four labelled sets of ≥30 cases each covering
  intent classification, FAQ match, sentiment and summary quality. Neither the
  pre-change nor the post-change baseline exists.
- **The Malay SMS corpus pass rate** — a 56-case corpus. The query normaliser
  built against it ships **disabled**, because its acceptance gate is literally
  "ship only if it measurably improves the corpus pass rate", and that rate has
  never been measured.

**The dangerous part is that the stub runs score 97–100% across all four
capabilities.** They do so for a reason that has nothing to do with quality:
**the same author wrote both the ground-truth labels and the naive keyword rules
being scored against them.** That is not a baseline, it is a harness agreeing
with itself.

Every report the suite can produce here carries `mode == "stub"`, a named stub
model identity, and a disclaimer, *structurally* rather than as a comment
somebody could miss. The baseline tables read `TBD — unmeasured`, deliberately.

**A 98% in a status update is exactly the kind of figure that gets repeated
without its qualifier**, and once repeated it becomes the number the client
expects. **No stub score may appear in any client-facing material, slide or
status report.** The proposed acceptance thresholds are a *proposal* and must not
be presented as met or agreed.

**What would close it:** real credentials, then the procedure in §5 of
`docs/testing/2026-08-08-ai-calibration-baseline.md` — run once against a
pre-change checkout and once against the current one, and record both scores per
capability. Only that comparison is evidence.

---

## R9 — The busiest AI surface is not metered, so no cost total can be complete

| | |
|---|---|
| **Likelihood** | High — already the case |
| **Impact** | Medium — cost reporting understates spend by an unknown amount |
| **Status** | **Open. Needs an upstream change or a fork, not a wiring fix.** |
| **Owner** | Engineering |

`chat.turn` — the WhatsApp conversational agent, the highest-volume AI surface in
the product — **cannot be metered at our client boundary at all.** google-adk
takes a model *string* and constructs its own `google.genai.Client` **inside the
installed package**, so no wrapper of ours can observe the call.

The consequence is deliberately visible rather than hidden. The metered rows from
that code path are labelled `chat.transcribe`, never `chat.turn`, because a
`chat.turn` row carrying only a transcription's tokens would tell the cost report
that the busiest surface in the product is nearly free.

Three further surfaces are incomplete for their own reasons:

| Surface | State |
|---|---|
| `chat.turn` | **Absent entirely.** ADK builds its own client |
| `phone.live` | **Absent.** The Live API reports usage in server messages, not on a response object |
| `embed` | **Visible but not priceable.** Embeddings bill per character; `EmbedContentResponse` carries no usage metadata at all, and `token_usage` has no character-count column |
| Thinking-model tokens | **Uncaptured.** `thoughts_token_count` and `tool_use_prompt_token_count` fall outside the three recorded classes, so even the five priced surfaces are understated for a thinking-enabled model |

**There is deliberately no total, and it must stay that way.** The only money
figure is `priced_subtotal_usd`, and a test fails the build on `total`, any
`total_*` and any `*_total`. Unmetered surfaces are rows with `cost_usd: null`
**and** `calls: null`, because `0` claims a surface is free and an absent row
claims the inventory is complete.

**This is as much a constraint on future work as a risk.** A later "tidy-up into
a single headline cost figure" is a regression, not an improvement, and it will
be proposed by somebody reasonable.

---

## R10 — The phone channel has never run against a real Twilio call

| | |
|---|---|
| **Likelihood** | High that first live use finds defects |
| **Impact** | High — voice is a customer-facing channel |
| **Status** | **Open. Blocked on a Twilio account and a phone number.** |
| **Owner** | Engineering + PROTON (number provisioning) |

Every part of the voice path — inbound call handling, the DTMF menu, agent
handoff, recording, voicemail ingest, live transcript, transcript classification
— is built and unit-tested against fakes. **No real call has ever been placed.**

This has a second-order consequence worth stating: **automated call-QA scoring is
deliberately not built** because of it. The five QA criteria are only ever set
from explicit `POST /qa/label` request fields; there is no transcript reader and
no model call anywhere in the QA path. An automated scorer built on a transcript
pipeline that has never run would produce confident noise. **Unblocking automated
QA scoring requires the live-call verification first, not more code.**

Compounding it: **all sixteen `PHONE_*` settings and all eight Twilio credentials
are set in neither `example.env` nor either compose file** (see
`../handover/configuration.md`), so an operator has no way to discover that the
configuration surface exists.

---

## R11 — `presence_events` grows without bound and has no retention owner

| | |
|---|---|
| **Likelihood** | High — it grows on every poll, forever |
| **Impact** | Medium, rising — read latency then storage cost |
| **Status** | **Open and unowned.** |
| **Owner** | Unassigned — this row exists to assign it |

The presence poller writes an event per agent status transition into a Firestore
`presence_events` collection. **Nothing ever deletes from it.** Two related
problems:

1. **Unbounded reads.** `presence_store.since()` and `_latest_at_or_before()`
   remain unbounded scans. Bounding them needs a composite index
   (`agent_id ASC, at ASC`) that cannot be provisioned or verified from the
   development environment.
2. **Unbounded growth.** The read-bounding work explicitly did not address the
   collection's size. Recorded verbatim in the engineering ledger as *"this fix
   bounds the read, not the collection; retention needs an owner."*

The workforce dashboard polls roughly every 30 seconds. This degrades gradually
and will present as "the dashboard got slow", months after the cause.

**What would close it:** a retention policy with a number attached, a scheduled
purge that honours it, and the composite index. **The number is a client
decision** — how long agent presence history must be retained is a workforce and
possibly an employment-relations question, not an engineering one.

---

## R12 — No restore has ever been rehearsed

| | |
|---|---|
| **Likelihood** | Medium that an untested restore fails when first needed |
| **Impact** | **Catastrophic** — it is the last line of defence for every tenant |
| **Status** | **Open. Owed.** |
| **Owner** | Engineering + operations |

`deploy/scripts/backup.sh` exists and runs. **No restore has ever been performed
from its output.** A backup that has never been restored is a hypothesis.

At the time of writing, `restore.sh`, `archive-old-data.sh` and `rebase.sh` do
not exist in `deploy/scripts/` at all, and neither do the four operational
runbooks (data retention, disaster recovery, environments, monitoring and
alerts). Package P13 delivered its code half — deep health checks and audit-log
purging — and none of its operational half.

Combined with **R1** this is the sharpest row in the register: one VM, no
failover, and an unrehearsed restore. The two risks multiply rather than add.

**What must happen:** a restore rehearsal against a scratch tenant, timed, with
the result written down — including how long it took, because "we can restore" and
"we can restore within the SLA" are different claims and only one of them is
contractual.

---

## R13 — The reporting warehouse has never been created

| | |
|---|---|
| **Likelihood** | High that the first real run surfaces SQL defects |
| **Impact** | Medium — reporting is delayed, not wrong |
| **Status** | **Open. Blocked on GCP credentials.** |
| **Owner** | Engineering |

**33 BigQuery views** are authored and asserted structurally. **Not one has ever
been executed against a real dataset.** The `ensure_views()` run that creates
them has never happened, and note that it **re-creates every view**, so the first
run is not additive.

Specific constructs a structural test cannot verify, and which will either work
or fail on that first run:

- `RANK() OVER (PARTITION BY day, channel, respondents >= N)` inside a `CASE`
- `SUM(COUNT(*)) OVER (PARTITION BY ...)` — a window over an aggregate
- `EXTRACT(HOUR FROM created_at AT TIME ZONE '<zone>')` against
  `DATE(created_at, '<zone>')` — the two take the zone in different positions,
  and only a real query proves they agree on the same bucket

Three further gaps are structural rather than syntactic: the `token_usage` views
have **no runtime caller** and must be created by hand; `v_kb_staleness` cannot be
created at all because it reads a `faq_entries` table that does not exist and that
nothing populates; and the `agent` service's token counts are written to Postgres
and **never reach the warehouse**, so the cost report labels that service
`unmetered` rather than zero.

Two migrations are also owed on already-deployed tenants, because this repository
has **no Alembic** and `create_all` does nothing to an existing table: an
`ALTER TABLE` on `ai_actions` for the token columns, and one on BigQuery's
`qa_labels` for the rubric columns.

---

## R14 — `REPORTING_TIMEZONE` silently re-buckets every historical figure

| | |
|---|---|
| **Likelihood** | Medium — only when somebody changes it |
| **Impact** | Medium — every dashboard moves, and it looks like a bug |
| **Status** | **Open by design. A warning, not a defect.** |
| **Owner** | Whoever changes the setting |

Changing `REPORTING_TIMEZONE` re-buckets **every historical figure on every
dashboard** the next time `ensure_views()` runs. Totals do not change; individual
cases slide between adjacent days, weeks and months. That is why it presents as
"close but not quite" rather than as obviously broken, and why somebody will
spend a day on it.

The default (UTC) is the **identity transform** — byte-identical DDL — so nothing
moves until somebody decides to move it.

**What must happen before it is changed:** run
`scripts/compare-reporting-timezone.py` and **keep the output**. It is the
evidence that Monday's movement was expected.

A related row for the same reader: **`v_dealer_escalation` keys on
`dealer_escalated_at`, not `created_at`**, so a case created in May and escalated
in June is a June row and that view's monthly total deliberately does not sum to
the month's case count. Somebody will file it as a bug; it is pinned by a named
test so the answer is findable.

---

## R15 — PII protection in AI summaries is a prompt, not a control

| | |
|---|---|
| **Likelihood** | Medium — depends on model compliance and operator configuration |
| **Impact** | High if it fails — personal data in a stored, searchable corpus |
| **Status** | **Open. The real fix is blocked on the client (Q7).** |
| **Owner** | PROTON (Q7), then engineering |

The summariser's prompt **asks** the model to leave out the customer's name,
phone number, email, home address and plate number. That is the mitigation the
design claims. What it is not:

- **Nothing validates the output.** No code inspects, strips or checks the
  returned text, so a summary can carry an identifier and be stored as-is —
  including into the pgvector resolved-case index.
- **An operator can argue with it.** The persona prefix — product name,
  guardrails, preferred language — is **prepended ahead of the task prompt**, so a
  tenant whose guardrails say *"always include the customer's full name"* places
  that instruction *earlier in the same request*, and the model may prefer it.

**Anyone with persona-edit access can therefore weaken the mitigation without
touching code, and no code change is required for it to fail.**

**This must not be presented as a control in any privacy or security discussion.**
It is a request to a language model. The real fix is R16, full PII masking,
blocked on open question Q7 (masking scope).

> **A correction recorded so it is not re-propagated:** an engineering ledger once
> stated that an operator persona *replaces* the summariser's system prompt
> wholesale, deleting the PII instruction entirely for that tenant. **That is not
> what the code does** — the persona is prepended, never substituted, and the
> persona's `instructions` field is not read on that path at all. The residual
> risk is the weaker instruction-following one described above. The stronger claim
> was false and no code change is owed for it.

---

## R16 — Features that are built, correct, and unreachable

| | |
|---|---|
| **Likelihood** | It has happened nine times |
| **Impact** | Medium — schedule and credibility; a delivered feature that does nothing |
| **Status** | **Recurring. Partially mitigated.** |
| **Owner** | Engineering practice |

**Nine times in this programme, something shipped correct, unit-tested and unable
to run.** Four requirements whose only writer had no caller; two features whose
instruction never reached the model; a flag with no consumer anywhere; a tunable
that did nothing at any value; and a router nobody mounted (three separate
instances). Plus two backend flags that turned out to be independent of the
frontend's real gate, so an operator flipping the documented switch got nothing.

**Every single one survived because its test called the inner function and passed
the arguments by hand, one layer below the bug.** The suites were green
throughout.

The class matters more than the instances, because it means **a green test suite
is not evidence that a feature is reachable** on this codebase, and any
acceptance criterion phrased as "tests pass" is weaker than it sounds.

**Known-unreachable-by-design today**, all declared rather than hidden:

| Item | State |
|---|---|
| The resolved-case index | Builds a pgvector corpus; **no surface queries it** |
| The follow-up date field | Works end to end in both services; **no Chatwoot UI renders it** |
| `v_kb_staleness` | Reads a table that does not exist and that nothing populates |
| `GET /calls/{id}/recording` | Reads an in-process registry nothing in production writes |
| The web widget's NPS survey | Sampling reaches WhatsApp, email and phone only |
| `my_team` alert scope | Behaves as `all` — no verified team-membership getter; **reach is wider than the design implies** |

**Partial mitigation in place:** the both-flag-states gate
(`deploy/scripts/check-suites-both-flag-states.sh`) is the only mechanism that has
ever caught this class, and it has caught several. Its own limits are R17.

---

## R17 — The one gate that catches this class was silently red for several commits

| | |
|---|---|
| **Likelihood** | Medium — it has already happened once |
| **Impact** | Medium — undetected regressions in every opt-in code path |
| **Status** | **Open. Green now; the process gap remains.** |
| **Owner** | Engineering practice |

`deploy/scripts/check-suites-both-flag-states.sh` runs both suites twice: every
feature flag off (the ship-dark guarantee) and every flag forced on (the run that
finds bugs, because the on-path is code nobody exercises until a tenant opts in).
It has caught defects plain `pytest` could not, including the vacuous-defaults
class described below.

**It was red for several commits and nobody noticed**, because plain `pytest` was
run instead and plain `pytest` cannot show it. Every "both flag states green"
claim made in that window was unverified for the ON half.

Two standing gaps:

1. **P11, P12 and P13's flags are not in `FLAGS_ON`.** Those packages never
   reached the step that adds them, so **the on-path of all sixteen `PHONE_*`
   settings and the P12/P13 settings has never been executed.** A flag missing
   from that list is a flag whose on-path is untested.
2. **A flag-membership test cannot detect a red ON run.** Only running the script
   can. Asserting the list is complete is not the same as running it.

Related and worth its own sentence: `Settings(_env_file=None)` **does not** stop
pydantic-settings reading `os.environ`, so a test asserting a default via a bare
`Settings()` proves nothing when the variable happens to be set — and under the
flags-on gate it asserts the *opposite* of its own name while still passing.
**Six such vacuous tests have been found**, and every one had a name that
described the right behaviour.

---

## R18 — P11 through P14 have no engineering record

| | |
|---|---|
| **Likelihood** | Certain — already the case |
| **Impact** | Medium — un-sourced claims, and knowledge lost with the author |
| **Status** | **Open.** |
| **Owner** | Delivery |

Packages P1–P10 each have a per-task ledger recording what was decided, what was
tested and what was left owed. **P11, P12 and P13 have none**, and their code
exists only because roughly 3,900 uncommitted lines were rescued from three
sessions that terminated on API rate limits before committing.

The consequence for this handover is specific: **anything asserted about P11
(voice partials), P12 (screen pop and Customer 360) or P13 (ops hardening)
behaviour in a client-facing document is currently un-sourced.** There is no
per-task record of what those packages decided or deliberately left out. This
register and the blocked-work register are the only account.

A near miss worth recording as a process risk: while those 3,900 lines were
uncommitted, a single `git clean -fdx`, a bad checkout, or another concurrent
agent's `git stash` would have destroyed all of it.

---

## R-GOV-1 — Five §6 requirements have no owner because they are not engineering work

| | |
|---|---|
| **Likelihood** | Certain — nothing has been written |
| **Impact** | Medium — five requirements remain GAP at sign-off |
| **Status** | **Open. Needs a delivery manager, not an engineer.** |
| **Owner** | Unassigned — this row exists to force the assignment |

Five §6 requirements are **not** closed by any package in this programme:
delivery approach, scope and change management, governance organisation, and the
project dashboard and status cadence.

They are GAP **because nobody has written them, not because anything is missing
from the product.** A steering committee has to be named, a change process
agreed, and a reporting cadence set. No amount of engineering closes them.

This is also why every "Owner" field in this register names a role rather than a
person: **there is no named governance forum to escalate any of these rows to.**
That is itself the risk.

---

## Summary

| # | Risk | L | I | Closed by |
|---|---|---|---|---|
| R1 | Single VM cannot meet 99.9% / P1 <2h | H | H | Commercial decision (R17 HA) |
| R2 | 58-patch fork rebase per upstream release | H | M | Priced as standing effort |
| R3 | DMS/TSP has no API spec; 8 requirements depend on it | H | H | **PROTON (Q4)** |
| R4 | Meta verification blocks social entirely | M | M | **PROTON — needs a date** |
| R5 | No call queue; 6 of 14 control items unmeasurable | H | H | R9, separate programme |
| R6 | Vendor response marks ≥17 unbuilt items "Fully OOTB" | H | H | **Reconcile before the next meeting** |
| R7 | 8 fork patches never applied to real upstream | H | M | Cloud Build, amd64, off-VM |
| R8 | AI baselines unmeasured; stub scores read 97–100% | H | H | Real Gemini credentials |
| R9 | `chat.turn` unmeterable; no cost total can be complete | H | M | Upstream change or fork |
| R10 | Phone path never run against a real Twilio call | H | H | Twilio account + number |
| R11 | `presence_events` unbounded, no retention owner | H | M | **A retention number from the client** |
| R12 | No restore ever rehearsed | M | **Catastrophic** | A timed rehearsal |
| R13 | 33 BigQuery views never executed | H | M | One live GCP run |
| R14 | `REPORTING_TIMEZONE` re-buckets history | M | M | Run the comparison script first |
| R15 | Summary PII protection is a prompt, not a control | M | H | **PROTON (Q7)**, then R16 |
| R16 | Built-correct-and-unreachable, nine times | — | M | Practice; the flag gate |
| R17 | The flag gate was silently red; P11–P13 flags absent | M | M | Run it, and add the flags |
| R18 | P11–P14 have no engineering record | Certain | M | Delivery |
| R-GOV-1 | Five §6 items need a delivery manager | Certain | M | **Name a governance forum** |

**The four rows the client must act on, none of which engineering can close:**
R6 (reconcile the vendor response), R3 and R4 (Q4 and a Meta date), R11 (a
retention number), R15 (Q7). **The two that should be settled before an SLA is
signed:** R1 and R12.
