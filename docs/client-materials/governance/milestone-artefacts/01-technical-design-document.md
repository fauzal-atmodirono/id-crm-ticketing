# 01 — Technical design document

**Requirement:** §6.3.2 · **Status:** ready to sign

## What it is

The consolidated technical design for the platform and for every package of this
programme. It is not a single file: the design is authored per package, as a
specification that precedes its implementation plan, which precedes the code.
That sequence — spec → plan → code → verification — is the practice the QA plan
describes, and it is why the design documents are contemporaneous with the work
rather than reconstructed after it.

## Traceability

| Evidence | Path |
|---|---|
| Programme design | `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md` |
| Per-package designs (P1–P14) | `docs/superpowers/specs/2026-08-08-rfp-p*-design.md` |
| Per-package implementation plans | `docs/superpowers/plans/2026-08-08-rfp-p*.md` |
| Architecture and trust boundaries | `docs/client-materials/handover/architecture.md` |
| API surface | `docs/client-materials/handover/api-schema.md` |
| Configuration surface (generated) | `docs/client-materials/handover/configuration.md` |
| Engineer-facing system description | `CLAUDE.md` |
| Per-tenant isolation design | `docs/superpowers/specs/2026-07-16-per-tenant-isolation-design.md` |

## What a reviewer should know

**The design documents record what was decided and what was deliberately not
done.** Each has a "what could go wrong" section and a "requirements closed"
section that names what it does *not* close. That is the part worth reading: the
specifications are more candid than the vendor response (see `../risk-register.md`
R6), and where the two disagree, **the specification is the accurate one.**

**Gap:** P11, P12 and P13 have design documents but **no per-task engineering
ledger**, because their implementing sessions terminated on API limits before
recording one. Anything asserted about those three packages' behaviour traces to
the code and to `docs/analysis/2026-08-09-blocked-work-register.md` §3j–3m, not to
a task record. See `../risk-register.md` R18.
