# Knowledge → Tools — Design (tenant registry + per-assistant enablement)

**Status:** approved design (2026-07-19). REVISED for the Captain-parity program
(see `…-captain-parity-program.md`). Tools are a **tenant-level registry**;
assistants **enable** which they may call. Adopts Captain's `custom_tool` shape.
Built after Playground. Ships as fork patch `0015`. Largest backend of the set.

## Decision (from brainstorm + program) — "Both (built-ins + webhooks)"
- **Built-in tools:** the existing `COPILOT_TOOLS` (search_knowledge_base, chatwoot
  context, …). Toggle/edit description at the registry level; each assistant’s
  `config.enabled_builtin_tools` decides which it actually calls.
- **Custom webhook tools:** tenant-level, defined once, executed by the backend.
  Each assistant’s `config.enabled_custom_tools` (slugs) opts in.

## Backend

### Store — `tools_store.py`
`ToolsStorePort` + `InMemory*` + `Firestore*` (mirror `live_faq.py`),
`build_tools_store(settings)`. Firestore collection `custom_tools` (one doc per
tool) + a small `builtin_tool_overrides` doc. Never raises on read.

### Custom tool shape (mirrors `enterprise/app/models/captain/custom_tool.rb`)
```json
{ "slug": "custom_check_order", "title": "Check order status", "description": "...",
  "endpoint_url": "https://...", "http_method": "GET|POST",
  "auth_type": "none|bearer|basic|api_key", "auth_config": { ... },   // secret
  "param_schema": { "type": "object", "properties": {...}, "required": [...] },
  "request_template": "...", "response_template": "...", "enabled": true }
```
- `slug` unique per tenant, `^[a-zA-Z_][a-zA-Z0-9_]*$`, ≤ 64 chars (Gemini function
  name limit); auto-derived from title with a random suffix on collision.
- **Cap: ≤ 15 custom tools** per tenant (Captain parity).

### Router — `kb_tools_router.py`
Auth `x-api-key`. Wire into `main.py` + `run_assist_local.py`. Never returns raw
`auth_config` secrets (return `auth_type` + `has_auth`).
- `GET /kb/tools` → `{ builtins: [{name, enabled, description}], custom: [safe…] }`
- `POST /kb/tools` (create; validate slug/schema/HTTPS/cap), `PUT /kb/tools/{slug}`,
  `DELETE /kb/tools/{slug}`, `PUT /kb/tools/builtins/{name}` (toggle/description).

### Execution — extend `ToolExecutor` (`copilot_tools.py`)
When the model calls a tool not in the built-in dispatch, look it up in the store:
if a custom tool, render `request_template` with the call args, issue the HTTP call
per `http_method` + `auth_type`/`auth_config`, apply `response_template` to the
body, and return the result to the model. On any failure return a model-visible
error string (never raise). Reuse an `httpx.AsyncClient` (the `clients/deps`
singleton pattern).

**Safety (v1 defaults, approved):** HTTPS-only `endpoint_url`; connect+read
**timeout 10s**; **response cap ~16 KB**; redirects disabled; optional host
allowlist via env `custom_tool_allowed_hosts` (empty = any HTTPS host); admin-only
(behind `x-api-key`). Document that tool URLs/creds are trusted-admin input.

### Tool-set assembly (per assistant)
`build_tool_declarations(assistant, tools_store)` = enabled built-ins
(`config.enabled_builtin_tools` ∩ `COPILOT_TOOLS`, with registry description
overrides) + enabled custom tools (`config.enabled_custom_tools` ∩ registry,
`enabled=true`). Used by `copilot_router.py` (replaces the static `COPILOT_TOOLS`
reference). Scenario `tools` (scenarios spec) must be a subset of this set.

## Frontend — `KnowledgeTools.vue`
- Two sections. **Registry** (tenant-wide): list built-ins (toggle/description) +
  custom tools (CRUD modal: title, description, endpoint_url, http_method,
  auth_type + fields, param_schema editor, templates, enabled). Secrets write-only.
- **Per-assistant enablement:** header **AssistantSelector**; checkboxes for which
  registry tools the selected assistant may call → saved to the assistant’s
  `enabled_builtin_tools`/`enabled_custom_tools` via `updateAssistant`.
- API helpers in `protonKnowledge.js`: `listTools`, `createTool`, `updateTool`,
  `deleteTool`, `setBuiltinTool`. Host wiring `section === 'tools'`.

## Testing
- `test_kb_tools_router.py`: CRUD + validation (dup slug, bad schema, non-HTTPS,
  cap>15) + secret masking + auth. InMemory store.
- `test_copilot_webhook_tools.py`: model calls a custom tool → `ToolExecutor` HTTP
  call (mock with `respx`) → response fed back; timeout/oversize/error →
  model-visible message, no raise; disabled/not-enabled tool absent from
  declarations. Tool-set assembly unit test (built-in filter + custom opt-in).
- Frontend: vite compile + smoke (add a webhook tool, enable it for an assistant,
  ask a question that triggers it in Playground).

## Out of scope / risks
- **Security:** webhook tools = admin-trusted SSRF surface. Mitigations above.
  No arbitrary code — HTTP only. No OAuth flows (static auth only) in v1.
- No per-tool usage metrics in v1.
