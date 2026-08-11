# Role-based training curricula (§2.3.3)

Three curricula — frontline agent, supervisor, administrator — **rendered from
the operator handbook, not written beside it.** The handbook source
(`../feature-guide-src-v3/`) is the single source of truth; each `##` section
declares which cohort needs it, and the build script filters.

That is the requirement, not a convenience: three hand-maintained decks
diverge, and the frontline one is the copy that goes stale silently, because
it is the one nobody re-reads when a feature changes.

## What is generated, and what is not

| File | Generated? |
|---|---|
| `agent/`, `supervisor/`, `admin/` — `facilitator-deck.md`, `exercises.md`, `competency-checklist.md` | **GENERATED** |
| `tag-coverage.md` | **GENERATED** — the audit table for the filter |
| `delivery-plan.md` | Hand-written. Schedule, prerequisites, refresher cadence |
| `reset-sandbox-tenant.sh` | Hand-written. **Never executed** — see its header |
| this README | Hand-written |

**Do not hand-edit a generated file.** The next run overwrites it, and a
generated document that was edited afterwards is worse than no generator: it
carries a "do not edit" banner that invites the reader to trust it.

```bash
# regenerate all ten generated files
python3 docs/client-materials/build_crm_feature_guide.py --curricula

# fail if a committed one is stale (the CI-shaped check)
python3 docs/client-materials/build_crm_feature_guide.py --check
```

## How a section declares its audience

One HTML comment, immediately after the `##` heading:

```markdown
## Labels
<!-- TRAINING: audience=agent, exercise -->
```

- **`audience=`** names the **most junior** cohort that needs the section.
  The three are cumulative — `agent` < `supervisor` < `admin` — because the
  design defines each senior role as "the above, plus …". A section tagged
  `agent` is taught to all three cohorts; one tagged `admin` only to
  administrators.
- **`exercise`** marks a section whose documented steps become a hands-on lab
  task. Optional, and per-section only.
- The same marker placed **before the first `##`** sets a default for the
  whole chapter, so a section added later inherits something sensible instead
  of falling through to the global fallback.

Three properties the mechanism is built for, each of which is tested in
`scripts/test_build_feature_guide_audiences.py`:

1. **Untagged content still reaches a cohort.** A section with no marker of
   its own and no chapter default falls back to `admin` — the widest
   curriculum, and the cohort with the broadest access, so the fallback can
   never teach a frontline group a page they cannot open. Every fallback is
   named in `tag-coverage.md`, so it is visible rather than assumed. There
   are currently **none**.
2. **A typo fails loudly.** `audience=agnet` aborts the build — including the
   plain handbook build — naming the file, the line and the valid names. A
   misspelling that merely dropped the section would produce a quietly
   thinner deck, which is the failure this whole mechanism exists to prevent.
3. **The handbook is untouched.** The markers are HTML comments, which the
   builder already strips from every line, so the shipped
   `PROTON - CRM Feature Guide v3.docx` is byte-for-byte what it was before
   the filter existed. The test proves it by extracting the generator from
   the commit *before* the filter and comparing the two builds member by
   member.

## A role-scoped handbook, if you want one

The same filter can render a shorter `.docx` for one role:

```bash
cd docs/client-materials
FG_OUT="$PWD/PROTON - CRM Feature Guide v3 (agents).docx" \
FG_COVER_SUBTITLE="Frontline Agent Handbook — August 2026" \
  python3 build_crm_feature_guide.py --audience agent
```

Always set `FG_OUT`: without it, a role-scoped build overwrites the full
handbook. None of the three role-scoped `.docx` files is committed — they are
12 MB each and derivable in a second.

## Read this before delivering any of it

`delivery-plan.md` §7 lists what has **not** been verified, and it is short
enough to read twice. The two that matter most: **no exercise has been
dry-run** (no sandbox tenant has been provisioned), and **every duration is
derived by rule, not measured** — no session has been delivered or timed.
