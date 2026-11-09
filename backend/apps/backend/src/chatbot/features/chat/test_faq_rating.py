"""FIX-23: agents rate the FAQ strip suggestion (thumbs), admin list shows the average.

Design notes recorded in the task report: thumbs map to score 5 / 1 on the
existing 1-5 `/kb/feedback` contract; one rating per (faq, agent, conversation),
re-rating replaces; auth accepts the QA key or the shared CRM backend key (the
browser only holds the latter, same as `/kb/faq`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.faq_ratings import InMemoryFaqRatingStore
from chatbot.features.chat.faq_admin_router import build_faq_admin_router
from chatbot.features.chat.ports import LiveFaqEntry
from chatbot.features.metrics.faq_router import build_faq_router
from chatbot.platform.config import Settings

_QA = "qa-key"
_CRM = "crm-backend-key"


def _settings() -> Settings:
    return Settings(qa_api_key=_QA, proton_backend_key=_CRM, faq_admin_api_key="admin-key")


def _body(**over: Any) -> dict[str, Any]:
    base = {
        "article_id": "faq-1",
        "session_id": "conv-9",
        "helpful": True,
        "score": 5,
        "agent_id": "7",
        "conversation_id": "9",
    }
    base.update(over)
    return base


def _fb_client(store: InMemoryFaqRatingStore, port: Any | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(build_faq_router(port or AsyncMock(), _settings(), rating_store=store))
    return TestClient(app)


def test_feedback_post_records_score_per_faq() -> None:
    store = InMemoryFaqRatingStore()
    c = _fb_client(store)
    assert c.post("/kb/feedback", json=_body(), headers={"X-API-Key": _CRM}).status_code == 200
    assert (
        c.post(
            "/kb/feedback",
            json=_body(article_id="faq-2", score=1, helpful=False, reason="outdated"),
            headers={"X-API-Key": _CRM},
        ).status_code
        == 200
    )
    s = asyncio.run(store.summaries())
    assert s["faq-1"] == (5.0, 1)
    assert s["faq-2"] == (1.0, 1)


def test_same_agent_same_conversation_rates_once_and_rerating_replaces() -> None:
    store = InMemoryFaqRatingStore()
    c = _fb_client(store)
    h = {"X-API-Key": _CRM}
    c.post("/kb/feedback", json=_body(score=5), headers=h)
    c.post("/kb/feedback", json=_body(score=1, helpful=False), headers=h)
    assert asyncio.run(store.summaries())["faq-1"] == (1.0, 1)
    # a different agent, and the same agent in another conversation, each count
    c.post("/kb/feedback", json=_body(agent_id="8", score=5), headers=h)
    c.post("/kb/feedback", json=_body(conversation_id="10", score=5), headers=h)
    assert asyncio.run(store.summaries())["faq-1"] == (pytest.approx(11 / 3), 3)


def test_feedback_accepts_qa_key_or_crm_key_rejects_others() -> None:
    store = InMemoryFaqRatingStore()
    c = _fb_client(store)
    assert c.post("/kb/feedback", json=_body(), headers={"X-API-Key": _QA}).status_code == 200
    assert c.post("/kb/feedback", json=_body(), headers={"X-API-Key": _CRM}).status_code == 200
    assert c.post("/kb/feedback", json=_body(), headers={"X-API-Key": "bad"}).status_code == 401
    assert c.post("/kb/feedback", json=_body()).status_code == 401


def test_unauthenticated_feedback_records_nothing() -> None:
    store = InMemoryFaqRatingStore()
    port = AsyncMock()
    c = _fb_client(store, port)
    c.post("/kb/feedback", json=_body())
    assert asyncio.run(store.summaries()) == {}
    port.record_feedback.assert_not_awaited()


class _Store:
    def __init__(self) -> None:
        self.entries = [
            LiveFaqEntry(id="faq-1", question="Q1", answer="A1"),
            LiveFaqEntry(id="faq-2", question="Q2", answer="A2"),
        ]

    async def list_all(self) -> list[LiveFaqEntry]:
        return self.entries


def test_faq_list_shows_average_score() -> None:
    ratings = InMemoryFaqRatingStore()
    fb = _fb_client(ratings)
    h = {"X-API-Key": _CRM}
    fb.post("/kb/feedback", json=_body(score=5), headers=h)
    fb.post("/kb/feedback", json=_body(agent_id="8", score=1, helpful=False), headers=h)
    app = FastAPI()
    app.include_router(build_faq_admin_router(_Store(), _settings(), rating_store=ratings))  # type: ignore[arg-type]
    rows = {
        e["id"]: e
        for e in TestClient(app)
        .get("/kb/faq", headers={"x-api-key": "admin-key"})
        .json()["entries"]
    }
    assert rows["faq-1"]["avg_score"] == 3.0
    assert rows["faq-1"]["rating_count"] == 2
    assert rows["faq-2"]["avg_score"] is None
    assert rows["faq-2"]["rating_count"] == 0


def test_faq_list_works_without_rating_store() -> None:
    app = FastAPI()
    app.include_router(build_faq_admin_router(_Store(), _settings()))  # type: ignore[arg-type]
    rows = TestClient(app).get("/kb/faq", headers={"x-api-key": "admin-key"}).json()["entries"]
    assert rows[0]["avg_score"] is None
    assert rows[0]["rating_count"] == 0


# --- review fixes: session-derived rater (RBAC on) -------------------------


class _Validator:
    async def resolve_identity(self, token: str, client: str, uid: str) -> tuple[int, bool] | None:
        return (42, False) if token == "good" else None


_SESSION = {
    "X-API-Key": _CRM,
    "x-chatwoot-access-token": "good",
    "x-chatwoot-client": "c",
    "x-chatwoot-uid": "u@x",
}


def _rbac_client(store: InMemoryFaqRatingStore, *, with_faqs: bool = True) -> TestClient:
    settings = Settings(qa_api_key=_QA, proton_backend_key=_CRM, rbac_enabled=True)
    app = FastAPI()
    app.include_router(
        build_faq_router(
            AsyncMock(),
            settings,
            store,
            faq_store=_Store() if with_faqs else None,  # type: ignore[arg-type]
            validator=_Validator(),  # type: ignore[arg-type]
        )
    )
    return TestClient(app)


def test_rbac_on_rater_comes_from_session_and_body_agent_is_ignored() -> None:
    store = InMemoryFaqRatingStore()
    c = _rbac_client(store)
    r = c.post("/kb/feedback", json=_body(agent_id="999"), headers=_SESSION)
    assert r.status_code == 200
    assert list(store._rows) == [("faq-1", "42", "9")]


def test_rbac_on_missing_session_is_401_even_with_shared_key() -> None:
    store = InMemoryFaqRatingStore()
    c = _rbac_client(store)
    assert c.post("/kb/feedback", json=_body(), headers={"X-API-Key": _CRM}).status_code == 401
    bad = {**_SESSION, "x-chatwoot-access-token": "bad"}
    assert c.post("/kb/feedback", json=_body(), headers=bad).status_code == 401
    assert asyncio.run(store.summaries()) == {}


def test_rbac_off_empty_agent_on_a_rating_is_400() -> None:
    store = InMemoryFaqRatingStore()
    c = _fb_client(store)
    r = c.post("/kb/feedback", json=_body(agent_id=""), headers={"X-API-Key": _CRM})
    assert r.status_code == 400
    assert asyncio.run(store.summaries()) == {}


def test_unknown_article_is_404_and_records_nothing() -> None:
    store = InMemoryFaqRatingStore()
    c = _rbac_client(store)
    r = c.post("/kb/feedback", json=_body(article_id="nope"), headers=_SESSION)
    assert r.status_code == 404
    assert asyncio.run(store.summaries()) == {}


def test_legacy_feedback_without_agent_or_conversation_still_works() -> None:
    store = InMemoryFaqRatingStore()
    c = _fb_client(store)
    body = {"article_id": "kb-9", "session_id": "s", "helpful": True, "score": 4}
    assert c.post("/kb/feedback", json=body, headers={"X-API-Key": _QA}).status_code == 200
    assert asyncio.run(store.summaries()) == {}


def test_faq_list_scores_are_cached_briefly() -> None:
    ratings = InMemoryFaqRatingStore()
    app = FastAPI()
    app.include_router(build_faq_admin_router(_Store(), _settings(), rating_store=ratings))  # type: ignore[arg-type]
    c = TestClient(app)
    h = {"x-api-key": "admin-key"}
    assert c.get("/kb/faq", headers=h).json()["entries"][0]["rating_count"] == 0
    asyncio.run(ratings.rate("faq-1", "7", "9", 5))
    assert c.get("/kb/faq", headers=h).json()["entries"][0]["rating_count"] == 0  # within TTL


# --- round 2: legacy QA-key callers survive RBAC; one shared validator -----


def test_rbac_on_legacy_qa_key_feedback_still_200_and_writes_no_rating() -> None:
    store = InMemoryFaqRatingStore()
    c = _rbac_client(store)
    body = {"article_id": "kb-9", "session_id": "s", "helpful": True, "score": 4}
    assert c.post("/kb/feedback", json=body, headers={"X-API-Key": _QA}).status_code == 200
    assert asyncio.run(store.summaries()) == {}


def test_rbac_on_shared_key_only_rating_is_401() -> None:
    store = InMemoryFaqRatingStore()
    c = _rbac_client(store)
    assert c.post("/kb/feedback", json=_body(), headers={"X-API-Key": _CRM}).status_code == 401
    assert asyncio.run(store.summaries()) == {}


def test_rbac_on_session_rating_is_200() -> None:
    store = InMemoryFaqRatingStore()
    c = _rbac_client(store)
    assert c.post("/kb/feedback", json=_body(), headers=_SESSION).status_code == 200
    assert asyncio.run(store.summaries())["faq-1"] == (5.0, 1)


def test_article_exists_check_is_cached() -> None:
    store = InMemoryFaqRatingStore()
    faqs = _Store()
    calls = 0
    orig = faqs.list_all

    async def counting() -> list[LiveFaqEntry]:
        nonlocal calls
        calls += 1
        return await orig()

    faqs.list_all = counting  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(
        build_faq_router(AsyncMock(), _settings(), store, faq_store=faqs)  # type: ignore[arg-type]
    )
    c = TestClient(app)
    for conv in ("1", "2", "3"):
        r = c.post("/kb/feedback", json=_body(conversation_id=conv), headers={"X-API-Key": _CRM})
        assert r.status_code == 200
    assert calls == 1


def test_main_builds_exactly_one_token_validator() -> None:
    src = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert src.count("TokenValidator(settings)") == 2  # shared one + the no-RBAC fallback
    assert src.count("_SharedTokenValidator(settings)") == 1
