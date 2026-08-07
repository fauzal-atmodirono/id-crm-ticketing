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

# Simple trail markers that are always a sign of quoted content.
_SIMPLE_TRAIL_MARKERS = (
    re.compile(r"^On .{0,200}\bwrote:\s*$", re.MULTILINE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^_{5,}\s*$", re.MULTILINE),
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

    # Check simple markers: "On ... wrote:", "-----Original Message-----", underscores
    for marker in _SIMPLE_TRAIL_MARKERS:
        match = marker.search(text)
        if match and match.start() < cut:
            cut = match.start()

    # Check "From:" only if followed by Sent:/To:/Subject: within 4 lines
    # (to avoid cutting at "From: ..." in prose paragraphs)
    for match in re.finditer(r"^From: .+$", text, re.MULTILINE):
        end_of_line = match.end()
        remaining_text = text[end_of_line:]
        # Split to get lines after this match
        lines_after = remaining_text.split('\n', 5)  # Max 5 parts
        next_4_lines = lines_after[1:5]  # Skip first part (rest of current line)

        # Check if any of the next 4 lines start with an email header
        has_email_header = any(
            line.lstrip().startswith(('Sent:', 'To:', 'Subject:'))
            for line in next_4_lines
        )
        if has_email_header and match.start() < cut:
            cut = match.start()

    # Check ">" - find trailing quoted block that extends to end of text.
    # Walk backwards from the end to avoid false positives when isolated quotes
    # appear earlier in the body.
    lines = text.split('\n')
    # Find the last non-blank line
    last_non_blank_idx = len(lines) - 1
    while last_non_blank_idx >= 0 and not lines[last_non_blank_idx].strip():
        last_non_blank_idx -= 1

    # If the last non-blank line is quoted, find the start of the trailing block
    if last_non_blank_idx >= 0 and lines[last_non_blank_idx].lstrip().startswith('>'):
        block_start_idx = last_non_blank_idx
        # Walk backwards through blank/quoted lines to find block start
        while block_start_idx > 0:
            prev_line = lines[block_start_idx - 1]
            if not prev_line.strip() or prev_line.lstrip().startswith('>'):
                block_start_idx -= 1
            else:
                break

        # Calculate text position of the block start
        cut_position = sum(len(lines[i]) for i in range(block_start_idx)) + block_start_idx
        if cut_position < cut:
            cut = cut_position

    stripped = text[:cut].strip()
    return stripped or text.strip()
