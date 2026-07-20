from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "routing_priorities"


@dataclass(frozen=True)
class AgentPriority:
    agent_id: int
    channel_priorities: list[str]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AgentPriority):
            return NotImplemented
        return self.agent_id == other.agent_id and list(self.channel_priorities) == list(
            other.channel_priorities
        )

    def __hash__(self) -> int:
        return hash((self.agent_id, tuple(self.channel_priorities)))


class ChannelPriorityStore:
    """Firestore-backed store for per-agent channel priority lists.

    Each document lives in the ``routing_priorities`` collection keyed by the
    string agent_id. The document schema is:
        { "agent_id": int, "channel_priorities": [str, ...] }

    All I/O is wrapped in ``asyncio.to_thread`` (same pattern as the handoff
    and FAQ stores) so the async FastAPI event loop is not blocked.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_ref(self, agent_id: int) -> firestore.DocumentReference:
        return self._client().collection(_COLLECTION).document(str(agent_id))

    async def get(self, agent_id: int) -> AgentPriority | None:
        """Return the priority record for ``agent_id``, or ``None`` if absent."""
        try:
            snap = await asyncio.to_thread(self._doc_ref(agent_id).get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            return AgentPriority(
                agent_id=int(data.get("agent_id", agent_id)),
                channel_priorities=list(data.get("channel_priorities") or []),
            )
        except Exception as e:
            _log.error("routing_store_get_failed", agent_id=agent_id, error=str(e))
            return None

    async def set(self, agent_id: int, channel_priorities: list[str]) -> None:
        """Upsert the priority list for ``agent_id``."""
        try:
            await asyncio.to_thread(
                self._doc_ref(agent_id).set,
                {"agent_id": agent_id, "channel_priorities": channel_priorities},
            )
        except Exception as e:
            _log.error("routing_store_set_failed", agent_id=agent_id, error=str(e))

    async def delete(self, agent_id: int) -> None:
        """Remove the priority record for ``agent_id`` (no-op if absent)."""
        try:
            await asyncio.to_thread(self._doc_ref(agent_id).delete)
        except Exception as e:
            _log.error("routing_store_delete_failed", agent_id=agent_id, error=str(e))

    async def list_all(self) -> list[AgentPriority]:
        """Return all stored agent priority records."""
        try:
            client = self._client()
            snaps = await asyncio.to_thread(
                lambda: list(client.collection(_COLLECTION).stream())
            )
            results: list[AgentPriority] = []
            for snap in snaps:
                data = snap.to_dict() or {}
                agent_id = data.get("agent_id")
                if agent_id is None:
                    continue
                results.append(
                    AgentPriority(
                        agent_id=int(agent_id),
                        channel_priorities=list(data.get("channel_priorities") or []),
                    )
                )
            return results
        except Exception as e:
            _log.error("routing_store_list_failed", error=str(e))
            return []
