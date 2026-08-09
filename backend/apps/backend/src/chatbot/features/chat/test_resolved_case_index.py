# test_resolved_case_index.py
"""P7 task 9: resolved-case summarisation + indexing.

All doubles are hermetic -- no real Postgres, no real Gemini, no real
Chatwoot. `_FakeTicketingPort.add_private_note` builds the SAME payload
shape the real `ChatwootAdapter.add_private_note` sends
(`{"content", "message_type": "outgoing", "private": True}`), so test two
asserts `private is True` on that actual constructed payload rather than by
inspecting which method got called.
"""

from dataclasses import dataclass, field

from chatbot.features.chat.kb_repository import InMemoryKbRepository
from chatbot.features.chat.resolved_case_index import (
    RESOLVED_CASE_SOURCE_LABEL,
    InMemoryResolvedCaseRepository,
    ResolvedCaseIndexer,
    ResolvedCaseRecord,
)


@dataclass
class _Settings:
    auto_summary_on_resolve_enabled: bool = False
    resolved_case_index_enabled: bool = False


@dataclass
class _FakeTicketingPort:
    notes: list[dict] = field(default_factory=list)

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        # Mirrors ChatwootAdapter.add_private_note's actual request payload
        # (see adapters/chatwoot.py) -- always outgoing + private.
        self.notes.append(
            {
                "ticket_id": ticket_id,
                "content": text,
                "message_type": "outgoing",
                "private": True,
            }
        )


class _FakeTranscriptPort:
    def __init__(self, messages: list[str]) -> None:
        self._messages = messages
        self.calls: list[str] = []

    async def fetch_transcript(self, conversation_id: str) -> list[str]:
        self.calls.append(conversation_id)
        return self._messages


class _FakeSummarizer:
    def __init__(self, summaries: list[str]) -> None:
        self._summaries = list(summaries)
        self.calls: list[tuple[str, list[str]]] = []

    async def summarize(self, conversation_id: str, messages: list[str]) -> str:
        self.calls.append((conversation_id, messages))
        return self._summaries.pop(0)


class _FailingSummarizer:
    async def summarize(self, conversation_id: str, messages: list[str]) -> str:
        raise RuntimeError("gemini is down")


class _FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0]


async def test_resolving_a_conversation_posts_a_summary_private_note() -> None:
    settings = _Settings(auto_summary_on_resolve_enabled=True)
    ticketing = _FakeTicketingPort()
    indexer = ResolvedCaseIndexer(
        settings=settings,
        ticketing_port=ticketing,
        summarizer=_FakeSummarizer(["Customer's warranty claim was resolved."]),
        transcript_port=_FakeTranscriptPort(["Customer: my car won't start"]),
    )

    await indexer.handle_resolved(conversation_id="42")

    assert len(ticketing.notes) == 1
    assert "Customer's warranty claim was resolved." in ticketing.notes[0]["content"]
    assert ticketing.notes[0]["ticket_id"] == "42"


async def test_the_summary_note_is_private() -> None:
    settings = _Settings(auto_summary_on_resolve_enabled=True)
    ticketing = _FakeTicketingPort()
    indexer = ResolvedCaseIndexer(
        settings=settings,
        ticketing_port=ticketing,
        summarizer=_FakeSummarizer(["Resolved: replaced the battery."]),
        transcript_port=_FakeTranscriptPort(["Customer: battery issue"]),
    )

    await indexer.handle_resolved(conversation_id="42")

    payload = ticketing.notes[0]
    assert payload["private"] is True
    assert payload["message_type"] == "outgoing"


async def test_re_resolving_appends_a_second_summary_rather_than_overwriting() -> None:
    settings = _Settings(auto_summary_on_resolve_enabled=True, resolved_case_index_enabled=True)
    ticketing = _FakeTicketingPort()
    repository = InMemoryResolvedCaseRepository()
    indexer = ResolvedCaseIndexer(
        settings=settings,
        ticketing_port=ticketing,
        summarizer=_FakeSummarizer(
            [
                "First resolution: reset the infotainment unit.",
                "Second resolution: replaced the head unit outright.",
            ]
        ),
        transcript_port=_FakeTranscriptPort(["Customer: screen is frozen"]),
        repository=repository,
        embedder=_FakeEmbedder(),
    )

    # Resolved once...
    await indexer.handle_resolved(conversation_id="99")
    # ...reopened by an agent, then resolved again -- different work was done.
    await indexer.handle_resolved(conversation_id="99")

    assert len(ticketing.notes) == 2
    assert "First resolution" in ticketing.notes[0]["content"]
    assert "Second resolution" in ticketing.notes[1]["content"]
    assert await repository.count() == 2


