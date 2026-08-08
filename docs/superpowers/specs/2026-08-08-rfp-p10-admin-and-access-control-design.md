# P10 — Self-Service Taxonomy Admin, Category→Department Wiring, Data-Scoped RBAC

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p10-admin-and-access-control.md`
**Closes:** 5 PARTIAL requirements
**Effort:** 2 weeks · **Wave:** 2 · **Blocked by:** nothing

---

## 1. The problem, precisely

**Changing a case category requires an engineer, a deploy and a restart.**
Appendix A's own closing line — A-6 — says *"The list will be expanded from time
to time; system shall allow addition or changes to the categories."* §2.2.1 and
§8.1.10 say the same thing in the technical document.

Today, adding one Level-1 value means:

1. editing `CASE_TAXONOMY_JSON` in the tenant `.env` (`agent/app/config.py:140`),
2. editing `CASE_DETAIL_OPTIONS_JSON` for its Level-2 children
   (`config.py:221`),
3. editing the **Chatwoot custom-attribute definition separately**, through
   Chatwoot's own admin, so the picker offers the value,
4. restarting the agent service.

Four steps, two of them in different systems, one of them a restart. The taxonomy
has 3 case types, 8 divisions, 79 Level-1 and 164 Level-2 values, and the client
has said in writing it will grow.

**The escalation engine is label-driven, not category-driven (§4.30).** An agent
applies `dept_<slug>` and then `escalate`. Critically, **`dept_*` slugs are a
separate taxonomy from the `case_category` labels used for reporting** — not
every case category has a matching PIC record. So a case can be correctly
categorised for reporting and have no routable department, and nothing detects
that. It is an operational trap: it fails silently, at escalation time, on the
cases that matter most.

**RBAC is function-level only (§4.83).** `authz/seed.py::PERMISSION_REGISTRY` is
real and well-built — ten permissions (`sla.manage`, `audit.view`,
`escalation.manage`, `customer360.view`, `call_recording.listen`,
`integration.manage`, …) enforced by `require_permission`, edited in patches
`0027`/`0028`, mirrored into a Chatwoot `CustomRole`. §4.83 asks for permissions
**by function and by data**. The data half does not exist: scoping is
account-wide, with no per-inbox, per-team, per-dealer or per-record rules beyond
three native Chatwoot conversation-visibility keys.

## 2. What this package delivers

1. A taxonomy admin screen — one place, no env edit, no restart.
2. Category → department mapping, with the unmapped-category trap made visible.
3. Data-scoped RBAC on top of the existing function-level model.

## 3. Design

### 3.1 Taxonomy store and admin

A Firestore-backed store following `PicStore` / `DealerStore` /
`SlaPolicyRepository` — the same pattern, for the fourth time, deliberately.

```python
@dataclass(frozen=True)
class TaxonomyNode:
    level: int            # 0=case_type, 1=division, 2=level-1, 3=case_detail
    key: str
    label: str
    parent: str | None
    active: bool
    department: str | None   # §3.2 — only meaningful at level 2/3
    sort_order: int
```

**The env vars become the seed, not a competing source** — exactly the
relationship P5 gives `RESOLUTION_SLA_TARGETS_JSON`. A tenant that never opens
the admin screen behaves precisely as today, because the store is seeded from the
same JSON the code reads now.

**Deactivate, never delete.** A category in use by 4,000 historical cases cannot
be removed without orphaning them, and reports grouped by category would silently
lose rows. `active: false` hides it from the picker and keeps it resolvable for
history. The admin UI offers "retire", not "delete", and says why.

**Chatwoot attribute-definition sync is the part that removes the restart.** The
store is the source of truth; a sync writes the flattened value list into the
Chatwoot custom-attribute definition via its API on every change. That collapses
steps 1–3 into one action and removes step 4.

The sync is idempotent, and a failure leaves the store updated and the picker
stale, with the admin page showing an explicit "picker out of sync — retry"
state. The alternative — rolling back the store on a Chatwoot API failure — would
mean a transient network error silently discards an operator's edit.

**Cascade integrity is enforced at write time:** a Level-2 value cannot be
created under a retired Level-1 parent, and retiring a parent prompts about its
children rather than silently orphaning them. Patch `0050`'s cascading picker
already assumes a well-formed tree; this keeps that assumption true.

### 3.2 Category → department (§4.30)

`TaxonomyNode.department` maps a case category to an escalation department, so
applying a category *suggests* the `dept_*` label rather than requiring the agent
to know the separate taxonomy.

**Suggest, not force.** The existing AI-suggested escalation department (commit
`da6c335`) is deliberately suggest-only, and this follows it: the mapping
pre-fills, an agent can override, and the override is recorded. Auto-applying a
department from a category would misroute every case whose category is right and
whose department is exceptional — and those are exactly the cases that get
escalated.

**The coverage report is the more valuable half.** An admin view listing every
active category with no mapped department, and every `dept_*` slug with no
categories mapped to it. That turns a silent runtime failure into a Tuesday
morning admin task. Cheap to build, and it addresses the operational trap rather
than only the requirement.

### 3.3 Data-scoped RBAC (§4.83)

Extend, do not replace. `require_permission` stays; a scope resolves alongside
it.

```python
@dataclass(frozen=True)
class DataScope:
    inboxes: list[int] | None = None     # None = all
    teams: list[str] | None = None
    dealers: list[str] | None = None
    own_only: bool = False
