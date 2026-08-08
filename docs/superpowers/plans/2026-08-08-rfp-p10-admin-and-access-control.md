# P10 — Self-Service Taxonomy Admin, Category→Department, Data-Scoped RBAC: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator add a case category without an engineer, a deploy or a restart — and make the silent gap between the reporting taxonomy and the escalation taxonomy visible before it misroutes a complaint.

**Architecture:** A Firestore-backed taxonomy store following the `PicStore`/`DealerStore`/`SlaPolicyRepository` pattern for the fourth time, seeded from the existing env JSON so an untouched tenant is byte-identical. The store is authoritative; a sync pushes the value list into Chatwoot's custom-attribute definition, which is what removes the restart.

**Tech Stack:** Python 3.12, FastAPI, Firestore, the Chatwoot custom-attribute API, Chatwoot fork patches, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p10-admin-and-access-control-design.md`

## Global Constraints

- **Retire, never delete.** A category in use by historical cases must stay resolvable forever. The UI says "retire" and explains why.
- **The store is authoritative; the Chatwoot sync is downstream.** A sync failure must never roll back an operator's edit — it surfaces an out-of-sync state instead.
- **Category→department is suggest-only**, matching the existing AI-suggested department behaviour (`da6c335`). Auto-applying would misroute exactly the exceptional cases that get escalated.
- **Scopes are intersective, never additive.** Adding a role must never widen data access. Asserted by test.
- **Scoping is enforced in the query layer, never by hiding UI.** Tests call the API directly, bypassing any front end.
- **`None` on every scope field = account-wide**, so every existing role behaves exactly as today.
- Env vars in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/taxonomy/store.py` | **New.** `TaxonomyNode` CRUD, cascade integrity |
| `backend/.../features/taxonomy/seed.py` | **New.** Seed from the env JSON, non-destructive |
| `backend/.../features/taxonomy/chatwoot_sync.py` | **New.** Push values into the attribute definition |
| `backend/.../features/taxonomy/router.py` | **New.** Admin CRUD + coverage report |
| `backend/.../features/authz/data_scope.py` | **New.** `DataScope`, intersection, enforcement dependency |
| `backend/.../features/authz/seed.py` | **Modify.** `taxonomy.manage` permission |
| `deploy/chatwoot-fork/patches/00NN-taxonomy-admin.patch` | **New.** |

---

### Task 1: The taxonomy store

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/taxonomy/store.py`
- Create: its test file

**Interfaces:**
- Consumes: Firestore.
- Produces: `TaxonomyNode(level, key, label, parent, active, department, sort_order)`, CRUD, and `tree() -> nested structure` that patch `0050`'s cascading picker can consume.

**Tests first:**

```python
async def test_a_node_can_be_created_at_each_of_the_four_levels():
async def test_a_level_2_node_cannot_be_created_under_a_missing_parent():
async def test_a_level_2_node_cannot_be_created_under_a_retired_parent():
async def test_retiring_a_node_hides_it_from_the_active_tree():
async def test_a_retired_node_is_still_resolvable_by_key():
async def test_there_is_no_delete_method_on_the_store():
async def test_retiring_a_parent_reports_its_active_children():
async def test_sort_order_is_respected_in_the_tree():
async def test_the_tree_shape_matches_what_the_cascading_picker_expects():
```

**Test six is a design assertion.** If a `delete` exists, someone will use it,
and four thousand historical cases will start reporting under a category that no
longer resolves.

Test nine keeps patch `0050` working — the picker already assumes a well-formed
tree, and this store is now what produces it.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/taxonomy/test_store.py -q`

---

### Task 2: Non-destructive seeding from the env JSON

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/taxonomy/seed.py`
- Create: its test file

**Interfaces:**
- Consumes: `CASE_TYPE_OPTIONS_JSON`, `CASE_TAXONOMY_JSON`, `CASE_DETAIL_OPTIONS_JSON`.
- Produces: a populated store on first run.

**Tests first:**

```python
async def test_an_empty_store_is_seeded_with_the_full_appendix_a_taxonomy():
async def test_all_three_case_types_are_seeded():
async def test_all_eight_divisions_are_seeded():
async def test_the_seeded_tree_matches_what_the_env_json_produces_today():
async def test_re_seeding_never_overwrites_an_operator_edited_label():
async def test_re_seeding_never_reactivates_a_retired_node():
async def test_re_seeding_adds_a_node_that_appeared_in_the_env_json():
```

**Tests five and six are the compatibility guarantees.** An operator who retires
a category must not have it resurrected by the next restart, and an operator who
renamed a label must not have it reverted. This is the exact same protection
P5's targets store needs, for the same reason.

Test four is the equivalence check: the seeded tree must be indistinguishable
from what the code builds from env today.

**Verify:** `uv run pytest src/chatbot/features/taxonomy/test_seed.py -q`

---

### Task 3: Chatwoot attribute-definition sync

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/taxonomy/chatwoot_sync.py`
- Create: its test file

