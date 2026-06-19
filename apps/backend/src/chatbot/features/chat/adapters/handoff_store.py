"""Persistent backing stores for the handoff bridge.

Two implementations:

- `InMemoryHandoffStore`: dict-backed, used in tests + when Firestore isn't
  configured. State is lost on backend restart.
- `FirestoreHandoffStore`: persists in a Firestore collection (default
  `handoff_sessions` under `proton-db`) so a backend restart preserves
  active handoffs.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

from chatbot.features.chat.ports import HandoffStorePort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class InMemoryHandoffStore(HandoffStorePort):
    """Volatile, single-process handoff store. Use for tests and dev."""

    def __init__(self) -> None:
        self._session_to_conv: dict[str, str] = {}
        self._conv_to_session: dict[str, str] = {}

    async def register(self, session_id: str, conversation_id: str) -> None:
        self._session_to_conv[session_id] = conversation_id
        self._conv_to_session[conversation_id] = session_id

    async def get_conversation_id(self, session_id: str) -> str | None:
        return self._session_to_conv.get(session_id)

    async def get_session_id(self, conversation_id: str) -> str | None:
        return self._conv_to_session.get(conversation_id)

    async def unregister(self, session_id: str) -> None:
        conv = self._session_to_conv.pop(session_id, None)
        if conv:
            self._conv_to_session.pop(conv, None)


class FirestoreHandoffStore(HandoffStorePort):
    """Firestore-backed handoff store.

    Each handoff is one document in `<collection>/<session_id>` with fields:
        - conversation_id: str
        - created_at: server timestamp

    Reverse lookup uses a `where("conversation_id", "==", …)` query. The
    Firestore SDK is synchronous; calls are dispatched to a worker thread
    via `asyncio.to_thread` so the event loop stays unblocked.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        self._collection_name = settings.firestore_handoff_collection
        self._server_ts = firestore.SERVER_TIMESTAMP
        _log.info(
            "firestore_handoff_store_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
            collection=self._collection_name,
        )

    def _collection(self) -> Any:
        return self._client.collection(self._collection_name)

    async def register(self, session_id: str, conversation_id: str) -> None:
        def _write() -> None:
            self._collection().document(session_id).set(
                {
                    "conversation_id": conversation_id,
                    "created_at": self._server_ts,
                }
            )

        await asyncio.to_thread(_write)

    async def get_conversation_id(self, session_id: str) -> str | None:
        def _read() -> str | None:
            snap = self._collection().document(session_id).get()
            if not snap.exists:
                return None
            value = snap.get("conversation_id")
            return value if isinstance(value, str) else None

        return await asyncio.to_thread(_read)

    async def get_session_id(self, conversation_id: str) -> str | None:
        def _query() -> str | None:
            docs = (
                self._collection()
                .where("conversation_id", "==", conversation_id)
                .limit(1)
                .stream()
            )
            for doc in docs:
                doc_id: str = doc.id
                return doc_id
            return None

        return await asyncio.to_thread(_query)

    async def unregister(self, session_id: str) -> None:
        def _delete() -> None:
            self._collection().document(session_id).delete()

        await asyncio.to_thread(_delete)


def build_handoff_store(settings: Settings) -> HandoffStorePort:
    """Pick the configured store. Falls back to in-memory if Firestore is
    requested but the SDK isn't importable (so dev still boots)."""
    if settings.handoff_store == "firestore":
        try:
            return FirestoreHandoffStore(settings)
        except Exception as e:
            _log.warning(
                "firestore_handoff_store_init_failed_falling_back_to_memory",
                error=str(e),
            )
    return InMemoryHandoffStore()
