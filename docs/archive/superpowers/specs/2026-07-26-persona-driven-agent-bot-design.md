# Persona-Driven, Multilingual Agent-Bot (Chatwoot-only) — Design

**Date:** 2026-07-26
**Status:** Approved design (pre-implementation)
**Scope:** Theme 2 of the no-code configuration roadmap
(`2026-07-26-no-code-config-roadmap.md`) — persona, language, and message
templates. Additive extension of the EXISTING assistant-persona system.

## Problem

Operators can already edit a rich persona for the **copilot** (instructions,
temperature, guardrails, response-guidelines, welcome/handoff/resolution
messages, feature flags) in `KnowledgeSettings.vue`, per-inbox. But:

1. **7 customer-facing lifecycle messages are hard-coded** in the agent service
   with no persona field and no UI (idle-warning, idle-close, YES/NO resolution
   prompt, AI survey, agent survey, thanks, and the "we'll assign an agent"
   fallback).
2. **The front-line agent-bot ignores the persona entirely** — `orchestrator.py`
   passes a fixed English `SYSTEM_PROMPT` to Gemini; the operator's tone /
   guardrails shape the copilot but not the customer-facing WhatsApp/chat bot.
3. **No language setting** — all "reply in X language" behavior is hard-coded
   prompt text. The product is multilingual (id/ms/en) and multi-tenant.

## Goal

Let an operator set up their own persona in the CRM — tone, language, and every
customer-facing message — and have it govern the customer-facing agent-bot AND
the copilot. No new settings surface; extend the existing persona editor + the
existing agent↔backend bridge.

## Non-goals (out of scope)

- **Zammad `responder.py`** — Zammad is being retired; its draft prompt is left
  untouched. **Chatwoot-only.**
- **`categorize.py`** — internal classifier emitting a language-independent slug;
  stays hard-coded.
- **email auto-ack** — remains env-configurable (`EMAIL_AUTOACK_TEMPLATE`); may
  join the persona messages in a later pass.
- Per-tool UI toggles, scenarios, tenant-model settings — already exist elsewhere.

## Design principle

Every new persona field defaults to empty. Empty → today's hard-coded behavior,
**byte-for-byte**. All agent-side reads are fail-open (proton unreachable or
field empty → the existing default). This is a strictly additive, default-
preserving change.

## Existing seams (verified) this extends

- Model: `AssistantConfig` dataclass —
  `backend/apps/backend/src/chatbot/features/chat/adapters/assistants_store.py:61`
  (flat string fields like `welcome_message`; merged via `PUT /kb/assistants/{id}`).
- Copilot prompt assembly: `build_system_prompt` —
  `backend/apps/backend/src/chatbot/features/assist/assistant_runtime.py:49`
  (`DEFAULT_COPILOT_PROMPT` at :24 ends "Reply in the language the agent used.").
- Assist-sidebar persona prefix: `_apply_persona` —
  `backend/apps/backend/src/chatbot/features/assist/router.py:185`
  (injects `product_name` + `guardrails` only).
- Agent↔backend bridge: `ProtonConfigClient.get_assistant_messages(inbox_id)` —
  `agent/app/clients/proton.py` (resolves inbox→assistant, returns
  welcome/handoff/resolution from the assistant config; cached, fail-open).
- Agent-bot prompt: `SYSTEM_PROMPT` + `gemini.decide(SYSTEM_PROMPT, context)` —
  `agent/app/services/orchestrator.py:62` / :362.
- Lifecycle message constants + use sites: `agent/app/services/lifecycle.py:38-52`;
  scanner uses `IDLE_WARNING_DEFAULT`/`IDLE_CLOSE_DEFAULT`/`RESOLUTION_PROMPT_DEFAULT`
  in `lifecycle_scanner.py:154/160/161`; `SURVEY_AI_DEFAULT` at `lifecycle.py:248`,
  `THANKS_DEFAULT` at :265, `SURVEY_AGENT_DEFAULT` at :299; "assign an agent"
  inline literal at :239.
- UI persona editor: `KnowledgeSettings.vue` (Panel 1) — fork patch 0013;
  already has a **Messages** section (welcome/handoff/resolution) and list editors
  for guardrails/response-guidelines.

## Components

### 1. Persona model — new fields on `AssistantConfig`

Add flat fields (defaults `""`), consistent with the existing message fields:

- `language: str = ""` — empty = reply in the customer's language (today);
  set (free-text, e.g. `"Bahasa Melayu"`, `"English"`) = force that language.
- `idle_warning_message: str = ""`
- `idle_close_message: str = ""`
- `resolution_prompt_message: str = ""`
- `survey_ai_message: str = ""`
- `survey_agent_message: str = ""`
- `thanks_message: str = ""`
- `assign_agent_message: str = ""`

