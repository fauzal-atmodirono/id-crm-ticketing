"""Pure TwiML builders for the phone channel."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr


def connect_stream_twiml(wss_url: str, announcement: str | None = None) -> str:
    """TwiML that bridges the call into a bidirectional Media Stream,
    optionally preceded by a spoken announcement.

    Package C Task 6 review fix (carried from Task 5): Malaysia's PDPA
    requires the recorded-line notice be spoken BEFORE recording can
    start. A `<Say>` here runs deterministically before `<Connect><Stream>`
    -- and therefore before Twilio ever opens the Media Stream whose
    "start" event is what triggers `PhoneBridge`/`CallControl.
    start_recording` -- unlike the best-effort in-session text hint in
    `bridge.py`'s `_maybe_start_recording`, which only asks the live model
    to say the notice and cannot prove it ran first. That text hint is
    left in place as a secondary reinforcement (some models may not act on
    a `<Say>` alone, e.g. if the caller talks over it) -- this does not
    replace it, it just makes the notice's precedence provable at the
    TwiML level instead of merely "best effort".

    `announcement=None` (the default) is BYTE-IDENTICAL to this
    function's shape before this parameter existed.
    """
    say = f"<Say>{escape(announcement)}</Say>" if announcement else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response>{say}<Connect>"
        f"<Stream url={quoteattr(wss_url)} />"
        "</Connect></Response>"
    )


def fallback_twiml(message: str) -> str:
    """TwiML spoken on a live call after a transfer attempt could not be
    completed (no answer / busy / failed -- see
    `/webhooks/phone/dial-status`): say `message`, then hang up.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{escape(message)}</Say><Hangup/></Response>"
    )
