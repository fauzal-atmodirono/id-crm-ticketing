"""The follow-up ladder: chat -> automatic call -> operations ticket.

After the tracking simulator sends a driver-app alert, `run_ladder` waits
`logisly_no_reply_call_seconds`. If the driver has not written anything in
that conversation by then (and it is still the trip's latest alert), the
agent calls the driver app. `watch_call` then gives the call the ring time
plus a margin to connect; a call that never reaches the media stream was
declined or not answered, so the trip is marked and operations get a
pre-filled `driver_unreachable` ticket with the conversation reopened. No
call is placed during quiet hours (22:00-05:00 WIB, `milestones.is_quiet`).

Tasks run in the backend process (the demo state is in memory too) and are
kept referenced so they are not garbage-collected mid-wait.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from chatbot.features.logisly import milestones
from chatbot.features.logisly import trips as trips_module
from chatbot.features.logisly.escalation import file_escalation

if TYPE_CHECKING:
    from chatbot.features.logisly.channel import ChatwootDriverChannel
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

RING_SECONDS = 25
CONNECT_MARGIN_SECONDS = 15

_TASKS: set[asyncio.Task[Any]] = set()


def _utcnow() -> datetime:
    return datetime.now(UTC)

StartCall = Callable[["Settings", str], Awaitable[str]]


def spawn(coro: Awaitable[Any]) -> asyncio.Task[Any]:
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def is_latest(session_id: str) -> bool:
    active = trips_module.ACTIVE_SESSIONS.get(session_id)
    if not active:
        return False
    latest = None
    for sid, other in trips_module.ACTIVE_SESSIONS.items():
        if other["order_id"] == active["order_id"]:
            latest = sid
    return latest == session_id


def mark_connected(session_id: str) -> None:
    active = trips_module.ACTIVE_SESSIONS.get(session_id)
    if active is not None:
        active["call_connected"] = True


async def watch_call(
    settings: Settings,
    channel: ChatwootDriverChannel,
    session_id: str,
    wait_seconds: float = RING_SECONDS + CONNECT_MARGIN_SECONDS,
) -> None:
    """Escalate as driver_unreachable if the call never connected."""
    await asyncio.sleep(wait_seconds)
    active = trips_module.ACTIVE_SESSIONS.get(session_id) or {}
    if active.get("call_connected"):
        return
    order_id = active.get("order_id")
    if order_id:
        trips_module.TRIPS.update(
            order_id, "alert:no_answer", "Driver tidak mengangkat telepon otomatis"
        )
    if active.get("milestone"):
        topic = f"checklist milestone '{trips_module.milestone_label(active['milestone'])}'"
    else:
        label = trips_module.ANOMALIES.get(active.get("anomaly", ""), {}).get("label", "alert")
        topic = f"'{label}'"
    summary = (
        f"Driver tidak membalas pesan Logisly tentang {topic} dan tidak mengangkat "
        "telepon otomatis. Perlu dihubungi manual oleh tim operasional."
    )
    state = {
        "handoff_reason": "driver_unreachable",
        "handoff_summary": summary,
        "order_id": order_id,
    }
    await file_escalation(settings, session_id, state, channel=channel)
    try:
        await channel.set_status(int(session_id.removeprefix("crm-")), "open")
    except (httpx.HTTPError, ValueError) as exc:
        _log.warning("logisly_unreachable_reopen_failed", session_id=session_id, error=str(exc))
    _log.info("logisly_driver_unreachable", session_id=session_id, order_id=order_id)


async def place_call(
    settings: Settings,
    channel: ChatwootDriverChannel,
    session_id: str,
    start_call: StartCall,
    wait_seconds: float = RING_SECONDS + CONNECT_MARGIN_SECONDS,
) -> str:
    """Ring the driver app and start watching for an answer."""
    active = trips_module.ACTIVE_SESSIONS.get(session_id)
    if active is not None:
        active["call_connected"] = False
    call_sid = await start_call(settings, session_id)
    if active is not None:
        trips_module.TRIPS.update(active["order_id"], "alert:call", "Telepon otomatis ke driver")
    spawn(watch_call(settings, channel, session_id, wait_seconds))
    return call_sid


async def run_ladder(
    settings: Settings,
    channel: ChatwootDriverChannel,
    session_id: str,
    start_call: StartCall,
    delay_seconds: float,
    wait_seconds: float = RING_SECONDS + CONNECT_MARGIN_SECONDS,
) -> None:
    await asyncio.sleep(delay_seconds)
    if not is_latest(session_id):
        return
    if milestones.is_quiet(_utcnow()):
        # 22:00-05:00 WIB: no proactive contact, same rule as the opening message.
        _log.info("logisly_ladder_quiet_hours", session_id=session_id)
        return
    try:
        messages = await channel.messages(int(session_id.removeprefix("crm-")))
    except (httpx.HTTPError, ValueError) as exc:
        _log.warning("logisly_ladder_check_failed", session_id=session_id, error=str(exc))
        return
    if any(m.get("message_type") == 0 for m in messages):
        return  # the driver answered in chat; the agent is handling it
    try:
        await place_call(settings, channel, session_id, start_call, wait_seconds)
    except httpx.HTTPError as exc:
        _log.warning("logisly_ladder_call_failed", session_id=session_id, error=str(exc))
        return
    _log.info("logisly_ladder_called", session_id=session_id)
