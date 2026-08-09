"""Resolved-case summarisation and indexing (P7 task 9, §3.6/§3.7).

On a conversation resolve, this module optionally does two INDEPENDENT
things, sharing a single summariser call between them:

1. **Auto-summary** (`Settings.auto_summary_on_resolve_enabled`): fire the
   existing agent-triggered summariser and post the result as a PRIVATE note
   on the conversation, via the existing `TicketingPort.add_private_note` --
   the same path patch 0002 already uses, so this never risks sending the
   customer a recap of their own case (the private-vs-public bug class the
   escalation work already had to fix once).
2. **Resolved-case index** (`Settings.resolved_case_index_enabled`): write a
   compact record -- the SUMMARY, never the transcript -- into a pgvector
   table (`resolved_case_summaries`) that lives in the SAME database as the
   operator-authored KB (`kb_documents`/`kb_chunks`) but in its OWN
   SQLAlchemy declarative Base and its OWN table. There is no foreign key,
   join, or shared metadata between the two: a resolved-case purge
   (`ResolvedCaseRepository.purge`) is a bare `DELETE FROM
   resolved_case_summaries` and has no code path, reference, or argument
   that could ever reach `kb_documents`/`kb_chunks`. That containment is
   what makes shipping machine-generated content into the same store
   reversible -- an operator can wipe every auto-generated summary without
   touching a single curated FAQ entry.

Both flags default False (owned by a prior wave's `config.py`/`example.env`
change -- this module reads them, never defines or overrides them) and are
independent of each other, not nested: they share this one resolve-event
hook, not a dependency relationship. With both off, `handle_resolved`
returns before touching any collaborator (no summariser call, no note, no
index write) -- byte-identical to pre-P7 resolve handling.

Every collaborator (`SummarizerPort`, `TranscriptPort`, `ResolvedCaseRepository`,
`Embedder`) is optional and every awaited call is caught and logged:
resolving a conversation is the agent's action, already complete by the time
`handle_resolved` runs (see the router's hook point); everything in this
module is a best-effort add-on that must never turn a successful resolve
into an error, and never prevents or reverses the resolve itself.

Suggestions drawn from this index MUST be labelled as such wherever they
reach an agent (`ResolvedCaseHit.source_label`, always `RESOLVED_CASE_SOURCE_
LABEL`): a resolved-case summary is what a colleague did last month, not
approved guidance, and without a visible label machine-generated content
silently acquires the curated KB's authority.

PII -- READ THIS BEFORE RELYING ON THIS MODULE FOR ANYTHING PRIVACY-RELATED:

    This module does NOT mask PII. It stores a conversation SUMMARY instead
    of the raw transcript, and the summariser prompt (owned by the sibling
    `/assist/summarize` endpoint, not by this file) is instructed to omit
    customer identifiers. That is a MITIGATION, not a GUARANTEE: an
    instruction to the model is a request, not an enforcement mechanism, and
    a summary of a conversation can still carry a name, a plate number, a
    phone number or an address if the model includes one -- nothing in this
    module inspects, redacts, or validates the summary text before it is
    stored or posted. The real fix is gap R16 (full PII masking), which is
    blocked on Q7. `resolved_case_index_enabled` defaults to False for
    exactly this reason; do not present this module's behaviour as "PII is
    masked" in any UI copy, doc, or incident write-up.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

import structlog
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Text, delete, func, select
from sqlalchemy import text as sa_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from chatbot.features.chat.ports import TicketingPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Matches kb_db.EMBEDDING_DIM (text-embedding-004). Kept as this module's OWN
# constant rather than importing kb_db's -- this table's schema is
# deliberately not coupled to the KB's, see the module docstring.
_EMBEDDING_DIM = 768

# Prefixes the private note so an agent scrolling the conversation can tell
# an auto-summary from a hand-typed note at a glance.
_NOTE_HEADER = "[Auto-summary — resolved case]"

# The label every ResolvedCaseHit carries. Distinct from the KB adapter's
# `source_type="pgvector"` (see adapters/pgvector_knowledge.py) on purpose --
# a caller merging both result sets must be able to tell them apart and must
# never present a resolved-case hit with the curated KB's authority.
RESOLVED_CASE_SOURCE_LABEL = "resolved_case"

# Human-readable disclaimer a suggestion panel can show next to a
# resolved-case hit. Exported so a later wiring wave has one canonical string
# rather than each surface inventing its own wording.
RESOLVED_CASE_DISCLAIMER = "From a previously resolved case — not verified guidance."


# --- Data shapes -------------------------------------------------------


@dataclass(frozen=True)
class ResolvedCaseRecord:
    """What gets indexed. Deliberately carries only the SUMMARY plus a few
    classification fields -- there is no `messages`/`transcript` field on
    this dataclass, so storing the raw transcript here is not just
    discouraged, it is not representable."""

    conversation_id: str
    summary: str
    category: str | None = None
    subcategory: str | None = None
    case_detail: str | None = None
    resolution: str | None = None
    outcome: str | None = None


@dataclass(frozen=True)
class ResolvedCaseHit:
    """A search hit, always labelled (`source_label`) so a caller merging
    this with curated-KB results cannot accidentally present it as
    approved guidance."""

    record: ResolvedCaseRecord
    score: float
    source_label: str = RESOLVED_CASE_SOURCE_LABEL


# --- Ports (all optional collaborators; a later wave supplies real ones) --


class SummarizerPort(Protocol):
    """Wraps the EXISTING `POST /assist/summarize` path (see
    `features/assist/router.py`). This module makes zero direct Gemini
    calls of its own -- a concrete adapter is expected to call that
    endpoint's logic (in-process or over HTTP), not stand up a second
    summarisation prompt."""

    async def summarize(self, conversation_id: str, messages: list[str]) -> str:
        """Return a summary string ("" on failure -- callers should also be
        prepared for this to raise; `ResolvedCaseIndexer` catches it)."""
        ...


class TranscriptPort(Protocol):
    """Fetches the conversation's messages for the summariser. Kept as its
    own small port (rather than this module reaching into a Chatwoot
    adapter directly) so this file has no dependency on any specific CRM
    client."""

    async def fetch_transcript(self, conversation_id: str) -> list[str]: ...


class Embedder(Protocol):
    """Injectable text->vector embedder. Structurally identical to
    `adapters.live_faq.Embedder` / the KB's embedder -- duck-typed locally
    rather than imported so this file has no import-time coupling to a
    module a sibling task edits."""

    async def embed(self, text: str) -> list[float]: ...


class ResolvedCaseRepository(Protocol):
    async def add(self, record: ResolvedCaseRecord, embedding: list[float]) -> str:
        """Persist one record. Always an INSERT -- re-resolving the same
        conversation appends a new row, never overwrites a prior one (the
        first summary remains a true record of the first resolution)."""
        ...

    async def search(self, embedding: list[float], limit: int) -> list[ResolvedCaseHit]:
        """Cosine-nearest records, each labelled via `ResolvedCaseHit.
        source_label`."""
        ...

    async def purge(self) -> int:
        """Delete every indexed record. Returns the number deleted. Must
        have no code path that can reach any other table/store -- see the
        module docstring's containment argument."""
        ...


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)


