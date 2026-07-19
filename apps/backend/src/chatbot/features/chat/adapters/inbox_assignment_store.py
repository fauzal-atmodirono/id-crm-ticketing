"""Per-inbox assistant + mode assignment store.

Maps each Chatwoot inbox_id to an {assistant_id, mode} pair. The mode is one of
"off" | "suggest" | "auto". An absent entry means "use tenant defaults" (handled
by the inbox_resolver layer, not this store).

Three implementations follow the same Port + InMemory + Firestore pattern as
`live_faq.py`, `assistants_store.py`, and `handoff_store.py`:

- `InMemoryInboxAssignmentStore` — volatile dict, used in tests/dev.
- `FirestoreInboxAssignmentStore` — persists in Firestore collection
  `inbox_assignments` (one doc per inbox_id, doc id = str(inbox_id),
  data = {assistant_id, mode}). Sync SDK calls dispatched via asyncio.to_thread.
  Every read path degrades to None/empty on failure — never raises.

`build_inbox_assignment_store(settings)` picks InMemory when
`firestore_project_id` is unset, Firestore otherwise.

`delete_for_assistant(assistant_id)` is the cascade-delete hook called when an
assistant is removed: it clears all inbox assignments that referenced that
assistant so the inboxes revert to tenant defaults.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_VALID_MODES = frozenset({"off", "suggest", "auto"})


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class InboxAssignmentStorePort(Protocol):
    """Read-and-write interface for inbox→assistant+mode assignments.

    All reads never raise: get_all returns {} on failure, get returns None.
    set/delete swallow errors after logging.
    delete_for_assistant scans all assignments and removes those referencing the
    given assistant_id (cascade from the assistants delete flow).
    """

    async def get_all(self) -> dict[int, dict[str, str]]:
        """Return all stored assignments as {inbox_id: {assistant_id, mode}}."""
        ...

    async def get(self, inbox_id: int) -> dict[str, str] | None:
        """Return the assignment for the given inbox, or None if not set."""
        ...

    async def set(self, inbox_id: int, assistant_id: str, mode: str) -> None:
        """Persist an assignment. mode must be one of 'off'|'suggest'|'auto'."""
        ...

    async def delete(self, inbox_id: int) -> None:
        """Remove the assignment for inbox_id (revert to tenant defaults)."""
        ...

    async def delete_for_assistant(self, assistant_id: str) -> None:
        """Remove all assignments whose assistant_id matches (cascade delete)."""
        ...


# ---------------------------------------------------------------------------
# InMemory implementation
# ---------------------------------------------------------------------------


class InMemoryInboxAssignmentStore:
    """Volatile assignment store — for tests and local dev."""

    def __init__(self) -> None:
        self._data: dict[int, dict[str, str]] = {}

    async def get_all(self) -> dict[int, dict[str, str]]:
        return {k: dict(v) for k, v in self._data.items()}

    async def get(self, inbox_id: int) -> dict[str, str] | None:
        entry = self._data.get(inbox_id)
        return dict(entry) if entry is not None else None

    async def set(self, inbox_id: int, assistant_id: str, mode: str) -> None:
        self._data[inbox_id] = {"assistant_id": assistant_id, "mode": mode}

    async def delete(self, inbox_id: int) -> None:
        self._data.pop(inbox_id, None)

    async def delete_for_assistant(self, assistant_id: str) -> None:
        to_remove = [k for k, v in self._data.items() if v.get("assistant_id") == assistant_id]
        for k in to_remove:
            del self._data[k]


# ---------------------------------------------------------------------------
# Firestore implementation
# ---------------------------------------------------------------------------


class FirestoreInboxAssignmentStore:
    """Firestore-backed inbox assignment store.

    Each assignment is one document in `inbox_assignments/<inbox_id>` where the
    doc id is str(inbox_id) and the data is {assistant_id, mode}. The Firestore
    SDK is synchronous; all calls run in a worker thread via asyncio.to_thread.
    Every read path degrades to None/empty on failure so callers always get a
    valid response even if Firestore is unreachable.
    """

    _COLLECTION = "inbox_assignments"

    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore  # noqa: PLC0415 — lazy: boot without the SDK

        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        _log.info(
            "firestore_inbox_assignment_store_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )

    def _collection(self) -> Any:
        return self._client.collection(self._COLLECTION)

    async def get_all(self) -> dict[int, dict[str, str]]:
        def _read() -> dict[int, dict[str, str]]:
            result: dict[int, dict[str, str]] = {}
            for doc in self._collection().stream():
                data = doc.to_dict() or {}
                try:
                    inbox_id = int(doc.id)
                except ValueError:
                    continue
                result[inbox_id] = {
                    "assistant_id": str(data.get("assistant_id", "")),
                    "mode": str(data.get("mode", "")),
                }
            return result

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("inbox_assignment_get_all_failed", error=str(e))
            return {}

    async def get(self, inbox_id: int) -> dict[str, str] | None:
        def _read() -> dict[str, str] | None:
            snap = self._collection().document(str(inbox_id)).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            if not isinstance(data, dict):
                return None
            return {
                "assistant_id": str(data.get("assistant_id", "")),
                "mode": str(data.get("mode", "")),
            }

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("inbox_assignment_get_failed", inbox_id=inbox_id, error=str(e))
            return None

    async def set(self, inbox_id: int, assistant_id: str, mode: str) -> None:
        def _write() -> None:
            self._collection().document(str(inbox_id)).set(
                {"assistant_id": assistant_id, "mode": mode}
            )

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("inbox_assignment_set_failed", inbox_id=inbox_id, error=str(e))

    async def delete(self, inbox_id: int) -> None:
        def _delete() -> None:
            self._collection().document(str(inbox_id)).delete()

        try:
            await asyncio.to_thread(_delete)
        except Exception as e:
            _log.error("inbox_assignment_delete_failed", inbox_id=inbox_id, error=str(e))

    async def delete_for_assistant(self, assistant_id: str) -> None:
        # Read all assignments and delete matching ones. A Firestore .where() query
        # would be cleaner but requires an index; a full scan is fine for the small
        # set of inboxes a tenant typically has.
        all_assignments = await self.get_all()
        for inbox_id, entry in all_assignments.items():
            if entry.get("assistant_id") == assistant_id:
                await self.delete(inbox_id)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_inbox_assignment_store(
    settings: Settings,
) -> InMemoryInboxAssignmentStore | FirestoreInboxAssignmentStore:
    """Return a FirestoreInboxAssignmentStore when firestore_project_id is set,
    else InMemoryInboxAssignmentStore (tests / dev without GCP credentials)."""
    if settings.firestore_project_id:
        try:
            return FirestoreInboxAssignmentStore(settings)
        except Exception as e:
            _log.warning("firestore_inbox_assignment_store_init_failed", error=str(e))
    return InMemoryInboxAssignmentStore()
