# 05 — Training manual

**Requirement:** §2.3.3, §6.3.2 · **Status: READY TO SIGN AS A MANUAL — the
delivery half is not**

> The three role curricula now exist and **render from the single handbook
> source**, so they cannot diverge from it or from each other. What has *not*
> happened is delivery: no sandbox tenant has been provisioned, and no exercise
> has been dry-run. Sign this as the training material; do not sign it as
> evidence that the training works.

## Traceability

| Evidence | Path | State |
|---|---|---|
| Operator handbook source, v3 — 14 chapters, 108 sections | `docs/client-materials/feature-guide-src-v3/` | **Complete and current** |
| Rendered handbook | `docs/client-materials/PROTON - CRM Feature Guide v3.docx` | Current, and **byte-for-byte unchanged** by the audience filter |
| Build pipeline | `docs/client-materials/build_crm_feature_guide.py` | Renders the handbook **and** the three curricula |
| Audience markers | `<!-- TRAINING: audience=... -->` on all 108 sections | 0 untagged |
| **Frontline agent curriculum** — 54 topics, 12 exercises | `docs/client-materials/training/agent/` | **Complete, generated** |
| **Supervisor curriculum** — 71 topics, 20 exercises | `docs/client-materials/training/supervisor/` | **Complete, generated** |
| **Administrator curriculum** — 108 topics, 32 exercises | `docs/client-materials/training/admin/` | **Complete, generated** |
| Competency checklist per cohort | `training/*/competency-checklist.md` | **Complete, generated** |
| Delivery plan — schedule, prerequisites, refresher cadence | `docs/client-materials/training/delivery-plan.md` | **Complete** |
| Audience tag coverage | `docs/client-materials/training/tag-coverage.md` | **Generated** — the filter's audit table |
| Sandbox reset script for exercises | `docs/client-materials/training/reset-sandbox-tenant.sh` | Written, **NEVER EXECUTED** |
| Exercise dry-run on a sandbox tenant | — | **NOT DONE** |
| Sandbox tenant | — | **NOT PROVISIONED** |
| Tests | `scripts/test_build_feature_guide_audiences.py` | 27 passing |
| Channel interaction guide | `docs/analysis/2026-08-08-crm-channel-interaction-guide-v2.md` | Present |

## One source, three audience filters — and why it had to be that

The design's central constraint was **not** three curricula; it was three
curricula **from one source**, because three separate decks diverge and the
frontline deck is the one that goes stale silently — it is the one nobody
re-reads when a feature changes.

Each `##` section of the handbook declares the most junior cohort that needs
it, in one HTML comment. The audiences are cumulative (`agent` <
`supervisor` < `admin`), matching the design's "the above, plus …" role table,
so one token per section replaces a three-way list nobody keeps in step:

```markdown
## Labels
<!-- TRAINING: audience=agent, exercise -->
```

```bash
python3 docs/client-materials/build_crm_feature_guide.py --curricula  # write them
python3 docs/client-materials/build_crm_feature_guide.py --check      # fail if stale
```

Nothing in a deck is written for the deck. Talking points are the handbook
section's own opening paragraph, demo steps are its documented procedure
verbatim, caveats are its own blockquotes, and every slide names the section it
came from. **The generated files were committed unedited** — a generated
document that was hand-edited afterwards is worse than no generator, because it
carries a "do not edit" banner that invites the reader to trust it.

Three properties, each tested rather than asserted:

1. **Untagged content still reaches a cohort.** No marker and no chapter
   default falls back to the administrator curriculum — the widest, and the
   cohort with the broadest access — and is named in `tag-coverage.md`. None
   currently falls back.
2. **A typo fails loudly.** `audience=agnet` aborts the build, naming the file,
   the line and the valid names. A misspelling that merely dropped the section
   would produce a quietly thinner deck.
3. **The client's handbook is unchanged.** The test extracts the generator from
   the commit *before* the filter existed, builds the default handbook with
   both, and compares every zip member's payload.

## What cannot be signed, and why

| Claim | State |
|---|---|
| The exercises are completable as written | **Not verified.** Nothing has been dry-run; no sandbox tenant exists |
| The sandbox reset works between cohorts | **Never executed** |
| The session lengths are realistic | **Derived by rule** (3 min per topic, +1 per two documented steps, +5 per hands-on), **never measured** — no session has been delivered or timed |
| The curricula cover everything this programme built | **They do not** — nine topics, below |

**The derived lengths are 5 h 31 min / 7 h 49 min / 12 h 29 min against the
design's 2 h / 3 h / 4 h.** The generator deliberately does not scale them into
agreement: the handbook reached 108 sections, 54 of which a frontline agent
needs before their first shift, and two hours across 54 topics is two minutes
each. `delivery-plan.md` §3 spends the difference as sessions — four shared,
three supervisor, three administrator. If the 2/3/4-hour figures are
contractual rather than indicative, that is a scope conversation to have before
a cohort is booked.

## Nine topics no curriculum teaches yet

Each curriculum ends with this list and its reasons; it lives in the generator,
not in the markdown, so a regeneration cannot drop it.

| Topic | Blocked by |
|---|---|
| Agent availability & the workforce dashboard | Fork patches `0053`/`0054` never built (P6) |
| Performance targets & attainment | No `/metrics/targets` on the deployed backend (P5) |
| Alert preferences / inbound alerts | Patch `0057` unbuilt, plus the two-switch gate (P9) |
| Case taxonomy administration | Patch `0060` unbuilt (P10) — admins still edit Custom Attributes |
| The redesigned Roles & Permissions page | Patch `0059` unbuilt (P10); the curriculum teaches the current page |
| **Data scopes** | **Deliberately not taught.** `DATA_SCOPED_RBAC_ENABLED` restricts nothing (`../risk-register.md` R16) — teaching it would teach a control that does not exist |
| AI conversational quality | Patches `0055`/`0056` unbuilt, no `/assist/translate` (P7) |
| AI cost & performance measurement | Eleven BigQuery views never created (P8) |
| Hands-on voice/phone practice | No real Twilio call has ever been placed (`../risk-register.md` R10); every `PHONE_*` switch off |

**Seven of the nine are unbuilt fork patches or uncreated views, not missing
documentation.** Writing those sections from a specification would produce a
curriculum that teaches pages the cohort cannot open — which is how a rollout
loses its audience on day one.

**Owed, in order:** provision the sandbox tenant; seed it; dry-run every
exercise and record the result; run the reset script once with
`RESET_DRY_RUN=1`; decide how an administrator cohort's configuration changes
are reverted between cohorts (`delivery-plan.md` §4 — the seeder does not undo
them); then deliver, and re-derive the durations from the first session.
