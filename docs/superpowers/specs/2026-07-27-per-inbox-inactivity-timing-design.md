# Per-inbox inactivity timing — native inbox settings UI, backend storage

**Date:** 2026-07-27
**Status:** Approved (design)
**Author:** brainstorming session

## Problem

The conversation-lifecycle auto-close flow (idle warn → grace → close, and the
resolution-confirm grace) is driven by four timing values that today are
**global environment variables** in `agent/app/config.py` with no per-inbox
override and no UI:

- `lifecycle_idle_warn_minutes` (default `10`) — warn after this many idle minutes
- `lifecycle_idle_close_grace_minutes` (default `5`) — extra grace before close, **in business hours**
- `lifecycle_idle_close_out_of_hours_grace_minutes` (default `0`) — extra grace before close, **out of hours**
- `lifecycle_confirm_grace_minutes` (default `10`) — grace on the resolution-confirm step

The Proton CRM process flow (`docs/CRM Process Flow (1).xlsx`) requires operators
to tune this idle behaviour themselves, and to differ it **per channel** (e.g.
WhatsApp vs Email). Operators cannot edit env vars.

## Non-goals

- **Business-hours editing.** Business hours + timezone are already configured
  natively per-inbox in Chatwoot (Settings → Inboxes → Business Hours), and the
  agent already reads them live via `chatwoot.get_inbox()` →
  `business_hours.is_within_business_hours()`. This design does **not** touch that.
- **No Chatwoot Ruby patches.** (See "Rejected approaches".)
- **No new env vars.** The four existing env vars remain the fallback defaults.
- `lifecycle_assigned_idle_resolve_minutes` is **not** exposed (not requested).

## Context / existing wiring (verified)

- `agent/app/services/lifecycle_scanner.py::_process_one` computes `warn_after`,
  in/out-of-hours `grace`, `close_after`, and `confirm_after` from
  `settings.*` (lines ~144–151), then calls `decide_idle_action(...)`.
- It already fetches the inbox live (`chatwoot.get_inbox(inbox_id)`, cached per
  scan) and resolves per-inbox assistant messages via
  `lifecycle._fetch_assistant_messages(inbox_id)` →
  `ProtonConfigClient.get_assistant_messages(inbox_id)`.
- `ProtonConfigClient` (`agent/app/clients/proton.py`) already fetches and
  **caches** `GET /kb/inboxes` (via `_fetch_cached`) to map `inbox_id →
  assistant_id`.
- The backend already has a per-inbox store pattern:
  `backend/.../adapters/inbox_assignment_store.py` (Port + InMemory + Firestore +
  `build_*` factory), served by `kb_inboxes_router.py`.
- The Chatwoot SPA fork already talks to our backend from patched SPA code via
  `kbRequest`/`protonKnowledge.js` with the `x-api-key` (patches 0001, 0002),
  and already patches upstream Chatwoot settings pages (reports, `settings.json`,
  store) — so patching the native inbox-settings view is within the established
  approach.

### Rejected approaches

