# Case categories & subcategories (one enforced main category + dependent subcategory)

**Date:** 2026-08-01
**Status:** Approved (design)
**Scope:** Replace today's flat, hierarchy-less `category_*`/`subcat_*` label convention with a single enforced main-category + dependent-subcategory model, driven by a tenant-configurable taxonomy. Roadmap item #1 in `docs/roadmap/2026-08-01-next-development-roadmap.md`.

## Problem

PRO-NET explicitly asked for CIF/banking-style categorization: exactly one main category per case, plus a dependent subcategory — "it cannot be like selecting two main categories." Today that's structurally impossible to prevent:

- Two independent, uncoordinated AI classifiers exist: `backend/apps/backend/src/chatbot/features/chat/agents.py`'s `classify_ticket_tool` (mid-conversation, free-text category/subcategory, no enum) and `agent/app/services/categorize.py` (resolution-time, closed-vocabulary single slug, no subcategory, gated off by default).
- Both ultimately write **Chatwoot labels** (`category_<slug>`, `subcat_<slug>`) — flat, account-wide strings with no parent/child relationship and no exclusivity. Nothing stops an agent from adding two `category_*` labels via Chatwoot's own native label picker.
- No agent-facing editor exists at all; the only UI surface is a read-only report column.
- No taxonomy has ever been written down — the client said they still need an internal discussion on the exact ID scheme.

## Decision (from brainstorming)

