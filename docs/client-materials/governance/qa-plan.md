# Quality assurance plan

**Programme:** PROTON e.MAS CRM enhancement (RFP 2026_028)
**Closes:** §6.1 (QA & risk management), the QA half
**Companion:** `risk-register.md` · `../sit/2026-08-08-sit-script.md`

---

## 1. What this document is for

Two substantial automated suites already exist, along with a
specification → plan → verification workflow and a habit of recording what is
*not* delivered. What has been missing is the document that describes the
practice — and, more usefully, the document that draws the line between **what
the automated evidence proves and what it structurally cannot**.

That line is the most important content here. A reader who takes away only one
thing should take away §5.

---

## 2. Test levels

| Level | Where it lives | What it covers | Who runs it |
|---|---|---|---|
| **Unit** | Co-located `test_*.py` beside the code under test | Pure logic, decision functions, SQL construction, prompt composition, parsing | Every developer, every change |
| **Integration (in-process)** | Same suites, driving the real ASGI app via `bootstrap_application()` | That an endpoint is mounted, reachable and gated by the permission it claims | Every developer, every change |
| **Contract (fork patches)** | `test_p*_task*_patch.py` | That a patch applies cleanly and that the shipped JavaScript/ERB behaves as described, executed in Node or Ruby | Every developer, every patch change |
| **Both-flag-states gate** | `deploy/scripts/check-suites-both-flag-states.sh` | Both suites with every flag off, then every flag on | **Before claiming any package done** |
| **System integration (SIT)** | `../sit/2026-08-08-sit-script.md` | Every integration point against real infrastructure, against a pre-agreed script | Once per release, with the client |
| **User acceptance (UAT)** | `../governance/milestone-artefacts/08-user-acceptance-sign-off.md` | Business outcomes, by PROTON's own users | PROTON |

### 2.1 Unit and in-process integration

The two suites, and the exact commands — **the prefixes are mandatory, not
decorative**:

```bash
# Backend. Without GOOGLE_API_KEY, five modules fail at COLLECTION,
# because google.genai.Client() demands a key at import time. The symptom
# reads as a broken suite rather than as a missing variable.
cd backend/apps/backend && GOOGLE_API_KEY=test-key uv run pytest -q

# Agent service. Bare `pytest` and `python3` cannot spawn in the
# development sandbox; use the venv path.
cd agent && ./.venv/bin/python -m pytest -q
```

Neither suite touches Postgres, the real Chatwoot API, Gemini, BigQuery, Twilio
or Firestore. The agent service's tests point `AGENT_DATABASE_URL` at a throwaway
SQLite file; HTTP is stubbed with `respx`; model clients are injected. This is
what makes the suites fast and deterministic, and it is also §5.

### 2.2 The both-flag-states gate, and why it is separate

Almost every feature in this programme ships behind a default-off flag, so **the
ship-dark path is the one the suite exercises by default and the on-path is code
nobody runs until a tenant opts in.** The gate runs both suites twice, once with
every flag forced on.

It has caught defects plain `pytest` could not, in three distinct classes:

1. A dealer record whose shape predated a new field raised `AttributeError` with
   `ESCALATION_CC_DEALER=true`, killing the entire dealer forward.
2. Three tests asserting *"this flag defaults to false"* by constructing
   `Settings()` without clearing the environment first. Under the flags-on run
   they were asserting **the exact opposite of their own names, and passing.**
3. Minimal test stubs in one router that lacked a field a sibling had added — 42
   failures that the flags-off run never showed.

**Two rules follow, and both come from a real failure:**

- **Every new default-off flag must be added to `FLAGS_ON`.** A flag missing from
  that list is a flag whose on-path has never been executed. Two settings sat
  missing for an entire package.
- **A tunable must be set to a non-default value in that list, not just to a
  boolean's `true`.** A tunable left at its own default makes the on-run walk the
  identical path to the off-run, so it catches nothing. `FAQ_KEYWORD_WEIGHT=0.5`,
  `CSAT_RANKING_MIN_SAMPLES=25` and `ANOMALY_HOURLY_ZSCORE_K=2.5` are set that way
  deliberately.

