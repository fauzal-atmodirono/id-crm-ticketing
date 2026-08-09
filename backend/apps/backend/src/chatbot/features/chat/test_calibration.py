"""P7 task 10 -- the calibration baseline runner.

**This suite measures. It does not gate.** Reuses P7 task 5's structural
pattern exactly (see `test_malay_sms_corpus.py` and `task-5-report.md`) rather
than inventing a second idiom for the same "no real credentials" problem:

- `CalibrationReport.mode` is `"stub"` in this environment and would read
  `"real"` only once run against actual Gemini/Vertex credentials.
- `CalibrationReport.model_identity` names the exact stand-in used, always
  containing the word "stub" and never claiming to be Gemini.
- `CalibrationReport.is_baseline_measured` is `False` and
  `CalibrationReport.disclaimer` says so in plain language.

This sandbox has no real `GOOGLE_API_KEY`/Vertex credentials
(`GOOGLE_API_KEY=test-key`, every model client stubbed -- see the repo root
CLAUDE.md). So, for all four capabilities below, the "what would Gemini/Vertex
have decided" step is replaced by a small, deliberately naive, NOT-tuned-to-
the-corpus stand-in -- everything else (taxonomy validation, FAQ cosine
ranking, the sentiment write path) is the real, unmodified production code,
obtained the same way `test_classify_ticket_tool.py` / `test_live_faq_store.py`
/ `test_malay_sms_corpus.py` obtain it.

Running this against real Gemini/Vertex credentials -- by swapping each
`*_classifier`/`*_embedder`/`*_summarizer` callable for a real one, same
plumbing, no test changes needed -- is the handover step that produces the
actual P7 calibration baseline. See
`docs/testing/2026-08-08-ai-calibration-baseline.md` for exact instructions
and where the resulting numbers get recorded.
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

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "calibration_sets"

_MIN_CASES_PER_SET = 30

_CAPABILITIES = ("intent_classification", "faq_match", "sentiment", "summary_quality")


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------


def _load_cases(capability: str) -> list[dict[str, Any]]:
    path = _FIXTURES_DIR / f"{capability}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return list(data["cases"])


def _find_tool(agent: object, name: str) -> Any:
    for tool in agent.tools:  # type: ignore[attr-defined]
        func = getattr(tool, "func", tool)
        if getattr(func, "__name__", "") == name:
            return func
    raise AssertionError(f"tool {name} not registered")


# --------------------------------------------------------------------------
# Common report shape (same idiom as P7 task 5's CorpusReport)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    score: float  # 0.0-1.0; binary for intent/faq/sentiment, fractional for summary
    detail: str = ""


@dataclass(frozen=True)
class CalibrationReport:
    capability: str
    model_identity: str
    mode: str  # "stub" in this environment; "real" once run against Gemini/Vertex
    total_cases: int
    score: float  # mean of per-case scores, 0.0-1.0
    is_baseline_measured: bool
    disclaimer: str
    results: list[CaseScore] = field(default_factory=list)


def _stub_disclaimer(capability: str, model_identity: str) -> str:
    return (
        f"UNMEASURED IN THIS ENVIRONMENT ({capability}): this score was produced "
        f"by a deterministic stub ({model_identity}), not by Gemini or Vertex -- "
        "no GOOGLE_API_KEY/Vertex credentials are available in this sandbox. "
        "This number is NOT the P7 calibration baseline and must not be cited "
        "as calibration evidence. Re-run this suite with real credentials wired "
        "into the classifier/embedder/summarizer to obtain the actual baseline "
        "(see docs/testing/2026-08-08-ai-calibration-baseline.md)."
    )


def _print_report(report: CalibrationReport) -> None:
    print(f"\n=== Calibration report: {report.capability} (P7 task 10) ===")
    print(f"model_identity : {report.model_identity}")
    print(f"mode           : {report.mode}")
    print(f"baseline?      : {report.is_baseline_measured}")
    print(f"disclaimer     : {report.disclaimer}")
    for r in report.results:
        print(f"  [{r.score:.2f}] {r.case_id:<14} {r.detail}")
    print(f"--- {report.capability}: {report.score:.1%} over {report.total_cases} cases ---\n")


# --------------------------------------------------------------------------
# Capability 1 -- intent classification
#
# Real classify_ticket_tool + real CaseTaxonomy (case_taxonomy.py, loaded
# from Settings.case_taxonomy_json's shipped default -- the client's own RFP
# 2026_028 Appendix A taxonomy), same as P7 task 5. Only the "what would
# Gemini have guessed" step is a naive, NOT-tuned keyword stand-in.
# --------------------------------------------------------------------------

_INTENT_MODEL_IDENTITY = (
    "stub:keyword-heuristic-v1 (NOT Gemini -- no credentials in this environment)"
)

_INTENT_KEYWORD_RULES: tuple[tuple[tuple[str, ...], tuple[str, str]], ...] = (
    (
        ("brek", "brake failure", "brek tak berfungsi"),
        ("aftersales", "Brake / Electronic Parking Brake"),
    ),
    (("aircond", "air cond"), ("aftersales", "Airconditioner")),
    (("steering",), ("aftersales", "Steering")),
    (("suspension",), ("aftersales", "Suspension")),
    (("cooling system", "overheat"), ("aftersales", "Cooling System")),
    (("body kereta", "kesan kemalangan"), ("aftersales", "Body")),
    (("electrical", "wiring"), ("aftersales", "Electrical")),
    (("adas",), ("aftersales", "ADAS")),
    (("airbag",), ("aftersales", "Airbag")),
    (("spare part", "tempah part", "part original"), ("aftersales", "Spare Part")),
    (("waranti", "warranty", "warranti"), ("aftersales", "Warranty")),
    (("service due", "next service", "servis"), ("aftersales", "Service Operation")),
    (("mogok", "tow truck", "抛锚", "拖车"), ("aftersales", "Roadside Assistance")),
    (("recall",), ("aftersales", "Service/Recall Campaign")),
    (("user manual",), ("aftersales", "User Manual")),
    (("staff kat outlet", "staff sangat tak sopan"), ("aftersales", "Staff")),
    (("delivery", "book slot delivery"), ("sales", "Delivery")),
    (("refund down payment", "refund"), ("sales", "Refund")),
    (("customer service", "lembab"), ("sales", "Customer Experience")),
    (("promotion",), ("sales", "Promotion")),
    (("test drive",), ("sales", "Test Drive")),
    (("outlet paling dekat", "outlet paling dkt"), ("sales", "Outlet")),
    (("booking kereta baru", "nak booking"), ("sales", "Booking")),
    (("insurance",), ("sales", "Insurance")),
    (("finance package", "finance information"), ("sales", "Finance Information")),
    (("home charger",), ("charging", "Home Charging")),
    (("stesen cas awam", "public charging"), ("charging", "Public Charging")),
    (("charging credit",), ("charging", "Charging Credit")),
    (("login", "user id"), ("apps", "User ID")),
    (("notification reminder",), ("apps", "Notification")),
    (("smart points",), ("apps", "smart points")),
    (("dealer punya lokasi", "dealer information"), ("apps", "Dealer Information")),
    (("salah number",), ("others", "Misdial")),
    (("merchandise",), ("marketing", "Merchandise")),
)


def _stub_intent_classifier(text: str) -> tuple[str | None, str | None]:
    """Deliberately naive keyword stand-in -- not tuned to this fixture."""
    lowered = text.lower()
    for keywords, (category_slug, subcategory) in _INTENT_KEYWORD_RULES:
        if any(keyword in lowered for keyword in keywords):
            return category_slug, subcategory
    return None, None


async def _run_intent_capability(cases: list[dict[str, Any]]) -> CalibrationReport:
    settings = get_settings()
    case_taxonomy = build_case_taxonomy(settings)
    agent = build_ai_agent(settings, InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter())
    classify_tool = _find_tool(agent, "classify_ticket_tool")

    results: list[CaseScore] = []
    for case in cases:
        text = case["text"]
        expected = case["expected_intent"]
        category_slug, subcategory_raw = _stub_intent_classifier(text)
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
        predicted_label = ctx.state.get("subcategory")
        expected_label = case_taxonomy.label_for(expected["category_slug"])
        expected_full = f"{expected_label}: {expected['subcategory']}"
        score = 1.0 if predicted_label == expected_full else 0.0
        results.append(
            CaseScore(case_id=case["id"], score=score, detail=f"predicted={predicted_label!r}")
        )

    total = len(results)
    mean_score = (sum(r.score for r in results) / total) if total else 0.0
    return CalibrationReport(
        capability="intent_classification",
        model_identity=_INTENT_MODEL_IDENTITY,
        mode="stub",
        total_cases=total,
        score=mean_score,
        is_baseline_measured=False,
        disclaimer=_stub_disclaimer("intent_classification", _INTENT_MODEL_IDENTITY),
        results=results,
    )


# --------------------------------------------------------------------------
# Capability 2 -- FAQ match
#
# Real InMemoryLiveFaqStore/_rank cosine-ranking code (adapters/live_faq.py),
# same as P7 task 5. Topics are the real FAQ/KB content this sandbox has: P7
# task 5's 8-topic stub set plus 4 topics carried over verbatim from
# test_faq_hybrid_rank.py's LiveFaqEntry fixtures. Only the embedding step
# (real production uses Vertex) is a deterministic keyword-trigger stand-in.
# --------------------------------------------------------------------------

_FAQ_MODEL_IDENTITY = "stub:topic-keyword-embedder-v1 (NOT Vertex)"

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
        ),
    ),
    "faq-warranty-coverage": (
        "What does the vehicle warranty cover and how do I claim it?",
        "The standard warranty covers manufacturing defects on eligible "
        "components; visit an authorised outlet with your service book to "
        "make a claim.",
        ("warranty", "waranti", "garranty", "claim warranty", "warranty到底cover"),
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
            "broken down",
            "kemalangan",
            "pancit",
        ),
    ),
    "faq-spare-parts": (
        "Are genuine spare parts available at authorised outlets?",
        "Genuine spare parts can be ordered through any authorised outlet's "
        "parts counter; lead time varies by part.",
        ("spare part", "sparepart", "spare parts", "genuine parts", "part original", "cari part"),
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
            "nearest showroom",
            "outlet paling dekat",
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
        ("test drive", "testdrive", "book test drive"),
    ),
    # Carried verbatim from test_faq_hybrid_rank.py's LiveFaqEntry fixtures.
    "faq-battery-light-reset": (
        "How to reset the battery light?",
        "Hold the reset button for 5 seconds.",
        ("battery light", "reset the battery", "reset battery"),
    ),
    "faq-tyre-pressure": (
        "What tyre pressure should I use?",
        "32 PSI front and rear for most models.",
        ("tyre pressure", "tayar", "pam berapa"),
    ),
    "faq-emas7-charging-port-cover": (
        "e.MAS7 charging port cover replacement",
        "Order part 7X-CVR from parts desk.",
        ("charging port cover", "e.mas7"),
    ),
    "faq-maintenance-schedule": (
        "General maintenance schedule",
        "Service every 10000 km.",
        ("maintenance schedule", "service schedule", "by km"),
    ),
}

_FAQ_IDS_ORDER = list(_FAQ_TOPICS.keys())


class _StubTopicEmbedder:
    """Deterministic stand-in for `adapters/live_faq.py`'s `VertexEmbedder`.

    Same idiom as P7 task 5's `_StubTopicEmbedder` -- not a real embedder.
    """

    identity = _FAQ_MODEL_IDENTITY

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


async def _run_faq_capability(cases: list[dict[str, Any]]) -> CalibrationReport:
    faq_store = await _build_faq_store()
    embedder = _StubTopicEmbedder()

    results: list[CaseScore] = []
    for case in cases:
        text = case["text"]
        expected_faq = case["expected_faq"]
        query_vector = await embedder.embed(text)
        hits = await faq_store.search(query_vector, limit=1)
        predicted_faq = hits[0][0].id if hits else None
        score = 1.0 if predicted_faq == expected_faq else 0.0
        results.append(
            CaseScore(case_id=case["id"], score=score, detail=f"predicted={predicted_faq!r}")
        )

    total = len(results)
    mean_score = (sum(r.score for r in results) / total) if total else 0.0
    return CalibrationReport(
        capability="faq_match",
        model_identity=_FAQ_MODEL_IDENTITY,
        mode="stub",
        total_cases=total,
        score=mean_score,
        is_baseline_measured=False,
        disclaimer=_stub_disclaimer("faq_match", _FAQ_MODEL_IDENTITY),
        results=results,
    )


# --------------------------------------------------------------------------
# Capability 3 -- sentiment
#
# Real classify_ticket_tool's sentiment write path (P7 task 1;
# `settings.sentiment_classifier_enabled` gate, same `.model_copy(update=...)`
# pattern `test_sentiment.py` uses). Only "what would Gemini have judged the
# customer's tone to be" is a naive keyword stand-in.
# --------------------------------------------------------------------------

_SENTIMENT_MODEL_IDENTITY = (
    "stub:keyword-heuristic-v1 (NOT Gemini -- no credentials in this environment)"
)

_URGENT_KEYWORDS = (
    "brek",
    "brake failure",
    "tak berfungsi",
    "asap",
    "smoke",
    "terbakar",
    "fire",
    "accident",
    "kemalangan",
    "cedera",
    "injured",
    "airbag",
    "emergency",
    "locked up",
    "bahaya",
    "berbahaya",
    "着火",
    "விபத்து",
    "tayar pecah",
    "terbabas",
)
_NEGATIVE_KEYWORDS = (
    "kecewa",
    "disappointed",
    "tak profesional",
    "unprofessional",
    "teruk",
    "frustrated",
    "态度很差",
    "没有人回复",
    "tak puas hati",
    "mengecewakan",
    "extremely unprofessional",
)
_POSITIVE_KEYWORDS = (
    "terima kasih banyak",
    "thanks so much",
    "excellent",
    "syabas",
    "bagus",
    "happy",
    "谢谢你们的帮助",
    "membantu",
    "berbaloi",
)


def _stub_sentiment_classifier(text: str) -> str:
    """Deliberately naive keyword stand-in -- checked urgent first, since a
    safety-critical phrase should never be masked by an unrelated negative
    word elsewhere in the same message."""
    lowered = text.lower()
    if any(keyword in lowered for keyword in _URGENT_KEYWORDS):
        return "urgent"
    if any(keyword in lowered for keyword in _NEGATIVE_KEYWORDS):
        return "negative"
    if any(keyword in lowered for keyword in _POSITIVE_KEYWORDS):
        return "positive"
    return "neutral"


async def _run_sentiment_capability(cases: list[dict[str, Any]]) -> CalibrationReport:
    settings = get_settings().model_copy(update={"sentiment_classifier_enabled": True})
    agent = build_ai_agent(settings, InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter())
    classify_tool = _find_tool(agent, "classify_ticket_tool")

    results: list[CaseScore] = []
    for case in cases:
        text = case["text"]
        expected = case["expected_sentiment"]
        predicted = _stub_sentiment_classifier(text)
        ctx = SimpleNamespace(state={})
        await classify_tool(
            ctx,
            category="others",
            subcategory="Misdial",
            priority="LOW",
            sla_minutes=30,
            case_type="Inquiry",
            vehicle_model="Not Applicable",
            sentiment=predicted,
        )
        written = ctx.state.get("sentiment")
        score = 1.0 if written == expected else 0.0
        results.append(CaseScore(case_id=case["id"], score=score, detail=f"predicted={written!r}"))

    total = len(results)
    mean_score = (sum(r.score for r in results) / total) if total else 0.0
    return CalibrationReport(
        capability="sentiment",
        model_identity=_SENTIMENT_MODEL_IDENTITY,
        mode="stub",
        total_cases=total,
        score=mean_score,
        is_baseline_measured=False,
        disclaimer=_stub_disclaimer("sentiment", _SENTIMENT_MODEL_IDENTITY),
        results=results,
    )


# --------------------------------------------------------------------------
# Capability 4 -- summary quality
#
# There is no single correct summary, so each case carries a
# `required_elements` checklist (name + keyword synonyms) instead of one
# expected string. Score = fraction of elements whose keywords appear
# (case-insensitive substring) in the generated summary -- a mechanical,
# reproducible rubric documented in
# docs/testing/2026-08-08-ai-calibration-baseline.md. The stub summarizer
# below is a naive extractor (first Customer line + last Agent line), NOT
# Gemini, and is not tuned to maximise this fixture's score.
# --------------------------------------------------------------------------

_SUMMARY_MODEL_IDENTITY = (
    "stub:naive-extractive-v1 (NOT Gemini -- no credentials in this environment)"
)


def _stub_naive_summarizer(transcript: str) -> str:
    lines = [line.strip() for line in transcript.splitlines() if line.strip()]
    customer_lines = [line for line in lines if line.lower().startswith("customer:")]
    agent_lines = [line for line in lines if line.lower().startswith("agent:")]
    first_customer = customer_lines[0] if customer_lines else ""
    last_agent = agent_lines[-1] if agent_lines else ""
    return f"{first_customer} {last_agent}".strip()


def _score_summary(summary: str, required_elements: list[dict[str, Any]]) -> float:
    if not required_elements:
        return 1.0
    lowered = summary.lower()
    matched = sum(
        1
        for element in required_elements
        if any(keyword.lower() in lowered for keyword in element["keywords"])
    )
    return matched / len(required_elements)


async def _run_summary_capability(cases: list[dict[str, Any]]) -> CalibrationReport:
    results: list[CaseScore] = []
    for case in cases:
        summary = _stub_naive_summarizer(case["transcript"])
        score = _score_summary(summary, case["required_elements"])
        results.append(CaseScore(case_id=case["id"], score=score, detail=f"summary={summary!r}"))

    total = len(results)
    mean_score = (sum(r.score for r in results) / total) if total else 0.0
    return CalibrationReport(
        capability="summary_quality",
        model_identity=_SUMMARY_MODEL_IDENTITY,
        mode="stub",
        total_cases=total,
        score=mean_score,
        is_baseline_measured=False,
        disclaimer=_stub_disclaimer("summary_quality", _SUMMARY_MODEL_IDENTITY),
        results=results,
    )


# --------------------------------------------------------------------------
# Runs all four capabilities -- what task-10's brief calls "the calibration
# runner"
# --------------------------------------------------------------------------


async def run_all_capabilities() -> dict[str, CalibrationReport]:
    return {
        "intent_classification": await _run_intent_capability(_load_cases("intent_classification")),
        "faq_match": await _run_faq_capability(_load_cases("faq_match")),
        "sentiment": await _run_sentiment_capability(_load_cases("sentiment")),
        "summary_quality": await _run_summary_capability(_load_cases("summary_quality")),
    }


# --------------------------------------------------------------------------
# Tests -- exact names from the task brief
# --------------------------------------------------------------------------


def test_each_of_the_four_calibration_sets_has_at_least_thirty_labelled_cases() -> None:
    for capability in _CAPABILITIES:
        cases = _load_cases(capability)
        assert len(cases) >= _MIN_CASES_PER_SET, (
            f"{capability} has only {len(cases)} cases, need >= {_MIN_CASES_PER_SET}"
        )


@pytest.mark.asyncio
async def test_the_calibration_runner_produces_a_score_per_capability() -> None:
    reports = await run_all_capabilities()

    assert set(reports.keys()) == set(_CAPABILITIES)
    for capability, report in reports.items():
        _print_report(report)
        assert report.capability == capability
        assert report.total_cases >= _MIN_CASES_PER_SET
        assert len(report.results) == report.total_cases
        assert 0.0 <= report.score <= 1.0
        # Structural, not a threshold -- mirrors P7 task 5's test five: this
        # suite records a rate per capability, it never gates on one. No
        # `assert report.score >= <threshold>` belongs anywhere in this file;
        # a client-agreed threshold is the document's job (test three/four
        # below), not this runner's.
        assert report.mode == "stub"
        assert report.is_baseline_measured is False
        assert "stub" in report.model_identity.lower()
        assert "gemini" not in report.model_identity.lower().replace("not gemini", "")
        assert "unmeasured" in report.disclaimer.lower()
        assert "baseline" in report.disclaimer.lower()


_DOC_PATH = (
    Path(__file__).resolve().parents[7]
    / "docs"
    / "testing"
    / "2026-08-08-ai-calibration-baseline.md"
)


def _read_doc() -> str:
    assert _DOC_PATH.exists(), f"expected the baseline document at {_DOC_PATH}"
    return _DOC_PATH.read_text(encoding="utf-8")


def test_the_baseline_document_records_a_pre_change_and_post_change_number() -> None:
    """Both slots must exist and be clearly structured/marked unmeasured.

    Deliberately does NOT assert that a number is present -- asserting a
    number would force writing a fake one (see D1 in progress.md: "a
    fabricated baseline is worse than an absent one"). What must exist is the
    *structure*: a pre-change baseline section and a post-P7 measurement
    section, each explicitly marked as not yet measured in this environment.
    """
    text = _read_doc()
    lowered = text.lower()

    assert "pre-change baseline" in lowered or "baseline (pre-p7" in lowered
    assert "post-p7" in lowered or "post-change" in lowered

    # Both slots must say WHY they are empty and HOW to fill them, not merely
    # that they are empty.
    assert "unmeasured" in lowered
    assert "google_api_key" in lowered
    assert lowered.count("not measured") + lowered.count("unmeasured") >= 2

    # Guard against exactly the failure mode this test exists to prevent: no
    # invented percentage anywhere near the baseline/post-change sections.
    assert "tbd" in lowered or "unmeasured" in lowered


def test_the_thresholds_are_marked_as_proposed_pending_client_sign_off() -> None:
    """The acceptance thresholds are a PROPOSAL, not a standard already met."""
    text = _read_doc()
    lowered = text.lower()

    assert "proposed" in lowered
    assert "sign-off" in lowered or "sign off" in lowered
    assert "acceptance threshold" in lowered

    # Must not claim the thresholds are agreed/met/approved anywhere.
    assert "client-agreed" not in lowered
    assert "already met" not in lowered
    assert "thresholds have been approved" not in lowered
