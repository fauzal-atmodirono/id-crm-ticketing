"""P6 task 5 -- After-Call-Work (ACW): a first-class presence state entered
automatically when a phone call ends, and exited by the agent or by a
timeout. Requirement 4.69 also asks for average-handling-time; that half
stays blocked on the missing call-queue instrumentation and is
deliberately NOT attempted here.

**Which agent gets ACW.** The phone call-end webhook (Twilio's
``<Dial action>`` callback) knows a ``CallSid`` and a dialled number, never
an agent id -- Phase 1's handoff dials a static hunt-group number with no
per-agent identity (see ``features/chat/phone/handoff_target.py``). This
module resolves the agent from the conversation's CURRENT Chatwoot assignee
at call-end time instead of guessing: ``start_after_call`` looks the ticket
up via the caller-supplied ``_AssigneeResolver`` (``RoutingAssigner.
resolve_assignee`` in production), and when there is no assignee it logs and
skips -- no ACW is entered, nothing is guessed.

**Restart safety (the reason ``ACW_TIMEOUT_SECONDS`` exists at all).** ACW's
timeout is derived, not scheduled: entering ACW appends a single
``PresenceEvent`` (via ``CustomStatusStore.set_status``, so the native
Chatwoot write and the event append happen in the catalogue's usual
ordering) carrying a real wall-clock timestamp. Whether that event is
"expired" is a pure function of ``now - event.at`` computed fresh on every
call to ``expire_if_due``/``sweep`` -- there is no ``asyncio.create_task``
sleeper, timer thread, or in-memory scheduler anywhere in this file. A
process restart loses no state because there was never any in-process state
to lose: any process -- the one that handled the call, a restarted
replacement, or an unrelated worker running ``sweep()`` on its own
schedule -- reads the same ``PresenceEventStore`` and reaches the identical
answer. This is also why the "enter" and "exit" paths are careful to always
go through ``CustomStatusStore``/``PresenceEventStore`` rather than any
local cache.

Two mechanisms both rely on that same derived check, per the task brief's
"derivable read, explicit sweep as the belt to that braces":

- ``expire_if_due(agent_id)`` -- call this from anywhere that is about to
  look at one agent's status (e.g. before assigning them new work): if
  they've been in ``acw`` longer than ``settings.acw_timeout_seconds``,
  it exits them for you and returns ``True``.
- ``sweep()`` -- the explicit belt: iterates every Chatwoot agent (via the
  injected ``_PresenceProbe.fetch_agents``) and calls ``expire_if_due`` on
  each. This module does not itself schedule ``sweep()`` on a timer --
  wiring that up (e.g. onto the existing presence-poller cadence) is a
  later wave's job, per this task's "do not touch main.py" instruction.

This module is inert unless ``settings.acw_enabled`` is True: both
``start_after_call`` (the call-end entry point) and, for defense in depth,
``enter`` check the flag/agent-state before writing anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import structlog

from chatbot.features.routing.custom_status import (
    CustomStatus,
    build_custom_status_store,
)
from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.presence_store import (
    PresenceEvent,
    build_presence_event_store,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# The seeded catalogue key entered automatically on call-end (task-2
# report). Never chosen by an agent directly.
ACW_STATUS_KEY = "acw"
# Where a manual/auto exit lands. "available" is the sole routable, native
# "online" status in the seeded catalogue -- the ordinary resting state.
ACW_EXIT_STATUS_KEY = "available"
# The one native value that means "gone for the day" -- see enter()'s
# docstring for why this specific value (not any custom-status key) is
# checked.
_OFFLINE = "offline"


class _AssigneeResolver(Protocol):
    """The one method this module needs from `RoutingAssigner` (task 5's
    addition there). Kept as a narrow structural protocol so tests can
    inject a lightweight fake instead of a real Chatwoot-backed assigner.
    """

    async def resolve_assignee(self, conversation_id: int) -> int | None: ...


class _StatusCatalogue(Protocol):
    """The two `CustomStatusStore` methods this module depends on."""

    async def get(self, key: str) -> CustomStatus | None: ...

    async def set_status(
        self, agent_id: int, key: str, *, source: str = "agent", now: datetime | None = None
    ) -> bool: ...


class _PresenceReader(Protocol):
    """The one `PresenceEventStore` method the timeout check depends on."""

    async def latest(self, agent_id: int) -> PresenceEvent | None: ...


class _PresenceProbe(Protocol):
    """The two `PresenceFetcher` methods this module depends on: a
    single-agent live-status check (offline guard) and the full agent list
    (the sweep's candidate set)."""

    async def fetch_agent_availability(self, agent_id: int) -> str: ...

    async def fetch_agents(self) -> list[AgentRecord]: ...


class ACWController:
    """Enter/exit After-Call-Work, routed entirely through the existing
    custom-status catalogue and presence-event store rather than any
    status string or write path invented here.
    """

    def __init__(
        self,
        settings: Settings,
        assigner: _AssigneeResolver,
        custom_status_store: _StatusCatalogue | None = None,
        presence_store: _PresenceReader | None = None,
        presence_fetcher: _PresenceProbe | None = None,
    ) -> None:
        self._settings = settings
        self._assigner = assigner
        self._custom_status_store: _StatusCatalogue = (
            custom_status_store or build_custom_status_store(settings)
        )
        self._presence_store: _PresenceReader = presence_store or build_presence_event_store(
            settings
        )
        self._presence_fetcher: _PresenceProbe = presence_fetcher or PresenceFetcher(settings)

    async def start_after_call(
        self, conversation_id: int, *, now: datetime | None = None
    ) -> int | None:
        """Call-end entry point (task 5's "which agent" resolution).

        Resolves the agent from the conversation's CURRENT Chatwoot
        assignee; when there is none (or the lookup fails), logs and skips
        -- no ACW is entered, nothing is guessed. Gated on
        ``settings.acw_enabled`` so a tenant with the flag off never even
        resolves an assignee, matching this task's "flag off leaves call-end
        handling completely unchanged" requirement. Never raises: every
        step is best-effort, matching the call-end webhook's own
        invariant that this must not affect the caller's TwiML.

        Returns the agent id actually entered into ACW, or ``None`` when
        nothing happened (flag off, no assignee, agent already offline, or
        the write failed).
        """
        if not self._settings.acw_enabled:
            return None
        try:
            agent_id = await self._assigner.resolve_assignee(conversation_id)
        except Exception as e:
            _log.error("acw_resolve_assignee_failed", conversation_id=conversation_id, error=str(e))
            return None
        if agent_id is None:
            _log.info("acw_no_assignee_skipped", conversation_id=conversation_id)
            return None
        entered = await self.enter(agent_id, now=now)
        return agent_id if entered else None

    async def enter(self, agent_id: int, *, now: datetime | None = None) -> bool:
        """Put `agent_id` into ACW, unless they are already offline.

        An agent who hung up and went home must not be resurrected into a
        wrap-up state -- checked against Chatwoot's LIVE native
        ``availability_status`` (not any locally cached/derived value),
        matching this package's standing rule that the native status is the
        real gate (`custom_status.py`'s own docstring). A failed check
        fails open (treated as not-offline) so a Chatwoot blip does not
        block ordinary ACW entry -- worst case is one avoidable ACW entry
        for an agent who happened to already be offline, not a silently
        dropped wrap-up state for one who is still online.
        """
        try:
            current = await self._presence_fetcher.fetch_agent_availability(agent_id)
        except Exception as e:
            _log.error("acw_availability_check_failed", agent_id=agent_id, error=str(e))
            current = None
        if current == _OFFLINE:
            _log.info("acw_agent_already_offline_skipped", agent_id=agent_id)
            return False
        ok = await self._custom_status_store.set_status(
            agent_id, ACW_STATUS_KEY, source="system", now=now
        )
        if not ok:
            _log.error("acw_enter_failed", agent_id=agent_id)
        return ok

    async def leave(
        self, agent_id: int, *, source: str = "agent", now: datetime | None = None
    ) -> bool:
        """Exit ACW back to `available`. `source` distinguishes an agent's
        own action (default) from the auto-exit timeout (`expire_if_due`
        passes `source="system"`)."""
        return await self._custom_status_store.set_status(
            agent_id, ACW_EXIT_STATUS_KEY, source=source, now=now
        )

    async def expire_if_due(self, agent_id: int, *, now: datetime | None = None) -> bool:
        """Derived timeout check: True (and auto-exits) when `agent_id`'s
        LATEST presence event is `acw` and has been running longer than
        `settings.acw_timeout_seconds`. Computed fresh from the stored
        event timestamp every call -- see the module docstring for why this
        is what makes ACW's timeout survive a process restart.
        """
        now = now or datetime.now(UTC)
        latest = await self._presence_store.latest(agent_id)
        if latest is None or latest.status != ACW_STATUS_KEY:
            return False
        elapsed_seconds = (now - latest.at).total_seconds()
        if elapsed_seconds < self._settings.acw_timeout_seconds:
            return False
        return await self.leave(agent_id, source="system", now=now)

    async def sweep(self, *, now: datetime | None = None) -> int:
        """Belt-and-braces sweep: check every known Chatwoot agent for an
        expired ACW window and auto-exit them. Returns how many agents were
        exited. Safe to call from any process on any schedule -- see the
        module docstring; this does not itself run on a timer.
        """
        now = now or datetime.now(UTC)
        try:
            agents = await self._presence_fetcher.fetch_agents()
        except Exception as e:
            _log.error("acw_sweep_fetch_agents_failed", error=str(e))
            return 0
        exited = 0
        for agent in agents:
            try:
                if await self.expire_if_due(agent.id, now=now):
                    exited += 1
            except Exception as e:
                _log.error("acw_sweep_agent_failed", agent_id=agent.id, error=str(e))
        return exited


def build_acw_controller(settings: Settings, assigner: _AssigneeResolver) -> ACWController:
    """Wiring-wave factory: `assigner` should be the same
    `RoutingAssigner(settings)` instance the tenant already constructs for
    `/routing/assign` (see `main.py`'s `_routing_assigner`) -- there is no
    reason to construct a second one.
    """
    return ACWController(settings, assigner)