**Standing gap (risk register R17):** P11, P12 and P13's flags are **not** in
`FLAGS_ON`, because those packages never reached the step that adds them. The
on-path of all sixteen `PHONE_*` settings has therefore never been executed.

**And the gate itself was silently red for several commits**, because plain
`pytest` was run in its place and plain `pytest` cannot reveal it. A test that
asserts the flag list is complete does not help: **flag-membership tests cannot
detect a red ON run; only running the script can.**

---

## 3. Conventions this programme actually follows

These are described because they are the reason the evidence is worth anything,
not as aspirations.

### 3.1 Test-driven, with the test named for what it checks

Tests are written before the implementation, and the naming convention is
load-bearing: a test is named for the behaviour it asserts, in a sentence
(`test_offline_is_catalogued_and_does_not_count_as_unavailable`). This is not
style. Two defects in this programme were tests whose **names promised more than
their bodies checked** — one asserted only that a route was findable, so deleting
the line that bound a handler to it left the test green. It was replaced with a
spy, and the fix was verified by **deliberately sabotaging the production line and
confirming the test went red.**

**A test whose name overclaims is worse than a missing test**, because it is
counted as coverage.

### 3.2 Prefer a test that drives the real application

Nine times in this programme something shipped correct, unit-tested and unable to
run (risk register R16). **Every one survived because its test called the inner
function and passed the arguments by hand, one layer below the bug.**

So the convention is: prove reachability through `bootstrap_application()` —
asserting a **401 rather than a 404** — in preference to calling the factory
directly. The `test_p*_wiring.py` modules exist for exactly this and are the
highest-value tests in the repository.

### 3.3 Assert defaults only after clearing the environment

`Settings(_env_file=None)` **does not** stop pydantic-settings reading
`os.environ`. A defaults assertion must delete the variable first **and assert
the delete worked**, routed through one shared helper so that removing the
clearing loop fails the test. Six vacuous tests were found before this became a
convention.

### 3.4 A zero is a claim; a blank is a statement about instrumentation

The single most-cited rule in this programme's engineering record:

> A value we did not measure renders as unavailable, never as `0`.

Applied consistently: missing model usage metadata is `None`, never `0`; an
unmetered surface is never `0` cost; an agent with no presence events is not
"0 minutes available"; and every rate metric returns its denominator, because a
score without a sample size is how a measurement becomes a grievance.

**Tests enforce this rather than reviewers remembering it.** One fails the build
on the appearance of `total`, any `total_*` or any `*_total` in the cost report,
because an unqualified total would imply a complete inventory that does not exist.

### 3.5 A patch test is named for what it can honestly check

The development environment cannot reach github.com, so no test can honestly be
named "applies onto the pinned upstream ref". The convention is to implement the
verifiable substitute — apply the patch to a synthetic pre-image reconstructed
from already-merged patches, with a real `git apply` — and **name the test for
exactly that**, with the limit stated in the module docstring.

Two further practices, established late and worth carrying forward:

- **Generate hunks with a real `git diff` against a synthetic pre-image**, never
  by writing `@@` counts by hand. A hand edit silently broke one patch's
  arithmetic (80 versus 107 added lines).
- **Execute the shipped code, not a re-implementation of it.** Where Node or Ruby
  is available, extract the JavaScript or ERB from the applied patch and run *that*.
  A Python re-implementation of shipped JavaScript tests the re-implementation.

### 3.6 Documentation is held to the same standard as code

Documentation defects are tracked as defects. This programme has had to correct a
client handbook describing a feature that did not exist, a README claiming a flag
had a consumer it lacked, two report descriptions presenting a status-derived
field as an AI-versus-human measure, and environment documentation describing a
flag as gating something it does not gate.

The rule is *exactly as true as the code, no more*, and where the source is code
the document is **generated** — `../handover/configuration.md` is produced by
`scripts/generate-config-doc.py` and a test fails the build while the committed
copy is stale.

---

## 4. Current evidence

Run on 2026-08-11 on branch `dev-yuda`:

