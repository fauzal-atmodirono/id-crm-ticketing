"""Active-handoff coordinator.

Combines a `HandoffStorePort` (in-memory or Firestore) for the
`session_id ↔ conversation_id` mapping with an in-process per-session
`asyncio.Queue` for SSE fan-out.

The store is the source of truth for "is this session handed off?"; the
in-memory cache is a fast-path that's repopulated on miss from the store.
The subscriber queues are intentionally not persisted — SSE clients
reconnect after a backend restart and re-subscribe, picking up the
mapping from the store.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import structlog

from chatbot.features.chat.models import AgentMessageEvent
from chatbot.features.chat.ports import HandoffStorePort

_log = structlog.get_logger(__name__)


class SurveyEvent:
    """Sentinel pushed to SSE subscribers to request a CSAT rating."""


class HandoffBridge:
    def __init__(self, store: HandoffStorePort) -> None:
        self._store = store
        self._session_cache: dict[str, str] = {}
        self._conv_cache: dict[str, str] = {}
        self._subscribers: dict[
            str, list[asyncio.Queue[AgentMessageEvent | SurveyEvent | None]]
        ] = defaultdict(list)

    # --- Registration --------------------------------------------------------

    async def register(
        self,
        session_id: str,
        conversation_id: str,
        transcript: list[dict[str, Any]] | None = None,
    ) -> None:
        await self._store.register(session_id, conversation_id, transcript)
        self._session_cache[session_id] = conversation_id
        self._conv_cache[conversation_id] = session_id
        _log.info(
            "handoff_bridge_registered",
            session_id=session_id,
            conversation_id=conversation_id,
        )

    async def save_message(self, session_id: str, role: str, text: str) -> None:
        """Relay message saving to the store."""
        try:
            await self._store.save_message(session_id, role, text)
            _log.info("handoff_bridge_message_saved", session_id=session_id, role=role)
        except Exception as e:
            _log.error("handoff_bridge_save_message_failed", session_id=session_id, error=str(e))

    async def unregister(self, session_id: str) -> None:
        await self._store.unregister(session_id)
        conv = self._session_cache.pop(session_id, None)
        if conv:
            self._conv_cache.pop(conv, None)
        # Wake any open subscribers so their streams terminate.
        for queue in self._subscribers.pop(session_id, []):
            queue.put_nowait(None)

    async def is_handed_off(self, session_id: str) -> bool:
        return (await self.conversation_id_for(session_id)) is not None

    async def conversation_id_for(self, session_id: str) -> str | None:
        cached = self._session_cache.get(session_id)
        if cached is not None:
            return cached
        conv = await self._store.get_conversation_id(session_id)
        if conv is not None:
            self._session_cache[session_id] = conv
            self._conv_cache[conv] = session_id
        return conv

    async def session_id_for(self, conversation_id: str) -> str | None:
        cached = self._conv_cache.get(conversation_id)
        if cached is not None:
            return cached
        sid = await self._store.get_session_id(conversation_id)
        if sid is not None:
            self._conv_cache[conversation_id] = sid
            self._session_cache[sid] = conversation_id
        return sid

    # --- Pub-sub (in-process only) ------------------------------------------

    def subscribe(self, session_id: str) -> asyncio.Queue[AgentMessageEvent | SurveyEvent | None]:
        queue: asyncio.Queue[AgentMessageEvent | SurveyEvent | None] = asyncio.Queue(maxsize=64)
        self._subscribers[session_id].append(queue)
        return queue

    def unsubscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[AgentMessageEvent | SurveyEvent | None],
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
        session_id = await self.session_id_for(event.conversation_id)
        if session_id is None:
            _log.warning(
                "handoff_publish_no_session",
                conversation_id=event.conversation_id,
            )
            return

        # Save incoming agent messages to the database
        await self.save_message(session_id, "agent", event.text)

        for queue in list(self._subscribers.get(session_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                _log.warning(
                    "handoff_publish_queue_full",
                    session_id=session_id,
                )

    async def publish_survey(self, session_id: str) -> None:
        for queue in list(self._subscribers.get(session_id, [])):
            try:
                queue.put_nowait(SurveyEvent())
            except asyncio.QueueFull:
                _log.warning("survey_publish_queue_full", session_id=session_id)
