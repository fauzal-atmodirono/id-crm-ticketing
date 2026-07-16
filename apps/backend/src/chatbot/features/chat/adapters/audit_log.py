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

    async def append(self, entry: AuditEntry) -> None:
        def _write() -> None:
            self._collection().add(asdict(entry))

        await asyncio.to_thread(_write)

    async def list_for_ticket(self, ticket_id: str) -> list[AuditEntry]:
        def _query() -> list[AuditEntry]:
            docs = self._collection().where("ticket_id", "==", ticket_id).stream()
            rows = [AuditEntry(**doc.to_dict()) for doc in docs]
            return sorted(rows, key=lambda r: r.at)

        return await asyncio.to_thread(_query)


def build_audit_log(settings: Settings) -> AuditLogPort:
    """Firestore when handoff_store is firestore; else in-memory (dev/tests)."""
    if settings.handoff_store == "firestore":
        try:
            return FirestoreAuditLog(settings)
        except Exception as e:
            _log.warning("firestore_audit_log_init_failed_falling_back_to_memory", error=str(e))
    return InMemoryAuditLog()
