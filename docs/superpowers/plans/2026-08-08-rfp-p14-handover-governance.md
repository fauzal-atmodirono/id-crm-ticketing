# P14 — Handover, Enablement & Governance Artefacts: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn strong raw material into the named artefacts the RFP asks to sign against — and produce the two things that genuinely do not exist in any form: role-differentiated training, and a SIT report against a script the client agreed in advance.

**Architecture:** One source, several audience renderings. The configuration document is **generated** from the `Settings` classes so it cannot drift; the training curricula are audience filters over the existing feature-guide source rather than three forks of it.

**Tech Stack:** Markdown → docx via the existing feature-guide build pipeline, python-docx, a settings-introspection generator, pytest for the generated-doc assertions.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p14-handover-governance-design.md`

## ⚠️ Sequencing

**Task 1 (the SIT script) runs in wave 1, not wave 4.** §2.2.6 requires a
**pre-agreed** script; a script agreed after execution is a report. Everything
else in this plan waits until P1–P13 exist, or it documents features that will
change.

## Global Constraints

- **Generated, not hand-written, wherever the source is code.** This programme
  alone adds ~40 settings; a hand-maintained configuration document is wrong
  within a sprint.
- **One training source, three audience filters.** Three separate decks will
  diverge, and the frontline deck is the one that will silently go stale.
- **Every sign-off artefact carries a traceability line** to the evidence it
  attests to.
- **Name the uncomfortable risks.** The register's value is entirely in the rows
  nobody wants to write.
- **Exercises run on a sandbox tenant.** Training an agent to escalate by
  escalating a real complaint is not an exercise.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `docs/client-materials/sit/2026-08-08-sit-script.md` | **New, wave 1.** The pre-agreed integration test script |
| `docs/client-materials/sit/2026-08-08-sit-report.md` | **New, wave 4.** Its execution |
| `docs/client-materials/training/{agent,supervisor,admin}/` | **New.** Three curricula |
| `scripts/generate-config-doc.py` | **New.** Settings → configuration document |
| `docs/client-materials/handover/architecture.md` | **New.** Map + trust boundaries |
| `docs/client-materials/handover/api-schema.md` | **New.** Curated OpenAPI rendering |
| `docs/client-materials/governance/qa-plan.md` | **New.** |
| `docs/client-materials/governance/risk-register.md` | **New.** |
| `docs/client-materials/governance/milestone-artefacts/` | **New.** The ten named documents |

---

### Task 1: SIT script *(do this in wave 1)*

**Files:**
- Create: `docs/client-materials/sit/2026-08-08-sit-script.md`

**Cover every integration point, with a test id, preconditions, steps, expected
result and a pass/fail column:**

| Integration | Cases |
|---|---|
| Chatwoot ↔ agent | Webhook signature, dedupe, all event types |
| agent ↔ backend | Persona fetch, escalation notify, assist, fail-open on outage |
| Twilio WhatsApp | Inbound, outbound, media, delivery status |
| Twilio Voice | Inbound, DTMF, handoff, recording, voicemail |
| Email | IMAP in, SMTP out, auto-ack, escalation threads, reply linking |
| BigQuery | Sync, view freshness, schema compatibility |
| Firestore | Every store: PIC, dealer, routing, SLA policy, taxonomy, targets |
| Gemini / Vertex | Decision, assist, embeddings, KB search |
| DMS shell | **`not_connected` behaviour is itself testable** |

**Two integration points must be listed as untestable, with the reason:**

```
- [ ] DMS/TSP: no real endpoint exists — no API specification, no sandbox (Q4).
      Only the shell's not_connected behaviour is testable.
- [ ] Facebook / Instagram: no inbox can be created — blocked on Meta Business
      verification, a client-side process gate.
