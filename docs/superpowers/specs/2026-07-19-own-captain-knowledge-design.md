# Our Own "Captain" — Knowledge Base + Authoring + Source Citations (design)

**Date:** 2026-07-19 · **Status:** design for review · **Companion docs:**
`2026-07-18-crm-enhancement-program-spec.md` (program roadmap; this extends Phase 0/1),
`2026-07-19-ask-copilot-full-parity.md` (the Copilot we already built).

## Goal

We are replacing Chatwoot's enterprise **Captain** with our own, self-hosted equivalent.
Captain has three pillars: **Copilot** (agent-facing assist), **Assistant** (customer-facing
bot), and a **Knowledge base** (Documents + FAQs, agent/admin-managed). We have already built
the **Copilot** (Ask Copilot + Suggest + Summarize). This spec covers the **Knowledge base**
pillar's near-term slice:

1. **Source citations** — when Suggest/Copilot ground an answer on knowledge, the agent SEES
   and can open the article(s) that grounded it ("ground it as their source").
2. **Knowledge authoring from the CRM** — admins/ops add and edit knowledge entries from
   inside Chatwoot, and those entries immediately enrich grounding.

The customer-facing **Assistant bot** and advanced Captain extras (Memories, Custom Tools,
auto-FAQ-from-conversations) are explicitly **out of scope** here (later specs).

## What already exists (reuse — do not rebuild)

Backend (`proton-conversational-ai/apps/backend`):
- **`live_faq` store** (`adapters/live_faq.py`): CRM-editable FAQ entries
  (`question`, `answer`, `keywords`, `tags`) embedded with a Vertex text-embedding model;
  cosine-similarity search. `LiveFaqPort`.
- **`faq_admin_router.py`**: full CRUD already — `POST /kb/faq`, `PUT /kb/faq/{id}`,
  `DELETE /kb/faq/{id}`, `GET /kb/faq`; guarded by `settings.faq_admin_api_key`.
- **`kb_suggest_router.py`**: `GET /kb/suggest?q=` merges live-FAQ + Vertex Search into one
  `suggestions: [{title, snippet, url, source_type}]` list.
- **`/assist/suggest`** already returns `{draft, sources: [{title, snippet, url}]}`.
- **Vertex AI Search** (`VertexAISearchAdapter`) = the bulk/static KB (crawled site/docs).

Frontend (`id-crm-ticketing` fork): the Phase-0 nav-app host pattern (Taxonomy Manager is a
working example of an in-Chatwoot iframe app), `useProtonConfig()` (`backendUrl`, `backendKey`),
and the Copilot panel (`ProtonCopilotPanel.vue`).

**Implication:** the authoring *backend* is essentially built; the net-new is a **UI** over it
plus **surfacing citations** in two places.

## Architecture decision — where the backend lives

The user's intent: "our own Captain backend, in our repo, configurable, so we don't rebuild it
per customer."

**Decision for this build:** *reuse the existing backend knowledge stack as-is* (live-FAQ +
Vertex + embeddings + faq_admin CRUD). Rebuilding that in `id-crm-ticketing/agent/` would
discard deeply-wired, tested infrastructure for no near-term gain. The net-new UI IS
first-party (a nav-app in our repo).

**"Don't rebuild per customer" is already satisfied by config, not repo location:** it is ONE
backend codebase deployed once per tenant with that tenant's config (`PROTON_BACKEND_URL/KEY`,
KB data store, embedding model). No customer needs a code rebuild either way.

