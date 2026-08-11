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

# Copied VERBATIM from `deploy/twilio/ivr-studio-flow.json`'s `after_hours_en` /
# `after_hours_ms` / `vm_prompt` `say` properties -- the actual home of Appendix
# B's wording. `test_phone_after_hours.py` reads that JSON and compares, so these
# cannot drift from it silently.
#
# The previous strings were hand-written and **told the customer the wrong
# operating hours**: "8 AM to 6 PM Monday through Friday", with no mention of the
# Saturday/Sunday/Public Holiday window Appendix B has. A weekend caller was
# being told the business is closed for the day when the published hours say
# 9:00 AM to 5:00 PM. A test named "matches appendix b verbatim" asserted the
# wrong text.
AFTER_HOURS_PROMPT_EN = (
    "Thank you for calling Proton e.MAS. Our operating hours are Monday to "
    "Friday, 8:30 AM to 5:30 PM, and Saturday, Sunday and Public Holidays, "
    "9:00 AM to 5:00 PM. Please leave your message and contact details. Our "
    "customer support team will reach out on the next business day."
)

AFTER_HOURS_PROMPT_MS = (
    "Terima kasih kerana menghubungi Proton e.MAS. Waktu operasi kami adalah "
    "Isnin hingga Jumaat, 8.30 pagi hingga 5.30 petang, dan Sabtu, Ahad dan "
    "Cuti Umum, 9.00 pagi hingga 5.00 petang. Sila tinggalkan mesej dan "
    "maklumat perhubungan anda. Pasukan khidmat pelanggan kami akan menghubungi "
    "anda pada hari bekerja berikutnya."
)

VOICEMAIL_PROMPT = (
    "Please leave your message and contact details after the tone. "
    "Sila tinggalkan mesej dan maklumat perhubungan anda selepas nada."
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
