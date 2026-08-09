# Archived docs

Nothing here is current. Every file was superseded by a named replacement,
or describes an event that has already happened. Kept because they record
what was true at the time — a demo run sheet or a gap analysis is evidence
of what was claimed on a given date, and that matters when a client asks
why a commitment changed.

**If you are looking for the current version, use the right-hand column.**

## `analysis/` — archived 2026-08-09

| Archived file | Last real edit | Superseded by |
|---|---|---|
| `crm-channel-interaction-guide.md` / `.docx` | 2026-08-04 | `docs/analysis/2026-08-08-crm-channel-interaction-guide-v2.md` |
| `crm-channel-ui-testing-guide.md` / `.docx` | 2026-08-04 | `docs/testing/2026-08-09-p1-p2-test-guide.md` |
| `crm-process-flow-runbook.md` | 2026-08-01 | Feature Guide v3 ch. 09 (administration) + `deploy/tenants/example.env` |
| `crm-process-flow-testing-guide.md` | 2026-08-01 | `docs/testing/` |
| `phase1-ops-runbook.md` | 2026-08-01 | `README.md` deploy runbook + `docs/analysis/2026-08-09-blocked-work-register.md` |
| `proton-crm-gap-analysis-2026-07-27.md` / `.docx` | 2026-08-01 | `docs/analysis/2026-08-08-rfp-2026_028-gap-analysis.md` |
| `proton-crm-knowledge-menu-features-and-capabilities.md` | 2026-08-01 | Feature Guide v3 ch. 04 (knowledge) |
| `proton-demo-presentation-guide.md` | 2026-08-01 | — (the demo it scripted has happened) |
| `proton-demo-feedback-coverage-2026-07-28.md` | 2026-08-06 | `docs/superpowers/specs/2026-08-07-proton-feedback-followups-design.md` |
| `2026-08-06-proton-demo-run-sheet.md` | 2026-08-06 | — (the demo it scripted has happened) |

> `proton-crm-gap-analysis-2026-07-27.md:131` is cited by the current RFP gap
> analysis as an example of a **claim that was wrong** — do not delete it, the
> citation needs a target.

Two older files sit directly in this directory rather than in `analysis/`:
`proton-requirements-gap-analysis.md` and
`proton-vs-crm-requirements-comparison.md`. Both predate the 2026-07-27 gap
analysis and were archived before this index existed.

## `../client-materials/archive/` — archived 2026-08-09

The Feature Guide v1 and v2 editions: `PROTON - CRM Feature Guide.docx`,
`PROTON - CRM Feature Guide v2.docx`, and their chapter sources
`feature-guide-src/` and `feature-guide-src-v2/`.

**Current edition: `docs/client-materials/PROTON - CRM Feature Guide v3.docx`**,
sources in `feature-guide-src-v3/`. `build_crm_feature_guide.py` now defaults
to v3; rebuild an archived edition by pointing `FG_SRC_DIR` / `FG_OUT` at the
paths under `archive/`.

## `plans/`, `superpowers/`

Older implementation plans and SDD specs, archived as their work landed.
