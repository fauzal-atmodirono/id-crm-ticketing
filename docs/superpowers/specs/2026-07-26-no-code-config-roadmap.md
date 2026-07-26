# No-Code Configuration Roadmap

**Date:** 2026-07-26
**Status:** Backlog / living document
**Author:** scan + brainstorming session

## Purpose

The end users of this platform are **non-technical business operators**, not
engineers. Today almost all configuration lives in environment variables
(deploy-time, immutable) or is hard-coded in source. This document captures the
full set of opportunities — found during a codebase-wide scan on 2026-07-26 — to
move configuration out of code and into a no-code settings surface, so operators
can run and tune the product themselves.

It is a **backlog**, not a spec. The first build (pgvector knowledge store +
no-code ingestion + Knowledge UI — "A+B") has its own design doc. Everything
here is "tackle later," organized by theme and priority.

## Guiding principles

- **Not everything belongs in a UI.** Credentials, infra URLs, DB connection
  strings, GCP project IDs stay in env / infrastructure-as-code. Only
  *behavioral* configuration that an operator would reasonably change should be
  surfaced.
- **Runtime-editable, no restart.** The pattern already exists — see "What
  already exists" below — extend it rather than inventing a new one.
- **Per-tenant.** This is a multi-tenant product; every setting is scoped to a
  tenant.
- **Multilingual.** The product is Indonesian-first ("id-crm") but nearly all
  operator-facing text is hard-coded in English today. Anything surfaced should
  be editable per language.

## What already exists (foundations to build on)

- **Runtime settings store:** `backend/.../features/chat/settings_facade.py` +
  `tenant_settings_store.py` expose `GET/PUT /kb/settings` — currently 8 keys,
  stored per-tenant in Firestore, guarded by an API key, no restart needed.
  **No UI yet.** This is the seam to grow for all settings work.
- **Four working dashboard-app UIs** (static HTML embedded in Chatwoot):
  `chatwoot-faq-admin` (full CRUD + server-side embeddings), `chatwoot-agent-app`,
  `chatwoot-routing-admin`, `chatwoot-my-tasks`. Precedent for no-code UI.
- **Pluggable knowledge layer:** `KnowledgePort` interface + `knowledge_provider`
  config (`mock` / `zendesk` / `vertex_search`) + an `Embedder` abstraction +
  `MergedKnowledgeAdapter`. Makes provider/embedder changes additive.
- **Live FAQ store:** Firestore + Vertex embeddings (`text-embedding-004`) +
  in-memory cosine — already a no-code knowledge path in miniature.

## Backlog

### Theme 1 — Knowledge providers & credentials (subsystems C + D)

Deferred from the first build deliberately (highest complexity + security risk).

> **Deferred from the A+B first build** (see
> `2026-07-26-pgvector-knowledge-base-design.md`): the provider picker (C),
> bring-your-own-GCP Service Account (C), self-hosted embeddings (D), the
> knowledge-source selector config, **URL / website-crawl ingestion**, and
> **migrating the Live FAQ store off Firestore**. The first build adds pgvector
> alongside Live FAQ with paste-text + file-upload ingestion only.

- **C. Pluggable provider picker + bring-your-own-GCP.** Let a tenant choose
  their knowledge backend (pgvector default vs. GCP Vertex Search) and embedding
  model from the UI, entering their own GCP Service Account and Vertex Search
  IDs.
  - **⚠️ Security-critical.** Storing operator-supplied Service Account JSON keys
    means: encryption-at-rest, a real secrets vault, key rotation, and a large
    blast radius on leak. Design this as its own security-reviewed spec. Prefer
    Workload Identity / short-lived credentials / OAuth over long-lived JSON keys
    if at all possible.
  - **Provider switching = re-index.** Moving a tenant's corpus between stores,
    or changing embedding model, changes vector dimensionality and requires a
    full re-embed/re-index. Needs a migration story, not just a dropdown.
- **D. Self-hosted embedding model on the VM.** For tenants who want zero GCP
  dependency. Real infra: model container, RAM/CPU budget on the shared GCE VM,
  dimensional consistency with existing indexes.
- **Configurable knowledge-source selector.** Once pgvector and Live FAQ (and
  optionally Vertex Search) coexist, let the operator choose per-tenant which
  source(s) are active for retrieval (e.g. enable/disable each, or set
  precedence). The first build merges pgvector + Live FAQ unconditionally; this
  selector makes that a setting.

### Theme 2 — Persona, prompts & message templates (high value, low risk)

All hard-coded in English today; prime candidates for per-tenant, per-language
UI editing. This is arguably the highest value/effort ratio in the whole backlog.