No migration (new optional fields default empty on existing stored assistants;
Firestore/InMemory stores read missing keys as the default). `PUT` already
merges an arbitrary `config` dict, so no router change is needed for storage.

### 2. Backend prompt assembly — language injection

- `build_system_prompt`: after the existing assembly, if `config.language` is
  non-empty, append a final `## Language\nAlways respond in {language}.` section.
  An explicit trailing directive overrides the default "reply in the language the
  agent used" line. Empty → unchanged (byte-identical).
- `_apply_persona` (assist sidebar): when `assistant_id` resolves a persona with
  a non-empty `language`, prepend a one-line language directive alongside the
  existing product/guardrails prefix. Empty → unchanged.

This makes the **copilot** (the KB-grounded answer generator that already
produces WhatsApp reply text via `POST /assist/copilot`) honor the language.

### 3. Agent-bot persona wiring — `orchestrator.py`

- New `ProtonConfigClient.get_assistant_persona(inbox_id)` → returns
  `{instructions: str, guardrails: list[str], language: str}` from the resolved
  inbox→assistant config (shares the same cached `/kb/assistants/{id}` fetch as
  `get_assistant_messages`; fail-open → `None`).
- New pure helper `_build_system_prompt(persona) -> str`:
  - base = `persona.instructions` if set, else the current module `SYSTEM_PROMPT`
    constant (unchanged);
  - append `## Guardrails\n- ...` if `persona.guardrails` non-empty;
  - append `Always reply in {persona.language}.` if `persona.language` non-empty.
  - `persona is None` OR all-empty → returns `SYSTEM_PROMPT` **verbatim**.
- The orchestrator resolves the persona (it already resolves `inbox_id` for
  mode/debounce) and passes the composed prompt to `gemini.decide(...)`.
  Redundancy note: when the default base is used AND a language is set, the base
  already contains a same-language line; the appended explicit line wins (last,
  most specific) — benign, and keeps the no-config default byte-identical.

### 4. Lifecycle messages — `lifecycle.py` / `lifecycle_scanner.py`

- Extend `get_assistant_messages(inbox_id)` to also return the 7 new fields
  (keys: `idle_warning`, `idle_close`, `resolution_prompt`, `survey_ai`,
  `survey_agent`, `thanks`, `assign_agent`), mapped from the config fields.
- At each hard-coded use site, resolve `messages.get(key) or DEFAULT` (fetch the
  message dict once where a send happens; the scanner already has the
  conversation's inbox to resolve `inbox_id`). Fail-open → the existing DEFAULT
  constant. The "assign an agent" inline literal gets promoted to a
  `ASSIGN_AGENT_DEFAULT` constant first, then made overridable.

### 5. UI — extend `KnowledgeSettings.vue` (new fork patch)

- Add a **Language** input to the persona form (Basic/persona section), bound to
  `config.language`, with a placeholder explaining empty = auto.
- Append the 7 new fields to the existing **Messages** section
  (idle warning, idle close, resolution prompt, AI survey, agent survey, thanks,
  assign-agent), same input style as welcome/handoff/resolution.
- No new page/route/nav — extends the one existing persona editor (prevents the
  duplication the pgvector phase hit). Delivered as the next fork patch number.

## Data flow

Operator edits persona in `KnowledgeSettings.vue` → `PUT /kb/assistants/{id}`
(config merge) → Firestore. At runtime: the **agent service** reads the inbox's
assistant (cached, fail-open) and applies persona to the bot decision prompt +
lifecycle messages; the **copilot** reads the persona for language / instructions
/ guardrails when generating answers.

## Error handling

Fail-open everywhere: proton unconfigured/unreachable, no assistant assigned, or
empty fields → the current hard-coded defaults. Empty persona → byte-identical to
today. No new failure can block a reply, a lifecycle transition, or a copilot
answer.

## Testing

- **Backend:** `build_system_prompt` language section (set vs empty);
  `_apply_persona` language prefix; new `AssistantConfig` fields default empty +
  survive a store round-trip + `PUT` config merge.
- **Agent:** `_build_system_prompt(persona)` — None → verbatim `SYSTEM_PROMPT`;
  all-empty persona → verbatim; instructions/guardrails/language set → composed
  correctly. `get_assistant_persona` / extended `get_assistant_messages` field
  mapping + fail-open. Lifecycle message resolution at each site (override vs
  default). Regression: default suites stay green (byte-identical off-path).
- **Frontend:** the new patch applies clean on the fork; `vite build` 0 errors;
  the new fields render and round-trip through `PUT`.

## Rollout

Ships with all fields empty = no behavior change. Operators opt in per assistant
by filling fields in the CRM. No flag needed (empty = off). Same
default-off-in-practice posture as prior phases.
