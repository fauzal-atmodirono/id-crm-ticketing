"""P7 task 5 -- the Malay SMS corpus.

**This suite measures. It does not gate.** The RFP's own example --
`"brp lama siap? nk service"` ("how long to finish? want to service") -- is
SMS-register Malaysian Malay, and nobody has ever run it against this
system's FAQ retrieval path or intent classifier. This corpus is 50+ more
cases like it, spanning the domains this CRM actually serves (service
booking, roadside assistance, parts, warranty, complaints, dealer location,
charging, test drive, apps), each labelled against the REAL taxonomy this
system ships -- `case_taxonomy.py` loaded from `Settings.case_taxonomy_json`,
whose *default* value (unchanged here) is the client's own RFP 2026_028
Appendix A taxonomy (8 divisions: Sales, Product, Network, Charging, Apps,
After Sales, Others, Marketing).

**Why the pass rate below is not a baseline.** This sandbox has no real
Gemini/Vertex credentials (`GOOGLE_API_KEY=test-key`, every model client
stubbed -- see the repo's root CLAUDE.md). The "intent classifier" in
production is Gemini deciding `classify_ticket_tool`'s arguments from raw
text inside one forced-function-call turn; the "FAQ retrieval path" in
production is `adapters/live_faq.py`'s cosine search over Vertex text
embeddings. Neither Gemini nor Vertex embeddings exist here. So this suite
drives the REAL, unmodified `classify_ticket_tool` (via `build_ai_agent`,
same pattern as `test_classify_ticket_tool.py`) and the REAL
`InMemoryLiveFaqStore`/`_rank` cosine-ranking code (via `adapters/live_faq.py`,
same pattern as `test_live_faq_store.py`) -- but the *argument-guessing* step
that only a real model can do is replaced by a small, deliberately naive
keyword-rule stand-in defined in this file, `STUB_MODEL_IDENTITY` and
`CorpusReport.mode` name it explicitly, and `CorpusReport.disclaimer` states
in plain language that the resulting number is not the P7 NLU baseline.

Running this against real Gemini/Vertex credentials -- by swapping the
`intent_classifier` callable and the FAQ embedder for real ones, same
plumbing, no test changes needed -- is the handover step that produces the
actual baseline. See `task-5-report.md` for exact instructions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chatbot.features.chat.adapters.live_faq import InMemoryLiveFaqStore
from chatbot.features.chat.adapters.mock import InMemoryKnowledgeAdapter, InMemoryTicketingAdapter
from chatbot.features.chat.agents import build_ai_agent
from chatbot.features.chat.case_taxonomy import build_case_taxonomy
from chatbot.features.chat.ports import LiveFaqEntry
from chatbot.platform.config import get_settings

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "malay_sms_corpus.json"

# Findable-by-id per the brief ("named case", not merely present in the list).
RFP_EXAMPLE_CASE_ID = "rfp-example-brp-lama-siap-nk-service"
RFP_EXAMPLE_TEXT = "brp lama siap? nk service"

_MIN_CORPUS_SIZE = 50


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------


def _load_corpus() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    return data


def _cases() -> list[dict[str, Any]]:
    return list(_load_corpus()["cases"])


def _cases_by_id() -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in _cases()}


# --------------------------------------------------------------------------
# A small, self-contained stub FAQ set (NOT production content) -- one entry
# per `expected_faq` id used in the fixture. Each entry's own question/answer
# text is written so it fires at least one of its own trigger phrases when
# run through `_StubTopicEmbedder` below, so it is self-consistent under
# retrieval without needing real embeddings.
# --------------------------------------------------------------------------

_FAQ_TOPICS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "faq-service-duration": (
        "How long does a standard vehicle service take to complete?",
        "A standard service typically takes 2-3 hours; we will call you once "
        "your vehicle is ready for collection.",
        ("how long", "lama siap", "berapa lama nak siap", "bila siap", "leh siap"),
    ),
    "faq-service-booking": (
        "How do I book a vehicle service appointment?",
        "Book a service appointment via the app, the outlet hotline, or by "
        "visiting any authorised service centre.",
        (
            "service appointment",
            "book service",
            "nak service",
            "nk service",
            "schedule maintenance",
            "servis dh",
            "slot service",
            "service my e.mas",
        ),
    ),
    "faq-warranty-coverage": (
        "What does the vehicle warranty cover and how do I claim it?",
        "The standard warranty covers manufacturing defects on eligible "
        "components; visit an authorised outlet with your service book to "
        "make a claim.",
        ("warranty", "waranti", "garranty", "claim warranty"),
    ),
    "faq-roadside-tow": (
        "How do I request roadside assistance or towing?",
        "Call the 24-hour roadside assistance hotline with your location and "
        "we will dispatch a tow truck.",
        (
            "roadside",
            "towing",
            "tow truck",
            "mogok",
            "breakdown",
            "accident",
            "kemalangan",
            "pancit",
        ),
    ),
    "faq-spare-parts": (
        "Are genuine spare parts available at authorised outlets?",
        "Genuine spare parts can be ordered through any authorised outlet's "
        "parts counter; lead time varies by part.",
        (
            "spare part",
            "sparepart",
            "spare parts",
            "genuine parts",
            "part original",
            "cari part",
        ),
    ),
    "faq-dealer-locations": (
        "Where can I find the nearest dealer outlet or showroom?",
        "Use the dealer locator in the app or website to find the nearest "
        "showroom and service centre.",
        (
            "nearest outlet",
            "outlet paling dkt",
            "showroom",
            "cawangan",
            "dealer near",
            "lokasi outlet",
            "branch",
        ),
    ),
    "faq-charging-public": (
        "Where can I find public EV charging stations?",
        "Public charging stations are listed in the app's charging map, "
        "including live availability status.",
        ("charging station", "public charging", "stesen cas", "cas awam"),
    ),
    "faq-test-drive": (
        "How do I book a test drive?",
        "Book a test drive via the app or by contacting your nearest outlet directly.",
        ("test drive", "testdrive", "book test drive", "booking test drive"),
    ),
}

_FAQ_IDS_ORDER = list(_FAQ_TOPICS.keys())


class _StubTopicEmbedder:
    """A deterministic stand-in for `adapters/live_faq.py`'s `VertexEmbedder`.

    NOT a real embedder -- there is no Vertex/Gemini credential in this
    sandbox. Returns a fixed-length vector over `_FAQ_IDS_ORDER`, one
    dimension per FAQ topic, set to 1.0 when any of that topic's trigger
    phrases appears as a substring of the (lower-cased) text. Used for BOTH
    the corpus's FAQ entries (embedded once, on `store.create`) and the
    per-case query text, so `InMemoryLiveFaqStore.search`'s real cosine-rank
    code (`adapters/live_faq.py::_rank`, unmodified) has something concrete
    to rank -- this file only fakes the embedding step, not the retrieval
    logic.
    """

    identity = "stub:topic-keyword-embedder-v0 (NOT Vertex)"

    async def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        vector = [0.0] * len(_FAQ_IDS_ORDER)
        for i, faq_id in enumerate(_FAQ_IDS_ORDER):
            _question, _answer, triggers = _FAQ_TOPICS[faq_id]
            if any(trigger in lowered for trigger in triggers):
                vector[i] = 1.0
        return vector


async def _build_faq_store() -> InMemoryLiveFaqStore:
    embedder = _StubTopicEmbedder()
    store = InMemoryLiveFaqStore(embedder)
    for faq_id, (question, answer, _triggers) in _FAQ_TOPICS.items():
        await store.create(LiveFaqEntry(id=faq_id, question=question, answer=answer))
    return store


# --------------------------------------------------------------------------
# Intent classification stand-ins. In production, Gemini fills
# `classify_ticket_tool`'s arguments from raw text inside one forced
# function-calling turn (see agents.py::build_ai_agent, service.py). Here,
# in the absence of real Gemini, a plain keyword-rule function stands in for
# that argument-guessing step; the REAL `classify_ticket_tool` closure
# (unmodified, obtained the same way `test_classify_ticket_tool.py` does)
# still does the taxonomy validation and state-write, so this suite is
# exercising real production code everywhere except "what would Gemini have
# guessed".
# --------------------------------------------------------------------------

_INTENT_KEYWORD_RULES: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (
        (
            "towing",
            "tow truck",
            "roadside",
            "accident",
            "kemalangan",
            "mogok",
            "breakdown",
            "pancit",
            "enjin xleh start",
        ),
        ("aftersales", "Roadside Assistance"),
    ),
    (("warranty", "waranti", "garranty"), ("aftersales", "Warranty")),
    (
        ("spare part", "sparepart", "spare parts", "part original", "genuine parts", "cari part"),
        ("aftersales", "Spare Part"),
    ),
    (("service", "servis", "sevis", "maintenance"), ("aftersales", "Service Operation")),
    (("test drive", "testdrive"), ("sales", "Test Drive")),
    (
        ("outlet", "showroom", "branch", "cawangan", "dealer near", "nearest"),
        ("sales", "Outlet"),
    ),
    (
        (
            "komplen",
            "complaint",
            "disappointed",
            "kecewa",
            "xsopan",
            "unprofessional",
            "x professional",
            "tak puas hati",
        ),
        ("sales", "Customer Experience"),
    ),
    (("charging station", "public charging", "stesen cas"), ("charging", "Public Charging")),
    (("log in", "login", "user id", "asik error"), ("apps", "User ID")),
    (("notification", "reminder"), ("apps", "Notification")),
    (("app ni asyik", "check charging status"), ("apps", "Function")),
    (("app xtunjuk dealer", "update lokasi"), ("apps", "Dealer Information")),
)


def _stub_keyword_intent_classifier(text: str) -> tuple[str | None, str | None]:
    """A best-effort (but deliberately naive) keyword stand-in for Gemini.

    Not tuned against the corpus's answer key -- it is meant to approximate
    what an engineer with no ML would write, not to maximise the pass rate.
    """
    lowered = text.lower()
    for keywords, (category_slug, subcategory) in _INTENT_KEYWORD_RULES:
        if any(keyword in lowered for keyword in keywords):
            return category_slug, subcategory
    return None, None


def _stub_always_wrong_intent_classifier(_text: str) -> tuple[str | None, str | None]:
    """Deliberately wrong on every case -- used only by test five to prove
    the suite records whatever rate results instead of gating on one."""
    return "marketing", "Merchandise"


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    text: str
    domain: str
    expected_intent: dict[str, str] | None
    predicted_intent_label: str | None
    expected_faq: str | None
    predicted_faq: str | None
    passed: bool


@dataclass(frozen=True)
class CorpusReport:
    model_identity: str
    mode: str  # "stub" in this environment; "real" once run against Gemini/Vertex
    total_cases: int
    passed_cases: int
    pass_rate: float
    is_baseline_measured: bool
    disclaimer: str
    results: list[CaseResult] = field(default_factory=list)


def _find_tool(agent: object, name: str) -> Any:
    for tool in agent.tools:  # type: ignore[attr-defined]
        func = getattr(tool, "func", tool)
        if getattr(func, "__name__", "") == name:
            return func
    raise AssertionError(f"tool {name} not registered")


def _stub_disclaimer(model_identity: str) -> str:
    return (
        "UNMEASURED IN THIS ENVIRONMENT: this pass rate was produced by a "
        f"deterministic stub ({model_identity}), not by Gemini or Vertex -- "
        "no GOOGLE_API_KEY/Vertex credentials are available in this sandbox. "
        "This number is NOT the P7 NLU baseline and must not be cited as "
        "calibration evidence. Re-run this suite with real credentials wired "
        "into the intent classifier and the FAQ embedder to obtain the "
        "actual baseline (see task-5-report.md)."
    )


async def _run_corpus(
    cases: list[dict[str, Any]],
    *,
    intent_classifier: Any,
    model_identity: str,
) -> CorpusReport:
    settings = get_settings()
    case_taxonomy = build_case_taxonomy(settings)
    agent = build_ai_agent(settings, InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter())
    classify_tool = _find_tool(agent, "classify_ticket_tool")

    faq_store = await _build_faq_store()
    embedder = _StubTopicEmbedder()

    results: list[CaseResult] = []
    for case in cases:
        text = case["text"]
        expected_intent = case.get("expected_intent")
        expected_faq = case.get("expected_faq")

        predicted_intent_label: str | None = None
        intent_ok = True
        if expected_intent is not None:
            category_slug, subcategory_raw = intent_classifier(text)
            ctx = SimpleNamespace(state={})
            await classify_tool(
                ctx,
                category=category_slug or "",
                subcategory=subcategory_raw or "",
                priority="LOW",
                sla_minutes=30,
                case_type="Inquiry",
                vehicle_model="Not Applicable",
            )
            predicted_intent_label = ctx.state.get("subcategory")
            expected_label = case_taxonomy.label_for(expected_intent["category_slug"])
            expected_full = f"{expected_label}: {expected_intent['subcategory']}"
            intent_ok = predicted_intent_label == expected_full

        predicted_faq: str | None = None
        faq_ok = True
        if expected_faq is not None:
            query_vector = await embedder.embed(text)
            hits = await faq_store.search(query_vector, limit=1)
            predicted_faq = hits[0][0].id if hits else None
            faq_ok = predicted_faq == expected_faq

        results.append(
            CaseResult(
                case_id=case["id"],
                text=text,
                domain=case.get("domain", ""),
                expected_intent=expected_intent,
                predicted_intent_label=predicted_intent_label,
                expected_faq=expected_faq,
                predicted_faq=predicted_faq,
                passed=intent_ok and faq_ok,
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


def _print_report(report: CorpusReport) -> None:
    print("\n=== Malay SMS corpus report (P7 task 5) ===")
    print(f"model_identity : {report.model_identity}")
    print(f"mode           : {report.mode}")
    print(f"baseline?      : {report.is_baseline_measured}")
    print(f"disclaimer     : {report.disclaimer}")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.case_id:<40} {r.text!r}")
        if r.expected_intent is not None:
            print(
                f"         intent expected={r.expected_intent} predicted={r.predicted_intent_label!r}"
            )
        if r.expected_faq is not None:
            print(f"         faq    expected={r.expected_faq!r} predicted={r.predicted_faq!r}")
    print(f"--- {report.passed_cases}/{report.total_cases} passed ({report.pass_rate:.1%}) ---\n")


STUB_MODEL_IDENTITY = "stub:keyword-heuristic-v0 (NOT Gemini -- no credentials in this environment)"


# --------------------------------------------------------------------------
# Tests -- exact names from the task brief
# --------------------------------------------------------------------------


def test_the_corpus_contains_at_least_fifty_cases() -> None:
    cases = _cases()
    assert len(cases) >= _MIN_CORPUS_SIZE


def test_the_rfp_example_brp_lama_siap_nk_service_is_a_named_case() -> None:
    cases = _cases_by_id()
    assert RFP_EXAMPLE_CASE_ID in cases, (
        "the RFP example must be findable by id, not merely present"
    )
    assert cases[RFP_EXAMPLE_CASE_ID]["text"] == RFP_EXAMPLE_TEXT


def test_every_case_has_an_expected_intent_or_expected_faq() -> None:
    for case in _cases():
        assert case.get("expected_intent") or case.get("expected_faq"), (
            f"case {case['id']!r} has neither an expected_intent nor an expected_faq"
        )


@pytest.mark.asyncio
async def test_the_corpus_runs_and_reports_a_pass_rate() -> None:
    cases = _cases()
    report = await _run_corpus(
        cases, intent_classifier=_stub_keyword_intent_classifier, model_identity=STUB_MODEL_IDENTITY
    )
    _print_report(report)

    assert report.total_cases == len(cases)
    assert len(report.results) == report.total_cases
    assert report.passed_cases == sum(1 for r in report.results if r.passed)
    assert 0.0 <= report.pass_rate <= 1.0
    # Structural, not a threshold: the report merely has to exist and be
    # internally consistent -- see test five for why no number is asserted.
    assert report.mode == "stub"
    assert "stub" in report.model_identity.lower()
    assert "gemini" not in report.model_identity.lower().replace("not gemini", "")


@pytest.mark.asyncio
async def test_the_pass_rate_is_recorded_as_the_baseline_not_asserted_as_a_threshold() -> None:
    """Deliberate and important: this task measures, it does not gate.

    Proven here the hard way -- the intent classifier below is wrong on
    every single case (it returns a real-but-irrelevant taxonomy leaf,
    "Marketing: Merchandise", so `classify_ticket_tool` still accepts and
    writes it; the mismatch is purely against the corpus's expectations).
    Every case in this corpus carries an `expected_intent`, so this drives
    `pass_rate` to exactly 0.0 -- and the suite must still be GREEN. If a
    future edit adds `assert report.pass_rate >= <some threshold>` anywhere
    on this path, it converts a measurement into a gate before a baseline
    exists, which is exactly what this test exists to prevent (task 10 is
    where a client-agreed threshold belongs).
    """
    cases = _cases()
    hostile_identity = (
        "stub:deliberately-wrong-v0 (NOT Gemini -- always answers Marketing: Merchandise)"
    )
    report = await _run_corpus(
        cases,
        intent_classifier=_stub_always_wrong_intent_classifier,
        model_identity=hostile_identity,
    )
    _print_report(report)

    assert report.pass_rate == 0.0, (
        "every case has an expected_intent, so a wrong-every-time stub must score 0.0"
    )
    assert report.is_baseline_measured is False
    assert "not" in report.disclaimer.lower()
    assert "baseline" in report.disclaimer.lower()
    assert "stub" in report.disclaimer.lower()
    # The point: a pass_rate of exactly zero does not fail this test. There
    # is no threshold assertion above and there must never be one here.
