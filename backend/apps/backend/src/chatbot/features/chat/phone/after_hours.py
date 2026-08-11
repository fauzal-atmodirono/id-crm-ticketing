"""P11 Task 3 -- After-hours message and RSA 24/7 bypass.

Handles out-of-hours phone routing with bilingual greetings and voicemail options.
CRITICAL SAFETY INVARIANT: An out-of-hours RSA call MUST BYPASS voicemail and route
directly to RSA targets (PHONE_RSA_AFTER_HOURS_BYPASS defaults to True).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

AFTER_HOURS_PROMPT_EN = (
    "Thank you for calling. Our offices are currently closed. "
    "Our operating hours are 8 AM to 6 PM Monday through Friday. "
    "Please leave a message after the tone and our customer service team will contact you on the next business day."
)

AFTER_HOURS_PROMPT_MS = (
    "Terima kasih kerana menghubungi kami. Pejabat kami kini ditutup. "
    "Waktu operasi kami adalah dari 8 pagi hingga 6 petang, Isnin hingga Jumaat. "
    "Sila tinggalkan mesej selepas bunyi nada dan pasukan kami akan menghubungi anda pada hari bekerja seterusnya."
)


def evaluate_after_hours_call(
    is_rsa: bool,
    is_within_business_hours: bool,
    settings: Settings,
) -> dict[str, Any]:
    """Evaluate whether an incoming call should go to after-hours voicemail or bypass."""
    if not settings.phone_after_hours_enabled:
        return {"action": "connect_in_hours", "reason": "after_hours_disabled"}

    # Business hours evaluation (fail open to in-hours on None/error)
    if is_within_business_hours:
        return {"action": "connect_in_hours", "reason": "within_business_hours"}

    # OUT OF HOURS BRANCH
    # Critical RSA bypass invariant: RSA callers 24/7 bypass after-hours message
    if is_rsa:
        if settings.phone_rsa_after_hours_bypass:
            _log.info("after_hours_rsa_bypass_activated", is_rsa=True)
            return {"action": "route_rsa_bypass", "reason": "rsa_24_7_bypass"}
        else:
            _log.warning("after_hours_rsa_bypass_disabled_by_config", is_rsa=True)

    # Standard out-of-hours call -> play prompt and offer voicemail
    return {
        "action": "play_after_hours_voicemail",
        "reason": "out_of_hours",
        "prompt_en": AFTER_HOURS_PROMPT_EN,
        "prompt_ms": AFTER_HOURS_PROMPT_MS,
    }
