"""P6 task 7 -- the assignment sweeper (requirement 4.16).

``RoutingService.pick_agent`` only ever runs at handoff time, triggered by a
Chatwoot webhook event. Nothing polls the queue, so a conversation that
arrives while every agent is over the concurrency cap / busy / offline sits
unassigned indefinitely -- it is waiting for a *new event*, not for an agent
to free up. This module is that missing poll: a periodic sweep that lists
Chatwoot's open conversations, filters down to the ones with no assignee that
have sat unassigned longer than ``routing_sweep_min_age_seconds``, and routes
each one through the exact same ``RoutingService.pick_agent`` /
``RoutingAssigner.assign`` collaborators the event-driven handoff path uses.
Agent *selection* (tiers, fair share, the per-agent concurrency cap) lives in
exactly one place, ``pick_agent`` itself -- this module never reimplements
any of it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from chatbot.features.metrics.sync import fetch_conversations as _fetch_conversations_sync

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class _AgentPicker(Protocol):
    """The one ``RoutingService`` method the sweeper needs.

    A narrow structural ``Protocol`` -- matching ``RoutingService``'s own
    ``_CustomStatusLookup``/``_PresenceEventLookup`` convention -- rather
    than the concrete class, so tests can inject a bare fake and so this
    module is structurally incapable of reaching into tier/fair-share/cap
    internals it has no business touching.
    """

    async def pick_agent(self, conv_channel: str) -> int | None: ...


class _ConversationAssigner(Protocol):
    """The three ``RoutingAssigner`` methods the sweeper needs."""

    async def resolve_channel(self, conversation_id: int) -> str: ...

    async def resolve_assignee(self, conversation_id: int) -> int | None: ...

    async def assign(self, conversation_id: int, agent_id: int) -> None: ...


# Serializes overlapping sweep ticks within THIS process. Deliberately a
# module-level singleton rather than a per-call default: every scheduler
# tick calls `run_sweep` without its own `lock`, so they must all share one
# guard for a later tick to ever see an earlier one still in flight. A fresh
# `asyncio.Lock()` handed out per call would never observe that. See
# `run_sweep`'s docstring for exactly what this does and doesn't protect
# against, and why it is paired with a second, independent check.
_default_lock: asyncio.Lock = asyncio.Lock()


async def _fetch_open_unassigned(settings: Settings) -> list[dict[str, Any]]:
    """Default conversation source: the existing Chatwoot conversation pager
    (``metrics.sync.fetch_conversations``), run off the event loop via
    ``asyncio.to_thread`` since it makes blocking ``httpx.Client`` calls.
    Reused rather than adding new Chatwoot API surface, per this task's
    brief; filtering to "open" and "unassigned" happens client-side in
    ``run_sweep``.
    """
    return await asyncio.to_thread(_fetch_conversations_sync, settings)


def _is_unassigned(conv: dict[str, Any]) -> bool:
    """A conversation's assignee lives at ``meta.assignee.id`` -- a
    *different*, per-conversation ``meta`` than the page-level ``meta`` --
    per ``PresenceFetcher.fetch_agent_open_counts``'s verified-shape
    docstring.
    """
    assignee = (conv.get("meta") or {}).get("assignee") or {}
    return not isinstance(assignee, dict) or assignee.get("id") is None


async def run_sweep(
    settings: Settings,
    routing_svc: _AgentPicker,
    assigner: _ConversationAssigner,
    *,
    fetch_conversations: Callable[[Settings], Awaitable[list[dict[str, Any]]]] | None = None,
    now: Callable[[], float] | None = None,
    lock: asyncio.Lock | None = None,
) -> dict[str, int]:
    """One sweep pass: assign every open, unassigned conversation older than
    ``routing_sweep_min_age_seconds``, using the exact same ``pick_agent`` /
    ``assign`` calls the event-driven handoff path uses. Returns
    ``{"scanned", "assigned", "skipped"}`` counts; never raises.

    Gated on BOTH ``routing_sweep_enabled`` and ``routing_enabled`` --
    the latter is the master switch for the whole routing engine (see
    ``features/routing/router.py``'s ``/routing/assign`` handler, which
    returns ``{"disabled": True}`` on the same flag), and a sweeper that
    keeps assigning work while an operator believes the engine is off would
    be a nasty surprise. Checked here, not only in ``start_routing_sweeper``,
    so flipping either flag at runtime takes effect on the very next tick
    without a process restart.

    A conversation that is not yet ``routing_sweep_min_age_seconds`` old is
    left alone entirely -- not even asked about via ``pick_agent`` -- so the
    event-driven handoff path (which runs at handoff time, essentially
    immediately) always gets the first attempt at a brand-new conversation.
    Without this gate, the sweeper and that path would both race to assign
    the same fresh conversation.

    A ``pick_agent`` result of ``None`` (everyone busy/offline/over-cap) is
    a normal outcome, not a failure: it is logged and the sweep moves on to
    the next conversation, never raising and never partially assigning.

    Double-assignment protection is two independent things, not one:

    1. **The fast-path guard, and the real protection for the common case**:
       ``lock`` (an ``asyncio.Lock``, defaulting to a process-wide singleton
       shared across every call that doesn't pass its own) makes a second
       overlapping tick return immediately -- before it even lists
       conversations -- while an earlier tick is still in flight. Checking
       ``lock.locked()`` and then acquiring it is not itself atomic, so a
       narrow window exists where two ticks both pass the check; in that
       case the loser simply blocks on the real ``acquire()`` until the
       winner finishes, then runs its own pass for real rather than being
       silently skipped.
    2. **The actual correctness backstop, including for #1's narrow window
       and for a multi-replica deployment**: immediately before writing,
       each candidate's assignee is re-read via ``assigner.resolve_assignee``
       and the conversation is skipped if it is no longer unassigned.
       ``lock`` cannot reach across processes, so a second replica's own
       sweep (or the event-driven handoff path itself) can still assign the
       same conversation in the gap between this tick's list fetch and its
       write; Chatwoot is the source of truth, and this re-check -- not the
       lock -- is what actually prevents a double assignment there. A
       reader who assumes the in-process lock alone is sufficient and
       removes this re-check when adding a second replica would silently
       reopen that gap.
    """
    if not settings.routing_enabled or not settings.routing_sweep_enabled:
        return {"scanned": 0, "assigned": 0, "skipped": 0}

    active_lock = lock if lock is not None else _default_lock
    if active_lock.locked():
        _log.info("routing_sweep_skipped_already_in_progress")
        return {"scanned": 0, "assigned": 0, "skipped": 0}

    async with active_lock:
        fetch = fetch_conversations or _fetch_open_unassigned
        clock = now or time.time

        try:
            conversations = await fetch(settings)
        except Exception as e:
            _log.error("routing_sweep_fetch_failed", error=str(e))
            return {"scanned": 0, "assigned": 0, "skipped": 0}

        min_age = settings.routing_sweep_min_age_seconds
        moment = clock()

        scanned = 0
        assigned = 0
        skipped = 0

        for conv in conversations:
            if not isinstance(conv, dict) or conv.get("status") != "open":
                continue
            if not _is_unassigned(conv):
                continue
            created_at = conv.get("created_at")
            if not isinstance(created_at, (int, float)):
                continue
            if moment - created_at < min_age:
                # Too fresh -- leave it to the event-driven handoff path.
                # This is the exact race the min-age gate exists to prevent.
                continue
            conv_id = conv.get("id")
            if not isinstance(conv_id, int):
                continue

            scanned += 1

            try:
                # Re-check right before writing -- see the docstring above
                # for why this, not the lock, is the real protection here.
                if await assigner.resolve_assignee(conv_id) is not None:
                    skipped += 1
                    continue

                channel = await assigner.resolve_channel(conv_id)
                agent_id = await routing_svc.pick_agent(channel)
                if agent_id is None:
                    _log.info("routing_sweep_no_agent_available", conversation_id=conv_id)
                    skipped += 1
                    continue

                await assigner.assign(conv_id, agent_id)
                assigned += 1
            except Exception as e:  # a bad conversation must never abort the sweep
                _log.error(
                    "routing_sweep_conversation_failed", conversation_id=conv_id, error=str(e)
                )
                skipped += 1

        _log.info("routing_sweep_done", scanned=scanned, assigned=assigned, skipped=skipped)
        return {"scanned": scanned, "assigned": assigned, "skipped": skipped}


def run_sweep_job(
    settings: Settings,
    routing_svc: _AgentPicker,
    assigner: _ConversationAssigner,
) -> dict[str, int]:
    """Run one sweep tick from a synchronous scheduler context.

    ``BackgroundScheduler`` calls plain sync functions; ``run_sweep`` is
    async. ``asyncio.run`` bridges that, the same solution
    ``run_presence_poll_job``/``run_report_job`` use elsewhere in this
    package. Wrapped in its own try/except on top of ``run_sweep``'s own --
    a failure here must never crash the scheduler thread or stop the next
    tick from running.
    """
    try:
        return asyncio.run(run_sweep(settings, routing_svc, assigner))
    except Exception as e:
        _log.error("routing_sweep_job_failed", error=str(e))
        return {}


def start_routing_sweeper(
    settings: Settings,
    routing_svc: _AgentPicker,
    assigner: _ConversationAssigner,
    *,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the in-app assignment sweeper when enabled; else return ``None``.

    Gated on both ``routing_sweep_enabled`` and ``routing_enabled`` (see
    ``run_sweep``'s docstring for why both) -- checked again here so a
    disabled sweep never even gets a scheduler job registered, not just a
    no-op tick. ``scheduler``/``job`` are injectable, matching
    ``start_metrics_scheduler``/``start_presence_poller``'s convention,
    purely so this is testable without a real ``BackgroundScheduler``
    thread. Takes ``routing_svc``/``assigner`` as required collaborators
    (not defaulted) since building them needs wiring (Firestore-backed
    stores, other P6 collaborators) this module has no business owning --
    that is main.py's job.
    """
    if not settings.routing_enabled or not settings.routing_sweep_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_sweep_job(settings, routing_svc, assigner))
    sched.add_job(
        run,
        trigger="interval",
        seconds=settings.routing_sweep_interval_seconds,
        id="routing_assignment_sweep",
        replace_existing=True,
    )
    sched.start()
    _log.info("routing_sweeper_started", interval_seconds=settings.routing_sweep_interval_seconds)
    return sched