**Interfaces:**
- Consumes: the store, the Chatwoot custom-attribute API.
- Produces: the flattened active value list written into each attribute definition.

**Tests first:**

```python
async def test_creating_a_node_pushes_the_new_value_into_the_attribute_definition():
async def test_retiring_a_node_removes_it_from_the_picker_values():
async def test_the_sync_is_idempotent():
async def test_a_sync_failure_leaves_the_store_updated():
async def test_a_sync_failure_surfaces_an_out_of_sync_state():
async def test_a_retry_after_a_failure_reconciles_the_picker():
async def test_no_service_restart_is_required_for_a_change_to_take_effect():
async def test_the_sync_never_removes_a_value_still_present_on_historical_cases():
```

**Tests four and five are the ordering decision**, and it is the whole reason
the store is authoritative: rolling back on a Chatwoot API failure would mean a
transient network error silently discards an operator's work.

Test eight prevents the picker sync from breaking history — a value in use stays
in the definition even when retired, marked inactive rather than removed.

**Verify:** `uv run pytest src/chatbot/features/taxonomy/test_chatwoot_sync.py -q`

---

### Task 4: Admin endpoints and page

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/taxonomy/router.py`
- Create: `deploy/chatwoot-fork/patches/00NN-taxonomy-admin.patch`
- Modify: `backend/apps/backend/src/chatbot/features/authz/seed.py` — add `taxonomy.manage`

**Tests first:**

```python
async def test_the_tree_endpoint_returns_the_nested_active_taxonomy():
async def test_creating_a_node_requires_taxonomy_manage():
async def test_an_agent_role_cannot_edit_the_taxonomy():
async def test_retiring_a_node_with_children_returns_a_confirmation_prompt_not_an_error():
async def test_the_permission_appears_in_the_permission_registry():
async def test_the_flag_off_returns_404_so_the_page_does_not_render():
```

**Registry note:** follow the existing comment convention in
`authz/seed.py::PERMISSION_REGISTRY` — each non-agent permission carries a
comment saying why it is auto-granted to `administrator` and withheld from
`agent`. `taxonomy.manage` is administrator-only.

**Fork-patch note:** reconstruct from the Escalation Routing admin patch
(`0039`) — closest analogue. Build via Cloud Build for `amd64`; never on the prod
VM, never from an arm64 Mac.

**Verify:** `uv run pytest src/chatbot/features/taxonomy/test_router.py -q`

---

### Task 5: Category → department mapping and the coverage report

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/taxonomy/store.py` (the `department` field)
- Modify: the escalation suggestion path
- Create: `backend/apps/backend/src/chatbot/features/taxonomy/test_category_department.py`

**Interfaces:**
- Consumes: `PicStore` (to validate department slugs), the applied case category.
- Produces: a suggested `dept_*` label; `GET /admin/taxonomy/coverage`.

**Tests first:**

```python
async def test_applying_a_mapped_category_suggests_its_department():
async def test_the_suggestion_can_be_overridden_by_the_agent():
async def test_an_override_is_recorded_in_the_audit_trail():
async def test_nothing_is_auto_applied_without_agent_confirmation():
async def test_a_department_slug_that_does_not_exist_in_pic_store_is_rejected():
async def test_the_coverage_report_lists_active_categories_with_no_department():
async def test_the_coverage_report_lists_departments_no_category_maps_to():
async def test_a_category_mapped_to_a_retired_department_is_flagged():
```

**Tests six through eight are the operationally valuable half.** The mapping
closes the requirement; the coverage report closes the *trap* — a case correctly
categorised for reporting with no routable department, failing silently at
escalation time. Build the report even if the mapping is deferred.

**Verify:** `uv run pytest src/chatbot/features/taxonomy/test_category_department.py -q`

---

