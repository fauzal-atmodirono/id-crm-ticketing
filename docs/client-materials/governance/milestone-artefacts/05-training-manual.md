# 05 — Training manual

**Requirement:** §2.3.3, §6.3.2 · **Status: NOT READY TO SIGN**

> A comprehensive operator handbook exists. **Role-differentiated curricula do
> not exist in any form**, and §2.3.3 asks for role-based training. This artefact
> must not be signed as satisfying §2.3.3.

## Traceability

| Evidence | Path | State |
|---|---|---|
| Operator handbook source, v3 — 13 chapters, 98 sections | `docs/client-materials/feature-guide-src-v3/` | **Complete and current** |
| Rendered handbook | `docs/client-materials/PROTON - CRM Feature Guide v3.docx` | Current |
| Build pipeline | `docs/client-materials/build_crm_feature_guide.py` | Renders the single audience-neutral guide |
| Screenshots | `docs/client-materials/feature-guide-assets/` | Present |
| Channel interaction guide | `docs/analysis/2026-08-08-crm-channel-interaction-guide-v2.md` | Present |
| **Frontline agent curriculum** | `docs/client-materials/training/agent/` | **DOES NOT EXIST** |
| **Supervisor curriculum** | `docs/client-materials/training/supervisor/` | **DOES NOT EXIST** |
| **Administrator curriculum** | `docs/client-materials/training/admin/` | **DOES NOT EXIST** |
| Sandbox reset script for exercises | — | **DOES NOT EXIST** |
| Delivery plan, schedule, refresher cadence | — | **DOES NOT EXIST** |

## What is missing, and why it was not produced here

§2.3.3 asks for **product and role-based training**. What exists is one
audience-neutral document: comprehensive, and therefore the wrong length for
everybody. The design calls for three curricula — frontline agent (~2 h),
supervisor (~3 h), administrator (~4 h) — each a facilitator deck, a hands-on
exercise set on a **sandbox tenant**, and a competency checklist, plus a delivery
plan with a refresher cadence.

The design's central constraint is **one source, three audience filters — not
three copies**, because three separate decks will diverge and the frontline deck
is the one that will silently go stale. Delivering three hand-authored curricula
would satisfy the artefact list and violate the requirement that makes it
maintainable.

**It was not produced in this package because the work requires modifying
`docs/client-materials/build_crm_feature_guide.py` to accept an audience filter,
and that file was outside this package's write scope.** Writing three
hand-maintained copies instead was the available alternative and was rejected as
worse than an honest gap.

**Owed, in order:** an audience-filter mechanism in the build pipeline; the three
renderings; exercise sets dry-run on a sandbox tenant and confirmed completable as
written; a reset script so a cohort can repeat them; competency checklists; and a
delivery plan.

**One content constraint for whoever writes the admin curriculum**, because it is
easy to get wrong: it must cover what this programme added — taxonomy admin,
targets, escalation routing, RBAC, alert preferences and the workforce dashboard —
**and must not teach features whose fork patch has never been built** (nine of
them, `../risk-register.md` R7), or data scoping, which does not restrict anything
(`../risk-register.md` R16). Training agents to use a feature that does not exist
on their tenant is how a rollout loses its audience on day one.
