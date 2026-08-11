"""KB retrieval falling back to the media when the text retrieves nothing.

The gap this closes, seen live on the proton tenant 2026-08-11: a customer
sends a video and asks "what model is this? / this one". The media reached
Gemini and it correctly identified a Proton X50 — but the KB query was built
from the words alone, retrieved zero articles, and the draft came back
"Saya tidak mempunyai maklumat mengenai Proton X50 dalam pangkalan data saya".
Identified the car, then said it knew nothing about it.

The expensive half of these tests is the assertion that the extra Gemini call
does NOT happen on the common path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.router import build_assist_router
from chatbot.features.chat.models import KbArticle
from chatbot.platform.config import Settings

_URL = "https://crm.example.test/rails/active_storage/blobs/redirect/abc/clip.mp4"
_KEY = "testkey"
_HEADERS = {"x-api-key": _KEY}

# The live failure: the question lives entirely in the attachment.
_DEICTIC = [
    {"role": "customer", "content": "what model is this?", "attachments": []},
    {"role": "customer", "content": "this one", "attachments": [{"file_type": "video"}]},
]


class _RecordingKnowledge:
    """Returns hits only for queries containing a term it knows."""

    def __init__(self, knows: str | None = None) -> None:
        self._knows = knows
        self.queries: list[str] = []

    async def search_kb(self, query: str, limit: int = 3) -> list:
        self.queries.append(query)
        if self._knows and self._knows.lower() in query.lower():
            return [KbArticle(title="X50 warranty", content="60 months", url="http://faq/1")]
        return []


class _FakeContext:
    async def get_messages(self, conversation_id: str) -> list[dict]:
        return [
            {
                "message_type": 0,
                "private": False,
                "attachments": [{"file_type": "video", "data_url": _URL}],
            }
        ]


def _build(knowledge: _RecordingKnowledge, *, terms: str = "Proton X50, front bumper"):
    """Router whose Gemini stub returns `terms` for the extraction call."""
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
            chatwoot_context=_FakeContext(),
        )
    )
    return TestClient(app), mock_genai


def _mock_download() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(200, content=b"clip", headers={"content-type": "video/mp4"})
    )


def _extraction_calls(mock_genai: MagicMock) -> int:
    return sum(
        1
        for c in mock_genai.aio.models.generate_content.call_args_list
        if "extract search keywords" in (c.kwargs.get("config") or {}).get("system_instruction", "")
    )


# ---------------------------------------------------------------------------
# The common path must not get more expensive
# ---------------------------------------------------------------------------


@respx.mock
def test_no_extra_call_when_text_retrieval_already_works() -> None:
    _mock_download()
    kb = _RecordingKnowledge(knows="warranty")
    client, mock_genai = _build(kb)
    r = client.post(
        "/assist/suggest",
        json={
            "conversation_id": "42",
            "messages": [{"role": "customer", "content": "warranty for X50?"}],
        },
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert _extraction_calls(mock_genai) == 0
    assert len(kb.queries) == 1


def test_no_extra_call_when_there_is_no_media() -> None:
    kb = _RecordingKnowledge()
    client, mock_genai = _build(kb)
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": ["Customer: hello"]},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert _extraction_calls(mock_genai) == 0
    assert len(kb.queries) == 1  # searched once, found nothing, gave up


# ---------------------------------------------------------------------------
# The failing path now recovers
# ---------------------------------------------------------------------------


@respx.mock
def test_zero_results_plus_media_triggers_a_second_search_on_media_terms() -> None:
    _mock_download()
    kb = _RecordingKnowledge(knows="X50")
    client, mock_genai = _build(kb)
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _DEICTIC},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert _extraction_calls(mock_genai) == 1
    assert len(kb.queries) == 2
    # first query is the customer's words and finds nothing
    assert "X50" not in kb.queries[0]
    # retry leads with the media terms, keeping the original text after
    assert kb.queries[1].startswith("Proton X50, front bumper")
    assert "this one" in kb.queries[1]
    # and the article the retry found is surfaced to the agent
    assert r.json()["sources"][0]["title"] == "X50 warranty"


@respx.mock
def test_recovered_context_reaches_the_prompt() -> None:
    _mock_download()
    client, mock_genai = _build(_RecordingKnowledge(knows="X50"))
    client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _DEICTIC},
        headers=_HEADERS,
    )
    final = mock_genai.aio.models.generate_content.call_args_list[-1]
    assert "X50 warranty" in final.kwargs["config"]["system_instruction"]


@respx.mock
def test_ask_uses_the_same_fallback() -> None:
    _mock_download()
    kb = _RecordingKnowledge(knows="X50")
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
    assert _extraction_calls(mock_genai) == 1
    assert len(kb.queries) == 2


# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------


@respx.mock
def test_empty_extraction_does_not_trigger_a_pointless_second_search() -> None:
    """The prompt tells the model to return nothing when it sees nothing
    concrete. Honour that instead of searching on an empty string."""
    _mock_download()
    kb = _RecordingKnowledge()
    client, _ = _build(kb, terms="")
    r = client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _DEICTIC},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert len(kb.queries) == 1


@respx.mock
def test_extraction_failure_still_returns_a_draft() -> None:
    _mock_download()
    kb = _RecordingKnowledge()
    mock_genai = MagicMock()
    calls = {"n": 0}

    def _respond(**kwargs):
        system = (kwargs.get("config") or {}).get("system_instruction", "")
        if "extract search keywords" in system:
            calls["n"] += 1
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
    assert calls["n"] == 1


@respx.mock
def test_runaway_extraction_output_is_capped() -> None:
    _mock_download()
    kb = _RecordingKnowledge()
    client, _ = _build(kb, terms="x" * 5000)
    client.post(
        "/assist/suggest",
        json={"conversation_id": "42", "messages": _DEICTIC},
        headers=_HEADERS,
    )
    assert len(kb.queries) == 2
    assert kb.queries[1].count("x") == 200