- **Unify the two classifiers around one taxonomy and one storage location**, without merging them into one codepath — `backend/` and `agent/` are intentionally decoupled services (HTTP-only, fail-open, no shared process/DB per this repo's architecture) and stay that way.
- **Taxonomy is configurable, not hardcoded**: `CASE_TAXONOMY_JSON`, same env-driven pattern as `PIC_MAP_JSON`, set identically in both services' tenant env. Ships with a sensible default (below) so the system works out of the box; swappable per-tenant once Proton finalizes their scheme, no redeploy of code required.
- **Storage moves from labels to Chatwoot custom attributes** — `case_category` and `case_subcategory`, registered as **List-type custom attribute definitions**. This is the same call shape already used by `escalation_notifier.py`'s `case_state` write (precedent in this codebase).
- **No new frontend component.** Chatwoot's native conversation sidebar renders List-type custom attributes as single-select dropdowns automatically — exclusivity is enforced by Chatwoot's own UI, not by a custom component an agent could bypass via the native label picker.
- **Subcategory is flattened**, not cascading. Chatwoot's native List-type attributes don't support one list filtered by another attribute's value. v1 ships subcategory as one list prefixed by category (e.g. "Sales: Test Drive Booking"). A cascading picker is a possible fast-follow, not in scope now.
- **`agent/`'s resolution-time classifier becomes fallback-only**: it checks `custom_attributes.case_category` first and only runs its own (taxonomy-constrained) Gemini call if that's still empty. It never overwrites a category `backend/` already set mid-conversation.
- **No backfill.** Existing `category_*`/`subcat_*` labels are left in place, untouched, as historical/inert data. New custom attributes start fresh going forward — the lower-risk option the roadmap doc already flagged.

## Non-goals

- Not migrating/backfilling historical label-based categorization.
- Not building a cascading/dependent subcategory picker (flattened list for v1).
- Not moving `division`/`department` labels to custom attributes — out of scope; the client's complaint was specifically about main-category exclusivity, not these.
- Not changing how PIC/escalation routing resolves department (`pic_registry.py` unaffected).

## Default taxonomy (draft, placeholder pending Proton's finalized scheme)

Built from categories already implied by the existing `CATEGORY_TO_DIVISION` mapping (`backend/apps/backend/src/chatbot/features/metrics/mapping.py`) plus domains raised in the meeting transcript:

```json
{
  "sales": {
    "label": "Sales",
    "subcategories": ["Test Drive Booking", "Pricing Inquiry", "Vehicle Availability", "Trade-In", "Financing"]
  },
  "aftersales": {
    "label": "Aftersales",
    "subcategories": ["Service Booking", "Warranty Claim", "Spare Parts", "Recall"]
  },
  "apps": {
    "label": "Apps",
    "subcategories": ["Login Issue", "App Crash", "Feature Request", "Account Sync"]
  },
  "charging": {
    "label": "Charging",
    "subcategories": ["Charger Fault", "Charging Station Locator", "Billing"]
  },
  "roadside_assistance": {
    "label": "Roadside Assistance",
    "subcategories": ["Breakdown", "Accident", "Towing"]
  },
  "general_enquiry": {
    "label": "General Enquiry",
    "subcategories": ["Product Info", "Dealer Locator", "Other"]
  },
  "complaint": {
    "label": "Complaint",
    "subcategories": ["Service Quality", "Product Defect", "Staff Conduct", "Other"]
  }
}
```

## Design

### 1. Taxonomy loader (new, one per service — `backend/` and `agent/`)

Mirrors `pic_registry.py`'s `build_pic_registry` pattern exactly: parse `CASE_TAXONOMY_JSON` once at startup into a `CaseTaxonomy` object exposing `main_categories() -> list[slug]`, `subcategories_for(slug) -> list[str]`, `is_valid(category, subcategory) -> bool`. Empty/malformed JSON → empty taxonomy (fail-open: log a warning, classification falls back to accepting free text as it does today, never breaks the AI turn).

### 2. `classify_ticket_tool` (`backend/.../features/chat/agents.py`) — validate against taxonomy

Reject a `category` not in `taxonomy.main_categories()`; reject a `subcategory` not in `taxonomy.subcategories_for(category)`. On rejection: log and don't write (keep any existing value rather than clobbering it with garbage). Docstring/prompt updated to enumerate the actual taxonomy instead of illustrative free-text examples.

### 3. Write path (`backend/.../features/chat/adapters/chatwoot.py`)

Replace the `category_*`/`subcat_*` entries in `_dimension_labels`'s label payload with a `POST /conversations/{conv_id}/custom_attributes` call setting `case_category`/`case_subcategory` — same call shape as `escalation_notifier.py::_write_case_state`. `division`/`department`/`sla` stay as labels, unchanged.

### 4. Provisioning (new, one-time + idempotent)

A script (mirrors the shape of existing tenant-provisioning scripts under `deploy/scripts/`) that reads `CASE_TAXONOMY_JSON` and calls Chatwoot's Custom Attribute Definitions API to create/update the `case_category` (List, options = main category labels) and `case_subcategory` (List, options = flattened "Category: Subcategory" strings) definitions. Re-run after Proton finalizes their taxonomy to update the dropdown options — no code change needed for a taxonomy update, only a JSON value + re-run.

### 5. `agent/app/services/categorize.py` — fallback-only, taxonomy-aware

Change the gate: run the classify call only when `custom_attributes.case_category` is empty on the conversation being resolved (fetch conversation, check attribute, short-circuit if already set). Replace `lifecycle_category_labels` (free comma-list) with the shared `CaseTaxonomy` loader. Best-effort subcategory: only write it if the model confidently matches one of `taxonomy.subcategories_for(category)`, else leave subcategory unset (main category is the hard-enforced field per the client ask; subcategory quality matters less here).

### 6. Reports (`deploy/chatwoot-fork/patches/0020-reports-native-merge.patch`)

Category/subcategory columns switch from reading `category_*`/`subcat_*` labels to reading `custom_attributes.case_category`/`case_subcategory`.

## Error handling

- Malformed/empty `CASE_TAXONOMY_JSON` → empty taxonomy, warning logged, classify tool falls back to free-text (today's behavior) rather than breaking the turn.
- Gemini emits a category/subcategory not in the taxonomy → rejected, not written, logged.
- Chatwoot custom_attributes API call fails → logged and swallowed, same as `_write_case_state`'s existing pattern — never raises out of the AI turn or the resolution webhook.

## Testing

- Taxonomy loader: valid parse, malformed JSON, missing/empty env — mirrors `test_pic_registry.py`.
- `classify_ticket_tool`: valid pair accepted and written; invalid category rejected; valid category + invalid subcategory-for-that-category rejected.
- `agent/categorize.py`: skips when `case_category` already set; classifies when empty; never writes a subcategory outside the taxonomy.
- Existing ticketing/classification tests updated to assert the custom_attributes call instead of the label call.
- Reports column test updated to read the new attribute source.

## Rollout

Provisioning script runs once per tenant after `CASE_TAXONOMY_JSON` is set. Backend/agent redeploy for the classifier + write-path changes. No Chatwoot image rebuild needed (List-type custom attributes are created via API, not a fork patch) unless a future cascading-picker fast-follow needs one.