```

A role gains an optional `DataScope`. `None` on every field means account-wide —
so **every existing role keeps its current behaviour**, which is the compatibility
requirement for shipping this on a live tenant.

Enforcement at two layers, and both are needed:

- **API:** a shared dependency injects the caller's scope into the metrics and
  admin query filters (composing with P4's `MetricFilters`), so a dealer-scoped
  supervisor's dashboard query is narrowed server-side.
- **Chatwoot-native:** the three existing conversation-visibility keys already
  mirror into a Chatwoot `CustomRole` (patch `0027`/`0028` + `chatwoot_role_mirror.py`).
  Inbox scoping maps onto Chatwoot's own inbox membership.

**Enforced server-side, never by hiding UI.** A scope that only removes a menu
item is not an access control, and this is a system holding customer PII and call
recordings.

**Scope is intersective, never additive.** Two scopes on one user narrow to the
intersection. A permission model where adding a role can *widen* data access is
one where nobody can answer "what can this person see".

**`own_only` deliberately does not apply to reports.** A supervisor scoped to a
team should see team aggregates; an agent with `own_only` sees their own
conversations but not a team report at all — the report endpoint returns 403
rather than a single-agent aggregate that reads like a team figure.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Deleting a category orphans historical cases | Retire, never delete; retired values stay resolvable |
| A Chatwoot sync failure discards an operator's edit | Store is authoritative; failure leaves an explicit retry state |
| Retiring a parent orphans its children | Cascade integrity enforced at write; prompt on retire |
| Auto-applied departments misroute exceptional cases | Suggest-only, matching the existing AI-suggestion behaviour |
| A data scope silently widens access | Intersective by construction; a test asserts adding a role never widens |
| Scoping implemented as UI hiding | Enforced in the query layer; tests call the API directly |
| Existing roles change behaviour | `None` on every scope field = account-wide; regression-tested |

## 5. Testing

- **Store** (`test_taxonomy_store.py`): seeded from env JSON; seeding never
  overwrites an operator edit; retire hides from picker and stays resolvable;
  cascade integrity; sort order.
- **Sync** (`test_taxonomy_sync.py`): idempotent; failure leaves store updated
  and surfaces an out-of-sync state; no restart required.
- **Category→dept** (`test_category_department.py`): suggestion pre-fills;
  override recorded; the coverage report lists unmapped categories and
  unreferenced departments.
- **Scopes** (`test_data_scope.py`): `None` = account-wide; inbox scope narrows;
  two roles intersect; `own_only` 403s on team reports; enforcement holds when
  called directly, bypassing the UI.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `TAXONOMY_ADMIN_ENABLED` | `false` | Off = env JSON only, exactly as today |
| `CATEGORY_DEPARTMENT_MAPPING_ENABLED` | `false` | Off = no suggestion, no coverage report |
| `DATA_SCOPED_RBAC_ENABLED` | `false` | Off = function-level only, as today |

## 7. Requirements closed

2.2.1, 4.30, 4.83, 8.1.10, A-6.

**Stated limit on §4.83:** this delivers per-inbox, per-team, per-dealer and
own-only scoping. **Per-field masking is not included** — that is PII masking,
gap R16, and it is blocked on client question Q7 (which regulation applies).
Claiming "permissions by data" as complete while any user with `customer360.view`
can read every phone number would overstate it.