**Ownership/relocation is a separate productization track (not this spec):** physically moving
the AI backend into a first-party, de-branded product service is real work best done when a
real second tenant lands (program-spec open-dependency #4). This spec is built so that track is
unaffected — the frontend talks to the backend only through the `PROTON_BACKEND_URL` contract,
so relocating the backend later changes a URL, not the UI.

## Scope

**In (v1):**
- A **Knowledge Manager** nav-app (first-party) for admins/ops to list/create/edit/delete
  knowledge entries (CRUD over the live-FAQ store).
- **Source citations** surfaced in the ✨ Suggest result and the Ask Copilot panel.
- Config + auth to connect the nav-app to the backend securely, per tenant.

**Out (later specs):** customer-facing Assistant bot; auto-suggested FAQs from conversations;
Captain Memories; Custom Tools; importing/crawling new Documents into Vertex Search from the UI
(v1 authoring targets the live-FAQ layer only); backend relocation/de-branding.

## Components

### C1 — Knowledge authoring API (backend, minimal additions)

Reuse `faq_admin_router` CRUD. Two small changes:
- **Auth alignment:** the nav-app authenticates with the same `x-api-key` the frontend already
  carries (`PROTON_BACKEND_KEY`). Decision: set `faq_admin_api_key = proton_backend_key` per
  tenant (config), OR add `proton_backend_key` as an accepted key on the `/kb/faq` routes. (Open
  decision D2.)
- **Optional `source_url` field** on a FAQ entry so a citation can deep-link to a canonical
  article if the entry mirrors one (nullable; ignored if absent).
- **CORS:** `/kb/*` must allow the Chatwoot origin (reuse the existing `assist_cors_origins`
  mechanism).

### C2 — Knowledge Manager nav-app (frontend, first-party)

A static in-Chatwoot app on the Phase-0 nav-host route (same shape as Taxonomy Manager):
- Lists entries (`GET /kb/faq`), with search/filter by keyword/tag.
- Create/edit form (`question`, `answer`, `keywords`, `tags`, optional `source_url`) →
  `POST/PUT /kb/faq`; delete → `DELETE /kb/faq/{id}`.
- On save, the backend re-embeds the entry, so it is **immediately** usable by grounding.
- Reads `backendUrl`/`backendKey` from `window.__PROTON_CONFIG__`.
- Gated behind a `knowledge` (or reuse `nav_menu`) Proton feature flag; appears as a left-nav
  item ("Knowledge").

### C3 — Source citations (backend + frontend)

- **Suggest:** `/assist/suggest` already returns `sources`. Frontend change only: render a
  compact **"Sources"** list under the inserted draft (title → opens `url` in a new tab; if no
  `url`, show title only).
- **Copilot:** the `search_knowledge_base` tool already fetches articles, but the endpoint only
  returns `{answer, tool_calls}`. Backend change: also return `sources: [{title, snippet, url}]`
  aggregated from KB tool results this turn. Frontend: render "Sources" under the assistant
  message in `ProtonCopilotPanel.vue` (dedup by title/url).

## Data flow

```
Admin authors entry ──▶ Knowledge Manager nav-app ──POST /kb/faq──▶ live_faq store
                                                        (Vertex embed on write)
Agent asks / drafts ──▶ /assist/suggest | /assist/copilot ──▶ grounding reads
   live_faq (cosine) + Vertex Search  ──▶ answer + sources[]  ──▶ agent sees cited articles
```

## Config (per tenant)

- Existing: `PROTON_BACKEND_URL`, `PROTON_BACKEND_KEY`, `PROTON_FEATURES` (+ `knowledge`).
- Backend: `faq_admin_api_key` aligned to `proton_backend_key` (D2); `assist_cors_origins`
  includes the tenant's Chatwoot origin (already used by /assist).
- "Configurable in Chatwoot settings": v1 = the tenant env + the in-Chatwoot Knowledge Manager
  app (that IS the settings surface for knowledge). No Chatwoot-core settings-page patch in v1.

## Testing

- Backend: unit tests for any auth/`source_url`/copilot-`sources` additions (pytest + respx,
  mock embedder), matching the existing assist/faq test style.
- Frontend nav-app: a local smoke (create → appears in list → shows up as a Suggest/Copilot
  source) against the local runner, per the fork's local-test loop.
- `default`-tenant live smoke before replicating.

## Multi-tenant / rollout

Additive: the nav-app ships in the shared custom image behind the `knowledge` flag; each tenant
enables it via `PROTON_FEATURES` and its own backend. No schema migration (live-FAQ is
Firestore/in-memory). Rollback = drop the flag.

## Open decisions (for the review)

- **D1 — Who authors:** admins/ops only (v1, recommended) vs. also agents. Affects the nav-app's
  visibility gating.
- **D2 — Auth key:** set `faq_admin_api_key = proton_backend_key` (simplest) vs. add
  `proton_backend_key` as an accepted key on `/kb/faq` vs. a separate knowledge key.
- **D3 — Citations link target:** live-FAQ entries have no public URL by default — cite by
  **title only** (v1) vs. require/author a `source_url`. Vertex Search hits do have URLs.
