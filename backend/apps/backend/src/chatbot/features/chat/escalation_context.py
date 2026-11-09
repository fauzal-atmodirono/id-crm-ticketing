"""Who escalated, and where the SLA stood, derived on the backend (FIX-17).

The agent service does not send either (and cannot be rebuilt yet), so the
backend works them out from data it can already read:

* **sla_status** -- the newest SLA-related row in the case's own audit trail
  (an ``sla_alert`` event or a legacy breach / reminder / tier-2 marker), else
  ``ACKNOWLEDGED`` / ``RESPONDED`` when the case has been answered, else
  ``WITHIN_SLA``. Reads only what the SLA engine already recorded.
* **actor** -- the Chatwoot user who applied the ``escalate`` label, read from
  the conversation's activity messages ("<name> added escalate"). Chatwoot's
  activity text carries a display name, not a user id, so ``actor_user_id`` is
  None here; the name is what RFP 3.2.6 asks for.

Both are best-effort and never raise: a failed lookup falls back to the
service actor with ``actor_source="agent-service"``.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.sla import (
    ACKNOWLEDGED_STATE,
    FIRST_RESPONSE_STATE,
    NO_RESPONSE_BREACH,
    REMINDER_WARNING_STATE,
    TIER2_ESCALATION,
    UNRESOLVED_BREACH,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from chatbot.features.chat.ports import AuditLogPort

_log = structlog.get_logger(__name__)

_SLA_MARKERS = {
    NO_RESPONSE_BREACH,
    UNRESOLVED_BREACH,
    REMINDER_WARNING_STATE,
    TIER2_ESCALATION,
}
# Chatwoot activity text: "Aisyah added escalate, dept_sales" / "... added escalate".
_LABEL_ACTIVITY = re.compile(r"^(?P<name>.+?) added (?P<labels>.+)$")
_ACTIVITY_MESSAGE_TYPE = 2
SOURCE_ACTIVITY = "chatwoot-activity"
SOURCE_AGENT_SERVICE = "agent-service"
SOURCE_AUTOMATION = "chatwoot-automation"
# Chatwoot labels automation-rule actions with this author name.
_AUTOMATION_AUTHOR = "Automation System"
# Hard ceiling on the whole derivation. The agent's HTTP timeout is 10 s and the
# notify route must not push the send past it; module-level so tests can shrink it.
DERIVE_TIMEOUT_SECONDS = 2.0


async def derive_context(
    audit: AuditLogPort | None,
    chatwoot_request: Callable[..., Awaitable[Any]],
    conv_id: str,
    *,
    need_actor: bool,
    need_sla: bool,
) -> tuple[str | None, str | None, str]:
    """(actor_name, sla_status, actor_source), bounded by DERIVE_TIMEOUT_SECONDS.

    The two lookups run concurrently; whichever finished inside the budget is
    kept and the other is cancelled, so a slow Chatwoot cannot cost the SLA
    status (or the send) more than the budget.
    """
    actor_task = (
        asyncio.ensure_future(derive_escalating_agent(chatwoot_request, conv_id))
        if need_actor
        else None
    )
    sla_task = asyncio.ensure_future(derive_sla_status(audit, conv_id)) if need_sla else None
    tasks = [t for t in (actor_task, sla_task) if t is not None]
    if tasks:
        _done, pending = await asyncio.wait(tasks, timeout=DERIVE_TIMEOUT_SECONDS)
        for t in pending:
            t.cancel()
        if pending:
            _log.warning("escalation_context_derivation_timed_out", conv_id=conv_id)

    def _result(task: asyncio.Future | None) -> str | None:
        if task is None or not task.done() or task.cancelled() or task.exception() is not None:
            return None
        return task.result()

    actor, sla = _result(actor_task), _result(sla_task)
    if actor:
        source = SOURCE_AUTOMATION if actor == _AUTOMATION_AUTHOR else SOURCE_ACTIVITY
    else:
        source = SOURCE_AGENT_SERVICE
    return actor, sla, source


async def derive_sla_status(audit: AuditLogPort | None, conv_id: str) -> str | None:
    if audit is None:
        return None
    try:
        rows = await audit.list_for_ticket(conv_id)
    except Exception as exc:
        _log.warning("escalation_sla_status_lookup_failed", conv_id=conv_id, error=str(exc))
        return None
    rows = sorted(rows, key=lambda r: r.at)
    alerts = [r for r in rows if r.event_type == "sla_alert" or r.to_state in _SLA_MARKERS]
    if alerts:
        return alerts[-1].sla_status or alerts[-1].to_state
    states = {r.to_state for r in rows}
    if ACKNOWLEDGED_STATE in states:
        return ACKNOWLEDGED_STATE
    if FIRST_RESPONSE_STATE in states:
        return "RESPONDED"
    return "WITHIN_SLA"


async def derive_escalating_agent(
    chatwoot_request: Callable[..., Awaitable[Any]], conv_id: str
) -> str | None:
    """Display name of whoever added the ``escalate`` label, or None."""
    try:
        data = await chatwoot_request("GET", f"/conversations/{conv_id}/messages", None)
    except Exception as exc:
        _log.warning("escalation_actor_lookup_failed", conv_id=conv_id, error=str(exc))
        return None
    messages = data.get("payload") if isinstance(data, dict) else data
    if not isinstance(messages, list):
        return None
    for msg in sorted(
        (m for m in messages if isinstance(m, dict)),
        key=lambda m: m.get("created_at") or 0,
        reverse=True,
    ):
        if msg.get("message_type") != _ACTIVITY_MESSAGE_TYPE:
            continue
        match = _LABEL_ACTIVITY.match(str(msg.get("content") or "").strip())
        if match and "escalate" in re.split(r"[,\s]+", match.group("labels")):
            return match.group("name").strip()
    return None
