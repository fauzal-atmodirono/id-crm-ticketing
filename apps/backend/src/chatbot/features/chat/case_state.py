"""Case status model + audit-recording transition helper."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from chatbot.features.chat.ports import AuditEntry

if TYPE_CHECKING:
    from chatbot.features.chat.ports import AuditLogPort

# Chatwoot custom-attribute key used to surface the live case_state in the
# right panel.  Written by EscalationNotifier and _log_status_transition.
CHATWOOT_CASE_STATE_ATTR = "case_state"


class CaseState(StrEnum):
    NEW = "NEW"
    OPEN = "OPEN"
    WIP = "WIP"
    PENDING = "PENDING"
    SOLVED = "SOLVED"


async def record_transition(
    audit: AuditLogPort,
    *,
    ticket_id: str,
    session_id: str,
    actor: str,
    from_state: CaseState,
    to_state: CaseState,
    at: str,
    remark: str = "",
) -> None:
    await audit.append(
        AuditEntry(
            ticket_id=ticket_id,
            session_id=session_id,
            actor=actor,
            from_state=from_state.value,
            to_state=to_state.value,
            at=at,
            remark=remark,
        )
    )
