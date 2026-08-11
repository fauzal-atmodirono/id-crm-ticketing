"""Turn a Chatwoot conversation's attachments into Gemini parts, or into words.

Two jobs, deliberately kept separable because they fail independently:

1. **Rendering** (`render_transcript`) turns the structured `messages` the
   Chatwoot fork now posts into the numbered transcript the assist prompts
   consume, adding a "[sent a video]" marker for every attachment. This needs
   no network and no flag: it runs on data the browser already had. Before
   this existed, an attachment with no caption was filtered out of the
   transcript entirely and the model was never told it happened.

2. **Fetching** (`collect_media_parts`) downloads the actual bytes and builds
   inline `types.Part`s. This needs the Chatwoot API, the network, and the
   `assist_media_understanding_enabled` flag.

When (2) fails or is off, (1) still tells the model a video exists — which is
the difference between "I see you sent a video, which part concerns you?" and
asking the customer what "this one" means.

**The invariant: no media condition may turn a working draft into no draft.**
Every failure here degrades to fewer parts, never to an exception reaching the
endpoint. That is why nearly everything is wrapped and why `collect_media_parts`
returns `[]` rather than raising.

Attachment URLs come from the **Chatwoot API response**, never from the request
body. That is a security boundary, not a style choice: a client-supplied URL
would make this function an SSRF gadget aimed at anything the backend container
can reach, including the GCE metadata endpoint. Keep it that way — if you ever
need a caller-supplied URL here, it needs a host allowlist first.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx
import structlog
from google.genai import types
from pydantic import BaseModel, Field

from chatbot.features.assist.media_registry import (
    drop_order,
    is_ingestible,
    kind_for,
    label_for,
    resolve_mime,
)

if TYPE_CHECKING:
    from chatbot.features.assist.chatwoot_context import ChatwootContextClient

_log = structlog.get_logger(__name__)

# Chatwoot message_type: 0 == incoming (customer), 1 == outgoing (agent/bot).
_MESSAGE_TYPE_INCOMING = 0

# How far back to look for attachments. Matches the 20-message window the
# agent service's `_build_context`/`_build_thread` already use, so "what the AI
# can see" means the same thing on both paths.
_WINDOW = 20

_FETCH_TIMEOUT_SECONDS = 15.0


class AssistAttachment(BaseModel):
    """One attachment as the Chatwoot fork reports it. `file_type` is Chatwoot's
    own value and is deliberately an open string, not an enum — an unrecognised
    kind must still round-trip into a marker rather than fail validation."""

    file_type: str = "file"


class AssistMessage(BaseModel):
    role: Literal["customer", "agent"]
    content: str = ""
    attachments: list[AssistAttachment] = Field(default_factory=list)


@dataclass(frozen=True)
class FetchedMedia:
    file_type: str
    data: bytes
    mime: str


class MediaTermsCache:
    """Per-conversation cache of keywords extracted from a conversation's media.

    Exists because the extraction call carries the media a second time, and an
    agent working a case clicks Suggest, then Ask, then Suggest again on the
    SAME conversation — without this, each click re-uploads the same video to
    describe the same car.

    Empty results are cached too, and deliberately: a conversation whose media
    yields nothing concrete is exactly the one that would otherwise re-extract
    on every single click, paying full media cost forever to learn "nothing"
    again. `get` therefore returns `None` for "not cached" and `""` for
    "cached, and there was nothing" — callers must distinguish the two.

    Bounded and TTL'd so a long-lived process cannot accumulate one entry per
    conversation ever seen. `time_fn` is injectable so expiry is testable
    without sleeping.
    """

    def __init__(
        self,
        ttl_seconds: float = 300.0,
        max_entries: int = 256,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._now = time_fn
        self._entries: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, stamp = entry
        if self._now() - stamp > self._ttl:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: str) -> None:
        self._entries[key] = (value, self._now())
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


# ---------------------------------------------------------------------------
# Rendering (no network, no flag)
# ---------------------------------------------------------------------------


def _pluralize(label: str, count: int) -> str:
    """ "a photo" + 3 -> "3 photos". Every registry label is phrased "a <noun>"
    or "an <noun>", so stripping the article and suffixing "s" is sufficient;
    a label that ever breaks that shape just reads slightly oddly, it does not
    fail."""
    if count == 1:
        return label
    noun = label.split(" ", 1)[1] if label.startswith(("a ", "an ")) else label
    return f"{count} {noun}s"


def _join_labels(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _attachment_marker(attachments: list[AssistAttachment]) -> str:
    """ "[sent a video]" / "[sent 3 photos and a voice note]", or "" for none.

    Counts per kind rather than listing each attachment, so a burst of eight
    photos reads as "8 photos" instead of eight identical clauses eating the
    prompt.
    """
    if not attachments:
        return ""
    counts: dict[str, int] = {}
    for attachment in attachments:
        label = label_for(attachment.file_type)
        counts[label] = counts.get(label, 0) + 1
    described = [_pluralize(label, n) for label, n in counts.items()]
    return f"[sent {_join_labels(described)}]"


def render_transcript(messages: list[str] | list[AssistMessage]) -> list[str]:
    """Structured messages -> "Customer: …" lines with attachment markers.

    A `list[str]` is passed through untouched: that is the legacy payload shape
    from before the fork sent structured messages, and it must keep producing
    byte-identical prompts so an un-upgraded Chatwoot image is never worse off
    than it is today.
    """
    rendered: list[str] = []
    for message in messages:
        if isinstance(message, str):
            rendered.append(message)
            continue
        speaker = "Customer" if message.role == "customer" else "Agent"
        body = " ".join(
            part
            for part in ((message.content or "").strip(), _attachment_marker(message.attachments))
            if part
        )
        rendered.append(f"{speaker}: {body}")
    return rendered


def customer_texts(messages: list[str] | list[AssistMessage]) -> list[str]:
    """The customer's own words, for KB retrieval.

    Reads `content` straight off structured messages rather than regex-stripping
    markers back out of rendered strings — parsing our own output would be a
    bug waiting to happen the first time a customer literally types "[sent a
    video]". Legacy `list[str]` keeps the original "Customer:"-prefix partition.

    Attachment markers are excluded on purpose: "a video" is not a search term,
    and letting it into the query derails retrieval on a short turn.
    """
    texts: list[str] = []
    for message in messages:
        if isinstance(message, str):
            label, sep, body = message.partition(":")
            if not sep or label.strip().lower() != "customer":
                continue
            body = body.strip()
        else:
            if message.role != "customer":
                continue
            body = (message.content or "").strip()
        if body:
            texts.append(body)
    return texts


# ---------------------------------------------------------------------------
# Fetching (Chatwoot API + network + flag)
# ---------------------------------------------------------------------------


def select_attachments(raw_messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(file_type, data_url) for the most recent instance of each fetchable kind.

    Scans the trailing `_WINDOW` messages backwards, so the newest photo wins
    over an older one, and only considers non-private incoming messages — the
    customer's own media is what an agent wants explained. Kinds the registry
    marks non-downloadable (a shared location, a contact card) are skipped
    here; they still reach the model as markers via `render_transcript`.

    Window-wide rather than current-turn-only: an agent often clicks an assist
    action several turns after the video arrived, and a marker without bytes is
    exactly the failure this change exists to fix.
    """
    picked: dict[str, str] = {}
    for message in reversed(raw_messages[-_WINDOW:]):
        if message.get("private"):
            continue
        if message.get("message_type") != _MESSAGE_TYPE_INCOMING:
            continue
        for attachment in message.get("attachments") or []:
            file_type = (attachment.get("file_type") or "").strip().lower()
            data_url = attachment.get("data_url")
            if not data_url or file_type in picked:
                continue
            if not kind_for(file_type).downloadable:
                continue
            picked[file_type] = data_url
    return list(picked.items())