### Task 6: Data scopes

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/authz/data_scope.py`
- Create: its test file

**Interfaces:**
- Consumes: the caller's roles.
- Produces: `DataScope(inboxes, teams, dealers, own_only)`, `intersect(a, b) -> DataScope`, and a FastAPI dependency composing with P4's `MetricFilters`.

**Tests first:**

```python
def test_an_all_none_scope_means_account_wide():
def test_every_existing_role_resolves_to_an_account_wide_scope():
def test_an_inbox_scope_narrows_to_those_inboxes():
def test_two_scopes_intersect_and_never_union():
def test_adding_a_second_role_can_never_widen_access():          # the invariant
def test_intersecting_a_scoped_role_with_an_unscoped_one_stays_scoped():
def test_own_only_resolves_to_the_calling_agent():
def test_an_empty_intersection_yields_access_to_nothing_not_to_everything():
```

**Test five is the invariant** and test eight is its dangerous edge: an empty
intersection must fail closed. A naive implementation that treats an empty list
as "no filter" grants everything to exactly the most restricted user.

**Verify:** `uv run pytest src/chatbot/features/authz/test_data_scope.py -q`

---

### Task 7: Scope enforcement

**Files:**
- Modify: `backend/.../features/metrics/query_adapter.py`, the admin routers
- Modify: `backend/.../features/authz/chatwoot_role_mirror.py` (inbox scope → Chatwoot inbox membership)
- Create: `backend/apps/backend/src/chatbot/features/authz/test_scope_enforcement.py`

**Tests first:**

```python
async def test_a_dealer_scoped_caller_sees_only_that_dealers_rows():
async def test_an_inbox_scoped_caller_sees_only_those_inboxes_conversations():
async def test_enforcement_holds_when_the_api_is_called_directly():   # not UI hiding
async def test_an_own_only_caller_receives_403_on_a_team_report():
async def test_an_own_only_caller_still_sees_their_own_conversations():
async def test_scope_composes_with_an_explicit_metrics_filter():
async def test_a_caller_cannot_widen_their_scope_via_a_query_parameter():
async def test_the_flag_off_leaves_every_endpoint_account_wide_as_today():
async def test_inbox_scope_is_mirrored_into_the_chatwoot_custom_role():
```

**Tests three and seven are the security pair.** A user supplying
`?dealer=someone_else` must be intersected down, never widened — the scope is a
ceiling, and a query parameter can only narrow within it.

Test four encodes the design decision: an `own_only` agent gets 403 on a team
report rather than a single-agent aggregate that reads like a team figure.

**Verify:** `uv run pytest src/chatbot/features/authz/ -q`

---

### Task 8: Flags, env, migration note

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`, `agent/app/config.py`
- Modify: `README.md`

**Tests first:**

```python
def test_the_three_settings_are_present_in_example_env():
def test_all_three_default_to_false():
def test_the_env_taxonomy_json_vars_are_still_read_when_the_admin_is_off():
def test_both_services_start_with_none_of_the_new_vars_set():
```

**The migration note (the deliverable):**

> With `TAXONOMY_ADMIN_ENABLED` on, `CASE_TAXONOMY_JSON`,
> `CASE_TYPE_OPTIONS_JSON` and `CASE_DETAIL_OPTIONS_JSON` become the **seed** for
> the taxonomy store rather than the live source. Editing them afterwards has no
> effect on a tenant whose store is already populated — edit the taxonomy in
> **Settings → Case Taxonomy** instead. Categories are **retired**, never
> deleted, so historical cases keep resolving. A retired category disappears from
> the picker and continues to appear in reports covering the period it was in
> use.
>
> With `DATA_SCOPED_RBAC_ENABLED` on, roles with no scope configured remain
> account-wide. Scopes **intersect**: giving a user a second role can only ever
> narrow what they can see, never widen it.

**Verify:** both suites green with flags off, then on.

---

## Definition of done

- [ ] All three flags off → suites green, behaviour identical to `d85f0d4`.
- [ ] A category can be added end to end with **no env edit and no restart**, verified on a scratch tenant.
- [ ] No delete path exists; retired categories still resolve on historical cases.
- [ ] A Chatwoot sync failure leaves the store updated and surfaces a retry state.
- [ ] The coverage report lists unmapped categories and unreferenced departments.
- [ ] Departments are suggested, never auto-applied.
- [ ] Scope intersection proven never to widen; an empty intersection fails closed.
- [ ] Enforcement holds against direct API calls, not just in the UI.
- [ ] §4.83's per-field masking documented as **not** included (R16, blocked on Q7).
- [ ] Nothing merged to `main`.
