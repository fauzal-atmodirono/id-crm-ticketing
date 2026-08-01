# Knowledge → Settings — Design (per-assistant persona + tenant ops)

**Status:** approved design (2026-07-19). REVISED for the Captain-parity program
(see `…-captain-parity-program.md`). Was ops-only; now the **per-assistant persona
editor** (Captain's assistant settings) plus a small **tenant ops** section.
Built after Assistants. Ships as fork patch `0013`.

## Summary
Two panels on one page:
- **Assistant settings (per-assistant persona)** — edits the selected assistant's
  `config`. Mirrors Captain's `AssistantBasicSettingsForm.vue` +
  `AssistantSystemSettingsForm.vue`.
- **Advanced / tenant ops** — a few tenant-wide runtime knobs (model, iterations,
  debounce, feature gates) that override env.

## Panel 1 — Assistant persona (per-assistant)
Edits the resolved assistant via `PUT /kb/assistants/{id}` (from the assistants
spec — no new endpoint). Fields (map 1:1 to Captain, see `captain/assistant.rb`):
| Field | UI | Captain source |
|---|---|---|
| `name`, `description`, `product_name` | text | AssistantBasicSettingsForm |
| `config.instructions` | textarea (system prompt) | AssistantSystemSettingsForm |
| `config.temperature` | slider 0–1 (default 1) | AssistantSystemSettingsForm |
| `config.guardrails[]` | list editor (add/remove strings) | assistant.guardrails |
| `config.response_guidelines[]` | list editor | assistant.response_guidelines |
| `config.welcome_message` / `handoff_message` / `resolution_message` | text | controller params |
| `config.feature_faq / feature_memory / feature_citations / feature_contact_attributes` | toggles | config store_accessor |

### System-prompt assembly (backend)
`build_system_prompt(assistant, scenarios)` (introduced in assistants spec) composes:
```
<config.instructions or default_prompt>
Product: <product_name>
## Guardrails
- <guardrails…>
## Response guidelines
- <response_guidelines…>
## Playbooks         (from scenarios spec, if any enabled)
```
`temperature` is passed to the Gemini call. `feature_faq=false` removes
`search_knowledge_base` from the tool set; `feature_citations` toggles whether
`sources` are surfaced; `feature_memory`/`feature_contact_attributes` gate the
respective context tools. Copilot/assist routers read these from the resolved
assistant instead of hardcoded values.

## Panel 2 — Tenant ops (`tenant_settings`)
New `TenantSettingsStore` (Port + InMemory/Firestore, doc `tenant_settings/current`)
holding overrides that take precedence over env; blank ⇒ env default. Endpoint
`GET/PUT /kb/settings` returning `{ key: { value, source: "override"|"env" } }`.
| Field | Env default (`platform/config.py`) | Consumer |
|---|---|---|
| `assist_gemini_model` | `assist_gemini_model` | proton-conversational-ai |
| `copilot_gemini_model` | `copilot_gemini_model` | proton-conversational-ai |
| `copilot_max_tool_iterations` | `copilot_max_tool_iterations` | proton-conversational-ai |
| `feature_ai_assist` / `feature_copilot` / `feature_drafts` | `PROTON_FEATURES` / drafts gate | proton-conversational-ai |
| `default_mode` (suggest/auto) | agent-bot `AGENT_MODE` | **`agent/` service (follow-up)** |
| `debounce_seconds` | agent-bot `DEBOUNCE_SECONDS` | **`agent/` service (follow-up)** |

An **effective-config facade** `get_effective(store, settings, key)` resolves
override→env; copilot/assist routers read model/iterations through it. `default_mode`
and `debounce_seconds` are **stored/shown now**, but their runtime effect on
auto-reply needs the separate `agent/` service to read this store — documented
follow-up (same boundary as the inboxes spec). UI labels them "applies to
auto-reply (wiring pending)".

## Backend
- `kb_settings_router.py` (`GET/PUT /kb/settings`) for panel 2; panel 1 reuses
  `/kb/assistants/{id}`. Wire into `main.py` + `run_assist_local.py`.

## Frontend — `KnowledgeSettings.vue`
- Header: shared **AssistantSelector**. Panel 1 = persona form (grouped Basic /
  System / Guardrails / Response guidelines / Features), saves to
  `updateAssistant(id, …)`. Panel 2 = tenant ops form with override/env badges +
  per-field "reset to default", saves to `PUT /kb/settings`.
- API helpers: `getSettings()`, `updateSettings(body)` in `protonKnowledge.js`
  (assistant helpers already added in the assistants spec).

## Testing
- `test_kb_settings_router.py`: GET merges override>env with `source`; PUT
  validates+persists; clear reverts to env; auth. Facade unit test.
- `test_system_prompt_persona.py`: instructions/guardrails/response_guidelines/
  product appear in the assembled prompt; `feature_faq=false` drops
  `search_knowledge_base`; temperature threaded to the model call.
- Frontend: vite compile + smoke (edit persona, confirm copilot answer reflects it).

## Out of scope / risks
- `default_mode`/`debounce_seconds` runtime effect = agent-bot follow-up (boundary).
- Frontend `__PROTON_CONFIG__` feature flags still injected at page load from
  `PROTON_FEATURES`; unifying with the store is a later item.
- Guardrails/response-guidelines are prompt-level (not hard enforcement) in v1.
