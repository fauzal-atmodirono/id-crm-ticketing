"""Package C Task 6: who a live call gets transferred to, and the TwiML
that dials them.

`HandoffTarget` is a target *descriptor*, not a bare phone number: Twilio
cannot connect a WhatsApp call to any PSTN endpoint (see the design doc's
appendix §12.3), so a future WhatsApp-capable resolver needs to be able to
return a Twilio Client identifier instead of an E.164 number. `kind`
distinguishes the two so `dial_twiml` knows which TwiML noun to emit;
today only `HandoffTargetResolver`'s "pstn" branch is reachable.

Phase 1 (this task) resolves a single static hunt-group number
(`phone_handoff_target_number`). The routing-backed per-agent
implementation described in the design doc's §5.2 is a second
implementation of the same `resolve() -> HandoffTarget | None` interface,
added once that decision lands -- not built speculatively here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

import structlog

from chatbot.features.metrics.business_hours import working_minutes_between

if TYPE_CHECKING:
    from chatbot.features.chat.ports import ConversationLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HandoffTarget:
    """`kind` is `"pstn"` (dial_twiml emits `<Number>`) or `"client"`
    (emits `<Client>`, for a Twilio Client/softphone identifier -- not
    reachable via Phase 1's static resolver, kept for the interface's
    sake). `value` is the E.164 number or Client identifier respectively.
    """

    kind: str
    value: str


class HandoffTargetResolver:
    """Phase 1: resolve the static `phone_handoff_target_number` hunt-group
    number, gated by `phone_handoff_enabled` and by whether the tenant's
    default Chatwoot inbox is currently within its configured business
    hours -- reusing `features.metrics.business_hours.
    working_minutes_between` (the SAME row-shape parser the BigQuery ETL
    already uses for RSA/turnaround-time reporting) rather than adding a
    second notion of "open" (design doc §5.3). Returning `None` here always
    means "do not attempt a transfer right now" -- the bridge's caller
    cannot distinguish disabled / unconfigured / out-of-hours from this
    return value alone, and by design does not need to: all three fall
    back identically to today's ticket-only behaviour (see bridge.py's
    `_attempt_transfer`). A dial that actually starts but goes unanswered
    is a DIFFERENT case, handled downstream by `/webhooks/phone/dial-status`.
    """

    def __init__(self, settings: Settings, log_port: ConversationLogPort) -> None:
        self._settings = settings
        self._log_port = log_port

    async def resolve(self) -> HandoffTarget | None:
        if not self._settings.phone_handoff_enabled:
            return None
        number = self._settings.phone_handoff_target_number.strip()
        if not number:
            return None
        if not await self._within_business_hours():
            return None
        return HandoffTarget(kind="pstn", value=number)

    async def _within_business_hours(self) -> bool:
        """Fail OPEN (True): an unconfigured inbox, or any failure reading
        it, must not silently disable the whole handoff feature -- matches
        `working_minutes_between`'s own "not configured -> always open"
        default (and `agent/`'s `is_within_business_hours`, the sibling
        point-in-time check for the same Chatwoot `working_hours` shape).
        """
        inbox_id = self._settings.chatwoot_inbox_id
        if not inbox_id:
            return True
        try:
            inbox = await self._log_port.get_inbox_working_hours(inbox_id)
        except Exception as e:
            _log.error("phone_handoff_hours_check_failed", error=str(e))
            return True
        if inbox is None:
            return True
        now = datetime.now(UTC)
        # A 1-minute probe window: working_minutes_between computes a
        # DURATION, not a point-in-time boolean, so this reuses it (rather
        # than a second parser) by asking "does the next minute overlap the
        # working-hours window at all".
        return working_minutes_between(now, now + timedelta(minutes=1), inbox) > 0


def dial_twiml(target: HandoffTarget, action_url: str, timeout: int) -> str:
    """TwiML that dials `target` and posts the outcome to `action_url`
    (see `/webhooks/phone/dial-status`). `timeout` is Twilio's `<Dial>`
    ring timeout in seconds before it gives up and fires `action` with
    `DialCallStatus=no-answer`.
    """
    noun = (
        f"<Client>{escape(target.value)}</Client>"
        if target.kind == "client"
        else f"<Number>{escape(target.value)}</Number>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Dial action={quoteattr(action_url)} timeout="{int(timeout)}">'
        f"{noun}</Dial></Response>"
    )