- **Store on the Chatwoot inbox `additional_attributes`.** Verified infeasible
  in stock Chatwoot **v4.15.1** (the pinned base image):
  - *Write blocked:* `additional_attributes` is **not** in the inbox-update
    permitted params (`inbox_attributes` allowlist); a write via the standard
    inbox API is silently dropped.
  - *Read channel-scoped:* the inbox jbuilder exposes
    `additional_attributes` only for **API-type** channels (and it is the
    *channel's* attributes), so a stored value would not return for
    WhatsApp/Email/etc. via `get_inbox`.
  - Making it work would require patching Chatwoot Ruby (controller strong-params
    + serializer, ~3–4 upstream files) → largest surface + highest upgrade drift.
    Rejected in favour of backend-owned storage.
- **Store timing inside `inbox_assignments`.** Rejected: an inbox can use the
  *default* assistant (no stored assignment) yet still need custom timing;
  coupling would force an assignment just to set timing, and the assignment's
  "null a field → delete the whole doc" clear-semantics would wipe timing.

## Design

### 1. Storage — new `InboxTimingStore` (backend)

New file `backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py`,
mirroring `inbox_assignment_store.py` exactly:

- `InboxTimingStorePort` (Protocol): `get_all() -> dict[int, dict]`,
  `get(inbox_id) -> dict | None`, `set(inbox_id, timing: dict) -> None`,
  `delete(inbox_id) -> None`. All reads fail-open (`{}`/`None`); writes swallow
  after logging.
- `InMemoryInboxTimingStore` (tests/dev).
- `FirestoreInboxTimingStore` — Firestore collection **`inbox_timing`**, one doc
  per `inbox_id` (doc id = `str(inbox_id)`). Sync SDK via `asyncio.to_thread`;
  every path degrades, never raises.
- `build_inbox_timing_store(settings)` — Firestore when `firestore_project_id`
  set, else InMemory.

**Stored shape** — the four keys, each optional (`int | None`):

```json
{ "idle_warn_minutes": 10, "idle_close_grace_minutes": 5,
  "idle_close_out_of_hours_grace_minutes": 0, "confirm_grace_minutes": 10 }
```

**Semantics:** a key that is `None` or absent = **inherit the global env
default**. An explicit `0` is a valid value (kept distinct from "unset"). The
store persists `None` values as absent keys (so `get` returns only set keys, or
`None`/all-`None` when nothing is configured).

Wired into the backend DI/bootstrap where the other stores are constructed
(same place `build_inbox_assignment_store` is called), and passed to the
inboxes router.

### 2. API (backend) — `kb_inboxes_router.py`

Add to `build_kb_inboxes_router(...)` (inject the new `timing_store`):

- **`GET /kb/inboxes/{inbox_id}/timing`** → `{ idle_warn_minutes,
  idle_close_grace_minutes, idle_close_out_of_hours_grace_minutes,
  confirm_grace_minutes }`, each `int | null` (null when unset). Auth: existing
  `_authorize(x_api_key)`.
- **`PUT /kb/inboxes/{inbox_id}/timing`** — **full-replace** semantics (the body
  represents the complete desired timing state):
  - Body is the four optional ints (`int | None`).
  - Validation: each **non-null** value must be an int in `0..1440` → `422`
    otherwise.
  - A field that is `null` or omitted = that field is **unset** (inherits the
    env default). No merge with prior state.
  - If the resulting state has **no** set fields, `delete(inbox_id)` (full
    revert to defaults); otherwise `set(inbox_id, {only the non-null fields})`.
  - Returns the stored timing (the four keys, each `int | null`).

  This full-replace rule needs no omit-vs-null distinction and maps exactly to
  the UI, which always sends all four fields each save (empty input → `null`).
- **`GET /kb/inboxes` list rows** additionally carry the four timing fields
  (joined from `timing_store.get_all()`; each `int | null`) — so the **agent**
  reads them from the fetch it already caches. Rows with no stored timing emit
  all-`null`.

Request model: a Pydantic `InboxTimingBody` with four `int | None = None`
fields.

### 3. Agent (agent/)

- **`ProtonConfigClient.get_assistant_lifecycle_timing(inbox_id) -> dict | None`**
  (`agent/app/clients/proton.py`): reads the four timing values for `inbox_id`
  from the **already-cached** `GET /kb/inboxes` response (`_fetch_cached`,
  same TTL as `get_assistant_messages`). Returns `{key: int | None}` or `None`
  on any failure. Never raises. **No new HTTP round-trip.**
- **`agent/app/services/lifecycle.py`**: add a thin
  `_fetch_lifecycle_timing(inbox_id)` fail-open wrapper, mirroring
  `_fetch_assistant_messages`.
- **`agent/app/services/lifecycle_scanner.py::_process_one`**: after resolving
  the inbox, fetch per-inbox timing and override each value only when present
  (not `None`):
  - `warn_after = timing.idle_warn_minutes ?? settings.lifecycle_idle_warn_minutes`
  - in-hours `grace = timing.idle_close_grace_minutes ?? settings.lifecycle_idle_close_grace_minutes`
  - out-of-hours `grace = timing.idle_close_out_of_hours_grace_minutes ?? settings.lifecycle_idle_close_out_of_hours_grace_minutes`
  - `confirm_after = timing.confirm_grace_minutes ?? settings.lifecycle_confirm_grace_minutes`
  - `close_after = warn_after + grace` (unchanged formula)
  - Business-hours selection of which grace to use is unchanged.
  - Fail-open: `timing is None` or any error → all env defaults (**byte-identical
    to today's behaviour**).

**Precedence:** per-inbox value (including explicit `0`) > global env default.

### 4. UI — new fork patch (native Settings → Inboxes)

New patch `deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch`:

- Add an **"Inactivity & auto-close"** card to Chatwoot's native inbox settings
  view, placed adjacent to Business Hours (shares the in/out-of-hours concept).
- Four `<input type="number" min="0" max="1440" step="1">` bound to a local
  form: warn-after, in-hours close grace, out-of-hours close grace,
  resolution-confirm grace.
- **Empty input = inherit** the global default; the placeholder shows the
  default value (10 / 5 / 0 / 10). Saving an empty field sends `null`.
- On mount: `GET /kb/inboxes/{inbox_id}/timing` (via `kbRequest`/a new
  `protonKnowledge.js` helper) to populate. Save button → `PUT
  /kb/inboxes/{inbox_id}/timing`. `useAlert` on success/failure, matching
  `KnowledgeInboxes.vue` conventions.
- A one-line helper text explains the flow: *warn after N min idle → close after
  the grace (in-hours vs out-of-hours) → resolution-confirm grace.*

**Implementation research step (at build time):** locate the exact upstream
component for the inbox business-hours / settings view in Chatwoot **v4.15.1**
(the SPA is not in this checkout — extract from the pinned image or the upstream
tag) and confirm the insertion point + i18n string location.

## Testing (TDD)

**Backend** (`.venv/bin/pytest src/`, co-located `test_*.py`):
- `InboxTimingStore` (InMemory): set/get/get_all round-trip; partial set;
  `None`/absent semantics; delete; fail-open.
- Inboxes router: `GET`/`PUT …/timing` happy path; `422` for out-of-range /
  non-int; null-clears a field; all-empty → delete; auth (`401` no/bad key);
  `GET /kb/inboxes` list rows include timing (set + unset → nulls).

**Agent** (`pytest`, respx-stubbed per `tests/conftest.py`):
- `get_assistant_lifecycle_timing`: full / partial / absent-row / all-`None` /
  HTTP error → correct dict or `None`; reuses cached `/kb/inboxes` (assert a
  single fetch).
- `lifecycle_scanner._process_one`: per-inbox override applied (incl. explicit
  `0`); env fallback when unset/None/error; in-hours vs out-of-hours grace
  selection still correct; fail-open leaves today's behaviour intact.

**UI:** no unit tests (fork patches aren't unit-tested here). Manual
verification: set values on an inbox → confirm persisted (GET) → confirm the
agent lifecycle scan honours them (log/behaviour) → empty field → confirm env
default resumes.

## Rollout / safety

- Fully backward-compatible and fail-open: with no `inbox_timing` docs and no UI
  use, the agent reads env defaults exactly as today.
- The new store + endpoints are additive; no migration (Firestore is
  schemaless; `Base.metadata.create_all` unaffected — this store is Firestore,
  not SQL).
- The SPA patch is additive and isolated to one settings view.
