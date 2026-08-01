# KB Copilot Grounding + Upload-Button Bugfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Ask Copilot grounding-lag bug by guaranteeing the base (Vertex) knowledge source can't be entirely crowded out of a small result-limit merge by pgvector/live-FAQ hits.

**Architecture:** `MergedKnowledgeAdapter.search_kb` reserves a minimum slot budget for `base` results within the requested `limit`, while still letting `pg`/`live` take priority within their own budget — preserving the intentional "freshly-authored knowledge first" design instead of replacing it with a plain relevance sort (which isn't achievable anyway, since `KbArticle` carries no comparable score field across sources).

**Tech Stack:** Python, pytest, existing `MergedKnowledgeAdapter` test conventions.

## Global Constraints

- The upload-button fix (Chatwoot fork patch 0021) and its env-doc note are **already complete and committed** (commit `1e42c25` on this branch) — verified via the fork's own documented patch-regeneration workflow (clone upstream, apply patches, edit, re-diff, confirm 0022-0024 still apply after the regenerated 0021). Nothing further needed for that half of this project.
- The `pg`/`live`-ranks-first behavior is intentional and must be preserved — this fix guarantees `base` gets *some* representation, it does not invert the priority order.
- No change to any source's own search/scoring logic (pgvector, live-FAQ, Vertex) — only how `MergedKnowledgeAdapter.search_kb` combines their results.
- Explicitly not touched, per the spec's Decision section: the `feature_faq` tool-gating toggle in `assistant_runtime.py` — a possible second contributing factor to the original bug, left alone since confirming it actually fired requires production config access this session doesn't have.

---

### Task 1: Guaranteed base-KB representation in the merge

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/merged_knowledge.py` (`search_kb`, lines 53-66)
- Test: `backend/apps/backend/src/chatbot/features/chat/adapters/test_merged_knowledge.py` (existing — read it first to match its real fixture/mock conventions before adding tests)

**Interfaces:**
- Consumes: nothing new — `_pg_articles`, `_live_articles`, `self._base.search_kb` (all existing, unchanged).
- Produces: `search_kb`'s external behavior is unchanged (same signature, same return type) — only the internal combine/truncate logic changes.

- [ ] **Step 1: Read the existing test file first**

Run: `grep -n "def test_\|class Test\|MergedKnowledgeAdapter(" backend/apps/backend/src/chatbot/features/chat/adapters/test_merged_knowledge.py`

Understand how test fixtures currently construct fake `pg`/`live`/`base` sources and `KbArticle` instances before writing new tests — match that convention exactly.

- [ ] **Step 2: Write the failing tests**

```python
# additions to test_merged_knowledge.py — adjust fixture/helper names to match
# the real file's existing conventions (read Step 1's output first)

@pytest.mark.asyncio
async def test_base_result_survives_when_priority_sources_would_fill_limit():
    """The exact demo symptom: several loosely-matching live-FAQ hits must not
    crowd out a genuinely relevant base (Vertex) result."""
    pg_port = _FakePort(articles=[])
    live_store, embedder = _fake_live_faq(
        hits=[
            (_FakeFaqEntry(question="Q1", answer="A1"), 0.6),
            (_FakeFaqEntry(question="Q2", answer="A2"), 0.6),
        ]
    )
    base = _FakePort(articles=[KbArticle(title="iMAS 5 specs", content="...", source_type="vertex")])
    adapter = MergedKnowledgeAdapter(base, live_store, embedder, pg_port=None)

    results = await adapter.search_kb("imas 5 specs", limit=2)

    titles = [r.title for r in results]
    assert "iMAS 5 specs" in titles  # base result must survive truncation


@pytest.mark.asyncio
async def test_priority_sources_still_win_their_reserved_slots():
    """pg/live ranking first (within their budget) must still hold — this fix
    reserves room for base, it does not invert the intentional priority."""
    pg_port = _FakePort(articles=[KbArticle(title="PG hit", content="...", source_type="pgvector")])
    live_store, embedder = _fake_live_faq(hits=[])
    base = _FakePort(articles=[
        KbArticle(title="Base 1", content="...", source_type="vertex"),
        KbArticle(title="Base 2", content="...", source_type="vertex"),
    ])
    adapter = MergedKnowledgeAdapter(base, live_store, embedder, pg_port=pg_port)

    results = await adapter.search_kb("query", limit=2)

    assert results[0].title == "PG hit"  # priority source still first


@pytest.mark.asyncio
async def test_dedup_by_title_across_two_pass_merge():
    """A title appearing in both a priority source and base must not appear
    twice or consume two slots."""
    pg_port = _FakePort(articles=[KbArticle(title="Shared Title", content="pg version", source_type="pgvector")])
    live_store, embedder = _fake_live_faq(hits=[])
    base = _FakePort(articles=[
        KbArticle(title="Shared Title", content="base version", source_type="vertex"),
        KbArticle(title="Unique Base", content="...", source_type="vertex"),
    ])
    adapter = MergedKnowledgeAdapter(base, live_store, embedder, pg_port=pg_port)

    results = await adapter.search_kb("query", limit=3)

    titles = [r.title for r in results]
    assert titles.count("Shared Title") == 1
    assert "Unique Base" in titles


@pytest.mark.asyncio
async def test_leftover_priority_hits_top_up_when_base_is_short():
    """If base has fewer results than its reserved budget, remaining slots
    come from leftover pg/live hits rather than being left empty."""
    pg_port = _FakePort(articles=[
        KbArticle(title="PG 1", content="...", source_type="pgvector"),
        KbArticle(title="PG 2", content="...", source_type="pgvector"),
        KbArticle(title="PG 3", content="...", source_type="pgvector"),
    ])
    live_store, embedder = _fake_live_faq(hits=[])
    base = _FakePort(articles=[])  # base has nothing
    adapter = MergedKnowledgeAdapter(base, live_store, embedder, pg_port=pg_port)

    results = await adapter.search_kb("query", limit=3)

    assert len(results) == 3  # all 3 slots filled from pg, not left short
```

Replace `_FakePort`, `_fake_live_faq`, `_FakeFaqEntry` with whatever this test file's real fixture helpers are actually called — these names are illustrative, matching the plan's general convention of showing intent over guessing exact existing test infrastructure.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/adapters/test_merged_knowledge.py -v`
Expected: FAIL on `test_base_result_survives_when_priority_sources_would_fill_limit` (today's code would return only the 2 live-FAQ hits, no base result, since `limit=2` and `pg`+`live` already total 2).

- [ ] **Step 4: Implement the fix**

Replace `search_kb` (lines 53-66) with:

```python
    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        pg = await self._pg_articles(query, limit)
        live = await self._live_articles(query, limit)
        base = await self._base.search_kb(query, limit)

        # pg/live intentionally rank first (freshly operator-authored
        # knowledge), but must never consume the ENTIRE limit when a
        # relevant base-KB result exists — that's the "Copilot can't find
        # things the main agent can" bug (the main agent queries base/Vertex
        # directly, unaffected by this merge). Reserve at least half of
        # `limit` (rounded up, min 1) for base.
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

        # If base didn't have enough results to fill its reserved slots, top
        # up with any remaining pg/live hits beyond the initial budget.
        if len(merged) < limit:
            for a in [*pg, *live]:
                if len(merged) >= limit:
                    break
                _add(a)

        return merged[:limit]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend/apps/backend && pytest src/chatbot/features/chat/adapters/test_merged_knowledge.py -v`
Expected: PASS (all tests, including pre-existing ones — check none of them asserted the OLD priority-only-then-truncate behavior in a way that now contradicts this fix; update any that do, since they'd be testing the exact bug this fix removes).

- [ ] **Step 6: Run broader regression check**

Run: `cd backend/apps/backend && export GEMINI_API_KEY=test-dummy-key GOOGLE_API_KEY=test-dummy-key && pytest src/chatbot/features/assist/ src/chatbot/features/chat/adapters/ -v`
Expected: PASS — Copilot's own tests (`features/assist/`) also consume this adapter indirectly; confirm nothing there assumed the old behavior.

- [ ] **Step 7: Commit**

```bash
cd backend/apps/backend
git add src/chatbot/features/chat/adapters/merged_knowledge.py src/chatbot/features/chat/adapters/test_merged_knowledge.py
git commit -m "fix(kb): reserve base-KB representation so it can't be crowded out by pg/live hits"
```

---

## Plan Self-Review Notes

- **Spec coverage:** Design item 1 (the merge fix) is fully covered by Task 1. Design items 2-3 (upload button + env doc) were already completed and committed directly outside the normal SDD task loop, since they required the Chatwoot fork's specialized patch-regeneration workflow (clone upstream, apply patches in sequence, re-diff) rather than a standard Python TDD cycle — verified via the fork's own documented process (patch applies cleanly, downstream patches 0022-0024 still apply after it) in lieu of a separate task-scoped review.
- **`feature_faq` gating explicitly not touched**, per the spec's Decision section — flagged as an open follow-up requiring production config access, not silently dropped.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-02-kb-copilot-grounding-upload-bugfix.md`. Proceeding with Subagent-Driven execution for the single remaining task (standing choice for this autonomous run).
