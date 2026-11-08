"""P11 Task 3 -- After-hours message and RSA 24/7 bypass.

Handles out-of-hours phone routing with bilingual greetings and voicemail options.
CRITICAL SAFETY INVARIANT: An out-of-hours RSA call MUST BYPASS voicemail and route
directly to RSA targets (PHONE_RSA_AFTER_HOURS_BYPASS defaults to True).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.phone.ivr_prompts import (  # noqa: F401 -- re-exported
    AFTER_HOURS_EN,
    AFTER_HOURS_MS,
    VOICEMAIL_PROMPT,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Task 31: the wording lives in `ivr_prompts.py` (one module for every IVR
# prompt) and is re-exported here under the historical names; it is still
# verbatim the Studio flow's `after_hours_en` / `after_hours_ms` / `vm_prompt`,
# which `test_phone_after_hours.py` checks. The IVR (`ivr_router.py`) is this
# evaluator's caller.
#
# History worth keeping: the first hand-written version told callers the wrong
# hours ("8 AM to 6 PM Monday through Friday", no weekend window).
AFTER_HOURS_PROMPT_EN = AFTER_HOURS_EN
AFTER_HOURS_PROMPT_MS = AFTER_HOURS_MS


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
        _log.warning("after_hours_rsa_bypass_disabled_by_config", is_rsa=True)

    # Standard out-of-hours call -> play prompt and offer voicemail
    return {
        "action": "play_after_hours_voicemail",
        "reason": "out_of_hours",
        "prompt_en": AFTER_HOURS_PROMPT_EN,
        "prompt_ms": AFTER_HOURS_PROMPT_MS,
    }