```

Listing them and why is a stronger deliverable than a script that quietly covers
only what happens to work.

**Then: send it to the client and get it agreed before wave 3 completes.** That
step is the requirement.

```
- [ ] Script drafted covering all nine integration areas
- [ ] The two untestable points listed with reasons
- [ ] Sent to PRO-NET
- [ ] Agreement received and recorded, with a date
```

---

### Task 2: Generated configuration document

**Files:**
- Create: `scripts/generate-config-doc.py`
- Create: `docs/client-materials/handover/configuration.md` (generated)
- Create: its test

**Interfaces:**
- Consumes: `agent/app/config.py::Settings`, `backend/.../platform/config.py::Settings`, `deploy/tenants/example.env`.
- Produces: every setting with its default, type, description, blast radius and who may change it.

**Tests first:**

```python
def test_every_setting_in_both_settings_classes_appears_in_the_generated_document():
def test_every_setting_in_example_env_appears():
def test_a_setting_present_in_code_but_missing_from_example_env_is_flagged():
def test_the_generator_is_deterministic():
def test_each_entry_carries_a_default_and_a_blast_radius():
```

**Test three is worth more than the document.** It is a standing check on
CLAUDE.md's rule that a new env var must appear in both `config.py` and
`example.env` — and it will find drift, because ~40 settings are being added by
this programme.

**Verify:** `pytest scripts/test_generate_config_doc.py -q`

---

### Task 3: Architecture map and API schema chart

**Files:**
- Create: `docs/client-materials/handover/architecture.md`
- Create: `docs/client-materials/handover/api-schema.md`

**Architecture map requirements:**
- One diagram: Caddy, Chatwoot (forked SPA / Rails / Sidekiq), `agent`,
  `backend`, Postgres, Firestore, BigQuery, Vertex, Twilio, Gemini.
- **Trust boundaries and the data crossing them** — what leaves the VM, where
  customer PII lives, which third parties receive what. `CLAUDE.md` describes the
  system well for an engineer joining the repo; this document's reader wants to
  know where the customer's phone number goes.
- Multi-tenancy: what is shared (Caddy, Postgres, Mailpit) and what is per-tenant.
- **The single-VM reality stated plainly**, cross-referencing P13's scope note.

**API schema chart requirements:**
- Endpoints grouped by capability, not alphabetically.
- Auth and required permission shown per endpoint.
- Generated from OpenAPI where possible so it does not drift.

```
- [ ] Diagram renders and is legible at A4
- [ ] Trust boundaries and cross-boundary data documented
- [ ] Reviewed by someone who has not worked on this repo; their questions became revisions
- [ ] Every endpoint's permission requirement shown
```

---

### Task 4: Three training curricula

**Files:**
- Create: `docs/client-materials/training/{agent,supervisor,admin}/`
- Modify: the feature-guide build pipeline to accept an audience filter

**Requirements:**
- One source (the v3 feature-guide source), three audience filters. Not three
  copies.
- Each: a facilitator deck, a hands-on exercise set, a competency checklist.
- **Exercises run on a sandbox tenant**, with a reset script so a cohort can
  repeat them.
- A delivery plan: schedule, prerequisites, refresher cadence.

**Content per role** — see the spec's §3.1 table. The admin curriculum must cover
everything this programme added: taxonomy admin (P10), targets (P5), escalation
routing, RBAC and data scopes (P10), alert preferences (P9), the workforce
dashboard (P6).

```
- [ ] Three curricula render from one source
- [ ] Every exercise dry-run on the sandbox tenant and completable as written
- [ ] Competency checklists per role
- [ ] Sandbox reset script works between cohorts
- [ ] Delivery plan with schedule and refresher cadence
```

---

### Task 5: QA plan and risk register

**Files:**
- Create: `docs/client-materials/governance/qa-plan.md`
- Create: `docs/client-materials/governance/risk-register.md`

**QA plan:** test levels, coverage expectations, the TDD convention these plans
use, defect severity definitions, entry/exit criteria per milestone. Cite the
real evidence — `agent/tests/`, `backend/.../test_*.py`, the spec → plan →
verification workflow.

**Risk register — the six rows from the spec's §3.3 must all be present:**

```
- [ ] Single-VM cannot meet 99.9% / P1 <2h  (R17, commercial)
- [ ] 49-patch fork rebase per upstream security release  (P13, standing cost)
- [ ] DMS/TSP has no API spec; 8 requirements depend on it  (Q4)
- [ ] Meta verification blocks social entirely  (client-side gate, needs a date)
- [ ] No call queue; 6 of 14 control items unmeasurable  (R9)
- [ ] The vendor response marks >=17 unbuilt items "Fully OOTB"  (reconcile now)
```

**The last row is the most urgent item in the whole analysis and it is not an
engineering task.** It goes in the register because that is the artefact read by
the people who can act on it. Do not soften it.

---

### Task 6: The ten milestone artefacts

**Files:**
- Create: `docs/client-materials/governance/milestone-artefacts/`

Produce each in the named form, with a traceability line to its evidence:

```
- [ ] Technical design document      → the design specs, consolidated
- [ ] Test plan                      → task 5's QA plan
- [ ] Test report                    → suite output + task 7's SIT report
- [ ] Deployment checklist           → README + deploy runbooks
- [ ] Training manual                → task 4
- [ ] Configuration document         → task 2
- [ ] Architecture document          → task 3
- [ ] User acceptance sign-off       → template + UAT script
- [ ] Handover checklist             → new
- [ ] Support handover               → P13 runbooks + the support model
```

**A sign-off document that cannot be traced to what it attests to is a signature
on nothing.** Every artefact names its evidence path.

---

### Task 7: Execute the SIT and write the report

**Files:**
- Create: `docs/client-materials/sit/2026-08-08-sit-report.md`

**Execute task 1's agreed script** against a non-production environment (P13
task 7) and report:

```
- [ ] Every test case executed and its result recorded
- [ ] Failures reported, not omitted
- [ ] The two untestable integration points restated with reasons
- [ ] Defects raised with severity per the QA plan
- [ ] Re-test results for any fixed defect
- [ ] Sign-off section referencing the agreed script and its agreement date
```

**Report failures.** A SIT report with no failures across nine integration areas
is not a credible document, and the client's own reviewers will read it that way.

---

### Task 8: Index and cross-reference

**Files:**
- Modify: `README.md`
- Create: `docs/client-materials/HANDOVER-INDEX.md`

```
- [ ] Every artefact indexed with its RFP requirement number
- [ ] The five §6 items this package does NOT close are listed with why
- [ ] The index states which artefacts are generated and must be regenerated after code changes
```

**The note on §6 (the deliverable):**

> Five §6 requirements are **not** closed by this package: delivery approach,
> scope & change management, governance organisation, and the project dashboard
> and status cadence. These are organisational commitments — a steering committee
> must be named, a change process agreed, a reporting cadence set. They are GAP
> because nobody has written them, not because anything is missing from the
> product, and they need a delivery manager rather than an engineer.

---

## Definition of done

- [ ] The SIT script was agreed by the client **before** it was executed, with the date recorded.
- [ ] The configuration document is generated, and its drift test passes against both `Settings` classes.
- [ ] The architecture map was reviewed by someone outside the project and revised on their questions.
- [ ] Three curricula render from one source; every exercise completable on the sandbox tenant.
- [ ] The risk register contains all six named rows, including the vendor-response reconciliation.
- [ ] Ten milestone artefacts exist, each with a traceability line.
- [ ] The SIT report includes failures.
- [ ] The five unclosed §6 items are stated in the index.
- [ ] Nothing merged to `main`.
