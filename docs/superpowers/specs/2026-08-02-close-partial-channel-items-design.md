# Design: Close out the ⚠️ Partial items in the channel UI testing guide

**Date:** 2026-08-02
**Source:** `docs/analysis/crm-channel-ui-testing-guide.md` — rows WA-2, WA-4, WA-8,
WA-12, IVR-4, IVR-9, plus a user-reported FAQ-upload 404.

## Scope

Five items, three of which need a code fix and two of which are already
code-complete and only need deploying/configuring on the live `proton` tenant.

| # | Item | Kind |
|---|---|---|
| 1 | Language-matching bug (WA-2 / IVR-4) | Code fix (agent + backend) |
| 2 | FAQ upload 404 (Uploads screen) | Code fix (Chatwoot fork patch) + config |
| 3 | WA-4 pending-doc fallback | Small code addition (agent) |
| 4 | Hierarchical categories (WA-8) | Deploy only — already built |
| 5 | WhatsApp media understanding (WA-12/IVR-9) | Deploy only — already built |

## 1. Language-matching bug

**Text path** — `agent/app/services/orchestrator.py:_build_system_prompt` and
`backend/apps/backend/src/chatbot/features/chat/chat_persona.py:compose_chat_agent_instruction`
both build a system prompt for Gemini. The module-level `SYSTEM_PROMPT` /
`base` already says "Always reply in the same language the customer is
using," but:
- if persona `instructions` is set, the base text is replaced outright,
  dropping that line entirely (`orchestrator.py` — the backend variant is
  additive already, doesn't drop it);
- an operator-set persona `language` field appends
  `"Always reply in {language}."`, which reads as a hard override.

**Fix:** in both files, always keep the base "match the customer's language"
instruction present regardless of custom `instructions`, and reword the
persona-language line to express a fallback preference, not an override:
`"Prefer {language} when the customer's language is unclear, but always match
the language the customer writes in."` Update the existing unit tests for
both functions (`test_orchestrator.py`, backend's `chat_persona` test file) to
cover: custom instructions + no language, custom instructions + language set,
default persona.

**Voice path** — `backend/.../features/chat/phone/gemini_live.py:_build_live_config`
only pins `speech_config.language_code` when `settings.gemini_live_language`
is non-empty; empty means Gemini Live auto-detects. No code change here — the
docstring says the tenant env was previously set to a fixed locale for a
demo. Action is operational: check `GEMINI_LIVE_LANGUAGE` on the proton
tenant's live env during the deploy pass (§6) and unset it if pinned.

## 2. FAQ upload 404

Fork patch `deploy/chatwoot-fork/patches/0021-knowledge-uploads-native.patch`:
`onFileChange` already special-cases `err.status === 404` with a friendly
"isn't enabled for this workspace yet" alert, but `load()` (which runs on
every mount — i.e., every time the Uploads tab opens) and `remove()` (delete)
don't, so a 404 there surfaces as a raw `Failed to ... 404: Not Found` alert.

**Fix:** add the same 404 branch to `load()` and `remove()` in the patch, so
all three handlers degrade the same way regardless of tenant config.

**Config:** `KNOWLEDGE_PG_ENABLED` gates whether the backend even mounts the
`/kb/knowledge*` routes (`backend/.../main.py`). `add-tenant.sh` provisions a
per-tenant pgvector `KNOWLEDGE_DATABASE_URL` for every tenant it creates, so
proton likely already has the DB — only the flag may be off. Confirm on the
VM during the deploy pass and flip `KNOWLEDGE_PG_ENABLED=true` if the DB URL
is already present; if it's missing, that's a separate infra step to flag
back to the user rather than provision blind.

## 3. WA-4 — pending-doc fallback

In `orchestrator.py`, when `kb_grounded_replies` is on and `copilot_answer`
returns no grounded answer, the bot currently falls back to Gemini's own
ungrounded draft reply (no explicit "still indexing" messaging). Add a
coarse, non-query-specific check: if grounding returned nothing **and** the
tenant has at least one KB entry in `pending` status, override the reply
text with a fixed message, e.g. *"I'm still processing some reference
material — please try again in a few minutes, or I can connect you with an
agent."* This intentionally does not try to determine whether the *specific*
pending doc would have matched the query (that requires the embedding step
that hasn't happened yet) — it's a best-effort signal, not a precise one.

Needs a lightweight way for the agent to check pending-doc existence for a
tenant — reuse whatever backend endpoint/model already backs the Uploads
screen's status column (read-only list call), gated the same way as the rest
of `kb_grounded_replies` (fail-open: any error checking pending status just
skips the override, falling back to today's behavior).

## 4. Hierarchical categories (WA-8) — deploy only

Already implemented: `agent/app/services/case_taxonomy.py`, Chatwoot custom
attributes `case_category`/`case_subcategory`, and
`chatwoot-config/provision_case_taxonomy.py`. No code change.

**Deploy steps:**
1. Write `CASE_TAXONOMY_JSON` explicitly into the proton tenant's env
   (`/opt/platform/tenants/<proton-env-file>` on the VM) using the existing
   default taxonomy from `backend/.../platform/config.py:352-367` (Sales /
   Aftersales / Apps / Charging / Roadside Assistance / General Enquiry /
   Complaint), unless the user wants it customized first.
2. Run `provision_case_taxonomy.py` once against the live Chatwoot API for
   the proton account (it reads `CASE_TAXONOMY_JSON` from the environment
   directly and errors if unset — the code-level default in `config.py` is
   not enough for the script itself).
3. Redeploy `agent`/`backend` so the runtime picks up the same env value.

## 5. WhatsApp media understanding (WA-12/IVR-9) — deploy only

Already implemented for audio + image attachments (video is genuinely not
handled — out of scope here). No code change.

**Deploy steps:** set `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` in the
proton tenant env, redeploy `agent`/`backend`, then run one real end-to-end
WhatsApp test (voice note + photo) to confirm it actually works live, since
this has not been demoed with the flag on.

## 6. Rollout plan

- VM: instance `crm-ticketing`, zone `asia-southeast2-a`, project
  `lv-playground-genai`.
- SSH in first to confirm the exact proton tenant env filename and current
  values of `GEMINI_LIVE_LANGUAGE`, `KNOWLEDGE_PG_ENABLED`,
  `KNOWLEDGE_DATABASE_URL`, `CASE_TAXONOMY_JSON`, and
  `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` before changing anything.
- Items 1 (runtime), 3, 4, 5 all land in `agent`/`backend` — one light
  redeploy (`docker compose -p proton -f docker-compose.tenant.yml
  --env-file tenants/<proton>.env up -d --build backend agent`) covers all
  four together.
- Item 2 requires rebuilding the Chatwoot custom image off-VM via Cloud
  Build (`gcloud builds submit deploy/chatwoot-fork/ --config
  deploy/chatwoot-fork/cloudbuild.yaml --substitutions
  _REGISTRY=<AR repo>`), pushing to Artifact Registry, then on the VM
  `docker compose ... pull` + `up -d --force-recreate chatwoot-rails
  chatwoot-sidekiq`. Slower and higher blast-radius than the others —
  run it as its own step, verify Chatwoot comes back healthy before moving
  on.
- After each redeploy, spot-check container logs for startup errors before
  declaring it done.

## Out of scope

- True cascading category UI (subcategory options filtering live as the
  category is picked) — the flattened `"Sales: Trade-In"` string model
  already satisfies the functional requirement without a Chatwoot fork
  patch/migration.
- Video understanding for WhatsApp attachments — genuinely unbuilt, would be
  new work.
- Meta Business verification for Facebook/Instagram — unrelated blocker, not
  touched here.
