"""The two ports the resolved-case index was missing (P7 task 11, job 2).

The load-bearing assertion in this file is the first one: the automatic
summariser must run the SAME prompt and the SAME code path as the
agent-triggered `POST /assist/summarize`, not a second summarisation prompt
that happens to look similar. It is checked by reading the `system_instruction`
the model actually received and comparing it to `_SUMMARIZE_SYSTEM` itself --
identity against the imported constant, so a reworded prompt cannot make this
test pass while the two paths have diverged.

Everything else here is about the failure modes, because resolving a case is
the agent's action and our summarisation is an add-on: an unbound summariser,
an unconfigured backend key, a Gemini failure and an unreadable transcript must
each produce nothing stored rather than an exception on the resolve path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from chatbot.features.assist.router import _SUMMARIZE_SYSTEM, build_assist_router
from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.resolved_case_adapters import (
    SUMMARIZE_PATH,
    AssistSummarizeAdapter,
    ChatwootTranscriptAdapter,
    find_summarize_endpoint,
)
from chatbot.features.chat.resolved_case_index import (
    InMemoryResolvedCaseRepository,
    ResolvedCaseIndexer,
)
from chatbot.platform.config import Settings


class _FakeKnowledge:
    async def search_kb(self, query: str, limit: int = 3) -> list[KbArticle]:
        return [KbArticle(title="t", content="c", url="u")]


def _assist(text: str = "- the customer's brake light was replaced") -> tuple[Any, MagicMock]:
    """A real assist router (the production factory) over a stub Gemini client."""
    genai = MagicMock()
    response = MagicMock()
    response.text = text
    genai.aio.models.generate_content = AsyncMock(return_value=response)
    app = FastAPI()
    router = build_assist_router(
        settings=Settings(proton_backend_key="k", assist_gemini_model="gemini-2.5-flash"),
        knowledge_port=_FakeKnowledge(),
        genai_client=genai,
    )
    app.include_router(router)
    return router, genai


class _RecordingTicketing:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        self.notes.append((ticket_id, text))


async def test_the_summariser_runs_the_real_assist_summarize_prompt():
    router, genai = _assist()
    adapter = AssistSummarizeAdapter(Settings(proton_backend_key="k"))
    assert adapter.bind(find_summarize_endpoint(router)) is True

    summary = await adapter.summarize("77", ["Customer: my brake light is out", "Agent: booked"])

    assert summary == "- the customer's brake light was replaced"
    kwargs = genai.aio.models.generate_content.await_args.kwargs
    # Identity against the imported constant, not a substring of prose: this is
    # what proves there is one summariser prompt in the codebase rather than
    # two that agree today. It also means the PII-omission sentence added to
    # _SUMMARIZE_SYSTEM reaches the automatic path for free.
    assert kwargs["config"]["system_instruction"] == _SUMMARIZE_SYSTEM
    assert "Do not include the customer's name" in kwargs["config"]["system_instruction"]
    # Both turns reached the model, numbered by the route's own formatter.
    assert "my brake light is out" in kwargs["contents"]
    assert "booked" in kwargs["contents"]


def test_the_summarize_endpoint_is_found_by_path_not_position():
    router, _ = _assist()
    paths = [getattr(route, "path", "") for route in router.routes]
    assert SUMMARIZE_PATH in paths
    assert find_summarize_endpoint(router) is not None
    # An unrelated router has no summariser, and that is a None rather than a
    # wrong endpoint or an exception.
    assert find_summarize_endpoint(FastAPI().router) is None


@pytest.mark.parametrize(
    ("bind", "key", "messages"),
    [
        (False, "k", ["Customer: hi"]),  # nothing bound: the assist router was never built
        (True, "", ["Customer: hi"]),  # no backend key: the route would answer 503
        (True, "k", []),  # nothing to summarise
    ],
)
async def test_the_summariser_returns_empty_instead_of_raising(bind, key, messages):
    router, genai = _assist()
    adapter = AssistSummarizeAdapter(Settings(proton_backend_key=key))
    if bind:
        adapter.bind(find_summarize_endpoint(router))

    assert await adapter.summarize("77", messages) == ""
    genai.aio.models.generate_content.assert_not_awaited()


async def test_a_gemini_failure_is_swallowed_into_an_empty_summary():
    router, genai = _assist()
    genai.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("429 quota"))
    adapter = AssistSummarizeAdapter(Settings(proton_backend_key="k"))
    adapter.bind(find_summarize_endpoint(router))

    assert await adapter.summarize("77", ["Customer: hi"]) == ""


async def test_the_transcript_is_labelled_and_excludes_notes_and_activity():
    captured: list[tuple[str, str, Any]] = []

    async def request(method: str, path: str, payload: Any = None) -> dict[str, Any]:
        captured.append((method, path, payload))
        return {
            "payload": [
                {"message_type": 0, "content": "brake light is out"},
                {"message_type": 1, "content": "booking you in"},
                # A private note -- and specifically the shape THIS feature
                # writes, so a re-resolve cannot summarise its own summary.
                {"message_type": 1, "content": "[Auto-summary] earlier", "private": True},
                {"message_type": 2, "content": "Conversation was resolved by Aisyah"},
                {"message_type": 3, "content": "template blast"},
                {"message_type": 0, "content": "   "},
                {"message_type": None, "content": "unknown type"},
                "not a dict",
            ]
        }

    lines = await ChatwootTranscriptAdapter(request).fetch_transcript("42")

    assert lines == ["Customer: brake light is out", "Agent: booking you in"]
    assert captured == [("GET", "/conversations/42/messages", None)]


async def test_the_transcript_keeps_only_the_trailing_turns():
    async def request(_method: str, _path: str, _payload: Any = None) -> dict[str, Any]:
        return {"payload": [{"message_type": 0, "content": str(i)} for i in range(10)]}

    lines = await ChatwootTranscriptAdapter(request, max_messages=3).fetch_transcript("42")
    assert lines == ["Customer: 7", "Customer: 8", "Customer: 9"]


@pytest.mark.parametrize("result", [None, {}, {"payload": "nope"}, [], "junk"])
async def test_an_unreadable_transcript_is_an_empty_list(result):
    async def request(_method: str, _path: str, _payload: Any = None) -> Any:
        return result

    assert await ChatwootTranscriptAdapter(request).fetch_transcript("42") == []


async def test_a_raising_transcript_read_is_an_empty_list():
    async def request(_method: str, _path: str, _payload: Any = None) -> Any:
        raise RuntimeError("connection reset")

    assert await ChatwootTranscriptAdapter(request).fetch_transcript("42") == []


async def test_the_two_ports_drive_a_real_resolve_end_to_end():
    """Both ports, the real indexer, the real assist route: one resolve in,
    one private note and one indexed summary out -- and the transcript itself
    provably not stored (the index holds summaries, never transcripts)."""
    router, _ = _assist(text="- brake light replaced under warranty")

    async def request(_method: str, _path: str, _payload: Any = None) -> dict[str, Any]:
        return {
            "payload": [
                {"message_type": 0, "content": "my plate is WXY 1234 and the light is out"},
                {"message_type": 1, "content": "replaced under warranty"},
            ]
        }

    summarizer = AssistSummarizeAdapter(Settings(proton_backend_key="k"))
    summarizer.bind(find_summarize_endpoint(router))
    ticketing = _RecordingTicketing()
    repo = InMemoryResolvedCaseRepository()

    indexer = ResolvedCaseIndexer(
        settings=Settings(
            resolved_case_index_enabled=True,
            auto_summary_on_resolve_enabled=True,
            proton_backend_key="k",
        ),
        ticketing_port=ticketing,  # type: ignore[arg-type]
        summarizer=summarizer,
        transcript_port=ChatwootTranscriptAdapter(request),
        repository=repo,
        embedder=None,
    )
    await indexer.handle_resolved(conversation_id="99")

    assert len(ticketing.notes) == 1
    conv_id, note = ticketing.notes[0]
    assert conv_id == "99"
    assert "brake light replaced under warranty" in note
    assert await repo.count() == 1
    hits = await repo.search([], limit=5)
    assert hits[0].record.summary == "- brake light replaced under warranty"
    # The raw turn -- plate number and all -- is not what got stored.
    assert "WXY 1234" not in hits[0].record.summary
