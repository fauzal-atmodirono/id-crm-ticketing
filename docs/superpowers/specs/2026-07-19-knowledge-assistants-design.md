# Knowledge → Assistants (foundation) — Design

**Status:** approved design (2026-07-19). The backbone of the Captain-parity
program (see `…-captain-parity-program.md`). **Built first**; every other page
depends on it. Ships as fork patch `0012`.

## Summary
Introduce multiple named **Assistants** per tenant. Each assistant carries its own
persona/config (edited on the Settings page), scenarios, tool enablement, and inbox
assignments; all assistants share the tenant KB. This spec covers the assistant
**store + CRUD API + default-assistant seed + `assistant_id` threading through the
assist/copilot endpoints + the frontend list/selector**. Persona *editing* UI is
the Settings spec; this spec creates+lists+selects assistants and wires the model.

## Backend

### Store — `src/chatbot/features/chat/adapters/assistants_store.py`
`AssistantsStorePort` + `InMemoryAssistantsStore` + `FirestoreAssistantsStore`
(mirror `live_faq.py`), `build_assistants_store(settings)`. Firestore collection
`assistants`, one doc per assistant. Never raises on read.

Port methods:
- `list_all() -> list[Assistant]`
- `get(assistant_id) -> Assistant | None`
- `get_default() -> Assistant` (seeds one if none exist — see migration)
- `create(assistant) -> str` (returns id)
- `update(assistant_id, fields: dict) -> None`
- `delete(assistant_id) -> None` (refuse deleting the default; 409)

`Assistant` dataclass = the canonical object in the program spec (id, name,
description, product_name, config{…}, enabled, is_default, created_at).

### Default-assistant seed (migration)
`get_default()`: if no assistant has `is_default`, create one:
`name="Default Assistant"`, `config.instructions` = the current hardcoded
copilot/assist system prompt (extract the existing prompt constant from
`copilot_router.py` into `config/default_prompt.py` and reuse it), all built-in
tools enabled, `feature_faq=True`. This preserves today's behavior with no UI step.

### Router — `src/chatbot/features/chat/kb_assistants_router.py`
Auth `x-api-key`. Wire into `main.py` `_wire_agent_assist` + `run_assist_local.py`.
- `GET /kb/assistants` → `{ assistants: [Assistant…] }`
- `POST /kb/assistants` → body `{name, description, product_name}`; seeds a
  `config` with sensible defaults (instructions="", temperature=1.0, empty arrays,
  all built-ins enabled); returns `{id}`.
- `GET /kb/assistants/{id}` → `Assistant`
- `PUT /kb/assistants/{id}` → partial update (used by Settings for persona too)
- `DELETE /kb/assistants/{id}` → 409 if `is_default`, else delete.

### `assistant_id` threading into assist/copilot
- `copilot_router.py` and the assist router accept optional `assistant_id` in the
  request body. Add a resolver `resolve_assistant(store, assistant_id) ->
  Assistant` = `store.get(id) or store.get_default()`.
- Build the **system prompt** from the resolved assistant via a new
  `build_system_prompt(assistant, scenarios)` helper (§ settings + scenarios specs
  own the full assembly; this spec introduces the seam and the default path:
  prompt = `config.instructions` when set, else the extracted default prompt).
- Build the **tool declarations** from `config.enabled_builtin_tools` (filter
  `COPILOT_TOOLS`); custom/scenario tools are layered in by the tools + scenarios
  specs. This spec wires the built-in filter + the seam.
- Existing tests must still pass: with no `assistant_id`, the default assistant’s
  seeded prompt == the old constant ⇒ identical behavior.

## Frontend

### Composable — `app/javascript/dashboard/composables/useKnowledgeAssistant.js`
Holds `assistants` (fetched from `/kb/assistants`), `selectedId` (persisted in
`localStorage['proton_knowledge_assistant']`, defaults to the default assistant),
`select(id)`, `refresh()`. Shared by every per-assistant page.

### API helpers — add to `app/javascript/dashboard/api/protonKnowledge.js`
`listAssistants()`, `createAssistant(body)`, `getAssistant(id)`,
`updateAssistant(id, body)`, `deleteAssistant(id)` (fetch + `x-api-key`).

### View — `app/javascript/dashboard/components/proton/KnowledgeAssistants.vue`
- List assistants (name, product, enabled, "default" badge), **New assistant**
  modal (name/description/product), delete (guard default).
- An **AssistantSelector** sub-component (dropdown bound to `useKnowledgeAssistant`)
  that the other per-assistant pages render in their header.
- Wire into `ProtonKnowledgeHost.vue` for `section === 'assistants'`, and **add an
  "Assistants" item to the Knowledge sub-nav** (Sidebar.vue children) as the first
  child.

## Data flow
UI → `/kb/assistants` CRUD. Per-assistant pages read `selectedId` from the
composable and pass `assistant_id` to their endpoints. `/assist/copilot` resolves
`assistant_id` → persona + tools → grounded answer on shared KB.

## Testing
- `test_kb_assistants_router.py`: CRUD, default seed on first `get_default`,
  delete-default → 409, auth. InMemory store.
- `test_assistant_resolution.py`: no `assistant_id` → default; seeded default
  prompt equals the extracted old constant (behavior-preserving).
- Existing copilot/assist tests unchanged and green.
- Frontend: vite compile + smoke (create an assistant, see it in the selector).

## Out of scope / risks
- Persona **editing** UI = Settings spec (this spec only creates/lists/selects +
  the model + seam).
- No per-assistant KB (shared, per program decision).
- Deleting an assistant leaves its scenarios/inbox-assignments orphaned — cascade
  cleanup handled in scenarios + inboxes specs (delete by `assistant_id`).
