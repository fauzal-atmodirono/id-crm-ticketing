"""Format an assistant reply for WhatsApp delivery.

Ported from the standalone proton-conversational-ai backend (its WhatsApp send
path: ``router._md_to_whatsapp`` + ``twilio_channel._chunk_whatsapp_body``).
WhatsApp renders Markdown literally / mis-toggles the ``*_~``` characters and
only auto-links bare URLs, and Twilio rejects a body over 1600 chars outright
(error 21617). ``md_to_whatsapp`` converts Markdown to WhatsApp-native
formatting; ``chunk_whatsapp`` splits a long reply into in-limit pieces.

Kept channel-specific — other channels (web widget, email) keep the raw Markdown
(the web frontend renders it), so the orchestrator only applies these on a
WhatsApp/Twilio inbox.
"""

from __future__ import annotations

import re

# `[label](https://url)` / `![label](https://url)` -> "label (url)".
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]+)\]\((https?://[^\s)]+)\)")
# **x** / __x__ -> *x* (WhatsApp bold); `# Heading` -> *Heading*; `- item` -> bullet.
_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_MD_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$")
_MD_BLOCKQUOTE = re.compile(r"(?m)^[ \t]*>[ \t]?")
_MD_BULLET = re.compile(r"(?m)^[ \t]*[-+*][ \t]+")
_INLINE_WS = re.compile(r"[^\S\n]{2,}")
_MANY_NEWLINES = re.compile(r"\n{3,}")

# Twilio rejects a WhatsApp body over 1600 chars (error 21617); split below it.
WHATSAPP_BODY_LIMIT = 1600


def md_to_whatsapp(text: str) -> str:
    """Render the assistant's Markdown reply as WhatsApp-native formatting:
    ``**x**``/``__x__`` -> ``*x*`` (bold), ``# Heading`` -> ``*Heading*``,
    ``- item`` -> ``• item``, ``[label](url)`` -> ``label (url)``. Structure and
    line breaks are preserved (only intra-line space runs and 3+ blank lines
    collapse)."""
    t = _MARKDOWN_LINK.sub(r"\1 (\2)", text or "")
    t = _MD_BOLD.sub(r"*\2*", t)
    t = _MD_HEADING.sub(r"*\1*", t)
    t = _MD_BLOCKQUOTE.sub("", t)
    t = _MD_BULLET.sub("• ", t)
    t = _INLINE_WS.sub(" ", t)
    t = _MANY_NEWLINES.sub("\n\n", t)
    return "\n".join(line.rstrip() for line in t.split("\n")).strip()


def chunk_whatsapp(text: str, limit: int = WHATSAPP_BODY_LIMIT) -> list[str]:
    """Split ``text`` into pieces each <= ``limit`` chars, breaking at a
    paragraph/newline, then a space, hard-cutting only an unbroken run — so words
    and paragraphs stay intact. ``[]`` for empty/whitespace-only input."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split = window.rfind("\n")
        if split < limit // 2:
            split = window.rfind(" ")
        if split < limit // 2:
            split = limit  # unbroken run → hard cut
        chunk = remaining[:split].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks
