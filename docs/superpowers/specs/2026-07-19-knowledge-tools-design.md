# Knowledge → Tools — Design

**Status:** approved design (2026-07-19). One of four editable Knowledge sub-pages.
**Ships as fork patch `0014` + a new `custom_tools` store, router, and a webhook
tool-execution path in the copilot.** Largest of the four.

## Summary
Manage the assistant's tool set: (a) toggle/edit the **built-in** tools and
(b) define **custom webhook tools** the model can call, which the backend executes
by POSTing to a user-supplied URL. Both persist in a store the copilot reads when
assembling its tool list.

## Decision (from brainstorm) — "Both (built-ins + webhooks)"

## Shared foundations
- **Store pattern:** `ToolsStorePort` + `InMemoryToolsStore` + `FirestoreToolsStore`
  (mirror `live_faq.py`), `build_tools_store(settings)`. Firestore collection
  `custom_tools` (one doc per tool) + a small `builtin_tool_overrides` doc.
- **Frontend:** `components/proton/KnowledgeTools.vue` + `api/protonKnowledge.js`
  helpers + host wiring `section === 'tools'`. Patch `0014`.
- Reuses the **effective-config facade** from the Settings spec for "enabled set".

## Current state
`src/chatbot/features/assist/copilot_tools.py` defines `COPILOT_TOOLS`
(a static `list[dict]` of function declarations) and `ToolExecutor` (Python
dispatch by tool name). `copilot_router.py` passes
`"tools": [{"function_declarations": COPILOT_TOOLS}]` and executes calls via
`ToolExecutor`. Both become **store-driven**.

## (a) Built-in registry
- Store holds per-built-in overrides: `{ name, enabled: bool, description?, when_to_use? }`.
- Copilot assembles declarations from `COPILOT_TOOLS` filtered to `enabled`, with
  description overridden where set. Execution unchanged (`ToolExecutor`).
- Built-ins can be disabled but not deleted (they're code).

## (b) Webhook tools
Definition (stored per doc):
```json
{ "id": "...", "name": "check_order_status", "description": "...",
  "parameters": { "type": "object", "properties": { ... }, "required": [ ... ] },
  "url": "https://...", "method": "POST",
  "headers": { "X-Api-Key": "..." },   // optional; secret-bearing
  "enabled": true }
```
- **Declaration:** `{ name, description, parameters }` appended to the model's
  function_declarations alongside enabled built-ins.
- **Execution:** extend `ToolExecutor` — when the model calls a tool not in the
  built-in dispatch, look it up in the store; if it's a webhook tool, POST
  `{ arguments }` (or method as configured) to `url` with `headers`, await, and
  return the response body (JSON or text, truncated) to the model as the tool
  result. On any failure return a model-visible error string (never raise).
- **Safety (v1 defaults, approved):** HTTPS-only URL; connect+read **timeout 10s**;
  **response size cap ~16 KB**; redirects disabled; per-turn tool-call cap already
  bounded by `copilot_max_tool_iterations`. Optional host allowlist via env
  (`custom_tool_allowed_hosts`); empty = any HTTPS host. Use `httpx.AsyncClient`
  (reuse the `clients/deps` singleton pattern).

## Backend — `kb_tools_router.py`
- `GET /kb/tools` → `{ builtins: [{name, enabled, description, when_to_use}],
  custom: [{id, name, description, parameters, url, method, headers_present, enabled}] }`
  (never returns raw secret header values — return `headers_present`/masked).
- `POST /kb/tools` (create webhook tool), `PUT /kb/tools/{id}`, `DELETE /kb/tools/{id}`,
  and `PUT /kb/tools/builtins/{name}` (toggle/description). Auth `x-api-key`.
- Validate: unique tool `name` across built-ins+custom; valid JSON-schema `parameters`;
  HTTPS `url`; name matches `^[a-zA-Z_][a-zA-Z0-9_]*$` (Gemini function-name rules).
- Wire into `main.py` + `run_assist_local.py`.

## Copilot integration
`copilot_router.py`: replace the static `COPILOT_TOOLS` reference with a
`build_tool_declarations(tools_store)` call (enabled built-ins + enabled custom),
and give `ToolExecutor` a reference to the store for webhook dispatch. Keep the
existing `sources`/`tool_calls` reporting; a webhook tool's name shows in `tool_calls`.

## Testing
- `test_kb_tools_router.py`: CRUD + validation (dup name, bad schema, non-HTTPS)
  + secret masking + auth. InMemory store.
- `test_copilot_webhook_tools.py`: model calls a custom tool → `ToolExecutor` POSTs
  (mock with `respx`) → response fed back; timeout/oversize/error → model-visible
  message, no raise; disabled built-in absent from declarations.
- Frontend: vite compile + smoke (add a webhook tool, ask a question that triggers it).

## Out of scope / risks
- **Security is the main risk:** webhook tools let an admin make the backend call
  arbitrary HTTPS endpoints (SSRF-adjacent). v1 mitigations: HTTPS-only, timeout,
  size cap, optional host allowlist, admin-only (behind `x-api-key`). Document that
  tool URLs/headers are trusted-admin input. No arbitrary code execution — only HTTP.
- No OAuth flows for tool auth (static headers only) in v1.
- No per-tool usage metrics in v1.
