"""Case audit-trail stores: in-memory (tests/dev) + Firestore (persistent)."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

from chatbot.features.chat.ports import AuditEntry, AuditLogPort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class InMemoryAuditLog(AuditLogPort):
    """Volatile, single-process audit log. Use for tests and dev."""

    def __init__(self) -> None:
        self._by_ticket: dict[str, list[AuditEntry]] = {}

    async def append(self, entry: AuditEntry) -> None:
        self._by_ticket.setdefault(entry.ticket_id, []).append(entry)

    async def list_for_ticket(self, ticket_id: str) -> list[AuditEntry]:
        return list(self._by_ticket.get(ticket_id, []))

    async def list_filtered(
        self,
        *,
        actor: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]:
        all_entries = [e for entries in self._by_ticket.values() for e in entries]
        if actor is not None:
            all_entries = [e for e in all_entries if e.actor == actor]
        if from_ts is not None:
            all_entries = [e for e in all_entries if e.at >= from_ts]
        if to_ts is not None:
            all_entries = [e for e in all_entries if e.at <= to_ts]
        all_entries.sort(key=lambda e: e.at, reverse=True)
        return all_entries[:limit]


class FirestoreAuditLog(AuditLogPort):
    """One document per event under `<collection>/<auto-id>`; queried by ticket_id."""

    def __init__(self, settings: Settings) -> None:
        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        self._collection_name = settings.firestore_audit_collection
        _log.info("firestore_audit_log_initialized", collection=self._collection_name)

    def _collection(self) -> Any:
        return self._client.collection(self._collection_name)

    def _to_entry(self, doc: Any) -> AuditEntry:
        """Convert a Firestore document to an AuditEntry.

        Unknown keys are dropped rather than passed to the constructor. Old
        documents are already safe (the new fields default), but a document
        written by a NEWER build than the one reading it would otherwise raise
        TypeError and take the whole audit query down -- during a rollback,
        exactly when the trail is most wanted.
        """
        data = doc.to_dict() or {}
        known = AuditEntry.__dataclass_fields__
        return AuditEntry(**{k: v for k, v in data.items() if k in known})

    async def append(self, entry: AuditEntry) -> None:
        def _write() -> None:
            self._collection().add(asdict(entry))

        await asyncio.to_thread(_write)

    async def list_for_ticket(self, ticket_id: str) -> list[AuditEntry]:
        def _query() -> list[AuditEntry]:
            docs = self._collection().where("ticket_id", "==", ticket_id).stream()
            rows = [self._to_entry(doc) for doc in docs]
            return sorted(rows, key=lambda r: r.at)

        return await asyncio.to_thread(_query)

    async def list_filtered(
        self,
        *,
        actor: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]:
        def _query() -> list[AuditEntry]:
            query = self._collection()
            if actor is not None:
                query = query.where("actor", "==", actor)
            if from_ts is not None:
                query = query.where("at", ">=", from_ts)
            if to_ts is not None:
                query = query.where("at", "<=", to_ts)
            docs = query.stream()
            entries = [self._to_entry(doc) for doc in docs]
            entries.sort(key=lambda e: e.at, reverse=True)
            return entries[:limit]

        return await asyncio.to_thread(_query)


def build_audit_log(settings: Settings) -> AuditLogPort:
    """Firestore when handoff_store is firestore; else in-memory (dev/tests)."""
    if settings.handoff_store == "firestore":
        try:
            return FirestoreAuditLog(settings)
        except Exception as e:
            _log.warning("firestore_audit_log_init_failed_falling_back_to_memory", error=str(e))
    return InMemoryAuditLog()
