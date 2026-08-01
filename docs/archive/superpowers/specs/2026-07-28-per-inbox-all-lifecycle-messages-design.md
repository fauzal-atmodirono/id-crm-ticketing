# Per-inbox override for all auto-close flow messages

**Date:** 2026-07-28
**Status:** Approved (design)
**Scope:** extends the per-inbox inactivity feature — surface all seven auto-close flow customer messages as per-inbox fields in the native inbox Business-Hours "Inactivity & auto-close" section. Backend `InboxTimingStore` + `/kb/inboxes/{id}/timing`, agent scanner + lifecycle, and fork patch 0023.

## Problem

The auto-close section exposes four *timers* and only **one** message (`idle_warning`). The flow actually posts seven customer messages; the other six are only editable per-assistant in Knowledge → Settings. Operators want every flow message editable per-inbox in one place.

## The seven flow messages + posting sites

| Persona key | Posted at | SOP default |
|---|---|---|
| `idle_warning` | scanner `warn` | "Your chat will close in {{minutes}} minutes if we do not hear from you." *(already per-inbox)* |
| `idle_close` | scanner `close` | "Closed due to inactivity." |
| `resolution_prompt` | scanner `close` | "Is your case resolved? Please reply YES or NO." |
| `assign_agent` | lifecycle `handle_lifecycle_reply` (NOT-resolved branch) | (ASSIGN_AGENT_DEFAULT) |
| `survey_ai` | lifecycle `handle_lifecycle_reply` (resolved→survey) | "Thank you. Please rate our AI assistant from 1 to 5." |
| `thanks` | lifecycle `handle_lifecycle_reply` (after rating) | "Thank you." |
| `survey_agent` | lifecycle `on_human_resolved` | "Thank you. Please rate our support agent from 1 to 5." |

## Decision (from brainstorming)

All **seven** messages per-inbox editable in the auto-close section. Precedence for each: **per-inbox → persona (Knowledge Settings) → SOP default**. Only `idle_warning` renders `{{minutes}}`.

## Non-goals

- No change to timers, the enable toggle, disclaimer (native), CSAT, or routing.
- No new lifecycle messages beyond the seven above (e.g. welcome/handoff/resolution stay persona-only).

## Design

### 1. Storage — `InboxTimingStore`

Generalize the single `MESSAGE_KEY` into `MESSAGE_KEYS` (7 keys, each a `str`):
```python
MESSAGE_KEYS = (
    "idle_warning_message",
    "idle_close_message",
    "resolution_prompt_message",
    "assign_agent_message",
    "survey_ai_message",
    "survey_agent_message",
    "thanks_message",
)
```
`_clean_timing` keeps each `MESSAGE_KEYS` value only if `isinstance(str)` (the 4 `TIMING_KEYS` stay int-guarded; `ENABLED_KEY` bool). (`MESSAGE_KEY = "idle_warning_message"` may remain as an alias to avoid churn, or be replaced by `MESSAGE_KEYS[0]`.)

### 2. API — `kb_inboxes_router.py`

- `InboxTimingBody` gains six more `str | None = Field(default=None, max_length=2000)` fields (the six new keys; `idle_warning_message` already present).
- `_normalize_timing` returns all seven message keys (`str | None`) + the four ints + `inactivity_enabled`.
- PUT stays full-replace (`to_store` keeps non-null; `False`/`0` unaffected). Both the `/timing` GET/PUT and the `/kb/inboxes` list rows carry them.

### 3. Agent — unified resolver + wiring

- New pure helper in `lifecycle.py`:
  ```python
  def _resolve_lifecycle_message(timing: dict | None, msgs: dict | None, key: str, default: str) -> str:
      """Per-inbox override -> persona message -> SOP default. `key` is the
      persona key (e.g. "idle_close"); the per-inbox store key is f"{key}_message"."""
      per = (timing or {}).get(f"{key}_message")
      if isinstance(per, str) and per.strip():
          return per
      return _resolve_message(msgs, key, default)
  ```
- Wire it at every posting site, each already having (or now fetching) the per-inbox `timing`:
  - **scanner `warn`**: `render_idle_warning(_resolve_lifecycle_message(timing, msgs, "idle_warning", IDLE_WARNING_DEFAULT), grace)` (replaces the current inline per-inbox idle_warning check).
  - **scanner `close`**: `idle_close` + `resolution_prompt` via the helper (scanner already has `timing`).
  - **lifecycle `handle_lifecycle_reply`**: add `timing = await _fetch_lifecycle_timing(inbox_id)` next to `msgs`; resolve `assign_agent`, `survey_ai`, `thanks` via the helper.
  - **lifecycle `on_human_resolved`**: add `timing = await _fetch_lifecycle_timing(inbox_id)`; resolve `survey_agent` via the helper.
- Agent client `get_assistant_lifecycle_timing` returns all seven message keys (str-or-None), read from the cached `/kb/inboxes` row.

### 4. UI — fork patch 0023 (regenerate)

Add six more `<textarea>`s to the "Inactivity & auto-close" block (7 total), each bound to a per-inbox message field, each with its SOP default as `placeholder`. Data: one string per message (default `''`). `loadTiming` sets each from `t.<key> ?? ''`; `updateInbox` sends each as `.trim() || null` in the `setInboxTiming` body. Labels: "Idle warning message" (has it), "Chat closed message", "Resolution prompt", "Assign-to-agent message", "AI rating survey", "Agent rating survey", "Thank-you message". Keep the toggle + four number inputs unchanged.

### 5. Testing (TDD)

- **store:** round-trip of all 7 message keys (partial + full); wrong-type dropped.
- **router:** PUT/GET the new fields; `max_length` 422; list rows carry them.
- **agent client:** returns the 7 message keys (present/absent/None).
- **agent resolver:** `_resolve_lifecycle_message` precedence (per-inbox non-empty → persona → default).
- **scanner:** close posts per-inbox `idle_close`/`resolution_prompt`; **lifecycle:** `handle_lifecycle_reply`/`on_human_resolved` post per-inbox `survey_*`/`thanks`/`assign_agent` when set, else persona/default.
- **UI:** manual smoke.

## Rollout

Backend + agent via the normal sync path; UI via Cloud Build + recreate. Fully backward-compatible: empty per-inbox message → persona → SOP default (byte-identical to today). Reuses the store/router/client typed-field machinery from the prior increment.
