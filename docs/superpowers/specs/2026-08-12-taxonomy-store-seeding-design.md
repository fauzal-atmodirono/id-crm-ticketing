# Seeding the Case Taxonomy store from RFP Appendix A

**Date:** 2026-08-12
**Status:** designed, not implemented
**Scope:** `backend/` only — no fork patch, no Chatwoot image rebuild.

## Problem

The Case Taxonomy admin page (fork patch `0060`, live on the proton tenant)
renders "No active taxonomy nodes yet". The page is not broken and the flag is
not off: **the Firestore store behind it is empty because nothing ever seeds
it.**

`backend/apps/backend/src/chatbot/features/taxonomy/seed.py` contains a
complete, non-destructive `seed_taxonomy_from_env()` that builds the whole tree
from `CASE_TYPE_OPTIONS_JSON` → `CASE_TAXONOMY_JSON` → `CASE_DETAIL_OPTIONS_JSON`.
Its only callers are its own tests. `main.py:1167` mounts
`build_taxonomy_admin_router(settings)` but no startup path calls the seeder.

The Appendix A data itself needs no re-transcription. It is already the
in-code default of those three settings, verified against
`backend/apps/backend/src/chatbot/platform/config.py`:

| Dimension | Count | Setting |
|---|---|---|
| Case Type | 3 (Inquiry, Complaint, Compliment & Feedback) | `case_type_options_json` |
| Division | 8 (sales, product, network, charging, apps, aftersales, others, marketing) | `case_taxonomy_json` |
| Level 1 (Category) | 89 | `case_taxonomy_json` `subcategories` |
| Level 2 (Detail) | 246 | `case_detail_options_json` |

Source of truth for the transcription is
`docs/client-materials/RFP 2026_028/case-categorisation.json`. The same data is
already provisioned as Chatwoot custom attributes
(`chatwoot-config/provision_case_taxonomy.py`, run 2026-08-08) — it is only
this store that never received it.

`CATEGORY_DEPARTMENT_MAPPING_ENABLED=false` on the tenant is a second,
independent gate. The coverage report it mounts reads level-2/3 nodes **from
this same empty store**, so turning it on before seeding yields an empty
report. Seeding comes first.

## Two defects found while validating

### 1. 100 of 246 detail values would be silently dropped

`seed.py` resolves a detail's parent by re-slugifying the detail string's first
segment:

```python
div_slug = _slugify(parts[0])          # "After Sales" -> "after_sales"
parent_key = f"cat_{div_slug}_{sub_slug}"
```

But division nodes are keyed from the `CASE_TAXONOMY_JSON` **key**, which for
this division is `aftersales`, not `after_sales`. Every After Sales detail
therefore resolves to a `cat_after_sales_*` parent that does not exist.
Measured against the live config defaults: **146 matched, 100 orphaned, across
18 distinct missing parent keys — all `after_sales_*`.**

The orphaned nodes are skipped by an `if parent_node is not None:` with no
`else`, so the failure produces no log line at all. That silence is why the
mismatch was not visible.

Fix: build `{_slugify(division_label) → division_key}` from
`CASE_TAXONOMY_JSON` and resolve the first segment through it, falling back to
`_slugify(segment)` when the label is unmapped — so a tenant whose JSON key
already equals its label is byte-identically unaffected. Verified: **246/246
matched, 0 orphaned.**

### 2. The tree asserts a relationship Appendix A does not have

`seed.py` attaches all 8 divisions to `l1_keys[0]` — whichever case type sorts
first in the options list. The rendered tree then reads "Inquiry → Sales →
Refund → …", with Complaint and Compliment & Feedback as childless roots.

That is a false claim. Appendix A's Case Category is orthogonal to Division —
any division can carry any type — and the fork confirms it: patch `0050`'s
cascade chain is `['case_category', 'case_subcategory', 'case_detail']`.
`case_type` is deliberately not in it.

The store cannot express orthogonality: `TaxonomyNode.validate()` requires a
parent for every node above level 1 and forbids one at level 1.

## Design

### Tree shape

Seed a neutral level-1 root that owns the cascade, alongside the three real
case types:

```
Complaint             (Type)
Inquiry               (Type)
Compliment & Feedback (Type)
Case divisions        (Type)   key: type_case_divisions
  Sales               (Division)
    Refund            (Category)
      Booking — Status — Dealer Refund   (Detail)
  After Sales         (Division)
  ... 8 divisions / 89 categories / 246 details
```

346 nodes total — 3 case types, the neutral root, 8 divisions, 89 categories
and 245 details. 245 rather than 246 because `"Charging: Public Charging:
others"` and `"Charging: Public Charging: Others"` in the transcription differ
only by letter case and slugify to one key. That is pre-existing client data;
the ruling was to leave `config.py` verbatim and let the store collapse them,
so both spellings still appear in the Chatwoot picker until that is addressed
separately.

Nothing downstream shifts: the coverage report still reads
levels 2–3 (Division and Category) exactly as designed, and the page's
`LEVEL_LABELS` (`1: Type, 2: Division, 3: Category, 4: Detail`) stay correct.

The alternative — dropping the type roots so divisions become level 1 — was
rejected: it shifts every level by one, which turns the coverage report's
level-2/3 read into Category/Detail (335 useless "unmapped" rows) and makes
every label on the page wrong, requiring both a backend change and a new fork
patch + Cloud Build.

### Changes to `features/taxonomy/seed.py`

1. Create the `type_case_divisions` root and parent divisions to it, replacing
   the `primary_l1 = l1_keys[0]` line.
