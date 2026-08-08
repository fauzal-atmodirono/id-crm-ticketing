# P14 — Handover, Enablement & Governance Artefacts

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p14-handover-governance.md`
**Closes:** 4 PARTIAL requirements + 2.2.6 (GAP)
**Effort:** 2 weeks · **Wave:** 4 (documents what waves 1–3 built)

---

## 1. The problem, precisely

These are the non-code partials, and they share one shape: **strong raw material
exists; the named artefact does not.**

| Req | What exists | What is missing |
|---|---|---|
| 2.3.3 Product & role-based training | `docs/feature-guide/` — 13 chapters with screenshots, now at v3; `docs/analysis/2026-08-08-crm-channel-interaction-guide-v2.md` | No role differentiation (admin / supervisor / frontline), no delivery plan |
| 2.3.4 Documentation handover | Design specs, `CLAUDE.md`, deploy runbooks, testing guides, auto-generated OpenAPI | No consolidated architecture map, no functional configuration document, no API schema chart **as named deliverables** |
| 6.1 QA & risk management | `agent/tests/`, `backend/.../test_*.py`, the spec → plan → verification workflow | No formal QA/risk plan document |
| 6.3.2 Milestone sign-off artefacts | Analogues for roughly half — design specs ≈ TDD, testing guides ≈ test reports, deploy runbooks ≈ deployment checklist, feature guide ≈ training manual | None in the named form; ten documents specified |
| 2.2.6 SIT/QA report *(GAP)* | Extensive automated tests | No SIT/QA report against a **pre-agreed script** |

The distinction matters commercially. A client reviewing §6.3.2 is looking for
ten named documents to sign against milestones. Handing them a repository and
saying the information is in there is not delivery, even when it is true.

**And it is not only a naming exercise.** Two of these have real content gaps:
role-differentiated training does not exist in any form, and §2.2.6 requires a
test script **agreed with the client before execution** — a report written after
the fact against tests we chose ourselves is not what SIT means.

## 2. Why this is wave 4

P14 documents what P1–P13 built. Writing an architecture map before the working-
hours clock, the presence store, the taxonomy admin and the screen-pop exist
would produce a document that is wrong the week it is delivered.

**One exception, and it is important: §2.2.6's test script must be agreed
early.** A SIT script agreed at the end is a report, not a test. The script is
drafted in wave 1 and agreed with the client before wave 3 completes; only its
*execution and report* belong to wave 4.

## 3. Design

### 3.1 Role-differentiated training (§2.3.3)

The feature guide is a single audience-neutral document — comprehensive, and
therefore the wrong length for everybody. Three curricula, drawn from the same
source so they cannot drift:

| Role | Length | Covers |
|---|---|---|
| **Frontline agent** | ~2 h | Conversation handling across channels, AI assist and when to overrule it, case categorisation, escalation, statuses and availability, CSAT |
| **Supervisor / team leader** | ~3 h | The above, plus routing and reassignment, the workforce dashboard, SLA policies, the weekly and monthly reports, anomaly response |
| **Administrator** | ~4 h | The above, plus taxonomy admin, escalation routing, targets, RBAC and data scopes, knowledge settings, deployment and backup runbooks |

Each is a facilitator deck plus a hands-on exercise set on a **sandbox tenant** —
because training an agent to escalate by escalating a real customer's complaint
is not a viable exercise.

Built as a rendering of the feature-guide source rather than a fork of it: one
source, three audience filters. The v3 build pipeline (13 chapters, 98 sections)
already renders from source, so this extends an existing mechanism.

**Delivery plan:** session schedule, prerequisites, a competency checklist per
role, and a refresher cadence. §2.3.3 asks for training, and a deck with no
delivery plan is a file, not training.

### 3.2 Documentation handover (§2.3.4)

Three named documents:

**Architecture map.** One diagram plus commentary: Caddy, Chatwoot (forked SPA,
Rails, Sidekiq), the `agent` service, the `backend` service, Postgres, Firestore,
BigQuery, Vertex, Twilio, Gemini — and, critically, the **trust boundaries and
the data that crosses them**. `CLAUDE.md` describes this well for an engineer
joining the repo; a handover document has a different reader, who wants to know
what leaves the VM and where customer data lives.

**Functional configuration document.** Every setting, what it does, its default,
its blast radius, and who may change it. This is the highest-value item in P14
for the client's operations team, and it is now large: this programme alone adds
roughly 40 flags. Generated from the `Settings` classes rather than hand-written,
so it cannot drift.

**API schema chart.** FastAPI auto-generates OpenAPI; the deliverable is a
curated rendering — endpoints grouped by capability, with auth and permission
requirements shown per endpoint. The raw spec is a reference, not a chart.

### 3.3 QA and risk plan (§6.1)

The evidence is unusually strong: two substantial test suites, a spec → plan →
verification workflow, and this programme's own habit of stating what is *not*
delivered. What is missing is the document that describes the practice.

- **QA plan:** test levels (unit, integration, SIT, UAT), coverage expectations,
  the TDD convention these plans use, defect severity definitions, entry and exit
  criteria per milestone.
- **Risk register:** and this is where it earns its keep. The gap analysis
  already identifies the real risks, and they belong in a client-facing register
  rather than only in an internal document:

| Risk | Likelihood | Impact | Mitigation / owner |
|---|---|---|---|
| Single-VM architecture cannot meet 99.9% / P1 `<2h` | High | High | R17 multi-zone HA; **commercial decision** |
| 49-patch fork rebase on every upstream security release | High | Medium | P13's rebase tooling; priced as standing effort |
| DMS/TSP has no API spec — 8 requirements depend on it | High | High | Q4; P12 ships degraded and labelled |
| Meta verification blocks the social channel entirely | Medium | Medium | Client-side process gate; needs a date |
| No call queue — 6 of 14 control items unmeasurable | High | High | R9, separate programme |
| The vendor response marks ≥17 unbuilt items "Fully OOTB" | High | High | **Reconcile before the clarification meeting** |

**The last row is the most urgent item in the entire analysis and it is not an
engineering task.** It belongs in a risk register because that is the artefact
that gets read by the people who can act on it.

### 3.4 Milestone sign-off artefacts (§6.3.2)

Ten named documents. Roughly half have analogues; the deliverable is producing
them in the named form and mapping each to its evidence:

| Named artefact | Source |
|---|---|
| Technical design document | The design specs, consolidated |
| Test plan | §3.3's QA plan |
| Test report | Suite output + the SIT report (§3.5) |
| Deployment checklist | `README.md` + the deploy runbooks |
| Training manual | §3.1's curricula |
| Configuration document | §3.2 |
| Architecture document | §3.2 |
| User acceptance sign-off | Template + the UAT script |
| Handover checklist | New |
| Support handover | P13's runbooks + the support model |

**Each carries a traceability line back to its evidence in the repository.** A
sign-off document that cannot be traced to what it attests to is a signature on
nothing.

### 3.5 SIT/QA report against an agreed script (§2.2.6)

The one genuine GAP here, and the only one with a sequencing requirement.

1. **Draft the SIT script early** — wave 1 — covering every integration point:
   Chatwoot ↔ agent, agent ↔ backend, Twilio (WhatsApp and voice), email
   in/out, BigQuery sync, Firestore, Gemini, and the DMS *shell* (its
   `not_connected` behaviour is itself testable).
2. **Agree it with the client before execution.** §2.2.6 says "against a
   pre-agreed script", and a script agreed afterwards is a report.
3. **Execute and report**, including failures.

**Two integration points cannot be tested and the script must say so rather than
omitting them:** DMS/TSP has no real endpoint (Q4), and the social channels have
no inbox (Meta verification). Listing them as untestable-and-why is a stronger
deliverable than a script that quietly covers only what happens to work.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Documents written before the features exist | Wave 4, except the SIT script |
| The SIT script is agreed too late to mean anything | Drafted in wave 1, agreed before wave 3 completes |
| The configuration document drifts from the code | Generated from the `Settings` classes |
| Three training decks fork and diverge | One source, three audience filters |
| Sign-off documents attest to nothing checkable | Traceability line per artefact |
| The risk register omits the uncomfortable rows | The four hardest risks are named in this spec so they cannot be quietly dropped |

## 5. Testing

Documentation is verified by use, not by assertion:

- The configuration document is **generated** and a test asserts every setting in
  both `Settings` classes appears in it.
- The architecture map is reviewed by someone who has not worked on the repo, and
  their questions become revisions.
- Each training curriculum is dry-run against the sandbox tenant; every exercise
  must be completable as written.
- The SIT script is executed and its report includes failures.

## 6. Requirements closed

2.3.3, 2.3.4, 6.1 (QA & risk), 6.3.2, and **2.2.6** (GAP).

**Not closed:** the other five §6 items (6.1 delivery approach, 6.1 scope &
change management, 6.2.1 governance organisation, 6.3.1 project dashboard &
cadence) are commercial and organisational — a steering committee has to be
named, a change process agreed, a reporting cadence set. They are GAP because
nobody has written them, not because anything is missing from the product, and
they need a delivery manager rather than an engineer.
