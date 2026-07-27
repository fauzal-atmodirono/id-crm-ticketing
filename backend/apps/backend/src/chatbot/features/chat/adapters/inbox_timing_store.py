"""Per-inbox conversation-lifecycle timing store.

Maps each Chatwoot inbox_id to a subset of the four lifecycle timing values
(`idle_warn_minutes`, `idle_close_grace_minutes`,
`idle_close_out_of_hours_grace_minutes`, `confirm_grace_minutes`). Only the
keys an operator explicitly sets are stored; an absent key means "inherit the
agent's global env default" (resolved in the agent, not here). An explicit 0 is
a valid, stored value.

Deliberately SEPARATE from `inbox_assignment_store`: an inbox can use the
default assistant (no stored assignment) yet still need custom timing, so the
two must not be coupled.

Follows the same Port + InMemory + Firestore pattern as
`inbox_assignment_store.py`. Firestore collection `inbox_timing`, one doc per
inbox_id (doc id = str(inbox_id)). Sync SDK via asyncio.to_thread; every read
degrades to None/{} on failure — never raises.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

TIMING_KEYS: tuple[str, ...] = (
    "idle_warn_minutes",
    "idle_close_grace_minutes",
    "idle_close_out_of_hours_grace_minutes",
    "confirm_grace_minutes",
)

MESSAGE_KEYS: tuple[str, ...] = (
    "idle_warning_message",
    "idle_close_message",
    "resolution_prompt_message",
    "assign_agent_message",
    "survey_ai_message",
    "survey_agent_message",
    "thanks_message",
)

MESSAGE_KEY = MESSAGE_KEYS[0]  # backward-compat alias
ENABLED_KEY = "inactivity_enabled"


def _clean_timing(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only recognised keys with the right type: the four ints (0..1440
    not enforced here — the router validates), the seven str messages, a bool enabled."""
    out: dict[str, Any] = {}
    for k in TIMING_KEYS:
        v = data.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    for mk in MESSAGE_KEYS:
        mv = data.get(mk)
        if isinstance(mv, str):
            out[mk] = mv
    en = data.get(ENABLED_KEY)
    if isinstance(en, bool):
        out[ENABLED_KEY] = en
    return out


@runtime_checkable
class InboxTimingStorePort(Protocol):
    """Read-and-write interface for per-inbox lifecycle timing.

    Reads never raise (get -> None, get_all -> {}); writes swallow after logging.
    """

    async def get_all(self) -> dict[int, dict[str, Any]]: ...

    async def get(self, inbox_id: int) -> dict[str, Any] | None: ...

    async def set(self, inbox_id: int, timing: dict[str, Any]) -> None: ...

    async def delete(self, inbox_id: int) -> None: ...


class InMemoryInboxTimingStore:
    """Volatile timing store — for tests and local dev."""

    def __init__(self) -> None:
        self._data: dict[int, dict[str, Any]] = {}

    async def get_all(self) -> dict[int, dict[str, Any]]:
        return {k: dict(v) for k, v in self._data.items()}

    async def get(self, inbox_id: int) -> dict[str, Any] | None:
        entry = self._data.get(inbox_id)
        return dict(entry) if entry is not None else None

    async def set(self, inbox_id: int, timing: dict[str, Any]) -> None:
        self._data[inbox_id] = _clean_timing(timing)

    async def delete(self, inbox_id: int) -> None:
        self._data.pop(inbox_id, None)


class FirestoreInboxTimingStore:
    """Firestore-backed timing store (collection `inbox_timing`)."""

    _COLLECTION = "inbox_timing"

    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore  # noqa: PLC0415 — lazy: boot without the SDK

        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        _log.info(
            "firestore_inbox_timing_store_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )

    def _collection(self) -> Any:
        return self._client.collection(self._COLLECTION)

    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        return _clean_timing(data)

    async def get_all(self) -> dict[int, dict[str, Any]]:
        def _read() -> dict[int, dict[str, Any]]:
            result: dict[int, dict[str, Any]] = {}
            for doc in self._collection().stream():
                try:
                    inbox_id = int(doc.id)
                except ValueError:
                    continue
                result[inbox_id] = self._clean(doc.to_dict() or {})
            return result

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("inbox_timing_get_all_failed", error=str(e))
            return {}

    async def get(self, inbox_id: int) -> dict[str, Any] | None:
        def _read() -> dict[str, Any] | None:
            snap = self._collection().document(str(inbox_id)).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            return self._clean(data) if isinstance(data, dict) else None

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("inbox_timing_get_failed", inbox_id=inbox_id, error=str(e))
            return None

    async def set(self, inbox_id: int, timing: dict[str, Any]) -> None:
        cleaned = _clean_timing(timing)

        def _write() -> None:
            self._collection().document(str(inbox_id)).set(cleaned)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("inbox_timing_set_failed", inbox_id=inbox_id, error=str(e))

    async def delete(self, inbox_id: int) -> None:
        def _delete() -> None:
            self._collection().document(str(inbox_id)).delete()

        try:
            await asyncio.to_thread(_delete)
        except Exception as e:
            _log.error("inbox_timing_delete_failed", inbox_id=inbox_id, error=str(e))


def build_inbox_timing_store(
    settings: Settings,
) -> InMemoryInboxTimingStore | FirestoreInboxTimingStore:
    """Firestore when firestore_project_id is set, else InMemory (tests/dev)."""
    if settings.firestore_project_id:
        try:
            return FirestoreInboxTimingStore(settings)
        except Exception as e:
            _log.warning("firestore_inbox_timing_store_init_failed", error=str(e))
    return InMemoryInboxTimingStore()
