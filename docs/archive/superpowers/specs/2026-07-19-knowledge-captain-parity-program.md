# Knowledge Console — Captain-Parity Program (umbrella)

**Status:** approved architecture (2026-07-19). Umbrella for the multi-Assistant
redesign of the Knowledge console. Individual specs:
`…-assistants-design.md`, `…-settings-design.md`, `…-playground-design.md`,
`…-tools-design.md`, `…-scenarios-design.md`, `…-inboxes-design.md`.

## Goal
Re-build Chatwoot **Captain** (enterprise AI) ourselves on the
proton-conversational-ai backend + native Chatwoot fork UI, tenant-agnostic, so
any tenant gets: multiple named **Assistants**, each with its own persona,
scenarios, tool enablement, and inbox assignments — all grounding on a **shared**
tenant knowledge base (our existing Vertex Documents + `/kb/faq`).

## Parity map (verified against upstream v4.15.1 enterprise source)
| Captain (`enterprise/app/models/…`) | Ours |
|---|---|
| `captain/assistant.rb` | `assistants` store (§ assistants spec) |
| `captain/document.rb` | **shared** Vertex `/kb/documents` (already shipped, unchanged) |
| `captain/assistant_response.rb` (FAQs) | **shared** `/kb/faq` (already shipped, unchanged) |
| `captain/custom_tool.rb` (account-level) | tenant `custom_tools` registry (§ tools spec) |
| `captain/scenario.rb` (per-assistant) | `scenarios` store (§ scenarios spec) |
| `captain_inbox.rb` (assistant↔inbox) | `inbox_assignments` (§ inboxes spec) |
| `AssistantPlayground.vue` | native Playground (§ playground spec) |
| assistant settings forms | per-assistant persona (§ settings spec) |
| `components-next/copilot/*` | already shipped (Ask Copilot, patch 0005) |

**Conscious divergence:** Captain scopes Documents/Responses per-assistant; **we
keep the KB tenant-wide/shared** (one Vertex data store + one FAQ store that all
assistants query). Assistants differ by persona / tools / scenarios / inboxes,
**not** by knowledge. This avoids a Vertex re-architecture. (User decision.)

## What is per-assistant vs shared
- **Per-assistant:** persona/config, scenarios, inbox assignment, tool
  *enablement* (which registry tools + which built-ins this assistant may call),
  Playground target.
- **Tenant-wide / shared:** Documents (Vertex), FAQs, the custom-tool *registry*,
  ops knobs (assist/copilot model, `copilot_max_tool_iterations`, debounce,
  feature gates).

## Data model — new stores
All follow the existing **Port + `InMemory*` + `Firestore*`** pattern
(`src/chatbot/features/chat/adapters/live_faq.py`, `handoff_store.py`), chosen by a
`build_*` factory that degrades to in-memory when `firestore_project_id` is unset.
Each store never raises on read. Tenant-scoped by deployment (one backend/tenant).

| Store | Collection | Scope | Spec |
|---|---|---|---|
| `AssistantsStore` | `assistants` | tenant (many rows) | assistants |
| `ToolsStore` | `custom_tools` | tenant (registry) | tools |
| `ScenariosStore` | `scenarios` (field `assistant_id`) | per-assistant | scenarios |
| `InboxAssignmentStore` | `inbox_assignments` (doc/inbox_id) | tenant | inboxes |
| `TenantSettingsStore` | `tenant_settings/current` | tenant | settings |

### Assistant object (canonical — referenced by all specs)
```json
{
  "id": "asst_...", "name": "Sales Bot", "description": "...",
  "product_name": "PROTON",
  "config": {
    "instructions": "You are ...",           // system prompt body
    "temperature": 0.7,
    "guardrails": ["Never quote prices ..."],
    "response_guidelines": ["Be concise", "..."],
    "welcome_message": "...", "handoff_message": "...", "resolution_message": "...",
    "feature_faq": true, "feature_memory": false,
    "feature_citations": true, "feature_contact_attributes": false,
    "enabled_builtin_tools": ["search_knowledge_base"],
    "enabled_custom_tools": ["custom_check_order"]
  },
  "enabled": true, "is_default": false, "created_at": "..."
}
```

## Backend endpoint changes (shared contract)
- Every per-assistant endpoint accepts `assistant_id`; when omitted it resolves the
  tenant's **default assistant** (back-compat with today's single-assistant flow).
- `/assist/*` + `/assist/copilot` gain optional `assistant_id`. The router builds
  the **system prompt** from the assistant's `config` (instructions + product_name
  + guardrails + response_guidelines + active scenarios), and the **tool set** from
  enabled built-ins + enabled custom tools, grounded on the shared KB. See the
  assistants + settings + tools + scenarios specs for the exact assembly.
- Auth unchanged (`x-api-key` = `proton_backend_key`/`faq_admin_api_key`), CORS
  unchanged, all reads degrade (never 500).

## Default-assistant migration
On first boot with no assistants, `AssistantsStore` seeds one `is_default: true`
assistant whose `config.instructions` = the current hardcoded copilot/assist
prompt, so existing behavior is preserved with zero UI interaction. Documented in
the assistants spec.

## Frontend
- New shared composable `useKnowledgeAssistant` (selected assistant id, list,
  persisted in localStorage) — every per-assistant page shows an **assistant
  selector** in its header.
- Pages: `KnowledgeAssistants.vue` (list/create/delete + selector), plus the
  revised `KnowledgeSettings/Playground/Tools/Scenarios/Inboxes` views. Each ships
  as its own fork patch. FAQs/Documents views are unchanged.

## Build sequence (each its own implementation plan)
1. **Assistants** (backbone: store, CRUD, default seed, `assistant_id` threading,
   selector composable) — everything else depends on it.
2. **Settings** (per-assistant persona editor + tenant ops knobs).
3. **Playground** (assistant selector; reuses `/assist/copilot`).
4. **Tools** (tenant registry + per-assistant enablement).
5. **Scenarios** (per-assistant playbooks + tool refs).
6. **Inboxes** (assistant↔inbox assignment + off/suggest/auto mode).

## Patch numbering
Fork patches continue `0012`+ in build order: 0012 Assistants, 0013 Settings,
0014 Playground, 0015 Tools, 0016 Scenarios, 0017 Inboxes (final numbers set when
each plan is executed; see each spec).

## Testing strategy (whole program)
Each backend store/router gets unit tests mirroring `test_faq_admin_router` /
`test_kb_documents_router` (InMemory store, mocked Chatwoot/`respx` for webhooks).
System-prompt assembly and tool-set assembly get focused unit tests. Frontend
validated by the vite compile gate at image build + browser smoke per patch.

## Risks / boundaries
- **Cross-service:** the auto-reply agent-bot (`AGENT_MODE`, `DEBOUNCE_SECONDS`,
  per-inbox auto-reply) lives in the separate `id-crm-ticketing agent/` service.
  This program builds the stores + gating for the **assist/copilot** paths; the
  agent-bot reading the same stores (inbox mode, default_mode, debounce) is a
  documented **follow-up** (called out in settings + inboxes specs).
- **Scenarios depth:** we implement Captain **V1-style** scenarios (prompt-injected
  playbooks with allowed tools), NOT Captain V2's agent-runner sub-agent delegation
  (out of scope — see scenarios spec).
- **Webhook tools** = admin-trusted SSRF surface; mitigations in tools spec.
