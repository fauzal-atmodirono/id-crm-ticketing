"""Periodic scanner that drives idle → warn → auto-close transitions.

The agent service is otherwise webhook-only; this adds one asyncio loop
(started from the app lifespan when LIFECYCLE_ENABLED) that ticks every
`lifecycle_scan_interval_seconds` over pending + open conversations. For each
chat-like, bot-phase (unassigned) conversation it computes idle time from
Chatwoot's `last_activity_at` and applies the next transition. Idempotent: the
transition only fires from the matching current state, so a re-scan can't
double-post. Fail-open: any per-conversation error is logged and skipped.

Email is excluded (async by nature; the SOP email flow has no idle-close).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.clients.deps import get_chatwoot_client
from app.config import get_settings
from app.services import business_hours, lifecycle, lifecycle_store

logger = logging.getLogger(__name__)

_SKIP_CHANNELS = {"Channel::Email"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def decide_idle_action(
    state: str,
    idle_minutes: float,
    state_age_minutes: float,
    warn_after: float,
    close_after: float,
    confirm_after: float,
) -> str | None:
    if state == lifecycle.ACTIVE and idle_minutes >= warn_after:
        return "warn"
    if state == lifecycle.IDLE_WARNED and idle_minutes >= close_after:
        return "close"
    if state in (lifecycle.AWAITING_RESOLUTION, lifecycle.AWAITING_SURVEY):
        if state_age_minutes >= confirm_after:
            return "resolve_timeout"
    return None


def _payload_list(raw) -> list:
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, dict) and isinstance(data.get("payload"), list):
            return data["payload"]
        if isinstance(raw.get("payload"), list):
            return raw["payload"]
    return raw if isinstance(raw, list) else []


def _minutes_since(epoch_seconds, now: datetime) -> float | None:
    if not epoch_seconds:
        return None
    return (now.timestamp() - float(epoch_seconds)) / 60.0


async def scan_once() -> None:
    settings = get_settings()
    chatwoot = get_chatwoot_client()
    now = _now()
    inbox_cache: dict[int, dict] = {}

    conversations: list = []
    for status in ("pending", "open"):
        try:
            conversations += _payload_list(
                await chatwoot.list_conversations(status=status, assignee_type="all")
            )
        except Exception:
            logger.exception("lifecycle_scanner: failed to list %s conversations", status)

    for conv in conversations:
        try:
            await _process_one(conv, settings, chatwoot, now, inbox_cache)
        except Exception:
            logger.exception(
                "lifecycle_scanner: failed processing conversation %s",
                conv.get("id") if isinstance(conv, dict) else conv,
            )


async def _get_inbox(chatwoot, inbox_id, cache: dict) -> dict:
    if inbox_id not in cache:
        cache[inbox_id] = await chatwoot.get_inbox(inbox_id)
    return cache[inbox_id]


async def _process_one(conv, settings, chatwoot, now, inbox_cache) -> None:
    if not isinstance(conv, dict):
        return
    conversation_id = conv.get("id")
    inbox_id = conv.get("inbox_id")
    if conversation_id is None or inbox_id is None:
        return

    # Bot-phase only: once an agent is assigned, they own the conversation.
    if (conv.get("meta") or {}).get("assignee"):
        return

    inbox = await _get_inbox(chatwoot, inbox_id, inbox_cache)
    channel = inbox.get("channel_type")
    if channel in _SKIP_CHANNELS:
        return

    row = await lifecycle_store.get_row(conversation_id)
    if row is None:
        await lifecycle_store.seed_active(conversation_id, channel=channel)
        state = lifecycle.ACTIVE
        state_changed_at = now
    else:
        state = row.state
        state_changed_at = row.state_changed_at
    if state == lifecycle.CLOSED:
        return

    idle = _minutes_since(conv.get("last_activity_at"), now)
    if idle is None:
        return
    # state_changed_at from sqlite may be tz-naive; normalize to UTC.
    if state_changed_at.tzinfo is None:
        state_changed_at = state_changed_at.replace(tzinfo=timezone.utc)
    state_age = (now - state_changed_at).total_seconds() / 60.0

    in_hours = business_hours.is_within_business_hours(inbox, now)
    warn_after = settings.lifecycle_idle_warn_minutes
    grace = (
        settings.lifecycle_idle_close_grace_minutes
        if in_hours
        else settings.lifecycle_idle_close_out_of_hours_grace_minutes
    )
    close_after = warn_after + grace
    confirm_after = settings.lifecycle_confirm_grace_minutes

    action = decide_idle_action(
        state, idle, state_age, warn_after, close_after, confirm_after
    )
    if action is None:
        return

    if action == "warn":
        await lifecycle._post(conversation_id, lifecycle.IDLE_WARNING_DEFAULT)
        await lifecycle_store.transition(
            conversation_id, lifecycle.IDLE_WARNED, warned_at=now
        )
        await lifecycle._mirror_state(conversation_id, lifecycle.IDLE_WARNED)
    elif action == "close":
        await lifecycle._post(conversation_id, lifecycle.IDLE_CLOSE_DEFAULT)
        await lifecycle._post(conversation_id, lifecycle.RESOLUTION_PROMPT_DEFAULT)
        await lifecycle_store.transition(conversation_id, lifecycle.AWAITING_RESOLUTION)
        await lifecycle._mirror_state(conversation_id, lifecycle.AWAITING_RESOLUTION)
    elif action == "resolve_timeout":
        await lifecycle_store.transition(conversation_id, lifecycle.CLOSED)
        await lifecycle._mirror_state(conversation_id, lifecycle.CLOSED)
        await lifecycle._resolve(conversation_id)


async def run_scanner() -> None:
    settings = get_settings()
    logger.info("lifecycle_scanner: started (interval=%ss)", settings.lifecycle_scan_interval_seconds)
    while True:
        try:
            await scan_once()
        except Exception:
            logger.exception("lifecycle_scanner: scan tick failed")
        await asyncio.sleep(settings.lifecycle_scan_interval_seconds)
