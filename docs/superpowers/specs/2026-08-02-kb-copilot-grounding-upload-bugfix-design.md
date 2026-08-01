# Ask Copilot grounding + document-upload bugfixes

**Date:** 2026-08-02
**Status:** Approved (autonomous — brainstormed and self-approved during an unattended run; no user available to review interactively, per standing authorization to proceed without interruption. Decisions and rationale documented inline for later human review.)
**Scope:** Roadmap item #5 in `docs/roadmap/2026-08-01-next-development-roadmap.md`, narrowed to its explicitly bug-flagged parts ("Bug, not backlog — fix Ask Copilot's grounding lag... triage before adding new KB features"). The two feature builds in that roadmap item — bulk FAQ CSV upload, auto-classify-document-into-FAQs — are **out of scope**, left for a future session (see Non-goals).

## Problem

Two bugs surfaced in the client demo:

1. **Ask Copilot's grounding lags the main WhatsApp agent's.** Copilot couldn't retrieve "iMAS 5" spec info the main agent could, despite both ultimately querying the same base Vertex AI Search data store. Root-caused to a structural defect unique to Copilot's code path: `MergedKnowledgeAdapter.search_kb` (`backend/apps/backend/src/chatbot/features/chat/adapters/merged_knowledge.py`) concatenates results by **source priority** (pgvector → Firestore live-FAQ → base Vertex) and hard-truncates to `limit`, instead of ranking by relevance across all three sources. With a small tool-call `limit` (3, per `copilot_tools.py`), a handful of loosely-matching live-FAQ hits can crowd a genuinely relevant Vertex result out of the response window entirely. The main agent bypasses this merge layer (queries Vertex directly), so it's immune.
2. **The Knowledge admin UI's document-upload button is non-functional.** Root cause: the frontend and backend are both correctly implemented and wired to each other (`KnowledgeUploads.vue` → `POST /kb/knowledge/file`, matching URL/auth/payload) — but `backend/`'s `/kb/knowledge/*` router is only mounted when `KNOWLEDGE_PG_ENABLED=true` and `KNOWLEDGE_DATABASE_URL` are set (`main.py`), and the deployed Proton tenant (`deploy/tenants/default.env`) sets neither. So the endpoint 404s before the button's own code or auth logic ever runs — a pure deployment-config gap, not a frontend or backend logic bug.

## Decision (autonomous, documented for review)

