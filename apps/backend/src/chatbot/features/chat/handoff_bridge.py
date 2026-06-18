"""In-process state for the human-agent handoff bridge.

Owns:
- `session_id` ↔ `conversation_id` mapping in both directions.
- One `asyncio.Queue` per active SSE subscriber, so an agent webhook can
  fan out to every open `/chat/stream/{session_id}` connection for that
  session (typically one per browser tab).

Single-process and in-memory by design. Restarting the backend drops live
handoffs. Persistent storage (Redis/DB) is the production upgrade path.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import structlog

from chatbot.features.chat.models import AgentMessageEvent

_log = structlog.get_logger(__name__)


class HandoffBridge:
    def __init__(self) -> None:
        self._session_to_conv: dict[str, str] = {}
        self._conv_to_session: dict[str, str] = {}
        self._subscribers: dict[str, list[asyncio.Queue[AgentMessageEvent | None]]] = (
            defaultdict(list)
        )

    # --- Registration --------------------------------------------------------

    def register(self, session_id: str, conversation_id: str) -> None:
        self._session_to_conv[session_id] = conversation_id
        self._conv_to_session[conversation_id] = session_id
        _log.info(
            "handoff_bridge_registered",
            session_id=session_id,
            conversation_id=conversation_id,
        )

    def unregister(self, session_id: str) -> None:
        conv_id = self._session_to_conv.pop(session_id, None)
        if conv_id:
            self._conv_to_session.pop(conv_id, None)
        # Wake any open subscribers so their streams terminate.
        for queue in self._subscribers.pop(session_id, []):
            queue.put_nowait(None)

    def is_handed_off(self, session_id: str) -> bool:
        return session_id in self._session_to_conv

    def conversation_id_for(self, session_id: str) -> str | None:
        return self._session_to_conv.get(session_id)

    def session_id_for(self, conversation_id: str) -> str | None:
        return self._conv_to_session.get(conversation_id)

    # --- Pub-sub -------------------------------------------------------------

    def subscribe(self, session_id: str) -> asyncio.Queue[AgentMessageEvent | None]:
        queue: asyncio.Queue[AgentMessageEvent | None] = asyncio.Queue(maxsize=64)
        self._subscribers[session_id].append(queue)
        return queue

    def unsubscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[AgentMessageEvent | None],
    ) -> None:
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        try:
            subs.remove(queue)
        except ValueError:
            return
        if not subs:
            self._subscribers.pop(session_id, None)

    async def publish(self, event: AgentMessageEvent) -> None:
        session_id = self.session_id_for(event.conversation_id)
        if session_id is None:
            _log.warning(
                "handoff_publish_no_session",
                conversation_id=event.conversation_id,
            )
            return
        for queue in list(self._subscribers.get(session_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                _log.warning(
                    "handoff_publish_queue_full",
                    session_id=session_id,
                )