async def fetch_attachment(data_url: str, file_type: str, max_bytes: int) -> FetchedMedia | None:
    """Download one attachment, or None on any failure or rejection.

    `follow_redirects=True` is load-bearing rather than a nicety: with
    `ACTIVE_STORAGE_SERVICE=local` (what the tenant stacks run) Chatwoot hands
    out a `/rails/active_storage/blobs/redirect/...` URL that answers 302, and
    httpx does not follow redirects by default. Without this every voice note
    was silently dropped — see `agent/app/services/media.py` and commit 3a009f2.

    The response is streamed so the headers can reject a file before its body
    is pulled: a 200 MB archive costs one round trip, not 200 MB of transfer.
    A plain unauthenticated client is used deliberately — no Chatwoot token is
    sent, so following the redirect cannot leak a credential onto its target.
    """
    try:
        async with (
            httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client,
            client.stream("GET", data_url) as response,
        ):
            response.raise_for_status()
            mime = resolve_mime(file_type, response.headers.get("content-type"))
            if not is_ingestible(mime):
                # Not an error: a .zip or .docx is a legitimate thing for a
                # customer to send, it just cannot go inline to Gemini. The
                # transcript marker already reports that it exists.
                _log.debug("assist_media_not_ingestible", file_type=file_type, mime=mime)
                return None
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                _log.warning(
                    "assist_media_too_large",
                    file_type=file_type,
                    bytes=int(declared),
                    max_bytes=max_bytes,
                )
                return None
            data = await response.aread()
        if len(data) > max_bytes:
            # Reached only when the server sent no Content-Length to check.
            _log.warning(
                "assist_media_too_large",
                file_type=file_type,
                bytes=len(data),
                max_bytes=max_bytes,
            )
            return None
        return FetchedMedia(file_type=file_type, data=data, mime=mime or "")
    except Exception:
        _log.warning("assist_media_fetch_failed", file_type=file_type, exc_info=True)
        return None