- **System prompts / persona:**
  - `agent/app/services/orchestrator.py` — `SYSTEM_PROMPT` (the core agent persona)
  - `agent/app/services/responder.py` — Zammad draft-reply persona
  - `agent/app/services/categorize.py` — classifier prompt
  - `backend/.../features/assist/assistant_runtime.py` — `DEFAULT_COPILOT_PROMPT`
    (already per-assistant overridable — extend this pattern)
  - `backend/.../features/assist/router.py` — `_SUGGEST_SYSTEM`, `_SUMMARIZE_SYSTEM`,
    `_ASK_SYSTEM` (hard-coded, not overridable)
- **Message templates:**
  - `agent/app/services/lifecycle.py` — disclaimer, idle warning, idle close,
    resolution prompt, CSAT surveys, thanks
  - `backend/.../features/chat/router.py` — handoff, survey, CSAT nudge/thanks,
    and the email-channel variants
  - `agent/app/config.py` — `email_autoack_template`

### Theme 3 — Conversation lifecycle & SLA (behavioral tunables)

Mostly already env-driven; needs surfacing + a **timezone/business-hours** setting.

- Lifecycle: `lifecycle_enabled`, idle warn/close timers, confirm grace, survey
  toggle, disclaimer toggle, auto-categorize + `lifecycle_category_labels`.
- SLA: `sla_engine_enabled`, `sla_response_hours`, `sla_resolution_hours`,
  scan interval, per-channel overrides (`sla_ack_minutes_by_channel_json`),
  PIC WhatsApp.
- **Gap — no timezone / business-hours config exists** anywhere, yet the
  lifecycle scanner references "business hours." Must be added before the
  lifecycle feature can be safely operator-controlled.
- **Gap — category taxonomy** is a comma-separated string today; deserves a
  visual add/reorder editor.

### Theme 4 — AI behavior & feature flags

- Mode `suggest` vs `auto` (`agent_mode` / `default_mode`), debounce seconds,
  model selection (`gemini_model`, `assist_gemini_model`, `copilot_gemini_model`),
  `copilot_max_tool_iterations`.
- Feature flags suitable for UI toggles: `zammad_ai_drafts`, `kb_grounded_replies`,
  `routing_enabled`, `sla_engine_enabled`, `metrics_sync_enabled`,
  `email_draft_assist`, `escalation_email_enabled`, `report_enabled`,
  `feature_ai_assist`, `feature_copilot`, `feature_drafts`.
- Not yet configurable but arguably should be: message-history window (hard-coded
  to 20 everywhere), Gemini temperature (not exposed in the agent service),
  retry attempts (`_MAX_ATTEMPTS = 2` in `agent/app/ai/gemini.py`).

### Theme 5 — Escalation & routing

- Escalation labels (`chatwoot_escalation_label`, `chatwoot_complaint_label`),
  complaint reasons, PIC routing map (`pic_map_json`), tier-2 escalation
  (`escalation_tier2_hours`, `escalation_level2_whatsapp`), CC-PIC toggle.
- Task reminders (`tasks_reminder_warning_minutes`, `tasks_reminder_whatsapp_enabled`).

### Theme 6 — Reports & metrics

- Scheduled report email (`report_enabled`, interval, recipients), anomaly
  detection thresholds (`anomaly_zscore_k`, `anomaly_min_baseline`).
- Note: BigQuery/SMTP/metrics **credentials** stay in env — only the
  behavioral toggles/schedules move to UI.

## Stays in env / infra (NOT for a UI)

All API tokens, webhook secrets, Twilio/Zendesk/SMTP credentials, DB URLs,
internal service URLs, GCP project IDs, Firestore/BigQuery dataset config, CORS
allowlists. Roughly half of the ~90 settings inventoried are in this bucket.

## Suggested sequencing (after the A+B knowledge build)

1. **Theme 2 (persona + message templates)** — highest value/effort ratio,
   low risk, directly serves the multilingual multi-tenant goal.
2. **Theme 3 + 4 (lifecycle/SLA + AI behavior/flags)** — extend the settings
   store + build the settings UI; add the missing timezone/business-hours model.
3. **Theme 5 + 6 (escalation/routing + reports)** — round out the settings UI.
4. **Theme 1 C + D (provider picker + BYO-GCP credentials + self-hosted
   embeddings)** — last, and only after a dedicated security review of
   credential storage.

## Reference — key files

- Agent config: `agent/app/config.py`
- Backend config: `backend/apps/backend/src/chatbot/platform/config.py`
- Runtime settings store: `backend/.../features/chat/settings_facade.py`,
  `.../adapters/tenant_settings_store.py`, `.../kb_settings_router.py`
- Per-tenant env template: `deploy/tenants/example.env`
- Dashboard-app UI precedent: `backend/apps/chatwoot-faq-admin/`
- Knowledge layer: `backend/.../features/chat/ports.py`,
  `.../adapters/vertex_search.py`, `.../adapters/live_faq.py`,
  `.../adapters/merged_knowledge.py`
