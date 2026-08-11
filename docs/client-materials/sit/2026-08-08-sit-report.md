# System integration test report

**Programme:** PROTON e.MAS CRM enhancement (RFP 2026_028)
**Closes:** §2.2.6 (SIT/QA report against a pre-agreed script) — the report half
**Script under test:** `2026-08-08-sit-script.md` v1.0
**Report status:** **NOT EXECUTED**

---

## 1. Result

> **The SIT has not been executed. No test case in the script has been run, and
> this report records no results.**
>
> | | |
> |---|---|
> | Cases in the agreed script | 155 |
> | Cases executed | **0** |
> | Cases passed | **0** |
> | Cases failed | **0** |
> | Cases blocked | **155** |
> | Script agreement date | **None. The script has not been sent to PRO-NET.** |

**This document exists in this state deliberately, and the state is the finding.**

§2.2.6 requires a report against a **pre-agreed** script. Two things must be true
before this report can contain results, and neither is true today: PROTON has not
agreed the script, and no environment exists in which the cases could be run.
Producing a report that asserted otherwise would be the single most damaging thing
this package could deliver — a signed integration-test report is exactly the kind
of document that gets cited in a client review, and one containing fabricated
results would discredit every other artefact alongside it.

**So: 0 executed, 155 blocked, and the reasons below.** A reader who wants the
integration risk picture should read §4, which states what is unverified, rather
than inferring it from an absence of failures.

---

## 2. Why it has not been executed

Two independent blockers. Both must clear.

### 2.1 The script is not agreed (process blocker)

`2026-08-08-sit-script.md` is drafted and covers all nine integration areas plus
access control, with the two untestable integration points listed and reasoned.
It has **not been sent to PRO-NET** and **not been agreed**.

**This blocker is cheap to clear and must be cleared first.** A script agreed
after execution is a description of what the vendor chose to check, and the
client's reviewers will read it that way — correctly. The sequence is: send,
review, agree, record the date, *then* execute.

### 2.2 No environment exists in which to execute it (infrastructure blocker)

The development environment has **no live credentials of any kind**. Every
prerequisite in the script's §"Prerequisites" is unmet:

| # | Prerequisite | State | Blocks |
|---|---|---|---|
| P-1 | Isolated scratch tenant | **Not provisioned** | All cases |
| P-2 | Chatwoot image built from the current fork, including patches 0052–0060 | **No image contains any of them** | All UI cases |
| P-3 | Real Gemini / Vertex credentials | `GOOGLE_API_KEY=test-key`; every model client stubbed | All AI cases |
| P-4 | BigQuery project with `ensure_views()` run | No project; **33 views never created** | All BQ cases |
| P-5 | Twilio account, voice number, WhatsApp sender | None | All WhatsApp and Voice cases |
| P-6 | Real Firestore database | In-memory fakes only | All store cases |
| P-7 | A mailbox the tester can **read** | None | All Email cases |
| P-8 | Both webhook secrets configured differently | No live Chatwoot | Webhook cases |
| P-9 | RBAC enabled with three test users | No live Chatwoot | All permission cases |

**P-2 is the prerequisite most likely to consume unplanned time.** Patches 0052
through 0059 have never been applied to a real Chatwoot checkout; they were
authored against synthetic reconstructions of their context. Several stack on each
other — `0054` on `0053`, `0056` on `0002`+`0055` — so a line-number fix-up to a
lower patch cascades upward, and `0053` is both the weakest-verified and the
lowest in a stack. **The build may fail and need repair before the SIT can start
at all.** That work belongs before the SIT is scheduled, not during it.

**P-7 is a real constraint, not an administrative one.** Every email case ends in
"the mailbox receives X". In earlier live testing the tester could drive the
entire server side and could not read the destination mailbox, which left eight
cases permanently unexecutable. A mailbox the tester can open is the difference
between an executed case and a blocked one.

---

## 3. What must happen, in order

| # | Action | Owner | Blocks |
|---|---|---|---|
| 1 | Send the script to PRO-NET; obtain and record agreement | Delivery | Everything. Do this first — it costs nothing and cannot be done retrospectively |
| 2 | Build the Chatwoot image from the current fork via Cloud Build (**off-VM, amd64**), repairing patches 0052–0060 as needed | Engineering | Every UI case |
| 3 | Provision an isolated scratch tenant | Engineering | All cases |
| 4 | Obtain Gemini/Vertex credentials, a GCP project for BigQuery, a Firestore database, a Twilio account with a voice number and WhatsApp sender, and a readable mailbox | Delivery + PROTON | Their respective areas |
| 5 | Run `ensure_views()` once, and the two owed `ALTER TABLE` migrations | Engineering | BigQuery cases |
| 6 | Enable RBAC and create the three test users | Engineering | Access-control cases |
| 7 | Execute the script, recording PASS / FAIL / BLOCKED per case | SIT coordinator (**unassigned**) | This report |
| 8 | Raise defects with severity per `../governance/qa-plan.md` §6; re-test fixes | Engineering | Sign-off |

Two items on that list have no owner: the **SIT coordinator** role is unassigned,
and there is **no governance forum** to escalate a descoping decision to. See
risk register R-GOV-1.

---

## 4. What is unverified, stated directly

