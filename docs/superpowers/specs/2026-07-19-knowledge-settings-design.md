# Knowledge → Settings — Design

**Status:** approved design (2026-07-19). One of four editable Knowledge sub-pages.
**Ships as fork patch `0013` + a new `assistant_settings` store & router.**

## Summary
An editable assistant-config page. Today the assist/copilot behaviour is fixed by
environment variables (pydantic `Settings`). This page adds a small **override
store** whose values take precedence over env at runtime, so an admin can tune the
assistant without a redeploy. Blank/unset override ⇒ fall back to the env default.

## Decision (from brainstorm) — "Core knobs"
Editable fields (last column = which service **consumes** the value):
| Field | Type | Env default (`platform/config.py`) | Consumer |
|---|---|---|---|
| `assist_gemini_model` | str | `assist_gemini_model` | proton-conversational-ai |
| `copilot_gemini_model` | str | `copilot_gemini_model` | proton-conversational-ai |
| `copilot_max_tool_iterations` | int | `copilot_max_tool_iterations` | proton-conversational-ai |
| `feature_ai_assist` | bool | from `PROTON_FEATURES` | proton-conversational-ai |
| `feature_copilot` | bool | from `PROTON_FEATURES` | proton-conversational-ai |
| `feature_drafts` | bool | `zammad_ai_drafts` / drafts gate | proton-conversational-ai |
| `default_mode` | enum `suggest`\|`auto` | agent-bot `AGENT_MODE` | **`agent/` service (follow-up)** |
| `debounce_seconds` | number | agent-bot `DEBOUNCE_SECONDS` | **`agent/` service (follow-up)** |

### Consumption boundary (important)
proton-conversational-ai owns the assist/copilot/KB endpoints; the **auto-reply
agent-bot** (incoming-message → AI, with `AGENT_MODE` + `DEBOUNCE_SECONDS`) lives
in the separate **`id-crm-ticketing agent/` service**. The end-state vision is a
single unified backend, so this store is the right home for *all* the knobs, but
in v1 only the proton-conversational-ai-consumed fields take effect immediately.
`default_mode` and `debounce_seconds` are **stored and shown now** but their
runtime effect requires the `agent/` service to read the same store — a documented
**follow-up** (same pattern as the Inboxes spec's agent-bot note). The Settings UI
labels these two "applies to auto-reply (wiring pending)" so the boundary is honest.

## Shared foundations
- **Store pattern:** mirror `src/chatbot/features/chat/adapters/live_faq.py`
  (`InMemoryLiveFaqStore` / `FirestoreLiveFaqStore`) and `handoff_store.py`:
  a `SettingsStorePort` + `InMemorySettingsStore` + `FirestoreSettingsStore`,
  `build_settings_store(settings)` picking Firestore when `firestore_project_id`
  is set, else in-memory. Firestore: a single doc (e.g. `assistant_config/current`)
  in a tenant-local collection. Never raises on read (returns `{}` → all env).
- **Frontend pattern:** `components/proton/KnowledgeSettings.vue` (n-* tokens,
  `useAlert`), helpers in `api/protonKnowledge.js`, host wiring by
  `section === 'settings'`. Patch `0013`.

## Backend
New `src/chatbot/features/chat/kb_settings_router.py`:
- `GET /kb/settings` → for each field `{ key: { value, source: "override"|"env" } }`
  by merging store overrides over the effective env defaults. Auth `x-api-key`.
- `PUT /kb/settings` → body of partial overrides; validates types/enums; writes to
  the store. Empty string / null on a field **clears** that override (revert to env).
- Wire into `main.py` `_wire_agent_assist` (+ `run_assist_local.py`), like
  `build_kb_documents_router`.

**Effective-settings accessor.** Add a small helper
`get_effective_setting(store, settings, key)` (or an `EffectiveConfig` facade) so
the copilot/assist routers resolve `model` / `max_tool_iterations` / `mode` from
`store-override → env`. The copilot router currently reads `settings.copilot_*`
directly; route those reads through the facade. This facade is the seam later
reused by Tools (enabled set) and Inboxes (default mode).

## Data model (`assistant_config/current`)
```json
{ "default_mode": "suggest", "assist_gemini_model": "", "copilot_gemini_model": "",
  "copilot_max_tool_iterations": null, "debounce_seconds": null,
  "feature_ai_assist": null, "feature_copilot": null, "feature_drafts": null }
```
`null`/`""` ⇒ "no override, use env". Types validated on PUT.

## Frontend — `KnowledgeSettings.vue`
A form (grouped: Behaviour / Models / Features). Each field shows its current
**effective value** and a badge when it's an **override** vs **env default**; a
"Reset to default" per field clears the override. Save → `PUT /kb/settings` →
`useAlert('Settings saved')` → reload. Validation mirrors backend (enum, int ≥ 1).

## Testing
- Backend: `test_kb_settings_router.py` — GET merges override>env with correct
  `source`; PUT validates + persists (InMemory store); clearing reverts to env;
  auth 401 like `test_faq_admin_auth`. Facade unit test: override wins over env.
- Frontend: vite compile + manual smoke (change model, confirm copilot uses it).

## Out of scope / risks
- Not every env var is exposed — only the core knobs (YAGNI). Broader surface later.
- **Live effect:** changing a model/iteration count affects subsequent calls only;
  in-flight calls unaffected. Firestore read per call is cheap but consider a short
  TTL cache if hot (note for later, not v1).
- Feature toggles here affect **backend** gating; the frontend `__PROTON_CONFIG__`
  feature flags are still injected at page load from `PROTON_FEATURES`. v1 keeps
  those in sync manually; unifying injection with the store is a later item.
