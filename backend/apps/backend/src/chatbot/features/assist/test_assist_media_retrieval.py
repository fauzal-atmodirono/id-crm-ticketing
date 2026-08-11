"""KB retrieval grounded on what the media shows.

The gap this closes, seen live on the proton tenant 2026-08-11: a customer
sends a video and asks "what model is this? / this one". The media reached
Gemini and it read the car correctly — but the KB query was built from the
words alone, so retrieval returned confidently irrelevant articles and the
draft came back "Saya tidak mempunyai maklumat ... dalam pangkalan data saya".
It could see the car and then said it knew nothing about it.

**Why this fires on every media request.** The first implementation gated the
extraction on the text search returning ZERO articles. In production that never
happened, not once: the KB is a similarity search, so a meaningless query gets
nearest neighbours rather than nothing. Retrieval fails IRRELEVANT, not EMPTY,
and `KbArticle` carries no score to separate the two. `test_..._never_fires_on
_a_similarity_search_kb` below pins that lesson so the gate is not reintroduced.

The cost is contained by caching per conversation, not by guessing which
questions look vague — several tests here exist purely to hold that line.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.assist_media import MediaTermsCache
from chatbot.features.assist.router import build_assist_router
from chatbot.features.chat.models import KbArticle
from chatbot.platform.config import Settings

_URL = "https://crm.example.test/rails/active_storage/blobs/redirect/abc/clip.mp4"
_KEY = "testkey"
_HEADERS = {"x-api-key": _KEY}
_TERMS = "Proton X50, front bumper"

# The live failure: the question lives entirely in the attachment.
_DEICTIC = [
    {"role": "customer", "content": "what model is this?", "attachments": []},
    {"role": "customer", "content": "this one", "attachments": [{"file_type": "video"}]},
]


class _RecordingKnowledge:
    """A similarity search: ALWAYS returns nearest neighbours, never nothing.

    This is the behaviour that made the original zero-result gate unfireable,
    so the stub models it faithfully rather than conveniently.
    """

    def __init__(self, relevant_for: str | None = None) -> None:
        self._relevant_for = relevant_for
        self.queries: list[str] = []

    async def search_kb(self, query: str, limit: int = 3) -> list:
        self.queries.append(query)
        if self._relevant_for and self._relevant_for.lower() in query.lower():
            return [KbArticle(title="X50 warranty", content="60 months", url="http://faq/1")]
        return [KbArticle(title="e.MAS 7", content="unrelated EV", url="http://faq/9")]


class _FakeContext:
    async def get_messages(self, conversation_id: str) -> list[dict]:
        return [
            {
                "message_type": 0,
                "private": False,
                "attachments": [{"file_type": "video", "data_url": _URL}],
            }
        ]


def _build(knowledge: _RecordingKnowledge, *, terms: str = _TERMS, context: object = None):
    context = context or _FakeContext()
    mock_genai = MagicMock()

    def _respond(**kwargs):
        system = (kwargs.get("config") or {}).get("system_instruction", "")
        out = MagicMock()
        out.text = terms if "extract search keywords" in system else "final answer"
        return out

    mock_genai.aio.models.generate_content = AsyncMock(side_effect=_respond)
    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=Settings(
                proton_backend_key=_KEY,
                assist_media_understanding_enabled=True,
                assist_media_max_bytes=1_000_000,
            ),
            knowledge_port=knowledge,
            genai_client=mock_genai,
            chatwoot_context=context,
        )
    )
    return TestClient(app), mock_genai


def _mock_download() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(200, content=b"clip", headers={"content-type": "video/mp4"})
    )


def _extractions(mock_genai: MagicMock) -> int:
    return sum(
        1
        for c in mock_genai.aio.models.generate_content.call_args_list
        if "extract search keywords" in (c.kwargs.get("config") or {}).get("system_instruction", "")
    )


def _suggest(client: TestClient, conv: str = "42", messages=None):
    return client.post(
        "/assist/suggest",
        json={"conversation_id": conv, "messages": messages or _DEICTIC},
        headers=_HEADERS,
    )


# ---------------------------------------------------------------------------
# The lesson that cost a deploy
# ---------------------------------------------------------------------------


async def test_the_kb_never_returns_zero_so_a_zero_result_gate_cannot_fire() -> None:
    """Documents WHY extraction is unconditional on media requests.

    If someone reintroduces `if not articles: ...` as the trigger, this test
    explains why it will silently never run.
    """
    kb = _RecordingKnowledge()
    assert await kb.search_kb("total gibberish xyzzy") != []


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


@respx.mock
def test_media_terms_lead_the_search_query() -> None:
    _mock_download()
    kb = _RecordingKnowledge(relevant_for="X50")
    client, mock_genai = _build(kb)
    r = _suggest(client)
    assert r.status_code == 200
    assert _extractions(mock_genai) == 1
    assert len(kb.queries) == 1
    assert kb.queries[0].startswith(_TERMS)
    # the customer's own words are kept after the terms
    assert "this one" in kb.queries[0]
    # and the article the grounded query found reaches the agent
    assert r.json()["sources"][0]["title"] == "X50 warranty"


@respx.mock
def test_recovered_context_reaches_the_prompt() -> None:
    _mock_download()
    client, mock_genai = _build(_RecordingKnowledge(relevant_for="X50"))
    _suggest(client)
    final = mock_genai.aio.models.generate_content.call_args_list[-1]
    assert "X50 warranty" in final.kwargs["config"]["system_instruction"]


@respx.mock
def test_ask_is_grounded_the_same_way() -> None:
    _mock_download()
    kb = _RecordingKnowledge(relevant_for="X50")
    client, mock_genai = _build(kb)
    r = client.post(
        "/assist/ask",
        json={
            "conversation_id": "42",
            "messages": _DEICTIC,
            "question": "what is wrong with this?",
        },
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert _extractions(mock_genai) == 1
    assert kb.queries[0].startswith(_TERMS)


# ---------------------------------------------------------------------------
# Cost containment
# ---------------------------------------------------------------------------


def test_no_media_means_no_extraction_ever() -> None:
    class _NoMedia:
        async def get_messages(self, conversation_id: str) -> list[dict]:
            return [{"message_type": 0, "private": False, "attachments": []}]

    kb = _RecordingKnowledge()
    client, mock_genai = _build(kb, context=_NoMedia())
    r = _suggest(client, messages=["Customer: warranty for X50?"])
    assert r.status_code == 200
    assert _extractions(mock_genai) == 0
    assert kb.queries == ["warranty for X50?"]


@respx.mock
def test_repeat_clicks_on_one_conversation_extract_once() -> None:
    """Suggest, then Ask, then Suggest again — one extraction, not three."""
    _mock_download()
    kb = _RecordingKnowledge()
    client, mock_genai = _build(kb)
    _suggest(client, "42")
    client.post(
        "/assist/ask",
        json={"conversation_id": "42", "messages": _DEICTIC, "question": "what now?"},
        headers=_HEADERS,
    )
    _suggest(client, "42")
    assert _extractions(mock_genai) == 1
    assert len(kb.queries) == 3
    assert all(q.startswith(_TERMS) for q in kb.queries)


@respx.mock
def test_a_different_conversation_extracts_again() -> None:
    _mock_download()
    client, mock_genai = _build(_RecordingKnowledge())
    _suggest(client, "42")
    _suggest(client, "99")
    assert _extractions(mock_genai) == 2


@respx.mock
def test_empty_extraction_is_cached_so_it_is_not_retried_forever() -> None:
    """Media that yields nothing concrete is the case that would otherwise pay
    full media cost on every click to learn "nothing" again."""
    _mock_download()
    kb = _RecordingKnowledge()
    client, mock_genai = _build(kb, terms="")
    _suggest(client, "42")
    _suggest(client, "42")
    assert _extractions(mock_genai) == 1
    # nothing extracted -> query is the customer's words alone
    assert not kb.queries[0].startswith(_TERMS)


# ---------------------------------------------------------------------------
# The cache itself
# ---------------------------------------------------------------------------


def test_cache_distinguishes_absent_from_empty() -> None:
    """`None` means "never extracted"; `""` means "extracted, found nothing".
    Collapsing them would re-extract on every click for silent media."""
    c = MediaTermsCache()
    assert c.get("a") is None
    c.put("a", "")
    assert c.get("a") == ""


def test_cache_expires_on_ttl() -> None:
    clock = {"t": 0.0}
    c = MediaTermsCache(ttl_seconds=10, time_fn=lambda: clock["t"])
    c.put("a", "X50")
    clock["t"] = 9
    assert c.get("a") == "X50"
    clock["t"] = 11
    assert c.get("a") is None


def test_cache_is_bounded() -> None:
    c = MediaTermsCache(max_entries=2)
    c.put("a", "1")
    c.put("b", "2")
    c.put("c", "3")
    assert c.get("a") is None  # oldest evicted
    assert c.get("c") == "3"


def test_cache_eviction_is_lru_not_fifo() -> None:
    c = MediaTermsCache(max_entries=2)
    c.put("a", "1")
    c.put("b", "2")
    c.get("a")  # touch a
    c.put("c", "3")
    assert c.get("a") == "1"  # survived because it was used
    assert c.get("b") is None


# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------


@respx.mock
def test_extraction_failure_still_returns_a_draft() -> None:
    _mock_download()
    kb = _RecordingKnowledge()
    mock_genai = MagicMock()

    def _respond(**kwargs):
        system = (kwargs.get("config") or {}).get("system_instruction", "")
        if "extract search keywords" in system:
            raise RuntimeError("gemini is down")
        out = MagicMock()
        out.text = "draft anyway"
        return out

    mock_genai.aio.models.generate_content = AsyncMock(side_effect=_respond)
    app = FastAPI()
    app.include_router(
        build_assist_router(
            settings=Settings(
                proton_backend_key=_KEY,
                assist_media_understanding_enabled=True,
                assist_media_max_bytes=1_000_000,
            ),
            knowledge_port=kb,
            genai_client=mock_genai,
            chatwoot_context=_FakeContext(),
        )
    )
    r = TestClient(app).post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _DEICTIC},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["draft"] == "draft anyway"


@respx.mock
def test_runaway_extraction_output_is_capped() -> None:
    _mock_download()
    kb = _RecordingKnowledge()
    client, _ = _build(kb, terms="x" * 5000)
    _suggest(client)
    assert kb.queries[0].count("x") == 200
