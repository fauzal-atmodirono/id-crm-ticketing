"""Tests for `_rank`'s hybrid (semantic + keyword) blend.

`FAQ_KEYWORD_WEIGHT=0.0` (the default) must reproduce today's pure-semantic
`_rank` ordering AND scores exactly -- entry for entry, score for score. That
equivalence is the entire safety argument for shipping hybrid ranking onto a
live tenant without re-validating retrieval (see the package's global
constraints). Tests one and two encode that argument, and their expected
values were captured by running today's (pre-hybrid) `_rank` against this
exact fixture *before* `_rank` grew the `query_text`/`keyword_weight`
parameters:

    $ GOOGLE_API_KEY=test-key uv run python3 -c "..."
    emas7-vague 0.9994309034530722
    battery 0.8035361284794313
    tyre 0.6864426629745594
    emas7-exact 0.6221010845113395

Those four floats (`_BASELINE_ORDER` / `_BASELINE_SCORES` below) are that
captured output, transcribed verbatim -- not recomputed from the new
implementation. If a future change to `_rank` needs different numbers here,
that is itself a sign the weight=0.0 path stopped being byte-identical to
pre-hybrid behaviour, which is exactly the regression these two tests exist
to catch.
"""

from __future__ import annotations

import pytest

from chatbot.features.chat.adapters.live_faq import _rank
from chatbot.features.chat.ports import LiveFaqEntry
from chatbot.platform.config import Settings

# The query embedding all tests rank against.
_QUERY_EMBEDDING = [0.62, 0.52, 0.28]

# Baseline captured from today's (pre-hybrid) `_rank` -- see module docstring.
_BASELINE_ORDER = ["emas7-vague", "battery", "tyre", "emas7-exact"]
_BASELINE_SCORES = {
    "emas7-vague": 0.9994309034530722,
    "battery": 0.8035361284794313,
    "tyre": 0.6864426629745594,
    "emas7-exact": 0.6221010845113395,
}


def _entries() -> list[LiveFaqEntry]:
    """Fresh entries per test -- `_rank` doesn't mutate them, but fixtures
    that hand back fresh objects avoid any cross-test aliasing surprises."""
    return [
        LiveFaqEntry(
            id="battery",
            question="How to reset the battery light?",
            answer="Hold the reset button for 5 seconds.",
            keywords=["battery reset", "battery light"],
            embedding=[0.9, 0.1, 0.05],
        ),
        LiveFaqEntry(
            id="tyre",
            question="What tyre pressure should I use?",
            answer="32 PSI front and rear for most models.",
            keywords=["tyre pressure", "psi"],
            embedding=[0.1, 0.95, 0.02],
        ),
        LiveFaqEntry(
            id="emas7-vague",
            question="General maintenance schedule",
            answer="Service every 10000 km.",
            keywords=[],
            embedding=[0.6, 0.5, 0.3],
        ),
        LiveFaqEntry(
            id="emas7-exact",
            question="e.MAS7 charging port cover replacement",
            answer="Order part 7X-CVR from parts desk.",
            keywords=["e.MAS7", "eMAS7 cover"],
            embedding=[0.05, 0.05, 0.2],
        ),
    ]


def test_weight_zero_reproduces_the_current_ordering_exactly() -> None:
    result = _rank(_entries(), _QUERY_EMBEDDING, limit=10)

    assert [entry.id for entry, _ in result] == _BASELINE_ORDER


def test_weight_zero_reproduces_the_current_scores_exactly() -> None:
    result = _rank(_entries(), _QUERY_EMBEDDING, limit=10)

    for entry, score in result:
        assert score == _BASELINE_SCORES[entry.id]


def test_a_keyword_hit_lifts_an_entry_that_semantic_search_ranked_lower() -> None:
    # "emas7-exact" ranks last on pure semantics (0.622, the lowest baseline
    # score) but its authored keyword is a verbatim substring of the query --
    # exactly the e.MAS7 scenario the feature exists for.
    result = _rank(
        _entries(),
        _QUERY_EMBEDDING,
        limit=10,
        query_text="ada masalah dengan e.MAS7 saya",
        keyword_weight=0.5,
    )

    assert result[0][0].id == "emas7-exact"
    boosted_score = {e.id: s for e, s in result}["emas7-exact"]
    assert boosted_score == pytest.approx(_BASELINE_SCORES["emas7-exact"] + 0.5)


def test_an_entry_with_no_keywords_is_unaffected_by_the_weight() -> None:
    # "emas7-vague" has keywords=[] -- no weight, however large, should move
    # its score even a little, because it never engages the keyword signal.
    for weight in (0.1, 0.5, 1.0, 5.0):
        result = _rank(
            _entries(),
            _QUERY_EMBEDDING,
            limit=10,
            query_text="ada masalah dengan e.MAS7 saya",
            keyword_weight=weight,
        )
        scores = {e.id: s for e, s in result}
        assert scores["emas7-vague"] == _BASELINE_SCORES["emas7-vague"]


