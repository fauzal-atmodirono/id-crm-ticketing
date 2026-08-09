from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import structlog

from chatbot.features.routing.presence import PresenceFetcher
from chatbot.features.routing.store import ChannelPriorityStore
from chatbot.platform.config import Settings

if TYPE_CHECKING:
    from chatbot.features.routing.custom_status import CustomStatus
    from chatbot.features.routing.presence_store import PresenceEvent

_log = structlog.get_logger(__name__)


class _CustomStatusLookup(Protocol):
    """The one `CustomStatusStore` method fair-share routability needs.

    A narrow structural `Protocol` (not the concrete `CustomStatusStore`
    type) so `RoutingService` doesn't have to import Firestore-backed code
    to be constructed, and tests can inject a plain fake.
    """

    async def get(self, key: str) -> CustomStatus | None: ...


class _PresenceEventLookup(Protocol):
    """The one `PresenceEventStore` method fair-share routability needs."""

    async def latest(self, agent_id: int) -> PresenceEvent | None: ...


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

    Tier order is fixed and is never reordered by load: channel priority
    picks *which* tier is in play before load ever gets a vote. Fair share
    (below) only chooses *within* whichever tier wins — a tier-1 agent
    buried in open conversations still beats an idle tier-3 agent, or fair
    share would silently route channel-specialised work to whoever happens
    to be idle.

    Returns ``None`` when all three tiers are exhausted (every agent is busy
    or offline). The caller should fall back to the static team assignment.

    Concurrent-conversation cap (``settings.routing_max_concurrent_per_agent``):
        When set to a positive integer, agents whose currently-open
        conversation count (``PresenceFetcher.fetch_agent_open_counts``) is
        already at or above the cap are excluded from ``online`` — and
        therefore from all three tiers, since every tier filters through
        ``online``. ``0`` (the default) disables the cap entirely.

    Fair-share selection (``settings.routing_fair_share_enabled``, requirement
    4.16): dict/set iteration order made ``pick_agent`` deterministic but not
    fair — the first agent inserted into a tier always won it, forever, until
    they hit the concurrency cap. With the flag off, every tier keeps that
    exact first-match behavior (in ``ChannelPriorityStore.list_all()``
    insertion order) and the open-count fetch is skipped unless the
    concurrency cap also needs it — flag-off is byte-identical to the
    pre-fair-share code, including which API calls are made. With the flag
    on:

    - The open-count fetch now also runs when the cap is off, because fair
      share needs the load data as a chooser, not just a ceiling.
    - Within the winning tier, the agent with the *fewest* currently-open
      conversations is picked instead of the first one encountered. As the
      least-loaded agent's count climbs, someone else becomes the
      least-loaded, which is what turns "first match forever" into
      rotation.
    - An empty open-count tally (either genuinely everyone-at-zero, or the
      fail-open ``{}`` that ``fetch_agent_open_counts`` returns on any
      failure — see its docstring) makes every candidate look equally
      unloaded. Selection then degrades gracefully to the
      least-recently-assigned tie-break alone, so routing still rotates
      instead of freezing on one agent or erroring out.
    - Ties (including the all-zero case above) are broken by
      least-recently-assigned, tracked in ``self._last_assigned`` — an
      in-memory, per-instance counter bumped every time fair share hands out
      an agent. This is deliberately **not durable**: it resets to
      "everyone looks equally fresh" on process restart. That is an
      acceptable cold-start cost for a tie-break heuristic (it still
      rotates from the first post-restart assignment onward), not a
      correctness requirement, so it does not belong in the append-only
      presence-event store (that log is about *status*, not assignment
      history) or in any new persistent store. Any further tie (e.g. two
      agents neither of which this instance has ever assigned) is broken by
      agent id, purely so the outcome is deterministic rather than
      insertion-order-dependent.
    - If a custom-status catalogue and a presence-event store are both
      wired in (both constructor args are optional, default ``None`` — a
      later wiring wave's job; unwired is today's behavior exactly), an
      additional ``routable`` filter is layered on top of the native
      ``online`` check: an agent whose current custom status (looked up via
      ``presence_store.latest(agent_id).status``, then
      ``custom_status_store.get(status)``) resolves to a catalogue entry
      with ``routable=False`` is excluded even though Chatwoot still
      reports them ``online``. The native ``online``/cap filter always
      remains the *primary* gate; ``routable`` only ever narrows it
      further, and it fails **open**: an unknown status key, no recorded
      status, or a catalogue lookup failure all return ``None`` (per
      ``CustomStatusStore.get``'s documented fail-open contract) and are
      treated as "no extra information, don't exclude" — never as "not
      routable" (which would silently halt routing on a store outage) and
      never as "routable" (which would let an agent parked in an
      unroutable status keep receiving work whenever the store happens to
      be down). A custom-status outage therefore degrades to exactly
      today's native-status-only routing, never to "nobody eligible" and
      never to "everybody eligible".
    """

    def __init__(
        self,
        presence: PresenceFetcher,
        store: ChannelPriorityStore,
        settings: Settings,
        custom_status_store: _CustomStatusLookup | None = None,
        presence_store: _PresenceEventLookup | None = None,
    ) -> None:
        self._presence = presence
        self._store = store
        self._settings = settings
        self._custom_status_store = custom_status_store
        self._presence_event_store = presence_store
        # Fair-share tie-break bookkeeping -- see the class docstring's
        # "Fair-share selection" section for why this is in-memory and
        # local to this service rather than a new persistent store.
        self._last_assigned: dict[int, int] = {}
        self._assignment_seq = 0

    async def _is_routable(self, agent_id: int) -> bool:
        """Fair share's extra `routable` filter. Fails open to `True`.

        Returns `False` only when both collaborators are wired, the agent
        has a recorded current status, and that status's catalogue entry
        explicitly says `routable=False`. Every other case -- a collaborator
        not wired, no presence history for this agent, an unknown status
        key, or a catalogue outage -- returns `True` (don't exclude), per
        `CustomStatusStore.get`'s fail-open contract.
        """
        if self._custom_status_store is None or self._presence_event_store is None:
            return True
        event = await self._presence_event_store.latest(agent_id)
        if event is None:
            return True
        status = await self._custom_status_store.get(event.status)
        if status is None:
            return True
        return status.routable

    def _pick_fair_share(self, candidates: list[int], open_counts: dict[int, int]) -> int:
        """Fewest open conversations wins; ties go to least-recently-assigned.

        `open_counts.get(agent_id, 0)` treats an agent absent from the tally
        -- including the fail-open `{}` `fetch_agent_open_counts` returns on
        any failure -- as having zero open conversations, i.e. maximally
        eligible. That is the deliberate choice for an empty tally: every
        agent looks equally unloaded, so selection falls through to the
        least-recently-assigned tie-break (and, beyond that, agent id) for a
        fully deterministic pick that still rotates rather than freezing on
        one agent.
        """
        chosen = min(
            candidates,
            key=lambda aid: (open_counts.get(aid, 0), self._last_assigned.get(aid, -1), aid),
        )
        self._assignment_seq += 1
        self._last_assigned[chosen] = self._assignment_seq
        return chosen

    async def pick_agent(self, conv_channel: str) -> int | None:
        """Return the best available agent id for ``conv_channel``, or ``None``."""
        agents = await self._presence.fetch_agents()
        priorities = await self._store.list_all()

        fair_share = self._settings.routing_fair_share_enabled
        cap_enabled = self._settings.routing_max_concurrent_per_agent > 0

        open_counts: dict[int, int] = {}
        if cap_enabled or fair_share:
            open_counts = await self._presence.fetch_agent_open_counts()

        channel_lower = conv_channel.lower()
        online = {
            a.id: a
            for a in agents
            if a.availability_status == "online"
            and (
                not cap_enabled
                or open_counts.get(a.id, 0) < self._settings.routing_max_concurrent_per_agent
            )
        }
        if fair_share:
            online = {
                agent_id: record
                for agent_id, record in online.items()
                if await self._is_routable(agent_id)
            }

        priority_map: dict[int, list[str]] = {
            p.agent_id: [c.lower() for c in p.channel_priorities] for p in priorities
        }
        prioritised_agent_ids = set(priority_map.keys())

        tier1 = [
            agent_id
            for agent_id, chans in priority_map.items()
            if agent_id in online and chans and chans[0] == channel_lower
        ]
        tier2 = [
            agent_id
            for agent_id, chans in priority_map.items()
            if agent_id in online and channel_lower in chans
        ]
        tier3 = [agent_id for agent_id in online if agent_id not in prioritised_agent_ids]

        for event_name, candidates in (
            ("routing_tier1_pick", tier1),
            ("routing_tier2_pick", tier2),
            ("routing_tier3_idle_fallback", tier3),
        ):
            if not candidates:
                continue
            chosen = self._pick_fair_share(candidates, open_counts) if fair_share else candidates[0]
            _log.info(event_name, agent_id=chosen, channel=conv_channel)
            return chosen

        _log.warning("routing_no_available_agent", channel=conv_channel)
        return None
