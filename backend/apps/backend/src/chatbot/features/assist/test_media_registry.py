"""Registry resolution: labels, mimes, ingestibility, drop order.

The point of these is that the registry stays OPEN — an attachment kind nobody
enumerated must still resolve to something usable rather than vanish, which is
the failure mode the previous hardcoded branches had.
"""

from __future__ import annotations

from chatbot.features.assist.media_registry import (
    REGISTRY,
    drop_order,
    is_ingestible,
    kind_for,
    label_for,
    resolve_mime,
)

# ---------------------------------------------------------------------------
# Kind resolution
# ---------------------------------------------------------------------------


def test_known_kinds_have_expected_labels() -> None:
    assert label_for("image") == "a photo"
    assert label_for("video") == "a video"
    assert label_for("audio") == "a voice note"
    assert label_for("file") == "a document"


def test_unknown_kind_still_resolves_and_is_labelled() -> None:
    """The whole point: a file_type nobody enumerated must not vanish."""
    assert label_for("hologram") == "a file"
    assert kind_for("hologram").downloadable is True


def test_kind_lookup_is_case_and_whitespace_insensitive() -> None:
    assert label_for("  VIDEO ") == "a video"


def test_missing_file_type_resolves_to_unknown() -> None:
    assert label_for(None) == "a file"
    assert label_for("") == "a file"


def test_non_downloadable_kinds_are_marked() -> None:
    assert kind_for("location").downloadable is False
    assert kind_for("contact").downloadable is False


# ---------------------------------------------------------------------------
# Mime resolution
# ---------------------------------------------------------------------------


def test_real_content_type_beats_registry_default() -> None:
    assert resolve_mime("image", "image/png") == "image/png"


def test_content_type_parameters_are_stripped() -> None:
    assert resolve_mime("audio", "audio/ogg; codecs=opus") == "audio/ogg"


def test_generic_content_type_falls_back_to_registry_default() -> None:
    assert resolve_mime("audio", "application/octet-stream") == "audio/ogg"
    assert resolve_mime("video", "") == "video/mp4"
    assert resolve_mime("image", None) == "image/jpeg"


def test_kind_without_default_resolves_to_none_rather_than_guessing() -> None:
    """A bare "file" could be a PDF or a .xlsx. Guessing wrong is worse than
    declining, so the caller sends a marker instead."""
    assert resolve_mime("file", "application/octet-stream") is None
    assert resolve_mime("file", "application/pdf") == "application/pdf"


# ---------------------------------------------------------------------------
# Ingestibility — decided by mime, not by kind name
# ---------------------------------------------------------------------------


def test_media_families_are_prefix_matched() -> None:
    """A container format nobody listed still works — that is why this is a
    prefix match rather than an exhaustive set."""
    assert is_ingestible("video/webm")
    assert is_ingestible("image/heif")
    assert is_ingestible("audio/flac")


def test_documents_are_exact_matched() -> None:
    assert is_ingestible("application/pdf")
    assert not is_ingestible("application/zip")
    assert not is_ingestible("application/vnd.ms-excel")


def test_unresolvable_mime_is_not_ingestible() -> None:
    assert not is_ingestible(None)
    assert not is_ingestible("")


# ---------------------------------------------------------------------------
# Drop order
# ---------------------------------------------------------------------------


def test_video_drops_first_and_audio_last() -> None:
    assert drop_order(["audio", "image", "video"]) == ["video", "image", "audio"]


def test_documents_drop_between_video_and_image() -> None:
    assert drop_order(["audio", "image", "file", "video"]) == [
        "video",
        "file",
        "image",
        "audio",
    ]


def test_unknown_kinds_drop_first() -> None:
    """We know least about them, so they are the cheapest thing to lose."""
    assert drop_order(["audio", "hologram"])[0] == "hologram"


def test_drop_order_is_deterministic_for_equal_priority() -> None:
    assert drop_order(["zeta", "alpha"]) == drop_order(["alpha", "zeta"])


def test_every_registered_kind_is_orderable() -> None:
    """Guards against a kind being added without a drop priority."""
    assert sorted(drop_order(list(REGISTRY))) == sorted(REGISTRY)
