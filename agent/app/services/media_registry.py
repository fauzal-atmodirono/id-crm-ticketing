"""Attachment-kind registry: one table behind labels, mimes, and drop order.

Chatwoot reports an attachment's `file_type` ("image", "video", "audio",
"file", "location", …). Everything downstream needs four things from that:
what to call it in a transcript marker, what mime type to assume when the HTTP
response won't say, whether Gemini can ingest it at all, and how eager we
should be to drop it when a turn blows the byte budget.

Encoding those as `if file_type == "video"` branches is what made the previous
implementation closed to new kinds — a `file_type` nobody enumerated vanished
without trace. Here an unrecognised kind still resolves (to `UNKNOWN_KIND`) and
still produces a marker, and ingestibility is decided by the **resolved mime
type**, not the kind name. A new Chatwoot attachment type that happens to be a
PDF is understood without touching this file.

DEPENDENCY-FREE ON PURPOSE. This module imports nothing outside the standard
library so that the backend's test suite can load it by file path for a parity
check (see the backend's `test_media_registry_parity.py`). The two services
have no shared package — CLAUDE.md is explicit that they communicate only over
HTTP — so the parity test is what stops the copies drifting. Keep this module
importable in isolation.

**Any edit here must be mirrored in
`backend/apps/backend/src/chatbot/features/assist/media_registry.py`.**
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentKind:
    """How one Chatwoot `file_type` is handled.

    `label` is the transcript marker wording ("a photo"), phrased to slot into
    "[sent {label}]". `default_mime` applies only when the HTTP response's
    Content-Type is missing or generic — a real Content-Type always wins.
    `downloadable` is False for kinds that carry no retrievable blob (a shared
    location, a contact card): they get a marker and are never fetched.
    `drop_priority` orders budget eviction, lowest dropped first.
    """

    label: str
    default_mime: str | None
    downloadable: bool = True
    drop_priority: int = 0


# Keyed by Chatwoot's reported `file_type`.
#
# Drop priority rationale: video is by far the largest and is least often the
# whole message, so it goes first. A voice note usually IS the entire message,
# so audio is evicted last — losing it costs the turn more than losing an
# illustrating photo. Documents sit between video and image.
REGISTRY: dict[str, AttachmentKind] = {
    "image": AttachmentKind("a photo", "image/jpeg", drop_priority=2),
    # WhatsApp/Twilio deliver customer videos as MP4.
    "video": AttachmentKind("a video", "video/mp4", drop_priority=0),
    # "audio/ogg" matches WhatsApp/Twilio's actual voice-note format.
    "audio": AttachmentKind("a voice note", "audio/ogg", drop_priority=3),
    # No safe default: a "file" is a PDF as often as a spreadsheet, and
    # guessing wrong is worse than declining to send it. Content-Type decides.
    "file": AttachmentKind("a document", None, drop_priority=1),
    "location": AttachmentKind("a location", None, downloadable=False),
    "contact": AttachmentKind("a contact card", None, downloadable=False),
    "story_mention": AttachmentKind("a story mention", None, downloadable=False),
    "fallback": AttachmentKind("an attachment", None, downloadable=False),
}

# Anything Chatwoot reports that isn't in REGISTRY. Still downloadable and
# still marked in the transcript — Content-Type decides whether it reaches the
# model. Dropped first under budget pressure because we know least about it.
UNKNOWN_KIND = AttachmentKind("a file", None, drop_priority=0)

# Content-Type values that tell us nothing, treated the same as a missing
# header so the registry default (or, absent one, rejection) applies.
GENERIC_CONTENT_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

# What gemini-2.5-flash accepts inline. Prefix-matched for the three media
# families so a new container format (video/webm, image/heif, …) needs no edit
# here; exact-matched for the document types, which are a short closed list.
INGESTIBLE_MIME_PREFIXES = ("image/", "video/", "audio/")
INGESTIBLE_MIME_EXACT = frozenset({"application/pdf", "text/plain"})


def kind_for(file_type: str | None) -> AttachmentKind:
    """The registry entry for a Chatwoot `file_type`; never raises, never None."""
    return REGISTRY.get((file_type or "").strip().lower(), UNKNOWN_KIND)


def label_for(file_type: str | None) -> str:
    """Transcript marker wording, e.g. "a video". Unknown kinds get "a file"."""
    return kind_for(file_type).label


def normalize_content_type(content_type: str | None) -> str:
    """Bare mime from a Content-Type header: lowercased, parameters stripped."""
    return (content_type or "").split(";")[0].strip().lower()


def resolve_mime(file_type: str | None, content_type: str | None) -> str | None:
    """Effective mime for an attachment, or None if it cannot be determined.

    A real Content-Type header always wins. Generic or missing ones fall back
    to the kind's `default_mime`; kinds without one (a bare "file") resolve to
    None, and the caller sends a marker instead of guessing.
    """
    resolved = normalize_content_type(content_type)
    if resolved and resolved not in GENERIC_CONTENT_TYPES:
        return resolved
    return kind_for(file_type).default_mime


def is_ingestible(mime: str | None) -> bool:
    """Whether Gemini can accept this mime type as an inline part."""
    if not mime:
        return False
    mime = mime.strip().lower()
    return mime in INGESTIBLE_MIME_EXACT or mime.startswith(INGESTIBLE_MIME_PREFIXES)


def drop_order(file_types: list[str]) -> list[str]:
    """`file_types` sorted into budget-eviction order, first dropped first.

    Ties break on the kind name so eviction is deterministic across runs and
    the warning logs are reproducible.
    """
    return sorted(file_types, key=lambda ft: (kind_for(ft).drop_priority, ft))


def registry_snapshot() -> dict[str, object]:
    """Comparable dump of every behavioural constant in this module.

    Exists solely for the cross-service parity test; nothing in production
    reads it. Anything added to this module that changes behaviour belongs in
    here too, or the parity test will pass while the copies diverge.
    """
    return {
        "kinds": {
            name: (k.label, k.default_mime, k.downloadable, k.drop_priority)
            for name, k in sorted(REGISTRY.items())
        },
        "unknown": (
            UNKNOWN_KIND.label,
            UNKNOWN_KIND.default_mime,
            UNKNOWN_KIND.downloadable,
            UNKNOWN_KIND.drop_priority,
        ),
        "generic_content_types": sorted(GENERIC_CONTENT_TYPES),
        "ingestible_prefixes": sorted(INGESTIBLE_MIME_PREFIXES),
        "ingestible_exact": sorted(INGESTIBLE_MIME_EXACT),
    }