- **Bug 1 fix is scoped to the provably-defective merge-ordering logic only.** A second contributing factor was found during research — Copilot's tool declarations drop `search_knowledge_base` entirely when an assistant's `feature_faq` config is `False` (`assistant_runtime.py`), while the main agent has no equivalent gate. This toggle **defaults to `True`** and is an intentional, documented, operator-configurable persona setting (per this repo's "Operator-configurable persona & knowledge" architecture) — not obviously a bug. Confirming whether it actually fired for the demo's specific inbox/assistant would require inspecting live Firestore/assistant config this session has no access to. **Decision: do not touch the `feature_faq` gate** — changing intentional, documented operator-facing behavior without being able to verify it's actually the cause would be a worse mistake than leaving a possible-but-unconfirmed second factor unaddressed. It's recorded as an open follow-up (see Non-goals) for whoever has production access to check next.
- **The relevance-ranking fix, by contrast, is a provable defect independent of any specific demo's config** — "search top-k most relevant" logic that returns results by source-priority-then-truncate instead of by actual relevance is wrong regardless of what caused this particular symptom, and fixing it can only help, never regress, Copilot's grounding quality. This is the actual code fix in this plan.
- **Bug 2 gets two fixes, one code and one documented-but-not-executed:**
  1. **Code fix**: `uploadKnowledgeFile()`'s error handling (in the Chatwoot fork patch) currently surfaces a raw 404 the same way it would surface any other failure — confusing for an operator who has no way to know "the pgvector KB feature isn't enabled for your tenant" from a bare 404. Detect a 404 specifically on this call and show a clear, actionable message.
  2. **Not a code fix — an ops/deployment prerequisite this repo's code can't satisfy**: the upload feature is genuinely unreachable until `KNOWLEDGE_PG_ENABLED`/`KNOWLEDGE_DATABASE_URL` (and a working Gemini embedder) are actually configured for the Proton tenant on the live VM. This session has no access to that VM (confirmed earlier — see the session's blocked "check live Proton VM tenant config" task). Documented as an explicit operational next-step, not something this plan claims to fix.
- **Both bugs share a root observation worth recording**: even after fixing the upload button's error message, uploaded documents still only reach Copilot's grounding once (a) the pgvector feature is actually enabled in production AND (b) bug 1's relevance-ranking fix is in place — otherwise newly-uploaded content remains vulnerable to being crowded out by the same merge-ordering defect. The two bugs compound; both are addressed here (one fully in code, one partially — code + a flagged ops dependency).

## Non-goals

- Bulk FAQ CSV upload — a new feature, not a bug; deferred to a future roadmap pass.
- Auto-classify an uploaded document into individual FAQ entries — same, deferred.
- Changing the `feature_faq` operator toggle's behavior — explicitly not touched, per the Decision section above; flagged as an open follow-up requiring production-config access this session doesn't have.
- Actually enabling `KNOWLEDGE_PG_ENABLED` on the live Proton tenant — an ops/deployment action outside this repo's code, documented but not executed.

## Design

### 1. Relevance-ranked merge (`merged_knowledge.py`)

`MergedKnowledgeAdapter.search_kb` currently does (approximately):

```python
merged = [*pg_results, *live_results, *base_results]
return merged[:limit]
```

Change to rank by each result's own relevance/similarity score (already computed by each underlying source — pgvector's cosine similarity, live-FAQ's `_rank` score per `live_faq.py`, Vertex's native relevance ordering) before truncating:

```python
merged = [*pg_results, *live_results, *base_results]
merged.sort(key=lambda r: r.score, reverse=True)  # exact field name TBD by implementer — read the actual result dataclass first
return merged[:limit]
```

If the three source result types don't already carry a directly-comparable score field (e.g. Vertex's relevance metric and pgvector's cosine similarity may be on different scales), the fallback is a **stable interleave** (round-robin across the three sources, one from each in turn, until `limit` is reached) rather than the current strict-priority-then-truncate — this at minimum guarantees no single source can starve out the other two entirely, even without perfectly comparable scores. The implementer should read the actual result dataclasses from all three sources (pgvector adapter, `live_faq.py`, Vertex adapter) before deciding which of these two approaches (true relevance sort vs. guaranteed interleave) is achievable — this is a legitimate implementation-time judgment call the plan should surface, not force blindly.

### 2. Upload button — clear error on 404 (Chatwoot fork patch)

In the patch touching `protonKnowledge.js`'s `uploadKnowledgeFile()` (currently `0021-knowledge-uploads-native.patch`): when the fetch response status is `404`, show a distinct message via `useAlert` — e.g. "Document upload isn't enabled for this workspace yet. Contact your administrator." — instead of falling through to the generic error path. Every other status code (401, 500, network error) keeps today's existing handling.

### 3. Documentation (not code)

Add a short note to `deploy/tenants/example.env` near `KNOWLEDGE_PG_ENABLED`/`KNOWLEDGE_DATABASE_URL` (if not already present) making explicit that the Knowledge-admin document-upload button depends on both being set — so a future operator enabling the Knowledge UI features doesn't miss this dependency.

## Error handling

- The merge fix must remain fail-open exactly like today: if any one source (pg/live/base) errors, the merge already degrades to the remaining sources (existing behavior per `MergedKnowledgeAdapter`, unchanged by this fix — only the combine/truncate step changes).
- The upload-button fix is purely additive UI error handling — no change to any success path.

## Testing

- `test_merged_knowledge.py` (existing, extend): a case where a lower-relevance live-FAQ result and a higher-relevance base-Vertex result are both returned by their sources, with a `limit` smaller than the combined count — assert the higher-relevance result survives truncation (regression guard for the exact demo symptom: a correct Vertex result must not be crowded out by weaker FAQ matches). Match whichever of the two implementation approaches (true sort vs. interleave) Design item 1 lands on.
- Chatwoot fork: a Vitest/component test if this patch area already has one (check the patch's own test coverage conventions first), else a manual smoke-test note in the patch's own description, matching this repo's existing convention for frontend-only fixes with no unit-test harness in place.

## Rollout

`backend/` redeploy for the merge fix. Chatwoot custom image rebuild (Cloud Build, per this repo's deploy conventions) for the upload-button error-message fix. Enabling `KNOWLEDGE_PG_ENABLED` for Proton (making upload actually functional, not just clearly-erroring) is a separate ops action for whoever has VM access — explicitly not part of this rollout.