| Suite | Result | Command |
|---|---|---|
| Backend (`backend/apps/backend`) | **2858 passed, 1 skipped** | `GOOGLE_API_KEY=test-key uv run pytest -q` |
| Agent service (`agent/`) | **438 passed** | `./.venv/bin/python -m pytest -q` |
| Configuration-document tests (`scripts/`) | **8 passed** | `uv run pytest ../../../scripts/test_generate_config_doc.py -q` |
| Both-flag-states gate | Green in both states **at the last recorded run**, which was before the in-flight work described below | `deploy/scripts/check-suites-both-flag-states.sh` |

Growth across this programme: the backend suite went from 2193 to 2858 tests
(+665) with zero regressions at any package boundary. Each package's ledger under
`.superpowers/sdd/` records the count at every commit, which is how a regression
would be attributed rather than argued about — for P1–P10; **P11 to P14 have no
ledger** (risk register R18).

**Two qualifications on the backend figure, because a test count quoted without
them is misleading.**

First, the number was **2838** at the last committed, quiet-tree measurement. The
20 additional tests are in-flight P11 wiring work that was uncommitted in the
working tree when this run was taken, so `2858` is a measurement of a tree that
includes another workstream's uncommitted changes. It should be re-taken on a
quiet tree before being quoted in a status report. That this needs saying at all
is itself a process finding.

Second, and more important: **a test count is a measure of volume, not of
coverage, and on this codebase it is specifically not a measure of
reachability** — see §3.2. Nine features had green tests and could not run.

---

## 5. What none of this proves

**This is the section to read before signing anything.** Everything in §4 was
produced in an environment with **no live credentials of any kind**. Specifically:

| Dependency | State in every test run to date |
|---|---|
| **Gemini / Vertex AI** | `GOOGLE_API_KEY=test-key`. Every model client stubbed. **No real model has ever answered.** |
| **BigQuery** | No project, no credentials. **33 views authored, none ever executed.** |
| **Twilio** | No account, no number. **No real call or WhatsApp message has ever been sent or received.** |
| **Postgres** | SQLite via `aiosqlite`. Same models, different engine — and no migration has run against a populated table. |
| **Firestore** | In-memory fakes. |
| **Chatwoot** | HTTP stubbed with `respx`. **No fork patch has been applied to a real checkout or built into an image.** |
| **Upstream GitHub** | Unreachable from the development environment. |

The concrete consequences, each of which is a risk-register row:

1. **No AI quality figure exists.** The calibration harness runs against a stub
   and scores 97–100% because the same author wrote both the labels and the rules
   being scored. **Those numbers must never be quoted** (R8).
2. **No SQL has been executed.** Three specific constructs — a `RANK() OVER` inside
   a `CASE`, a window over an aggregate, and two different time-zone syntaxes
   agreeing on the same bucket — will either work or fail on the first real run
   (R13).
3. **No voice path has run.** Inbound calls, DTMF, handoff, recording and
   voicemail are all unit-tested against fakes (R10).
4. **No UI has been seen.** Eight fork patches (0052–0059) have never been applied
   to real upstream, and several stack on each other, so a line-number fix to a
   lower patch cascades upward (R7).
5. **No restore has been rehearsed.** A backup that has never been restored is a
   hypothesis (R12).
6. **A green suite is not evidence of reachability** on this codebase (R16).

**The honest summary:** the automated evidence is strong on logic and structure,
and silent on integration. Closing that gap is what the SIT exists for, and the
SIT cannot be executed in the development environment either — it needs a
non-production environment with real credentials.

---

## 6. Defect severity

| Severity | Definition | Response | Release gate |
|---|---|---|---|
| **S1 Critical** | Data loss or corruption; a security or access-control failure; total unavailability of a channel or tenant; a customer-facing message sent in error | Immediate. Work stops. | **Blocks release. No exceptions.** |
| **S2 Major** | A committed requirement does not function; a reporting figure is wrong in a way a reader would act on; an escalation silently fails to reach its recipient | Fix before release, or descope the requirement explicitly and tell the client | **Blocks release** unless formally descoped |
| **S3 Moderate** | A feature works but a documented path to it does not; a figure is right but unlabelled; a degraded state is not visible to the user | Fix in the release if the schedule allows; otherwise scheduled with a date | Does not block; **must be listed** in the release note |
| **S4 Minor** | Cosmetic, wording, log-only, or a counter that is wrong while behaviour is right | Backlog | Does not block |

