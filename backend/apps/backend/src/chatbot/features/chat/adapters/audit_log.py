"""Case audit-trail stores: in-memory (tests/dev) + Firestore (persistent).

Append-only by construction: the port has no update or delete, and no route
exposes one (RFP 2.2.5 tamper-proof history). Retention purging is a separate,
report-only job (features/authz/audit_purge.py)."""

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


def _apply_event_filters(
    entries: list[AuditEntry],
    event_type: str | None,
    actor_user_id: int | None,
    target: str | None,
) -> list[AuditEntry]:
    if event_type is not None:
        entries = [e for e in entries if e.event_type == event_type]
    if actor_user_id is not None:
        entries = [e for e in entries if e.actor_user_id == actor_user_id]
    if target is not None:
        entries = [e for e in entries if e.target == target]
    return entries


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
        event_type: str | None = None,
        actor_user_id: int | None = None,
        target: str | None = None,
    ) -> list[AuditEntry]:
        all_entries = [e for entries in self._by_ticket.values() for e in entries]
        if actor is not None:
            all_entries = [e for e in all_entries if e.actor == actor]
        if from_ts is not None:
            all_entries = [e for e in all_entries if e.at >= from_ts]
        if to_ts is not None:
            all_entries = [e for e in all_entries if e.at <= to_ts]
        all_entries = _apply_event_filters(all_entries, event_type, actor_user_id, target)
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
        event_type: str | None = None,
        actor_user_id: int | None = None,
        target: str | None = None,
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
            # Applied in memory: a further equality filter next to the existing
            # range filter on `at` would need a new composite index per combination.
            entries = _apply_event_filters(entries, event_type, actor_user_id, target)
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
