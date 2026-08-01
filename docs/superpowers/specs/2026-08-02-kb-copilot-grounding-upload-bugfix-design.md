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

### 1. Guaranteed base-KB representation in the merge (`merged_knowledge.py`)

Corrected understanding from reading the actual code (superseding an earlier draft of this design that assumed a plain relevance-sort fix — that would have fought this file's own documented intent): `KbArticle` (the shared result type across all three sources) carries **no score field at all**, and the file's own docstring/comments explicitly document that live-FAQ hits are *meant* to rank first ("freshly-authored knowledge" should surface ahead of the static batch KB). So the bug isn't "wrong ordering" — the ordering is intentional. The bug is that **`pg`+`live` can consume 100% of a small `limit` and leave zero room for `base`**, even when a `base` (Vertex) result would have been exactly what the customer/agent needed — matching the "iMAS 5 specs" symptom exactly (a batch-KB fact with no corresponding live-FAQ entry, silently squeezed out).

`search_kb` currently does:
```python
async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
    pg = await self._pg_articles(query, limit)
    live = await self._live_articles(query, limit)
    base = await self._base.search_kb(query, limit)
    merged: list[KbArticle] = []
    seen: set[str] = set()
    for a in [*pg, *live, *base]:
        key = (a.title or "").strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(a)
    return merged[:limit]
```

Fix: reserve a minimum slot budget for `base` so it's never entirely crowded out, while still letting `pg`/`live` take priority within their budget (preserving the intentional "freshly-authored knowledge first" behavior):

```python
async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
    pg = await self._pg_articles(query, limit)
    live = await self._live_articles(query, limit)
    base = await self._base.search_kb(query, limit)

    # pg/live intentionally rank first (freshly operator-authored knowledge),
    # but must never be allowed to consume the ENTIRE limit when a relevant
    # base-KB result exists — that's the "Copilot can't find things the main
    # agent can" bug (the main agent queries base/Vertex directly, unaffected
    # by this merge). Reserve at least half of `limit` (rounded up, min 1)
    # for base.
    base_reserved = max(1, -(-limit // 2))  # ceil(limit / 2)
    priority_budget = max(0, limit - base_reserved)

    merged: list[KbArticle] = []
    seen: set[str] = set()

    def _add(article: KbArticle) -> bool:
        key = (article.title or "").strip().lower()
        if key and key in seen:
            return False
        if key:
            seen.add(key)
        merged.append(article)
        return True

    priority_added = 0
    for a in [*pg, *live]:
        if priority_added >= priority_budget:
            break
        if _add(a):
            priority_added += 1

    for a in base:
        if len(merged) >= limit:
            break
        _add(a)

    # If base didn't have enough results to fill the reserved slots, top up
    # with any remaining pg/live hits beyond the initial priority budget.
    if len(merged) < limit:
        for a in [*pg, *live]:
            if len(merged) >= limit:
                break
            _add(a)

    return merged[:limit]
```

### 2. Upload button — clear error on 404 (Chatwoot fork patch)

In the patch touching `protonKnowledge.js`'s `uploadKnowledgeFile()` (currently `0021-knowledge-uploads-native.patch`): when the fetch response status is `404`, show a distinct message via `useAlert` — e.g. "Document upload isn't enabled for this workspace yet. Contact your administrator." — instead of falling through to the generic error path. Every other status code (401, 500, network error) keeps today's existing handling.

### 3. Documentation (not code)

Add a short note to `deploy/tenants/example.env` near `KNOWLEDGE_PG_ENABLED`/`KNOWLEDGE_DATABASE_URL` (if not already present) making explicit that the Knowledge-admin document-upload button depends on both being set — so a future operator enabling the Knowledge UI features doesn't miss this dependency.

## Error handling

- The merge fix must remain fail-open exactly like today: if any one source (pg/live/base) errors, the merge already degrades to the remaining sources (existing behavior per `MergedKnowledgeAdapter`, unchanged by this fix — only the combine/truncate step changes).
- The upload-button fix is purely additive UI error handling — no change to any success path.

## Testing

- `test_merged_knowledge.py` (existing, extend): (a) more `pg`+`live` hits than `limit` alone, plus a `base` hit — assert at least one `base` result survives truncation (the exact demo symptom: a correct Vertex result must not be entirely crowded out). (b) `pg`/`live` still win their reserved priority slots when both `pg`/`live` and `base` have results (regression guard — the fix must not accidentally invert the intentional freshly-authored-knowledge-first behavior). (c) dedup by title still works across the new two-pass merge (a title appearing in both `live` and `base` must not appear twice, and must not consume two separate slots). (d) `base` has fewer results than its reserved budget — remaining slots correctly top up from leftover `pg`/`live` hits, not left empty when more relevant content exists.
- Chatwoot fork: a Vitest/component test if this patch area already has one (check the patch's own test coverage conventions first), else a manual smoke-test note in the patch's own description, matching this repo's existing convention for frontend-only fixes with no unit-test harness in place.

## Rollout

`backend/` redeploy for the merge fix. Chatwoot custom image rebuild (Cloud Build, per this repo's deploy conventions) for the upload-button error-message fix. Enabling `KNOWLEDGE_PG_ENABLED` for Proton (making upload actually functional, not just clearly-erroring) is a separate ops action for whoever has VM access — explicitly not part of this rollout.