Three classifications specific to this programme, because they have each been
argued about:

- **A feature that is built, tested and unreachable is S2, not S4.** It does not
  function, whatever its test suite says.
- **A documentation overclaim is S2 when it is client-facing.** A handbook
  describing a feature that does not exist causes exactly the damage a broken
  feature causes, and is discovered later.
- **A value rendered as `0` where it should render as unavailable is S2.** It is a
  false measurement, not a display defect — and it will be quoted.

Currently open and accepted: **13 S4 items in P7** and **7 + 14 deferred S4 items
in P6**, each recorded in its package ledger with the reason. No S1 or S2 is open.

---

## 7. Entry and exit criteria

### 7.1 Per change

**Entry:** a written specification and plan exist; the change is on `dev-yuda`,
never `main`.

**Exit, all required:**
- `pytest --collect-only -q` clean **before** committing. A killed session once
  left a broken import naming a symbol it never created, and 27 modules failed at
  *collection* — worse than a red test, because it hides everything else.
- Both suites green.
- Any new flag added to `FLAGS_ON`, with a non-default value if it is a tunable.
- Every new setting present in both `config.py` and `deploy/tenants/example.env`
  — enforced by `scripts/test_generate_config_doc.py`.
- Reachability proved through the real app, not the factory.
- `configuration.md` regenerated if any setting changed.

### 7.2 Per package

- The both-flag-states **gate script run**, not plain `pytest`.
- A code review, with every finding either fixed or recorded with a reason.
- Every caveat, gap and owed item written into
  `../../analysis/2026-08-09-blocked-work-register.md`.
- No claim in any commit message, docstring or document that implies a
  verification which did not happen.

### 7.3 Per milestone / release

- All S1 and S2 defects closed or formally descoped **in writing**.
- The SIT executed against the pre-agreed script, with failures reported.
- The risk register reviewed and re-dated.
- Every generated artefact regenerated.
- **Every fork patch in the release built into an image and seen rendering**, not
  merely applying. This is the criterion this programme has never yet met.

---

## 8. Coverage expectations

**Line coverage is not a target here, deliberately.** On a codebase where nine
features had green tests and could not run, a percentage would have been met
throughout and would have measured nothing. The expectations are behavioural
instead:

1. **Every endpoint proved reachable through the real app** — 401 rather than 404.
2. **Every flag traced to a consumer**, every router to a mount, every store to a
   reader. If something is genuinely unreachable, it is *declared* — in the
   docstring, in a named test, and in the blocked-work register.
3. **Both flag states green.**
4. **Every default-off flag's on-path executed at least once.**
5. **Every "we did not measure this" path asserted**, so it cannot silently become
   a `0`.
6. **Every documented operator path walked**, because a documented path that does
   not work is the defect the client finds first.

Requirement 2 is the one that would have prevented nine defects. Requirement 5 is
the one that keeps the reporting honest.

---

## 9. Roles

| Role | Responsibility | Named? |
|---|---|---|
| Developer | Tests before implementation; both suites; reachability | Yes |
| Reviewer | Findings fixed or recorded with a reason | Yes |
| Release manager | The gate script; S1/S2 closure; regenerating artefacts | **No — unassigned** |
| SIT coordinator | Agreeing the script with PROTON, then executing it | **No — unassigned** |
| PROTON UAT owner | Acceptance against business outcomes | **No — unassigned** |
| Governance forum | Escalation for descoping and risk acceptance | **No — does not exist (R-GOV-1)** |

**Four of these six roles are unassigned, and that is a finding rather than an
omission from this document.** The §6 requirements covering governance
organisation and reporting cadence are GAP because nobody has written them; until
a forum exists, this plan has no body to escalate a descoping decision to. See
`risk-register.md` R-GOV-1.