2. Resolve detail division segments through the label→key map described above.
3. Count and `warning`-log details whose parent cannot be resolved, naming the
   unresolved parent key. Silence here is what hid defect 1.
4. Pre-read existing keys **once** via `store.list_nodes(active_only=False)`
   and skip `create_node` entirely for keys already present.

Point 4 is a cost fix, not a behaviour change. `TaxonomyStore._client()`
constructs a new `firestore.Client` per operation and `create_node()` issues an
existence check plus a parent check before writing, so an unmodified re-seed
costs ~700 round trips on every boot. With the pre-read, first boot is ~347
writes and every subsequent boot is 1 read and 0 writes.

Seeding stays non-destructive, as it already is: an operator-edited label is
never overwritten, a retired node is never reactivated, and only missing keys
are created.

### Changes to `main.py`

An `@app.on_event("startup")` hook following the `_init_authz_db` pattern at
`main.py:978`, gated on `settings.taxonomy_admin_enabled`:

```python
@app.on_event("startup")
async def _seed_taxonomy() -> None:
    if not settings.taxonomy_admin_enabled:
        return
    asyncio.create_task(_run_taxonomy_seed(settings))
```

The seed is **dispatched, not awaited**. 347 sequential Firestore writes take
15–30s; awaiting them holds the container below its health check on every cold
start of a tenant whose store is empty. The task body wraps the call so a
Firestore failure logs and never prevents the app from booting — the fail-open
posture used throughout this service.

### Changes to `deploy/tenants/example.env`

Extend the `TAXONOMY_ADMIN_ENABLED` block to state that turning it on now seeds
the store from the three `CASE_*_JSON` vars on the next boot. The existing
"ONCE A TENANT'S STORE HAS BEEN SEEDED … THEY BECOME THE SEED ONLY" paragraph
already describes the intended semantics; it becomes true rather than aspirational.

## What the coverage report will say

After seeding and `CATEGORY_DEPARTMENT_MAPPING_ENABLED=true`, no node carries a
`department`, so the report will read **97 active categories with no department**
(8 divisions + 89 categories) and list every `dept_*` slug as unreferenced.

That is the correct baseline, not a bug. Department mappings are deliberately
**not** pre-seeded: the live PIC store holds `dept_engineer`, `dept_pre_sales`
and `dept_sales` with PICs, plus `dept_aftersales`, `dept_cs` and
`dept_technical` with none. Any mapping invented here would be wrong tenant
config presented to an operator as fact. Operators map categories on the page,
and the report is what makes the unmapped ones visible.

Note the `department` field remains a mapping only — nothing applies a `dept_*`
label or routes a case from it. That is unchanged by this work and documented
as such in `example.env`.

## Testing

Extend `features/taxonomy/test_seed.py`:

- the `type_case_divisions` root is created and all 8 divisions parent to it;
  the 3 case types remain childless level-1 roots
- no detail is dropped for want of a parent — the seeder's
  `taxonomy_seed_details_unresolved` warning never fires — including every
  After Sales detail (the regression test for defect 1)
- a detail whose parent genuinely does not exist is skipped, logged, and does
  not raise
- a second seed against a populated store creates 0 nodes and issues no writes
- an operator-edited label and a retired node both survive a re-seed

Add a wiring test alongside `test_p10_wiring.py`: the startup hook seeds when
`taxonomy_admin_enabled` is true and does nothing when it is false.

Run with `GEMINI_API_KEY=dummy uv run pytest -q` from
`backend/apps/backend` — without the key, five modules fail at collection and
the suite never runs.

## Deploy

1. Back up: `sudo tar czf /tmp/platform-src-backup-20260812.tgz -C /opt/platform backend agent`
   and `cp tenants/proton.env tenants/proton.env.bak-20260812`.
2. Full-tree source sync to `/opt/platform` (never a single-file copy — see
   the crash-loop recorded in the deploy notes), then verify by `.py` file
   count on both sides.
3. `docker compose -p proton -f docker-compose.tenant.yml --env-file
   tenants/proton.env up -d --build backend`. The `agent` service reads none of
   these settings and does not need rebuilding.
4. Set `CATEGORY_DEPARTMENT_MAPPING_ENABLED=true` in `tenants/proton.env`.
   `TAXONOMY_ADMIN_ENABLED` is already true — that is why the page renders at
   all today.
5. Recreate the backend so it picks up the flag, then verify **from inside the
   container** before opening the page: `GET /admin/taxonomy/tree` returns 4
   roots and 346 nodes, and `GET /admin/taxonomy/coverage` returns 200 with 97
   unmapped categories rather than 404.
6. Confirm the seed ran once: a second restart logs `taxonomy_seeded` with
   `newly_created=0`.

No Chatwoot image rebuild is required — patch `0060` is already in the live
`proton-chatwoot:v4.15.1-custom-rc1` image (`.git_sha` `3006906`, all 59
patches).

## Out of scope

- Wiring `department` into escalation routing. `example.env` states the
  suggestion path was never built and that when it is, it must be suggest-only,
  matching `DEPT_SUGGESTION_ENABLED`. Unchanged here.
- `retired_department_categories` in the coverage body stays `[]`; flagging a
  category whose mapped department is retired needs a retired/active
  distinction `PicStore` does not expose.
- Adding `case_detail` to `CONVERSATIONS_SCHEMA` in BigQuery. Tracked
  separately in the RFP gap analysis §8.
- The other two tenants. `default` and `wahchan` are out of scope by standing
  decision.
