"""P11 Task 2 -- DTMF Menu inside the conversational AI voice bridge.

Implements Appendix B's DTMF options using Twilio <Gather> inside TwiML, falling through
to the conversational bridge on timeout or key 0, ensuring callers are never trapped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Copied VERBATIM from `deploy/twilio/ivr-studio-flow.json`'s `main_menu_en` /
# `main_menu_ms` / `language_gather` `say` properties, which is where Appendix B's
# wording actually lives. `test_dtmf_menu.py` reads that JSON and compares, so
# these cannot drift from it silently.
#
# They were previously hand-written approximations that got the menu wrong: option
# 2 read "Sales" and option 3 "Service and Product Enquiries", where Appendix B
# has 2 = Inquiry and 3 = Complaint. A caller with a complaint pressing 3 was
# being labelled a service enquiry, and the Malay string contained the English
# word "for". Both were asserted by tests named "matches appendix b verbatim".
PROMPT_EN = (
    "For Roadside Assistance, press 1. For Inquiry, press 2. "
    "For Complaint, press 3. To repeat, please press zero."
)

PROMPT_MS = (
    "Untuk Bantuan Kerosakan, tekan 1. Untuk Pertanyaan, tekan 2. "
    "Untuk Aduan, tekan 3. Untuk Ulangan, sila tekan sifar."
)

LANGUAGE_GATHER_PROMPT = (
    "Welcome to Proton e.MAS service centre. "
    "Selamat datang ke Pusat Perkhidmatan Proton e.MAS. "
    "For English, press 1. Untuk Bahasa Melayu, tekan 2."
)


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
    elif digit == "2":
        return {"target": "bridge", "action": "context_inquiry", "context": "Inquiry"}
    elif digit == "3":
        return {"target": "bridge", "action": "context_complaint", "context": "Complaint"}
    elif digit == "0":
        if repeat_count < 1:
            return {"target": "repeat_menu", "action": "repeat", "repeat_count": repeat_count + 1}
        return {"target": "bridge", "action": "fallthrough", "context": "Conversational Bridge"}
    else:
        # Timeout (None) or invalid key -> fallthrough to bridge
        return {"target": "bridge", "action": "fallthrough", "context": "Conversational Bridge"}
