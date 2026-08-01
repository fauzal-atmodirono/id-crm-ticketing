# Knowledge → Scenarios — Design

**Status:** approved design (2026-07-19). Part of the Captain-parity program
(see `…-captain-parity-program.md`). Per-assistant. Built after Tools (needs the
tool registry to reference). Ships as fork patch `0016`.

## Summary
Per-assistant **playbooks**: named, described instruction blocks that tell the
assistant how to handle a situation, optionally naming which tools it may use.
Mirrors `enterprise/app/models/captain/scenario.rb`.

## Decision — Captain **V1-style** (prompt-injected), not V2 agent-runner
Captain V2 turns each scenario into a sub-agent the main assistant hands off to via
an `Agent::AgentRunnerService`. That framework is out of scope. We implement the
simpler, effective V1 shape: **enabled scenarios are injected into the system
prompt** as conditional playbooks ("When the customer asks about warranty claims,
follow: … You may use: search_knowledge_base, custom_check_order."). The model
already function-calls tools; scenarios steer *when/how*. (Flagged for review.)

## Backend

### Store — `scenarios_store.py`
`ScenariosStorePort` + `InMemory*` + `Firestore*` (mirror `live_faq.py`),
`build_scenarios_store(settings)`. Firestore collection `scenarios`, one doc per
scenario, field `assistant_id`. Never raises on read.

Port: `list_for_assistant(assistant_id)`, `get(id)`, `create(scenario)`,
`update(id, fields)`, `delete(id)`, `delete_for_assistant(assistant_id)` (cascade
when an assistant is deleted).

`Scenario` = `{ id, assistant_id, title, description, instruction, tools: [slug…],
enabled, created_at }`. `instruction` is markdown; `tools` are tool slugs
(built-in names or custom-tool slugs) referenced by the instruction.

### Router — `kb_scenarios_router.py`
Auth `x-api-key`. Wire into `main.py` + `run_assist_local.py`.
- `GET /kb/scenarios?assistant_id=…` → `{ scenarios: [...] }`
- `POST /kb/scenarios` → `{assistant_id, title, description, instruction, tools[]}`;
  validate each tool slug exists (built-in name in `COPILOT_TOOLS` or a custom-tool
  slug in the registry) — 422 on unknown slug. Returns `{id}`.
- `PUT /kb/scenarios/{id}`, `DELETE /kb/scenarios/{id}`.

### System-prompt integration
Extend `build_system_prompt(assistant, scenarios)` (introduced in the assistants
spec, owned jointly with settings): after the persona block, append an enabled-
scenarios section:
```
## Playbooks
### <title> — <description>
<instruction>
Allowed tools: <tools joined>
```
Only `enabled` scenarios for the resolved assistant are included. The assistant's
`enabled_*_tools` still bound what can actually be called; scenario `tools` are
guidance + must be a subset (validate on save, warn otherwise).

## Frontend — `KnowledgeScenarios.vue`
- Header: the shared **AssistantSelector**; list scenarios for the selected
  assistant (title, description, enabled toggle, tool chips).
- Create/edit modal: title, description, `instruction` (textarea, markdown), and a
  **multi-select of tools** (built-ins + this assistant's enabled custom tools,
  fetched from `/kb/tools` + the assistant config). Enabled checkbox.
- API helpers in `protonKnowledge.js`: `listScenarios(assistantId)`,
  `createScenario`, `updateScenario`, `deleteScenario`.
- Host wiring `section === 'scenarios'`; add "Scenarios" to the Knowledge sub-nav.

## Testing
- `test_kb_scenarios_router.py`: CRUD, unknown-tool-slug → 422, list filters by
  `assistant_id`, auth. InMemory store.
- `test_system_prompt_scenarios.py`: enabled scenarios appear in the assembled
  prompt with their tools; disabled excluded; cascade delete on assistant removal.
- Frontend: vite compile + smoke.

## Out of scope / risks
- **No V2 sub-agent delegation** (no `agent_name` handoff chains). Prompt-injection
  playbooks only. Note for a later "Scenarios V2" if true agent routing is wanted.
- Very many/large scenarios inflate the system prompt (token cost) — cap count/size
  per assistant (e.g. ≤ 20 scenarios, instruction ≤ 4 KB), enforced on POST.