class InMemoryResolvedCaseRepository:
    """Hermetic test double. Never touches Postgres; used by every test in
    this package's suite and safe as a real (if unpersisted) fallback."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[ResolvedCaseRecord, list[float]]] = {}

    async def add(self, record: ResolvedCaseRecord, embedding: list[float]) -> str:
        row_id = uuid.uuid4().hex
        self._rows[row_id] = (record, embedding)
        return row_id

    async def search(self, embedding: list[float], limit: int) -> list[ResolvedCaseHit]:
        hits = [
            ResolvedCaseHit(record=record, score=_cosine(embedding, emb))
            for record, emb in self._rows.values()
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    async def purge(self) -> int:
        n = len(self._rows)
        self._rows.clear()
        return n

    async def count(self) -> int:
        """Test convenience, not part of the Protocol."""
        return len(self._rows)


# --- pgvector-backed implementation -------------------------------------
#
# A dedicated declarative Base (NOT kb_db.Base) so this table's schema and
# migration path never share metadata with the KB's. `build_engine`/
# `build_session_maker` are still reused from kb_db.py -- same async-driver
# upgrade, same engine/session machinery, same physical database (whatever
# `Settings.knowledge_database_url` points at) -- there is no second
# database story here, only a second table.


class _ResolvedCaseBase(DeclarativeBase):
    pass


class ResolvedCaseSummaryRow(_ResolvedCaseBase):
    __tablename__ = "resolved_case_summaries"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    subcategory: Mapped[str | None] = mapped_column(Text)
    case_detail: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


async def init_resolved_case_index_db(engine: AsyncEngine) -> None:
    """Create the `resolved_case_summaries` table (own Base, own index).

    Deliberately NOT `kb_db.init_kb_db` / `kb_db.Base.metadata` -- see the
    module docstring on namespace separation. Idempotent (`IF NOT EXISTS`
    throughout), matching `kb_db.py`'s own pattern; safe to call even when
    the `vector` extension already exists (created by `kb_db.init_kb_db`,
    or here first if this module's engine start-up runs first).
    """
    async with engine.begin() as conn:
        await conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(_ResolvedCaseBase.metadata.create_all)
        await conn.execute(
            sa_text(
                "CREATE INDEX IF NOT EXISTS resolved_case_summaries_embedding_idx "
                "ON resolved_case_summaries USING hnsw (embedding vector_cosine_ops)"
            )
        )


class PgResolvedCaseRepository:
    """pgvector-backed `ResolvedCaseRepository`. Opens a short-lived session
    per call, matching `PgKbRepository`'s pattern in `kb_repository.py`.

    `purge()` is `DELETE FROM resolved_case_summaries` via this table's own
    ORM mapping -- there is no join, subquery, or reference to
    `KbDocument`/`KbChunk` anywhere in this class, so it is not merely
    unlikely but structurally impossible for a call to `purge()` to affect
    the authored FAQ corpus.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = session_maker

    async def add(self, record: ResolvedCaseRecord, embedding: list[float]) -> str:
        row_id = uuid.uuid4().hex
        async with self._sm() as s:
            s.add(
                ResolvedCaseSummaryRow(
                    id=row_id,
                    conversation_id=record.conversation_id,
                    summary=record.summary,
                    category=record.category,
                    subcategory=record.subcategory,
                    case_detail=record.case_detail,
                    resolution=record.resolution,
                    outcome=record.outcome,
                    embedding=embedding,
                )
            )
            await s.commit()
        return row_id

    async def search(self, embedding: list[float], limit: int) -> list[ResolvedCaseHit]:
        async with self._sm() as s:
            dist = ResolvedCaseSummaryRow.embedding.cosine_distance(embedding).label("dist")
            rows = (
                await s.execute(select(ResolvedCaseSummaryRow, dist).order_by(dist).limit(limit))
            ).all()
        return [
            ResolvedCaseHit(
                record=ResolvedCaseRecord(
                    conversation_id=row.conversation_id,
                    summary=row.summary,
                    category=row.category,
                    subcategory=row.subcategory,
                    case_detail=row.case_detail,
                    resolution=row.resolution,
                    outcome=row.outcome,
                ),
                score=1.0 - float(d),
            )
            for row, d in rows
        ]

    async def purge(self) -> int:
        # Count-then-delete (rather than reading the DELETE result's
        # `rowcount`) so the return type is a plain `Result[Any]` throughout
        # -- no dependency on the DBAPI-level `CursorResult` shape.
        async with self._sm() as s:
            count = (
                await s.execute(select(func.count()).select_from(ResolvedCaseSummaryRow))
            ).scalar_one()
            await s.execute(delete(ResolvedCaseSummaryRow))
            await s.commit()
        return int(count)


# --- Orchestrator --------------------------------------------------------


def _format_private_note(summary: str) -> str:
    return f"{_NOTE_HEADER}\n{summary}"


class ResolvedCaseIndexer:
    """Orchestrates the resolve-event add-on: summarise once, then fan out
    to the private note and/or the pgvector write, each gated by its own
    Settings flag. See the module docstring for the full flag semantics,
    the containment argument, and the PII caveat.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        ticketing_port: TicketingPort,
        summarizer: SummarizerPort | None = None,
        transcript_port: TranscriptPort | None = None,
        repository: ResolvedCaseRepository | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self._settings = settings
        self._ticketing_port = ticketing_port
        self._summarizer = summarizer
        self._transcript_port = transcript_port
        self._repository = repository
        self._embedder = embedder

    async def handle_resolved(
        self,
        *,
        conversation_id: str,
        category: str | None = None,
        subcategory: str | None = None,
        case_detail: str | None = None,
        resolution: str | None = None,
        outcome: str | None = None,
    ) -> None:
        """Call once per actual resolve (see the router's hook point --
        NOT on every status-change webhook). Never raises: every awaited
        collaborator call is wrapped, and resolving is already complete by
        the time this runs, so nothing in here can undo it."""
        note_wanted = self._settings.auto_summary_on_resolve_enabled
        index_wanted = self._settings.resolved_case_index_enabled
        if not note_wanted and not index_wanted:
            return  # both flags off: zero collaborator calls, unchanged behaviour.

        if self._summarizer is None:
            _log.info("resolved_case_index_no_summarizer", conversation_id=conversation_id)
            return

        try:
            messages = (
                await self._transcript_port.fetch_transcript(conversation_id)
                if self._transcript_port is not None
                else []
            )
            summary = await self._summarizer.summarize(conversation_id, messages)
        except Exception as e:
            # Fail-open: summarisation is an add-on, the resolve already happened.
            _log.error(
                "resolved_case_summarize_failed", conversation_id=conversation_id, error=str(e)
            )
            return

        summary = (summary or "").strip()
        if not summary:
            return

        if note_wanted:
            await self._post_note(conversation_id, summary)
        if index_wanted:
            await self._index(
                conversation_id,
                summary,
                category=category,
                subcategory=subcategory,
                case_detail=case_detail,
                resolution=resolution,
                outcome=outcome,
            )

    async def _post_note(self, conversation_id: str, summary: str) -> None:
        try:
            await self._ticketing_port.add_private_note(
                conversation_id, _format_private_note(summary)
            )
        except Exception as e:
            _log.error("resolved_case_note_failed", conversation_id=conversation_id, error=str(e))

    async def _index(
        self,
        conversation_id: str,
        summary: str,
        *,
        category: str | None,
        subcategory: str | None,
        case_detail: str | None,
        resolution: str | None,
        outcome: str | None,
    ) -> None:
        if self._repository is None:
            _log.info("resolved_case_index_no_repository", conversation_id=conversation_id)
            return
        try:
            embedding = await self._embedder.embed(summary) if self._embedder is not None else []
            record = ResolvedCaseRecord(
                conversation_id=conversation_id,
                summary=summary,
                category=category,
                subcategory=subcategory,
                case_detail=case_detail,
                resolution=resolution,
                outcome=outcome,
            )
            await self._repository.add(record, embedding)
        except Exception as e:
            _log.error(
                "resolved_case_index_write_failed", conversation_id=conversation_id, error=str(e)
            )