def test_keyword_matching_is_case_insensitive() -> None:
    baseline = _BASELINE_SCORES["battery"]

    matched = _rank(
        _entries(),
        _QUERY_EMBEDDING,
        limit=10,
        query_text="BATTERY RESET please, urgent",
        keyword_weight=0.4,
    )
    scores = {e.id: s for e, s in matched}

    assert scores["battery"] == pytest.approx(baseline + 0.4)


def test_an_exact_model_code_like_emas7_is_matched_as_a_keyword() -> None:
    # The keyword is authored as "E.MAS7" (dot, upper-case); the customer
    # types it as lower-case with no punctuation at all. Both must match --
    # this is the whole reason the signal exists: e.MAS7 embeds badly (its
    # baseline semantic score is the lowest of the four entries) but is an
    # exact keyword.
    entries = _entries()
    entries[3] = LiveFaqEntry(
        id="emas7-exact",
        question=entries[3].question,
        answer=entries[3].answer,
        keywords=["E.MAS7"],
        embedding=entries[3].embedding,
    )

    result = _rank(
        entries,
        _QUERY_EMBEDDING,
        limit=10,
        query_text="parts desk tak ada stok emas7 utk saya",
        keyword_weight=0.6,
    )
    scores = {e.id: s for e, s in result}

    assert scores["emas7-exact"] == pytest.approx(_BASELINE_SCORES["emas7-exact"] + 0.6)


def test_the_weight_is_read_from_settings_not_hardcoded() -> None:
    low = Settings(faq_keyword_weight=0.1)
    high = Settings(faq_keyword_weight=0.9)
    query_text = "ada masalah dengan e.MAS7 saya"

    low_result = _rank(
        _entries(),
        _QUERY_EMBEDDING,
        limit=10,
        query_text=query_text,
        keyword_weight=low.faq_keyword_weight,
    )
    high_result = _rank(
        _entries(),
        _QUERY_EMBEDDING,
        limit=10,
        query_text=query_text,
        keyword_weight=high.faq_keyword_weight,
    )

    low_score = {e.id: s for e, s in low_result}["emas7-exact"]
    high_score = {e.id: s for e, s in high_result}["emas7-exact"]
    # If the weight were hardcoded inside `_rank`, two different
    # settings-sourced weights would produce the same score. They must not.
    assert low_score != high_score
    assert high_score > low_score


def test_query_text_none_degrades_to_pure_semantic_ranking() -> None:
    # A caller with an embedding but no raw query string (query_text
    # defaults to None) must get exactly today's behaviour, even with a
    # non-zero weight -- there's nothing to match keywords against.
    result = _rank(_entries(), _QUERY_EMBEDDING, limit=10, keyword_weight=0.7)

    assert [entry.id for entry, _ in result] == _BASELINE_ORDER
    for entry, score in result:
        assert score == _BASELINE_SCORES[entry.id]


# --- The reachability regression the P7 final review caught ---------------
#
# Every test above calls `_rank` directly and passes `query_text` by hand. That
# is why the shipped code could leave `FirestoreLiveFaqStore.search` never
# forwarding a query string at all: `_keyword_hit` was False for every entry at
# every weight, so raising `FAQ_KEYWORD_WEIGHT` did nothing, and no unit test
# noticed. These two drive the store's own `search` instead, so the tunable is
# asserted end to end rather than one layer below where it is consumed.


class _StubStore:
    """`FirestoreLiveFaqStore.search` with Firestore removed.

    Subclasses the real class and stubs only `list_active`, so `search`'s own
    body -- including whether it forwards `query_text` -- is the code under test.
    """

    def __init__(self, weight: float) -> None:
        from chatbot.features.chat.adapters.live_faq import FirestoreLiveFaqStore

        self._impl = FirestoreLiveFaqStore.__new__(FirestoreLiveFaqStore)
        self._impl._keyword_weight = weight  # type: ignore[attr-defined]
        self._impl.list_active = self._list_active  # type: ignore[method-assign]

    async def _list_active(self) -> list[LiveFaqEntry]:
        return _entries()

    async def search(self, *args, **kwargs):
        return await self._impl.search(*args, **kwargs)


async def test_the_store_forwards_query_text_so_the_weight_is_not_inert() -> None:
    store = _StubStore(weight=0.5)

    without = await store.search(_QUERY_EMBEDDING, 10)
    with_text = await store.search(_QUERY_EMBEDDING, 10, query_text="stok e.MAS7 ada?")

    # The exact-code entry ranks last on semantics alone; the authored keyword
    # must lift it once the raw query reaches the store.
    assert [e.id for e, _ in without] == _BASELINE_ORDER
    assert [e.id for e, _ in with_text] != _BASELINE_ORDER
    assert {e.id: s for e, s in with_text}["emas7-exact"] > _BASELINE_SCORES["emas7-exact"]


async def test_the_stores_default_weight_of_zero_still_reproduces_the_baseline() -> None:
    # Flag-off safety: a store built with the default 0.0 weight must be
    # byte-identical to pre-P7 even when a query string IS forwarded.
    store = _StubStore(weight=Settings().faq_keyword_weight)

    result = await store.search(_QUERY_EMBEDDING, 10, query_text="stok e.MAS7 ada?")

    assert [e.id for e, _ in result] == _BASELINE_ORDER
    for entry, score in result:
        assert score == _BASELINE_SCORES[entry.id]
