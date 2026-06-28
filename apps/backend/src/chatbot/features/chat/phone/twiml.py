"""Pure TwiML builders for the phone channel."""

from __future__ import annotations

from xml.sax.saxutils import quoteattr


def connect_stream_twiml(wss_url: str) -> str:
    """TwiML that bridges the call into a bidirectional Media Stream."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f"<Stream url={quoteattr(wss_url)} />"
        "</Connect></Response>"
    )