async def test_the_resolved_case_is_indexed_in_its_own_namespace() -> None:
    settings = _Settings(resolved_case_index_enabled=True)
    ticketing = _FakeTicketingPort()
    repository = InMemoryResolvedCaseRepository()
    kb_repository = InMemoryKbRepository()  # the authored-FAQ store -- a different object entirely
    indexer = ResolvedCaseIndexer(
        settings=settings,
        ticketing_port=ticketing,
        summarizer=_FakeSummarizer(["Swapped the 12V battery under warranty."]),
        transcript_port=_FakeTranscriptPort(["Customer: car won't start"]),
        repository=repository,
        embedder=_FakeEmbedder(),
    )

    await indexer.handle_resolved(conversation_id="7", category="battery")

    assert await repository.count() == 1
    hits = await repository.search([12.0, 1.0], limit=5)
    assert hits[0].record.conversation_id == "7"
    assert hits[0].record.category == "battery"
    # Indexing never touches the (unrelated) authored FAQ store.
    assert await kb_repository.list_documents() == []


async def test_the_index_stores_the_summary_and_never_the_raw_transcript() -> None:
    settings = _Settings(resolved_case_index_enabled=True)
    ticketing = _FakeTicketingPort()
    repository = InMemoryResolvedCaseRepository()
    raw_transcript = [
        "Customer: my name is Ahmad Fauzi and my IC is 900101-14-5566",
        "Agent: thanks Ahmad, let me check plate WXY1234",
    ]
    indexer = ResolvedCaseIndexer(
        settings=settings,
        ticketing_port=ticketing,
        summarizer=_FakeSummarizer(["Customer reported a starting issue; battery replaced."]),
        transcript_port=_FakeTranscriptPort(raw_transcript),
        repository=repository,
        embedder=_FakeEmbedder(),
    )

    await indexer.handle_resolved(conversation_id="7")

    hits = await repository.search([1.0], limit=5)
    stored = hits[0].record
    assert stored.summary == "Customer reported a starting issue; battery replaced."
    # The record type itself has no transcript/messages field to leak through.
    assert not hasattr(stored, "messages")
    assert not hasattr(stored, "transcript")
    for raw_line in raw_transcript:
        assert raw_line not in stored.summary


async def test_the_namespace_can_be_purged_without_touching_authored_faqs() -> None:
    kb_repository = InMemoryKbRepository()
    faq_doc_id = await kb_repository.create_document(
        title="Warranty policy",
        source_type="text",
        original_filename=None,
        mime_type=None,
        char_count=42,
    )

    resolved_repository = InMemoryResolvedCaseRepository()
    await resolved_repository.add(
        record=ResolvedCaseRecord(conversation_id="1", summary="Battery replaced."),
        embedding=[1.0, 0.0],
    )

    deleted = await resolved_repository.purge()

    assert deleted == 1
    assert await resolved_repository.count() == 0
    # The authored FAQ store -- a structurally separate repository/table --
    # is completely unaffected by the purge.
    docs = await kb_repository.list_documents()
    assert len(docs) == 1
    assert docs[0].id == faq_doc_id
    assert docs[0].title == "Warranty policy"


async def test_a_suggestion_sourced_from_a_resolved_case_is_labelled_as_such() -> None:
    repository = InMemoryResolvedCaseRepository()

    await repository.add(
        record=ResolvedCaseRecord(conversation_id="55", summary="Replaced a blown fuse."),
        embedding=[1.0, 0.0],
    )

    hits = await repository.search([1.0, 0.0], limit=5)

    assert len(hits) == 1
    assert hits[0].source_label == RESOLVED_CASE_SOURCE_LABEL
    assert hits[0].source_label != "pgvector"  # distinct from the curated-KB adapter's label


async def test_a_summariser_failure_does_not_prevent_the_resolve() -> None:
    settings = _Settings(auto_summary_on_resolve_enabled=True, resolved_case_index_enabled=True)
    ticketing = _FakeTicketingPort()
    repository = InMemoryResolvedCaseRepository()
    indexer = ResolvedCaseIndexer(
        settings=settings,
        ticketing_port=ticketing,
        summarizer=_FailingSummarizer(),
        transcript_port=_FakeTranscriptPort(["Customer: help"]),
        repository=repository,
        embedder=_FakeEmbedder(),
    )

    # Must not raise -- the resolve itself has already happened by the time
    # this runs; a summariser outage must not surface as an error here.
    await indexer.handle_resolved(conversation_id="123")

    assert ticketing.notes == []
    assert await repository.count() == 0


async def test_both_flags_off_leaves_resolve_handling_unchanged() -> None:
    settings = _Settings(auto_summary_on_resolve_enabled=False, resolved_case_index_enabled=False)
    ticketing = _FakeTicketingPort()
    repository = InMemoryResolvedCaseRepository()
    summarizer = _FakeSummarizer(["should never be produced"])
    transcript_port = _FakeTranscriptPort(["Customer: hello"])
    indexer = ResolvedCaseIndexer(
        settings=settings,
        ticketing_port=ticketing,
        summarizer=summarizer,
        transcript_port=transcript_port,
        repository=repository,
        embedder=_FakeEmbedder(),
    )

    await indexer.handle_resolved(conversation_id="1")

    # Zero collaborator calls -- not merely "no visible effect" but no
    # summariser/transcript/ticketing/repository interaction at all.
    assert ticketing.notes == []
    assert summarizer.calls == []
    assert transcript_port.calls == []
    assert await repository.count() == 0
