# 08 — User acceptance sign-off

**Requirement:** §6.3.2 · **Status: NOT READY TO SIGN — UAT has not been run**

> This is a **template**. No user acceptance testing has taken place. There is no
> UAT script, no cohort, and no environment in which PROTON's users could exercise
> the system.

## Traceability

| Evidence | Path | State |
|---|---|---|
| SIT script (**precedes** UAT) | `docs/client-materials/sit/2026-08-08-sit-script.md` | Drafted, **not agreed** |
| SIT report | `docs/client-materials/sit/2026-08-08-sit-report.md` | **Not executed** |
| Defect severity definitions | `../qa-plan.md` §6 | Current |
| Requirement traceability | `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md` | Current |
| Operator handbook | `docs/client-materials/feature-guide-src-v3/` | Current |
| **UAT script** | — | **DOES NOT EXIST** |
| **UAT environment** | — | **DOES NOT EXIST** |

## Prerequisites before UAT can begin

- [ ] SIT agreed, executed, and its S1/S2 defects closed or formally descoped
- [ ] A non-production environment with real credentials for every third party
- [ ] The Chatwoot image built with all 59 fork patches (nine have never been built)
- [ ] Role-differentiated training delivered — **users cannot accept what they have not been shown** (artefact 05, which does not exist)
- [ ] A UAT script written against **business outcomes**, not endpoints
- [ ] PROTON nominating a UAT owner and cohort

## Template

| Requirement | Description | Expected outcome | Result | Accepted by | Date |
|---|---|---|---|---|---|
| | | | | | |

**Scope statement to complete before signing.** Sign-off must name the
requirements it covers **and the requirements it does not**, because several
cannot be accepted at all in the current state:

- DMS/TSP-dependent requirements — no endpoint exists (`../risk-register.md` R3)
- Facebook and Instagram — no inbox can be created (R4)
- 6 of the 14 monthly control items — not instrumented (R5)
- Data-scoped access restriction — the flag gates nothing (R16)
- Any feature behind an unbuilt fork patch (R7)

| | Name | Role | Date | Signature |
|---|---|---|---|---|
| Prepared by | | Delivery | | |
| Tested by | | PROTON UAT owner | | |
| **Accepted by** | | PROTON | | |

**A signature here without the scope statement completed is an acceptance of
requirements that were never demonstrated**, and it is the row a later dispute
will turn on.
