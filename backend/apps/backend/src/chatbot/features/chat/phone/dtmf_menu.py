"""P11 Task 2 -- DTMF Menu sketch (superseded by Task 31's `ivr.py`).

Implements Appendix B's DTMF options using Twilio <Gather> inside TwiML, falling through
to the conversational bridge on timeout or key 0, ensuring callers are never trapped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import structlog

from chatbot.features.chat.phone.ivr_prompts import (  # noqa: F401 -- re-exported
    LANGUAGE_GATHER_PROMPT,
    MENU_EN,
    MENU_MS,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Task 31: the wording now lives in `ivr_prompts.py`, checked against Proton's
# SOP workbook itself (`test_ivr.py`'s drift tests), and is re-exported here
# under the historical names. The live IVR is `ivr.py` + `ivr_router.py`;
# `build_dtmf_twiml` / `handle_dtmf_digit` below are the original P11 sketch
# (its `wss://bridge.example.com` is a placeholder) and no call path uses them.
#
# History worth keeping: these were once hand-written and said "Sales" for
# option 2 and "Service and Product Enquiries" for option 3, where the SOP has
# 2 = Inquiry and 3 = Complaint.
PROMPT_EN = MENU_EN
PROMPT_MS = MENU_MS


def build_dtmf_twiml(
    language: Literal["en", "ms"] = "en", repeat_count: int = 0, enabled: bool = True
) -> str:
    """Generate TwiML for DTMF menu gather, or fall through if disabled/repeated."""
    if not enabled or repeat_count >= 2:
        # Fall through to conversational bridge directly
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<Response>\n"
            "    <Say>Connecting you to our assistant.</Say>\n"
            "    <Connect><Stream url=\"wss://bridge.example.com/voice\" /></Connect>\n"
            "</Response>"
        )

    prompt = PROMPT_EN if language == "en" else PROMPT_MS
    action_url = f"/voice/dtmf-action?lang={language}&repeat={repeat_count}"

    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<Response>\n"
        f"    <Gather numDigits=\"1\" timeout=\"5\" action=\"{action_url}\">\n"
        f"        <Say language=\"{language}\">{prompt}</Say>\n"
        "    </Gather>\n"
        "    <!-- Fallthrough on timeout or invalid key -->\n"
        "    <Say>Connecting to assistant.</Say>\n"
        "    <Connect><Stream url=\"wss://bridge.example.com/voice\" /></Connect>\n"
        "</Response>"
    )


def handle_dtmf_digit(
    digit: str | None, language: str = "en", repeat_count: int = 0
) -> dict[str, Any]:
    """Process DTMF digit selection."""
    if digit == "1":
        return {"target": "rsa", "action": "route_rsa", "context": "Roadside Assistance"}
    if digit == "2":
        return {"target": "bridge", "action": "context_inquiry", "context": "Inquiry"}
    if digit == "3":
        return {"target": "bridge", "action": "context_complaint", "context": "Complaint"}
    if digit == "0":
        if repeat_count < 1:
            return {"target": "repeat_menu", "action": "repeat", "repeat_count": repeat_count + 1}
        return {"target": "bridge", "action": "fallthrough", "context": "Conversational Bridge"}
    # Timeout (None) or invalid key -> fallthrough to bridge
    return {"target": "bridge", "action": "fallthrough", "context": "Conversational Bridge"}
