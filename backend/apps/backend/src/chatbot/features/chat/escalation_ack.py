"""Which transport carries the customer acknowledgement, per channel.

Escalation has three legs: notify the PIC, forward to the dealer, and
acknowledge the customer. The first two are channel-agnostic -- they are email
to a configured address and do not care where the case came from. Only the
third is channel-specific, because "tell the customer we are on it" means
sending mail on an Email inbox and posting into the thread everywhere else.

Isolating that decision here keeps `EscalationNotifier` from growing channel
knowledge: it asks for a transport and dispatches, and adding a channel is a
line in one dict rather than a new branch in the notifier.
"""

from __future__ import annotations

from typing import Literal

AckTransport = Literal["email", "conversation", "none"]

_BY_CHANNEL: dict[str, AckTransport] = {
    "Channel::Email": "email",
    "Channel::Whatsapp": "conversation",
    "Channel::TwilioSms": "conversation",
    "Channel::FacebookPage": "conversation",
    "Channel::Instagram": "conversation",
    "Channel::WebWidget": "conversation",
    "Channel::Api": "conversation",
    # A voice call has no thread to post into and no address to mail. The
    # caller was already spoken to, so there is nothing to acknowledge in
    # writing -- the PIC and dealer legs still fire.
    "Channel::Voice": "none",
}

_FALLBACK: AckTransport = "conversation"


def ack_transport(channel_type: str | None) -> AckTransport:
    """Resolve a Chatwoot ``channel_type`` to its acknowledgement transport.

    Unknown and missing channel types fall back to ``conversation``, NOT to
    ``none``. An unknown channel almost certainly has a conversation thread,
    and defaulting to silence is precisely the defect this package exists to
    fix: before P2, everything that was not ``Channel::Email`` acknowledged
    nobody, silently.
    """
    if not channel_type:
        return _FALLBACK
    return _BY_CHANNEL.get(channel_type, _FALLBACK)
