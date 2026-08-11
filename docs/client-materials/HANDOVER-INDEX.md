# Handover index

**Programme:** PROTON e.MAS CRM enhancement (RFP 2026_028)
**Every handover, enablement and governance artefact, with its RFP requirement.**

---

## 1. Read these three first

Whatever else you do with this repository, these three are the ones that change
what you believe about it:

| Document | Why |
|---|---|
| [`analysis/2026-08-09-blocked-work-register.md`](../analysis/2026-08-09-blocked-work-register.md) | Everything that is built-but-unreachable, deliberately blank, or owed. **It pre-answers most "is this a bug?" questions.** Not a client deliverable; the most operationally valuable file here |
| [`governance/risk-register.md`](governance/risk-register.md) | The nineteen risks, including the six the design named in advance so they could not be dropped. R6 is the most urgent item in the programme and is not engineering work |
| [`governance/qa-plan.md`](governance/qa-plan.md) §5 | **What none of the automated evidence proves.** Two large suites, and no real Gemini, BigQuery, Twilio, Postgres or Chatwoot has ever been touched |

---

## 2. Artefacts by RFP requirement

### §2.2.6 — SIT/QA report against a pre-agreed script *(was GAP)*

| Artefact | Path | Status |
|---|---|---|
| SIT script — 155 cases, ten areas | [`sit/2026-08-08-sit-script.md`](sit/2026-08-08-sit-script.md) | Drafted. **NOT sent to PRO-NET, NOT agreed** |
| SIT report | [`sit/2026-08-08-sit-report.md`](sit/2026-08-08-sit-report.md) | **NOT EXECUTED** — 0 of 155 cases run |

**The requirement is not met, and the agreement step is why.** §2.2.6 asks for a
report against a *pre-agreed* script; a script agreed afterwards is a description
of what the vendor chose to check. Sending the script costs nothing and cannot be
done retrospectively, so it is the single cheapest item on this page.

### §2.3.3 — Product and role-based training

