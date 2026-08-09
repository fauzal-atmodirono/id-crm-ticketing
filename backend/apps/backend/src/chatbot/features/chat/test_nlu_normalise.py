"""P7 task 6 -- the SMS-register-Malay query normaliser.

Eight tests, exact names from the task brief. Tests six and seven are the
package's global constraint ("never normalise the text the model or the
agent sees") and are asserted against **actual call payloads** captured from
real, unmodified production code (`service.py`'s `OrchestratorService` for
the model call, `kb_suggest_router.py`'s real `/kb/suggest` handler for the
agent-facing surface) -- not by reading the source and reasoning about it.

Test eight cannot be the brief's literal acceptance gate ("ship only if the
corpus pass rate improves") because there is no real Gemini/Vertex credential
in this sandbox -- see `test_malay_sms_corpus.py`'s module docstring and
`nlu_normalise.py`'s "Ship gate" section. What it *can* honestly be, and is,
is a test that the with/without comparison **mechanism** works end to end
against the same stubbed harness task 5 built, reusing its `CorpusReport`/
`CaseResult`/disclaimer machinery so the stub-vs-real distinction is
structural (a `mode` field), not a comment someone can miss. It reports both
rates and asserts nothing about which is larger.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters import merged_knowledge as merged_knowledge_module
from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter
from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.kb_suggest_router import build_kb_suggest_router
from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.nlu_normalise import (
    NORMALISE_RETRIEVAL_QUERY_ENABLED,
    normalise,
)
from chatbot.features.chat.ports import LiveFaqEntry
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.test_malay_sms_corpus import (
    CaseResult,
    CorpusReport,
    _build_faq_store,
    _cases,
    _stub_disclaimer,
    _StubTopicEmbedder,
)
from chatbot.platform.config import get_settings

# --------------------------------------------------------------------------
# Tests one-four: the abbreviation table + repeated-character collapse
# --------------------------------------------------------------------------


def test_repeated_characters_are_collapsed() -> None:
    assert normalise("lamaaaa") == "lama"
    assert normalise("brooo servisssss lamaaaa sgttt") == "bro servis lama sangat"


def test_known_abbreviations_are_expanded() -> None:
    assert normalise("nk book service utk keta sy esok blh x") == (
        "nak book service untuk kereta saya esok boleh tak"
    )


def test_brp_expands_to_berapa_and_nk_to_nak() -> None:
    assert normalise("brp lama siap? nk service") == "berapa lama siap? nak service"


def test_an_unknown_token_is_left_untouched() -> None:
    # Neither "e.mas7" (a real product code, not an abbreviation) nor
    # "sekarang" (already the full word) is in the abbreviation table, and
    # neither has a 3+ repeated-letter run -- both must survive verbatim.
    # This is also the documented trap: a naive normaliser could otherwise
    # turn a standalone "x" inside "x50"/"e.mas7" into "tak" and break exact
    # model-code matching, which whole-word-boundary matching prevents.
    assert normalise("service e.mas7 sekarang") == "service e.mas7 sekarang"
    assert normalise("brp harga windscreen wiper utk x50") == (
        "berapa harga windscreen wiper untuk x50"
    )


# --------------------------------------------------------------------------
# Test five: applied to the retrieval query only, within the single
# production call site (MergedKnowledgeAdapter.search_kb).
# --------------------------------------------------------------------------


class _CapturingEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1, 0.2]


class _CapturingSearchKb:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        self.calls.append(query)
        return []


class _EmptyLiveStore:
    async def search(
        self,
        query_embedding: list[float],
        limit: int,
        *,
        query_text: str | None = None,
    ) -> list[tuple[LiveFaqEntry, float]]:
        # The embedding may be normalised; `query_text` must stay RAW, because
        # normalising an exact product code is how it stops matching.
        self.last_query_text = query_text
        return []


@pytest.mark.asyncio
async def test_normalisation_is_applied_to_the_retrieval_query_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "brp lama siap? nk service"
    normalised_text = normalise(raw_text)
    assert normalised_text != raw_text  # sanity: the fixture actually exercises expansion

    pg = _CapturingSearchKb()
    base = _CapturingSearchKb()
    embedder = _CapturingEmbedder()
    adapter = MergedKnowledgeAdapter(base, _EmptyLiveStore(), embedder, pg_port=pg)

    # Flag OFF (the shipped default): every branch, including the two
    # embedding-driven ones, must see the untouched raw query.
    assert NORMALISE_RETRIEVAL_QUERY_ENABLED is False
    await adapter.search_kb(raw_text, limit=2)
    assert pg.calls == [raw_text]
    assert embedder.calls == [raw_text]
    assert base.calls == [raw_text]

    # Flag ON: only the retrieval-facing branches (pg + live/embed) get the
    # normalised copy. `base` -- out of this task's scope, and potentially
    # keyword/exact-match sensitive -- always keeps the raw query. This is
    # the "retrieval query only" property from the production-wiring side.
    monkeypatch.setattr(merged_knowledge_module, "NORMALISE_RETRIEVAL_QUERY_ENABLED", True)
    await adapter.search_kb(raw_text, limit=2)
    assert pg.calls == [raw_text, normalised_text]
    assert embedder.calls == [raw_text, normalised_text]
    assert base.calls == [raw_text, raw_text]


# --------------------------------------------------------------------------
# Test six: the text passed to the model is never normalised. Asserted
# against the actual `types.Content` handed to the ADK Runner by real,
# unmodified `service.py` code -- the same monkeypatch-`_run_support_agent`
# capture technique `test_service.py` already uses for this exact purpose.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_text_passed_to_the_model_is_never_normalised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Also flip the retrieval flag on, so if normalisation ever leaked into
    # the model-turn path this test would catch it regardless of the flag's
    # shipped-off default.
    monkeypatch.setattr(merged_knowledge_module, "NORMALISE_RETRIEVAL_QUERY_ENABLED", True)

    settings = get_settings()
    svc = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
    )

    captured: dict[str, Any] = {}

    async def fake_run_support_agent(
        _session_id: str, new_message: Any
    ) -> tuple[str, list[str], bool]:
        captured["parts"] = new_message.parts
        return "ok", [], False

    svc._run_support_agent = fake_run_support_agent  # type: ignore[method-assign]

    raw_text = "brp lama siap? nk service, kete sy blh x"
    await svc.handle_turn(session_id="nlu-normalise-model-payload", text=raw_text)

    sent_text = captured["parts"][0].text
    assert sent_text == raw_text  # bit-for-bit -- not `normalise(raw_text)`
    assert "brp" in sent_text and "nk" in sent_text  # the register cues survive
    assert "berapa" not in sent_text and "nak" not in sent_text


# --------------------------------------------------------------------------
# Test seven: the text shown to the agent is never normalised. Asserted
# against the actual JSON payload of the real, unmodified `/kb/suggest`
# handler -- the "real-time FAQ suggestions for agents" endpoint (its own
# docstring's words) -- with a `MergedKnowledgeAdapter` wired in as the
# `knowledge_port` so the SAME normalisation this task ships is actually
# exercised end to end within the same request.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_text_shown_to_the_agent_is_never_normalised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(merged_knowledge_module, "NORMALISE_RETRIEVAL_QUERY_ENABLED", True)

    pg = _CapturingSearchKb()
    base = _CapturingSearchKb()
    embedder = _CapturingEmbedder()
    knowledge_port = MergedKnowledgeAdapter(base, _EmptyLiveStore(), embedder, pg_port=pg)

    app = FastAPI()
    app.include_router(build_kb_suggest_router(knowledge_port))
    client = TestClient(app)

    raw_text = "brp lama siap? nk service"
    response = client.get("/kb/suggest", params={"q": raw_text, "limit": 2})

    assert response.status_code == 200
    body = response.json()
    # The agent-facing surface echoes the query verbatim -- never normalised.
    assert body["query"] == raw_text
    # Meanwhile the retrieval branches this same request drove DID receive
    # the normalised copy, proving the constraint is a real split and not
    # merely "normalisation never ran".
    assert pg.calls == [normalise(raw_text)]
    assert embedder.calls == [normalise(raw_text)]
    assert base.calls == [raw_text]


# --------------------------------------------------------------------------
# Test eight: the acceptance gate this environment cannot evaluate for real.
# --------------------------------------------------------------------------


async def _run_faq_only_corpus(
    cases: list[dict[str, Any]],
    *,
    query_transform: Any,
    model_identity: str,
) -> CorpusReport:
    """A with/without variant of task 5's FAQ-half runner.

    Deliberately reuses task 5's `CorpusReport`/`CaseResult`/`_stub_disclaimer`
    machinery unmodified (imported, not copied) so the stub-vs-real
    distinction stays structural (`mode`/`disclaimer`) rather than a comment.
    Only measures the FAQ half (`expected_faq`) -- the property under test is
    "does normalising the query change FAQ-retrieval hits", which the intent
    classifier half of task 5's corpus cannot speak to.
    """
    faq_store = await _build_faq_store()
    embedder = _StubTopicEmbedder()

    results: list[CaseResult] = []
    for case in cases:
        query_text = query_transform(case["text"])
        query_vector = await embedder.embed(query_text)
        hits = await faq_store.search(query_vector, limit=1)
        predicted_faq = hits[0][0].id if hits else None
        expected_faq = case["expected_faq"]
        results.append(
            CaseResult(
                case_id=case["id"],
                text=case["text"],
                domain=case.get("domain", ""),
                expected_intent=None,
                predicted_intent_label=None,
                expected_faq=expected_faq,
                predicted_faq=predicted_faq,
                passed=predicted_faq == expected_faq,
            )
        )

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    return CorpusReport(
        model_identity=model_identity,
        mode="stub",
        total_cases=total,
        passed_cases=passed,
        pass_rate=(passed / total) if total else 0.0,
        is_baseline_measured=False,
        disclaimer=_stub_disclaimer(model_identity),
        results=results,
    )


@pytest.mark.asyncio
async def test_the_corpus_pass_rate_improves_or_the_normaliser_is_not_shipped() -> None:
    """The brief's literal gate cannot be evaluated here -- see this file's
    module docstring, `nlu_normalise.py`'s "Ship gate" section, and
    `docs/analysis/2026-08-09-blocked-work-register.md`. What is asserted
    instead: the with/without comparison mechanism runs end to end against
    the same stubbed harness task 5 built, both runs are honestly labelled
    `mode="stub"` / `is_baseline_measured=False`, and both pass rates are
    reported. No improvement is asserted -- doing so would fabricate a
    number this environment cannot produce.
    """
    cases = [c for c in _cases() if c.get("expected_faq")]
    assert cases, "the FAQ-labelled half of the corpus must be non-empty for this comparison"

    without_normaliser = await _run_faq_only_corpus(
        cases,
        query_transform=lambda text: text,
        model_identity="stub:topic-keyword-embedder-v0 (NOT Vertex, normaliser OFF)",
    )
    with_normaliser = await _run_faq_only_corpus(
        cases,
        query_transform=normalise,
        model_identity="stub:topic-keyword-embedder-v0 (NOT Vertex, normaliser ON)",
    )

    print(
        "\n=== P7 task 6 -- corpus FAQ pass rate, stubbed, UNMEASURED ===\n"
        f"without normaliser: {without_normaliser.passed_cases}/{without_normaliser.total_cases} "
        f"({without_normaliser.pass_rate:.1%})\n"
        f"with normaliser   : {with_normaliser.passed_cases}/{with_normaliser.total_cases} "
        f"({with_normaliser.pass_rate:.1%})\n"
        "Neither number is the P7 NLU baseline -- see task-6-report.md and "
        "the blocked-work register.\n"
    )

    # Structural: both runs are honestly stub-labelled, never claiming to be
    # a real measurement.
    for report in (without_normaliser, with_normaliser):
        assert report.mode == "stub"
        assert report.is_baseline_measured is False
        assert "stub" in report.disclaimer.lower()
        assert "not" in report.disclaimer.lower()
        assert 0.0 <= report.pass_rate <= 1.0
        assert report.total_cases == len(cases)

    # The comparison mechanism itself works: both runs cover the identical
    # case set and are directly comparable (same total, same case ids).
    assert without_normaliser.total_cases == with_normaliser.total_cases
    assert [r.case_id for r in without_normaliser.results] == [
        r.case_id for r in with_normaliser.results
    ]

    # The one assertion this task IS entitled to make: the flag stays off
    # regardless of what this stub comparison shows, because the stub result
    # is explicitly not the real evidence the brief's gate requires.
    assert NORMALISE_RETRIEVAL_QUERY_ENABLED is False


async def test_the_keyword_signal_gets_the_raw_query_even_when_the_embedding_is_normalised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The authored-`keywords` signal must never see the normalised copy.

    Normalisation exists to help the *embedding* match FAQs written in standard
    Malay. Keyword matching wants the opposite: an exact product code like
    "e.MAS7" is precisely the thing normalisation would mangle, and it is the
    case the keyword signal exists for. So `search_kb` must hand the live store a
    normalised `query_embedding` and a RAW `query_text` in the same call.

    Regression guard for the P7 final review's C1: the store used to receive no
    `query_text` at all, which made `FAQ_KEYWORD_WEIGHT` inert at every value.
    """
    raw_text = "brp lama siap? nk service e.MAS7"
    normalised_text = normalise(raw_text)
    assert normalised_text != raw_text  # sanity: the fixture exercises expansion

    live = _EmptyLiveStore()
    embedder = _CapturingEmbedder()
    adapter = MergedKnowledgeAdapter(_CapturingSearchKb(), live, embedder)

    monkeypatch.setattr(
        "chatbot.features.chat.adapters.merged_knowledge.NORMALISE_RETRIEVAL_QUERY_ENABLED",
        True,
    )
    await adapter.search_kb(raw_text, limit=2)

    assert embedder.calls == [normalised_text], "the embedding should get the normalised copy"
    assert live.last_query_text == raw_text, "the keyword signal must get the RAW query"
