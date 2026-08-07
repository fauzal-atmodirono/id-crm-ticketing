"""Link an emailed reply back onto the conversation it was escalated from.

Escalation mail leaves with a correlation token (`Reply-To:
…+case<id>@…` plus a `[CASE-<id>]` subject tag -- see the backend's
EscalationNotifier). The reply comes back through the tenant's ordinary
Email inbox, so Chatwoot files it as a NEW conversation with no connection
to the original. This module puts that connection back: it reads the token,
verifies the sender, copies the reply onto the original conversation, and
resolves the throwaway one.

Fail-open like every other background task here: an unparseable payload, an
unknown sender, or an unreachable backend means the reply is simply not
linked -- logged and skipped, never raised. For the sender check that
posture is also the safe one: refusing to link an unverifiable sender is
what stops someone guessing a conversation id to inject a private note.
"""

import logging
import re

logger = logging.getLogger(__name__)

# `support+case42@host` in a To/Cc header — the primary correlation key.
_ADDRESS_TOKEN = re.compile(r"\+case(\d+)@", re.IGNORECASE)
# `[CASE-42]` in the subject — the fallback for relays that strip
# plus-addressing.
_SUBJECT_TOKEN = re.compile(r"\[CASE-(\d+)\]", re.IGNORECASE)

# Where a mail client starts quoting what it is replying to. Anchored at a
# line start so the words can't match mid-sentence.
_TRAIL_MARKERS = (
    re.compile(r"^On .{0,200}\bwrote:\s*$", re.MULTILINE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^_{5,}\s*$", re.MULTILINE),
    re.compile(r"^From: .+$", re.MULTILINE),
    re.compile(r"^>", re.MULTILINE),
)


def _email_meta(message: dict) -> dict:
    attrs = message.get("content_attributes")
    if not isinstance(attrs, dict):
        return {}
    meta = attrs.get("email")
    return meta if isinstance(meta, dict) else {}


def extract_case_id(message: dict) -> int | None:
    """The escalated conversation id this message is a reply to, or None.

    Checks the To/Cc addresses first (a header the sender cannot edit by
    accident), then the subject tag.
    """
    meta = _email_meta(message)
    for key in ("to", "cc"):
        addresses = meta.get(key)
        if isinstance(addresses, str):
            addresses = [addresses]
        if not isinstance(addresses, list):
            continue
        for address in addresses:
            match = _ADDRESS_TOKEN.search(str(address))
            if match:
                return int(match.group(1))
    match = _SUBJECT_TOKEN.search(str(meta.get("subject") or ""))
    return int(match.group(1)) if match else None


def strip_quoted_trail(text: str) -> str:
    """Drop everything from the first quote marker onward.

    Keeps the reply the sender actually typed, so neither the private note
    nor the AI draft re-ingests the whole thread. If stripping would leave
    nothing (the sender top-quoted with no new text), the original is
    returned instead -- an over-eager strip that silently drops the reply is
    worse than a noisy note.
    """
    cut = len(text)
    for marker in _TRAIL_MARKERS:
        match = marker.search(text)
        if match and match.start() < cut:
            cut = match.start()
    stripped = text[:cut].strip()
    return stripped or text.strip()