Because this report contains no results, the integration risk must be stated
rather than inferred. **A reader must not read "no failures recorded" as "no
failures exist."**

The following are unverified against real infrastructure, and each one is a
category the automated suites structurally cannot reach:

| Area | Unverified |
|---|---|
| **Gemini / Vertex** | **No real model has ever answered.** No AI quality baseline exists; the stub harness scores 97–100% because the same author wrote both the labels and the rules being scored (R8) |
| **BigQuery** | **None of the 33 views has ever been executed.** Three specific SQL constructs — a `RANK() OVER` inside a `CASE`, a window over an aggregate, and two time-zone syntaxes agreeing on the same bucket — will either work or fail on the first real run (R13) |
| **Twilio Voice** | **No real call has ever been placed.** Inbound, DTMF, handoff, recording, voicemail and live transcript are all unit-tested against fakes (R10) |
| **Twilio WhatsApp** | No real message has been sent or received; media understanding has never been in front of the real model with a real photo |
| **Email** | Eight live E2E cases have never been formally executed; only two were proven in an earlier session |
| **Chatwoot fork** | **Eight patches have never been applied to a real checkout or seen in a browser** (R7) |
| **Firestore** | All store behaviour verified against in-memory fakes |
| **Postgres** | SQLite in tests; no migration has run against a populated production table |
| **Restore** | **No restore has ever been performed** from the backup script's output (R12) |
| **DMS / TSP** | Not connected and not connectable — no endpoint, specification or sandbox (R3) |
| **Facebook / Instagram** | No inbox can exist until Meta Business verification completes (R4) |

**Two expected failures are already known and should be predicted rather than
discovered**, because the code does not support them today:

- **VO-07** (recording retrieval) will fail: the handler reads an in-process
  registry that nothing in production writes to.
- **BQ-11** (`v_kb_staleness`) will fail: it reads a `faq_entries` table that does
  not exist and that nothing populates.

Both are declared in the script with their expected outcome, which is the correct
way to carry a known gap into a test run — a case that is expected to fail and
does is evidence; a case quietly omitted is not.

---

## 5. The two untestable integration points, restated

Restated here rather than only in the script, because a report reader may not
have the script to hand and these will be their first two questions.

```
- [ ] DMS/TSP: no real endpoint exists — no API specification, no sandbox (Q4).
      Only the shell's not_connected behaviour is testable.
- [ ] Facebook / Instagram: no inbox can be created — blocked on Meta Business
      verification, a client-side process gate.
```

**DMS / TSP (open question Q4).** No endpoint, no API specification, no sandbox,
no credentials. Eight requirements depend on it. What *is* testable is the
integration shell: that an unconfigured or unreachable DMS reads as **"we could
not ask"** rather than as **"this customer has no vehicles"** — a distinction that
matters because a service advisor would act on the second. The demo mock client
exists but is behind an explicit opt-in and is never the default, so a demo cannot
be mistaken for an integration. **No SIT case can validate DMS data, because the
only DMS data this platform can produce is fabricated.** Unblocked by PROTON
supplying a specification and a sandbox.

**Facebook / Instagram.** An inbox **cannot be created at all** until Meta
Business verification completes for PROTON's business account. This is not a
technical gap — the code path is Chatwoot's own and unmodified. There is nothing
to test and nothing to schedule: **there are no cases, rather than zero cases
passing.** Unblocked by PROTON completing verification; the specific ask is a
**target date**, because these cases, the training content and the acceptance
criteria all queue behind it.

---

## 6. Defects raised

**None, because no case has been executed.** This section is not empty because the
system is defect-free; it is empty because nothing has been tested.

When the run happens, each failure is recorded here with an id, the case that
found it, a severity per `../governance/qa-plan.md` §6, and its re-test result:

| Defect | Case | Severity | Description | Fix | Re-test |
|---|---|---|---|---|---|
| — | — | — | *No cases executed* | — | — |

**Expected defect profile, so a clean run is treated as suspicious rather than
reassuring.** 155 cases across ten integration areas, none of which has ever run
against real infrastructure, including a voice channel that has never handled a
call and 33 database views that have never been executed. **A first run reporting
zero failures would indicate a problem with the testing, not with the system**,
and the client's reviewers will reach that conclusion too. Report the failures.

---

## 7. Sign-off

**This report is not in a state to be signed.** The sign-off block is present so
the required form is visible, and it must not be completed until §1 contains real
results.

| | Name | Role | Date | Signature |
|---|---|---|---|---|
| Script agreed by | | PROTON | **— not agreed —** | |
| Executed by | | SIT coordinator | **— not executed —** | |
| Results accepted by | | PROTON | | |

**Traceability.** This report attests to the execution of
`docs/client-materials/sit/2026-08-08-sit-script.md` v1.0. Supporting evidence,
when the run happens: the completed case table in §1, defect records in §6, and
the automated-suite output described in `../governance/qa-plan.md` §4. The
automated evidence is **not** a substitute for this report and does not overlap
with it — `qa-plan.md` §5 states exactly what the suites cannot prove, and this
report exists to cover that gap.

**A sign-off document that cannot be traced to what it attests to is a signature
on nothing.** A sign-off on a report with no results would be worse: a signature
on an absence.
