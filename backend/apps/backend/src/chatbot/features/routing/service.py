from __future__ import annotations

import structlog

from chatbot.features.routing.presence import PresenceFetcher
from chatbot.features.routing.store import ChannelPriorityStore
from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class RoutingService:
    """Status-aware agent selection with three-tier routing + idle fallback.

    Tier 1 — Priority-first match:
        Agents whose ``channel_priorities[0]`` (first preference) equals the
        inbound channel AND whose availability_status is ``"online"``.

    Tier 2 — Priority-secondary match:
        Agents whose ``channel_priorities`` list contains the channel at any
        position AND whose availability_status is ``"online"``.

    Tier 3 — Idle fallback (spec item 7):
        Online agents with NO priority record configured. These are agents the
        ops team has not yet assigned a channel preference — treated as a
        generic overflow pool.

    Returns ``None`` when all three tiers are exhausted (every agent is busy
    or offline). The caller should fall back to the static team assignment.

    Concurrent-conversation cap (``settings.routing_max_concurrent_per_agent``):
        When set to a positive integer, agents whose currently-open
        conversation count (``PresenceFetcher.fetch_agent_open_counts``) is
        already at or above the cap are excluded from ``online`` — and
        therefore from all three tiers, since every tier filters through
        ``online``. ``0`` (the default) disables the cap entirely and skips
        the open-count fetch, preserving prior behavior byte-for-byte.
    """

    def __init__(
        self, presence: PresenceFetcher, store: ChannelPriorityStore, settings: Settings
    ) -> None:
        self._presence = presence
        self._store = store
        self._settings = settings

    async def pick_agent(self, conv_channel: str) -> int | None:
        """Return the best available agent id for ``conv_channel``, or ``None``."""
        agents = await self._presence.fetch_agents()
        priorities = await self._store.list_all()

        open_counts: dict[int, int] = {}
        if self._settings.routing_max_concurrent_per_agent > 0:
            open_counts = await self._presence.fetch_agent_open_counts()

        channel_lower = conv_channel.lower()
        online = {
            a.id: a
            for a in agents
            if a.availability_status == "online"
            and (
                self._settings.routing_max_concurrent_per_agent <= 0
                or open_counts.get(a.id, 0) < self._settings.routing_max_concurrent_per_agent
            )
        }
        priority_map: dict[int, list[str]] = {
            p.agent_id: [c.lower() for c in p.channel_priorities] for p in priorities
        }
        prioritised_agent_ids = set(priority_map.keys())

        # Tier 1: first-priority channel matches AND online
        for agent_id, chans in priority_map.items():
            if agent_id in online and chans and chans[0] == channel_lower:
                _log.info(
                    "routing_tier1_pick",
                    agent_id=agent_id,
                    channel=conv_channel,
                )
                return agent_id

        # Tier 2: channel appears anywhere in priority list AND online
        for agent_id, chans in priority_map.items():
            if agent_id in online and channel_lower in chans:
                _log.info(
                    "routing_tier2_pick",
                    agent_id=agent_id,
                    channel=conv_channel,
                )
                return agent_id

        # Tier 3: online agents with no priority configured (idle overflow pool)
        for agent_id in online:
            if agent_id not in prioritised_agent_ids:
                _log.info(
                    "routing_tier3_idle_fallback",
                    agent_id=agent_id,
                    channel=conv_channel,
                )
                return agent_id

        _log.warning("routing_no_available_agent", channel=conv_channel)
        return None
