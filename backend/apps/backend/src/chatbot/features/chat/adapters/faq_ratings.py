"""Agent ratings of live-FAQ entries (FIX-23: "score the FAQ quality").

One rating per (faq, agent, conversation): the document id is derived from that
triple, so a double-click or a re-rate is an idempotent upsert, never a second
vote -- the dedupe lives in the key, not in a check-then-insert. Aggregation is
read-side (`summaries`), a full scan of a small collection; the admin listing
is the only caller.

Fail-open like the rest of the FAQ stack: a Firestore failure is logged and
degrades to "no ratings", never breaking `/kb/feedback` or the FAQ listing.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class FaqRatingPort(Protocol):
    async def rate(
        self, faq_id: str, agent_id: str, conversation_id: str, score: int, reason: str = ""
    ) -> None:
        """Upsert this agent's rating of `faq_id` in `conversation_id`. Never raises."""
        ...

    async def summaries(self) -> dict[str, tuple[float, int]]:
        """faq_id -> (average score, rating count). Never raises."""
        ...


def _summarise(rows: list[tuple[str, int]]) -> dict[str, tuple[float, int]]:
    acc: dict[str, list[int]] = {}
    for faq_id, score in rows:
        acc.setdefault(faq_id, []).append(score)
    return {k: (sum(v) / len(v), len(v)) for k, v in acc.items()}


class InMemoryFaqRatingStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], tuple[int, str]] = {}

    async def rate(
        self, faq_id: str, agent_id: str, conversation_id: str, score: int, reason: str = ""
    ) -> None:
        self._rows[(faq_id, agent_id, conversation_id)] = (score, reason)

    async def summaries(self) -> dict[str, tuple[float, int]]:
        return _summarise([(k[0], v[0]) for k, v in self._rows.items()])


class FirestoreFaqRatingStore:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore  # noqa: PLC0415 -- lazy: boot without the SDK

        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        self._collection_name = settings.faq_rating_collection

    def _collection(self) -> Any:
        return self._client.collection(self._collection_name)

    async def rate(
        self, faq_id: str, agent_id: str, conversation_id: str, score: int, reason: str = ""
    ) -> None:
        doc_id = hashlib.sha256(f"{faq_id}|{agent_id}|{conversation_id}".encode()).hexdigest()[:40]
        data = {
            "faq_id": faq_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "score": score,
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
        }

        def _write() -> None:
            self._collection().document(doc_id).set(data)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("faq_rating_write_failed", faq_id=faq_id, error=str(e))

    async def summaries(self) -> dict[str, tuple[float, int]]:
        def _read() -> list[tuple[str, int]]:
            out: list[tuple[str, int]] = []
            for doc in self._collection().stream():
                d = doc.to_dict() or {}
                out.append((str(d.get("faq_id", "")), int(d.get("score", 0))))
            return out

        try:
            return _summarise(await asyncio.to_thread(_read))
        except Exception as e:
            _log.error("faq_rating_read_failed", error=str(e))
            return {}


def build_faq_rating_store(settings: Settings) -> FaqRatingPort | None:
    try:
        return FirestoreFaqRatingStore(settings)
    except Exception as e:
        _log.warning("faq_rating_store_init_failed", error=str(e))
        return None
