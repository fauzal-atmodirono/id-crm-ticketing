# Per-inbox idle-warning message + inactivity enable toggle

**Date:** 2026-07-27
**Status:** Approved (design)
**Scope:** extends the per-inbox inactivity-timing feature (spec `2026-07-27-per-inbox-inactivity-timing`). Backend `InboxTimingStore` + `/kb/inboxes/{id}/timing`, agent scanner, and the native inbox Business-Hours UI (`WeeklyAvailability.vue`, fork patch 0023).

## Problem

The inbox "Inactivity & auto-close" section exposes the four numeric timers but
(1) has no field to edit the **warning message text** there — it lives only in
Knowledge → Settings (per-assistant persona `idle_warning_message`), and (2)
has no way to **enable/disable** the whole idle warn→close flow per inbox
(today it runs for every inbox whenever `LIFECYCLE_ENABLED`).

## Decisions (from brainstorming)

- Add a per-inbox **idle-warning message** field in the auto-close section; it
  **overrides** the persona message for that inbox.
- Add a per-inbox **enable toggle** for the whole idle warn→close→resolution/
  survey flow, styled like Chatwoot's "Enable business availability" toggle.
- Toggle default when unset = **enabled** (no regression for existing inboxes).

## Non-goals

- No change to CSAT, disclaimer (native), email auto-ack, or routing.
- The toggle gates the **bot-phase idle flow** (warn/close/resolution/survey).
  It does not gate the separate assigned-handoff auto-resolve
  (`lifecycle_assigned_idle_resolve_minutes`, its own niche config).
- No change to the global `LIFECYCLE_ENABLED` env (still the master on/off that
  starts the scanner).

## Design

### 1. Storage — `InboxTimingStore` (backend)

Today the store persists four `int` keys (`TIMING_KEYS`). Add two typed fields
to the same per-inbox record:

- `idle_warning_message: str` — per-inbox warning text (may contain `{{minutes}}`).
- `inactivity_enabled: bool` — per-inbox master switch.

Changes to `adapters/inbox_timing_store.py`:
- New module constants: `MESSAGE_KEY = "idle_warning_message"`,
  `ENABLED_KEY = "inactivity_enabled"`.
- `set(inbox_id, timing)` coerces per key type: `int(v)` for `TIMING_KEYS`,
  `str(v)` for `MESSAGE_KEY`, `bool(v)` for `ENABLED_KEY`; unknown keys ignored;
  only provided keys stored.
- Firestore `_clean` extracts each key by its type (int guard as today for the
  timing keys; `str` for message; `bool` for enabled). `get`/`get_all` return
  whatever subset is stored.

### 2. API — `kb_inboxes_router.py`

- `InboxTimingBody` gains `idle_warning_message: str | None = Field(default=None, max_length=2000)`
  and `inactivity_enabled: bool | None = None` (the four ints keep `ge=0, le=1440`).
- `PUT /kb/inboxes/{id}/timing` stays **full-replace**: `to_store` = fields that
  are not `None`; if none set → `delete(inbox_id)`; else `set`.
- `_normalize_timing(stored)` returns the four int keys (`int | None`) **plus**
  `idle_warning_message` (`str | None`) and `inactivity_enabled` (`bool | None`).
  Both the `GET …/timing` response and the `/kb/inboxes` list rows carry all six.

### 3. Agent

- `ProtonConfigClient.get_assistant_lifecycle_timing(inbox_id)` returns the four
  ints (as today) **plus** `idle_warning_message` (`str | None`) and
  `inactivity_enabled` (`bool | None`), read from the cached `/kb/inboxes` row.
  Non-str message → `None`; non-bool enabled → `None`.
- `lifecycle_scanner._process_one` — after fetching per-inbox `timing`:
  - **Enable gate:** if `timing.get("inactivity_enabled") is False` → `return`
    (skip the idle flow for this conversation). `None`/absent → runs (default
    enabled). Place the gate right after the timing fetch, before
    `decide_idle_action`.
  - **Message precedence** at the `warn` action:
    per-inbox `timing["idle_warning_message"]` (if a non-empty string) →
    persona `_resolve_message(msgs, "idle_warning", …)` → `IDLE_WARNING_DEFAULT`;
    then `render_idle_warning(warning, grace)` (existing `{{minutes}}` render).
  - Fail-open: `timing` empty/None → behaves exactly as today (enabled, persona/
    default message).

### 4. UI — `WeeklyAvailability.vue` (fork patch 0023 update)

In the "Inactivity & auto-close" block already injected into the form:
- Add a **toggle** above the fields, using Chatwoot's `SettingsToggleSection`
  (already imported): `v-model="inactivityEnabled"`, header
  "Enable inactivity & auto-close for this inbox", a one-line description. The
  toggle row renders whether or not it is on (fields stay visible below so an
  operator can configure while disabled).
- Add an **"Idle warning message"** `<textarea>` (helper: "use `{{minutes}}`
  for the close-grace value") bound to `idleWarningMessage`.
- Data: `inactivityEnabled` (default `true`), `idleWarningMessage` (default `""`).
- `loadTiming`: set `inactivityEnabled` from `inactivity_enabled ?? true`;
  `idleWarningMessage` from `idle_warning_message ?? ""`.
- `updateInbox` (the single "Update business hours settings" button) sends, via
  `setInboxTiming`, the four normalized numbers **plus**
  `idle_warning_message: idleWarningMessage.trim() || null` and
  `inactivity_enabled: inactivityEnabled`.

### Data-flow summary

Operator edits toggle+message+numbers → one button → `PUT …/timing`
(full-replace) → `InboxTimingStore` → agent reads them from the cached
`/kb/inboxes` row → scanner gates on `inactivity_enabled` and picks the message
by precedence, rendering `{{minutes}}`.

## Testing (TDD)

- **backend store:** round-trip of message (str) + enabled (bool) alongside the
  ints; partial set; type coercion; delete.
- **router:** `PUT`/`GET …/timing` with the two new fields; `max_length` → 422;
  `inactivity_enabled=false` round-trips; all-null → delete; list rows carry
  the six fields.
- **agent client:** `get_assistant_lifecycle_timing` returns message + enabled
  (present / null / wrong-type → null).
- **scanner:** `inactivity_enabled=false` → no warn (conversation left ACTIVE);
  `true`/unset → warns; per-inbox message overrides persona/default and renders
  `{{minutes}}`.
- **UI:** manual (patches aren't unit-tested); documented smoke.

## Rollout

- Backend + agent deploy via the normal `docker compose … up -d --build backend agent`
  sync path. UI needs a Chatwoot image rebuild (Cloud Build, amd64) + recreate.
- Backward-compatible: inboxes with no stored `inactivity_enabled` keep running
  (default enabled); empty per-inbox message falls back to persona/default.