def apply_budget(fetched: list[FetchedMedia], max_bytes: int) -> list[FetchedMedia]:
    """Drop whole attachments until the combined payload fits `max_bytes`.

    The cap is a budget for the WHOLE request, not per attachment: a 14 MB video
    plus a 5 MB photo is ~25 MB once base64-encoded into the JSON body, so
    guarding each one alone still produces a request Gemini rejects. Eviction
    follows the registry's drop priority (video first, voice note last) and
    never truncates a payload mid-stream — an attachment is either whole or
    absent.
    """
    surviving = {media.file_type: media for media in fetched}
    total = sum(len(media.data) for media in fetched)
    for file_type in drop_order(list(surviving)):
        if total <= max_bytes:
            break
        dropped = surviving.pop(file_type)
        total -= len(dropped.data)
        _log.warning(
            "assist_media_budget_exceeded",
            dropped=file_type,
            dropped_bytes=len(dropped.data),
            max_bytes=max_bytes,
            remaining_bytes=total,
        )
    return [media for media in fetched if media.file_type in surviving]


async def collect_media_parts(
    context: ChatwootContextClient | None,
    conversation_id: str,
    *,
    enabled: bool,
    max_bytes: int,
) -> list[types.Part]:
    """Inline Gemini parts for the conversation's attachments; `[]` on anything.

    Returns `[]` when the flag is off (without making a single Chatwoot call),
    when no client is wired, when Chatwoot is unreachable, when nothing is
    fetchable, and when every candidate fails — all indistinguishable to the
    caller by design, because in every one of those cases the correct behaviour
    is the same: produce the text-only draft.
    """
    if not enabled or context is None:
        return []
    try:
        raw_messages = await context.get_messages(conversation_id)
        candidates = select_attachments(raw_messages)
        if not candidates:
            return []
        fetched = [
            media
            for media in [
                await fetch_attachment(data_url, file_type, max_bytes)
                for file_type, data_url in candidates
            ]
            if media is not None
        ]
        if not fetched:
            return []
        kept = apply_budget(fetched, max_bytes)
        parts: list[types.Part] = []
        for media in kept:
            try:
                parts.append(types.Part.from_bytes(data=media.data, mime_type=media.mime))
            except Exception:
                # Per-kind guard, mirroring features/chat/service.py: one
                # malformed blob must not cost the turn its other attachments.
                _log.warning("assist_media_part_failed", file_type=media.file_type)
        if parts:
            _log.info(
                "assist_media_attached",
                conversation_id=conversation_id,
                kinds=[media.file_type for media in kept],
                total_bytes=sum(len(media.data) for media in kept),
            )
        return parts
    except Exception:
        _log.warning("assist_media_collect_failed", conversation_id=conversation_id, exc_info=True)
        return []
