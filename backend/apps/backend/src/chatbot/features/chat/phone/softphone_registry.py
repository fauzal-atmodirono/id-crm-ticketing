"""Which agents currently hold a REGISTERED Twilio Device in a browser tab.

Distinct from Chatwoot availability (`features/routing/presence.py`), which
says whether an agent is at work. An agent can be `online` in Chatwoot with
no CRM tab open, and a `<Dial><Client>` to an unregistered identity fails
immediately -- so dialling on availability alone burns a ring stage on a
dead identity.

Firestore-backed rather than an in-process dict because the backend runs
multiple workers: the worker that mints a token and receives heartbeats is
usually NOT the worker holding the websocket for the call being transferred.

**Advisory, never authoritative.** Every method fails to the empty/no-op
answer. A stale, empty, or wrong result can cost one wasted ring or one
skipped stage; it can never prevent the PSTN fallback or the apology, both
of which hang off Twilio's dial-status callback and fire regardless of
anything this module returns. Keep that property when editing.

`_collection` is a plain instance attribute (not a property): tests replace
it wholesale with a fake three-verb (`set`/`delete`/`all`) stand-in, which a
data-descriptor property would refuse at assignment time
(`AttributeError: can't set attribute`). It is built lazily -- `None` until
first touched -- so constructing a registry never eagerly talks to
Firestore.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "softphone_registrations"


class _FirestoreCollection:
    """Thin adapter so `SoftphoneRegistry` depends on three verbs, not on the
    Firestore client shape."""

    def __init__(self, ref: Any) -> None:
        self._ref = ref

    def set(self, doc_id: str, data: dict[str, Any]) -> None:
        self._ref.document(doc_id).set(data)

    def delete(self, doc_id: str) -> None:
        self._ref.document(doc_id).delete()

    def all(self) -> list[dict[str, Any]]:
        return [d.to_dict() or {} for d in self._ref.stream()]


class SoftphoneRegistry:
    def __init__(self, settings: Settings, clock: Callable[[], datetime] | None = None) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        # Plain attribute, lazily populated -- see module docstring for why
        # this is not a @property.
        self._collection: Any = None

    def _now(self) -> datetime:
        return self._clock()

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_id(self, agent_id: int) -> str:
        return f"agent-{agent_id}"

    def _get_collection(self) -> Any:
        if self._collection is None:
            self._collection = _FirestoreCollection(self._client().collection(_COLLECTION))
        return self._collection

    async def heartbeat(self, agent_id: int) -> None:
        """Record (or refresh) this agent's registration. Fail-open: a browser
        whose heartbeat fails simply ages out of the fan-out."""
        try:
            await asyncio.to_thread(
                self._get_collection().set,
                self._doc_id(agent_id),
                {"agent_id": agent_id, "at": self._now()},
            )
        except Exception as e:
            _log.error("softphone_heartbeat_failed", agent_id=agent_id, error=str(e))

    async def unregister(self, agent_id: int) -> None:
        try:
            await asyncio.to_thread(self._get_collection().delete, self._doc_id(agent_id))
        except Exception as e:
            _log.error("softphone_unregister_failed", agent_id=agent_id, error=str(e))

    async def registered_ids(self) -> set[int]:
        """Agent ids whose last heartbeat is within the TTL. Empty on any
        failure -- see the module docstring."""
        ttl = timedelta(seconds=self._settings.phone_softphone_registration_ttl_seconds)
        cutoff = self._now() - ttl
        try:
            docs = await asyncio.to_thread(self._get_collection().all)
        except Exception as e:
            _log.error("softphone_registry_read_failed", error=str(e))
            return set()
        ids: set[int] = set()
        for doc in docs:
            at = doc.get("at")
            agent_id = doc.get("agent_id")
            if not isinstance(agent_id, int) or not isinstance(at, datetime):
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if at >= cutoff:
                ids.add(agent_id)
        return ids