| Artefact | Path | Status |
|---|---|---|
| Operator handbook, v3 — 14 chapters, 108 sections | `feature-guide-src-v3/` | **Complete** |
| Frontline agent curriculum — 54 topics, 12 exercises | [`training/agent/`](training/agent/facilitator-deck.md) | Complete, **generated** |
| Supervisor curriculum — 71 topics, 20 exercises | [`training/supervisor/`](training/supervisor/facilitator-deck.md) | Complete, **generated** |
| Administrator curriculum — 108 topics, 32 exercises | [`training/admin/`](training/admin/facilitator-deck.md) | Complete, **generated** |
| Competency checklist per cohort | `training/*/competency-checklist.md` | Complete, **generated** |
| Delivery plan — schedule, prerequisites, refresher cadence | [`training/delivery-plan.md`](training/delivery-plan.md) | Complete |
| Sandbox reset between cohorts | `training/reset-sandbox-tenant.sh` | Written, **NEVER EXECUTED** |
| Audience tag coverage (the filter's audit table) | [`training/tag-coverage.md`](training/tag-coverage.md) | **Generated** |

**Met on the artefact side; the delivery half is owed.** All three curricula
render from the one handbook source through an audience filter in
`build_crm_feature_guide.py` — `<!-- TRAINING: audience=... -->` markers on each
`##` section, cumulative (`agent` < `supervisor` < `admin`) — so they cannot
diverge from the guide or from each other. Three hand-maintained copies would
have satisfied this table and lost that property.

**Two things a reader must not assume.** No exercise has been dry-run: no
sandbox tenant has been provisioned, so "completable as written" is owed rather
than verified. And **nine topics cannot be taught yet** — the workforce
dashboard, AI quality, AI cost, targets, alert preferences, taxonomy admin and
the Roles & Permissions redesign are all behind unbuilt fork patches or
uncreated BigQuery views, and data scopes are *deliberately* not taught because
`DATA_SCOPED_RBAC_ENABLED` restricts nothing (R16). Each curriculum ends with
that list and its reasons. See
[milestone artefact 05](governance/milestone-artefacts/05-training-manual.md)
and [`training/delivery-plan.md`](training/delivery-plan.md) §7.

### §2.3.4 — Documentation handover

| Artefact | Path | Status |
|---|---|---|
| Architecture map, trust boundaries, multi-tenancy | [`handover/architecture.md`](handover/architecture.md) | Complete; **outside review not done** |
| API schema chart | [`handover/api-schema.md`](handover/api-schema.md) | Complete, verified against a booted app |
| Functional configuration document | [`handover/configuration.md`](handover/configuration.md) | Complete, **generated** |

**Met.** One caveat: the architecture map's own definition of done requires review
by someone who has not worked on this repository, and that has not happened.

### §6.1 — QA and risk management

| Artefact | Path | Status |
|---|---|---|
| QA plan | [`governance/qa-plan.md`](governance/qa-plan.md) | Complete |
| Risk register — 19 rows | [`governance/risk-register.md`](governance/risk-register.md) | Complete |

**Met** for the QA and risk halves. The other §6.1 items are not closed — see §4.

### §6.3.2 — Milestone sign-off artefacts

Ten named documents in [`governance/milestone-artefacts/`](governance/milestone-artefacts/00-README.md),
each with a traceability line to its evidence.

| # | Artefact | Ready to sign? |
|---|---|---|
| 01 | Technical design document | Yes |
| 02 | Test plan | Yes |
| 03 | Test report | **No** — the SIT has not run |
| 04 | Deployment checklist | Yes, with the fork-build caveat |
| 05 | Training manual | Yes as a manual; the exercises have not been dry-run |
| 06 | Configuration document | Yes |
| 07 | Architecture document | **No** — outside review not done |
| 08 | User acceptance sign-off | **No** — UAT has not been run |
| 09 | Handover checklist | Yes as a checklist; items within are open |
| 10 | Support handover | Partly — the support model is undefined |

**Seven of ten are ready; 03, 07 and 08 are not.** Those three are not a
paperwork problem, and presenting any of them as signable would repeat a mistake
this programme has already had to correct four times in client-facing text.

---

## 3. Which artefacts are generated, and must be regenerated

**Eleven artefacts are generated. Treat every other document as hand-written and
therefore capable of going stale.**

| Artefact | Generated from | Regenerate with | Enforced by |
|---|---|---|---|
| [`handover/configuration.md`](handover/configuration.md) | Both `Settings` classes + `example.env` + both compose files | `python3 scripts/generate-config-doc.py` | `scripts/test_generate_config_doc.py` fails while the committed copy is stale |
| The ten files under [`training/`](training/README.md) — three curricula plus the coverage table | `feature-guide-src-v3/`, filtered by each section's `<!-- TRAINING: audience=... -->` marker | `python3 docs/client-materials/build_crm_feature_guide.py --curricula` | `--check` exits 1 while a committed copy is stale; `scripts/test_build_feature_guide_audiences.py` runs it |

**Regenerate the curricula after any change to a handbook chapter**, and note
that a new `##` section needs its audience marker in the same edit — without one
it falls back to the administrator curriculum and is named in
`training/tag-coverage.md`.

**Regenerate the configuration document after any change to a setting**, in
either `backend/apps/backend/src/chatbot/platform/config.py` or
`agent/app/config.py`:

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-key uv run python ../../../scripts/generate-config-doc.py
GOOGLE_API_KEY=test-key uv run pytest ../../../scripts/test_generate_config_doc.py -q
```

**Do not hand-edit it.** The next run overwrites the edit, and a generated document
that was edited afterwards is worse than no generator — it carries a
"Generated, do not edit" banner that invites the reader to trust it.

### Not generated, and therefore drift-prone

| Artefact | Re-verify by | After |
|---|---|---|
| [`handover/api-schema.md`](handover/api-schema.md) | Booting `bootstrap_application()` twice (default and gated) and diffing `app.openapi()["paths"]` — the recipe is in the document's last section | **Any change to `main.py`** |
| [`handover/architecture.md`](handover/architecture.md) | Reading it | Any topology or third-party change |
| [`governance/risk-register.md`](governance/risk-register.md) | Reviewing and re-dating it | Every milestone |
| [`sit/2026-08-08-sit-script.md`](sit/2026-08-08-sit-script.md) | Re-agreeing it with PROTON | Any new integration point |

**The API schema is the one most likely to go wrong silently**, because a router
can be added, or added and left unmounted, without any document noticing. This
package found one router that was written, unit-tested and mounted nowhere; the
verification recipe exists so the next one is found in minutes.

---

## 4. The five §6 requirements this package does NOT close

> Five §6 requirements are **not** closed by this package: delivery approach,
> scope & change management, governance organisation, and the project dashboard
> and status cadence. These are organisational commitments — a steering committee
> must be named, a change process agreed, a reporting cadence set. They are GAP
> because nobody has written them, not because anything is missing from the
> product, and they need a delivery manager rather than an engineer.

| Requirement | Needs |
|---|---|
| §6.1 delivery approach | A delivery manager to define phasing and method |
| §6.1 scope & change management | An agreed change-request process, with an approver |
| §6.2.1 governance organisation | A named steering committee and escalation path |
| §6.3.1 project dashboard | A reporting format and a tool |
| §6.3.1 status cadence | An agreed meeting rhythm and attendees |

**This is why every "Owner" field in the risk register names a role rather than a
person.** There is no named governance forum, so this programme has nowhere to
escalate a descoping decision or a risk acceptance — which is itself risk
`R-GOV-1`.

---

## 5. What is still owed

Ordered by how much worse it gets if deferred.

| # | Owed | Why it is first | Reference |
|---|---|---|---|
| 1 | **Reconcile the vendor response** — it marks ≥17 unbuilt requirements "Fully OOTB" | It is the only item that actively degrades with time, and the client's reviewers already queried one demo claim | R6 |
| 2 | **Send the SIT script to PRO-NET and get it agreed** | Costs nothing; cannot be done retrospectively; blocks the whole SIT | §2.2.6 |
| 3 | **Rehearse a restore, and time it** | One VM, no failover, and a backup nobody has restored. "We can restore" and "we can restore within the SLA" are different claims | R12 |
| 4 | **Build the nine unbuilt fork patches** (0052–0060), off-VM, amd64 | Every UI feature behind them does not exist yet, and several stack, so a fix to a lower one cascades | R7 |
| 5 | **Settle the SLA or fund HA** | 99.9% and P1 <2h are not supportable on one VM. Better before signing than after the first outage | R1 |
| 6 | Answer Q4 (DMS spec), Q5 (HQ escalation), Q7 (PII masking scope); give a date for Meta verification | Each blocks requirements that are currently GAP | R3, R4, R15 |
| 7 | Run `ensure_views()` and the two owed `ALTER TABLE` migrations | Reporting cannot be demonstrated until then | R13 |
| 8 | Measure the AI baselines with real credentials | **The stub scores of 97–100% must never be quoted** — one author wrote both the labels and the rules | R8 |
| 9 | Place one real Twilio call | The whole voice channel is unverified, and automated call QA waits on it | R10 |
| 10 | **Provision the sandbox tenant and dry-run every training exercise** | The curricula exist and render from one source; no exercise has been run, so "completable as written" is a claim nobody has checked | Artefact 05, [`training/delivery-plan.md`](training/delivery-plan.md) §7 |
| 11 | Have the architecture map reviewed by an outsider | Its own definition of done requires it | Artefact 07 |
| 12 | Agree a `presence_events` retention number | It grows on every poll, forever, and has no owner | R11 |
| 13 | Name a governance forum | Four of six QA roles are unassigned | R-GOV-1 |

---

## 6. Full file map

```
docs/client-materials/
├── HANDOVER-INDEX.md                  ← this file
├── handover/
│   ├── architecture.md                §2.3.4  map + trust boundaries
│   ├── api-schema.md                  §2.3.4  112 endpoints, verified live
│   └── configuration.md               §2.3.4  GENERATED — 256 settings
├── governance/
│   ├── qa-plan.md                     §6.1    levels, conventions, severity
│   ├── risk-register.md               §6.1    19 risks
│   └── milestone-artefacts/           §6.3.2  ten named documents
│       ├── 00-README.md
│       └── 01…10-*.md
├── sit/
│   ├── 2026-08-08-sit-script.md       §2.2.6  155 cases — NOT AGREED
│   └── 2026-08-08-sit-report.md       §2.2.6  NOT EXECUTED
├── feature-guide-src-v3/              §2.3.3  operator handbook source
│                                              (audience markers live here)
├── build_crm_feature_guide.py                 handbook + curricula generator
└── training/                          §2.3.3  three curricula from one source
    ├── README.md                              the mechanism, and what is generated
    ├── delivery-plan.md                       schedule, prerequisites, cadence
    ├── reset-sandbox-tenant.sh                NEVER EXECUTED
    ├── tag-coverage.md                GENERATED  108 sections × 3 cohorts
    └── {agent,supervisor,admin}/      GENERATED  deck + exercises + checklist

scripts/generate-config-doc.py                 the configuration generator
scripts/test_generate_config_doc.py            its 8 tests, incl. the drift check
scripts/test_build_feature_guide_audiences.py  27 tests for the audience filter,
                                               incl. the default-build-unchanged proof
docs/analysis/2026-08-09-blocked-work-register.md   read this first
docs/runbooks/                                 P13 operational runbooks
```
