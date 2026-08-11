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

PROMPT_EN = (
    "Press 1 for Roadside Assistance. "
    "Press 2 for Sales. "
    "Press 3 for Service and Product Enquiries. "
    "Press 0 to repeat options."
)

PROMPT_MS = (
    "Tekan 1 untuk Bantuan Tunda dan Bantuan Tepi Jalan. "
    "Tekan 2 untuk Jualan. "
    "Tekan 3 for Pertanyaan Perkhidmatan dan Produk. "
    "Tekan 0 untuk ulang."
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
        return {"target": "bridge", "action": "context_sales", "context": "Sales Inquiry"}
    elif digit == "3":
        return {"target": "bridge", "action": "context_service", "context": "Service Enquiry"}
    elif digit == "0":
        if repeat_count < 1:
            return {"target": "repeat_menu", "action": "repeat", "repeat_count": repeat_count + 1}
        return {"target": "bridge", "action": "fallthrough", "context": "Conversational Bridge"}
    else:
        # Timeout (None) or invalid key -> fallthrough to bridge
        return {"target": "bridge", "action": "fallthrough", "context": "Conversational Bridge"}
