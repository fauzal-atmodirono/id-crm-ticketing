"""Transcript rendering and the attachment fetch pipeline.

The bug these exist to keep fixed: a customer sends a video captioned "this
one", and the assist draft comes back asking what "this one" refers to —
because the attachment never reached the model in any form, not even as words.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from chatbot.features.assist.assist_media import (
    AssistAttachment,
    AssistMessage,
    FetchedMedia,
    apply_budget,
    collect_media_parts,
    customer_texts,
    fetch_attachment,
    render_transcript,
    select_attachments,
)

_URL = "https://crm.example.test/rails/active_storage/blobs/redirect/abc/clip.mp4"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(role: str, content: str = "", *kinds: str) -> AssistMessage:
    return AssistMessage(
        role=role,
        content=content,
        attachments=[AssistAttachment(file_type=k) for k in kinds],
    )


def _raw(
    message_type: int = 0,
    *,
    private: bool = False,
    attachments: list[dict] | None = None,
) -> dict:
    return {
        "message_type": message_type,
        "private": private,
        "attachments": attachments or [],
    }


class _FakeContext:
    def __init__(self, messages: list[dict] | None = None, raises: bool = False) -> None:
        self._messages = messages or []
        self._raises = raises
        self.calls: list[str] = []

    async def get_messages(self, conversation_id: str) -> list[dict]:
        self.calls.append(conversation_id)
        if self._raises:
            raise RuntimeError("chatwoot is down")
        return self._messages


# ---------------------------------------------------------------------------
# Rendering — the half that needs no network and no flag
# ---------------------------------------------------------------------------


def test_the_reported_bug_caption_plus_video() -> None:
    """ "this one" alone is unanswerable; with the marker it is not."""
    assert render_transcript([_msg("customer", "this one", "video")]) == [
        "Customer: this one [sent a video]"
    ]


def test_caption_less_attachment_is_no_longer_invisible() -> None:
    """Previously filtered out entirely — the AI was never told it happened."""
    assert render_transcript([_msg("customer", "", "audio")]) == ["Customer: [sent a voice note]"]


def test_repeated_kinds_are_counted_not_repeated() -> None:
    assert render_transcript([_msg("customer", "look", "image", "image", "image")]) == [
        "Customer: look [sent 3 photos]"
    ]


def test_mixed_kinds_read_as_a_list() -> None:
    rendered = render_transcript([_msg("customer", "", "image", "video")])
    assert rendered == ["Customer: [sent a photo and a video]"]


def test_unknown_kind_still_gets_a_marker() -> None:
    assert render_transcript([_msg("customer", "see", "hologram")]) == [
        "Customer: see [sent a file]"
    ]


def test_non_downloadable_kinds_are_still_described() -> None:
    """A shared location has no blob to send, but the model should know."""
    assert render_transcript([_msg("customer", "", "location")]) == ["Customer: [sent a location]"]


def test_agent_role_renders_as_agent() -> None:
    assert render_transcript([_msg("agent", "Hello")]) == ["Agent: Hello"]


def test_legacy_string_payload_passes_through_unchanged() -> None:
    """An un-upgraded Chatwoot image must keep producing today's exact prompt."""
    legacy = ["Customer: hi", "Agent: hello"]
    assert render_transcript(legacy) == legacy


def test_message_with_no_attachments_has_no_marker() -> None:
    assert render_transcript([_msg("customer", "just text")]) == ["Customer: just text"]


# ---------------------------------------------------------------------------
# Retrieval text
# ---------------------------------------------------------------------------


def test_customer_texts_excludes_markers_and_agent_turns() -> None:
    messages = [_msg("customer", "warranty?", "video"), _msg("agent", "which model?")]
    assert customer_texts(messages) == ["warranty?"]


def test_customer_texts_skips_caption_less_turns() -> None:
    """A marker is not a search term; an attachment-only turn contributes nothing."""
    assert customer_texts([_msg("customer", "", "video")]) == []


def test_customer_texts_handles_legacy_strings() -> None:
    assert customer_texts(["Customer: hi", "Agent: hello"]) == ["hi"]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_most_recent_instance_of_each_kind_wins() -> None:
    messages = [
        _raw(attachments=[{"file_type": "image", "data_url": "old.jpg"}]),
        _raw(attachments=[{"file_type": "image", "data_url": "new.jpg"}]),
        _raw(attachments=[{"file_type": "video", "data_url": "clip.mp4"}]),
    ]
    assert dict(select_attachments(messages)) == {
        "image": "new.jpg",
        "video": "clip.mp4",
    }


def test_outgoing_and_private_messages_are_ignored() -> None:
    messages = [
        _raw(message_type=1, attachments=[{"file_type": "image", "data_url": "agent.jpg"}]),
        _raw(private=True, attachments=[{"file_type": "video", "data_url": "note.mp4"}]),
    ]
    assert select_attachments(messages) == []


def test_non_downloadable_kinds_are_not_fetched() -> None:
    messages = [_raw(attachments=[{"file_type": "location", "data_url": "pin"}])]
    assert select_attachments(messages) == []


def test_attachment_without_data_url_is_skipped() -> None:
    messages = [_raw(attachments=[{"file_type": "image"}])]
    assert select_attachments(messages) == []


def test_selection_looks_past_the_current_turn() -> None:
    """The agent often clicks Suggest several turns after the video arrived."""
    messages = [
        _raw(attachments=[{"file_type": "video", "data_url": "clip.mp4"}]),
        _raw(message_type=1),
        _raw(),
    ]
    assert dict(select_attachments(messages)) == {"video": "clip.mp4"}


def test_selection_window_is_bounded() -> None:
    old = [_raw(attachments=[{"file_type": "video", "data_url": "ancient.mp4"}])]
    recent = [_raw() for _ in range(25)]
    assert select_attachments(old + recent) == []


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


@respx.mock
async def test_active_storage_redirect_is_followed() -> None:
    """Local-disk tenants get a 302; not following it silently dropped every
    voice note (see commit 3a009f2)."""
    respx.get(_URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://cdn.test/real.mp4"})
    )
    respx.get("https://cdn.test/real.mp4").mock(
        return_value=httpx.Response(
            200, content=b"video-bytes", headers={"content-type": "video/mp4"}
        )
    )
    result = await fetch_attachment(_URL, "video", 1_000_000)
    assert result is not None
    assert result.data == b"video-bytes"
    assert result.mime == "video/mp4"


@respx.mock
async def test_generic_content_type_falls_back_to_registry_default() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, content=b"ogg", headers={"content-type": "application/octet-stream"}
        )
    )
    result = await fetch_attachment(_URL, "audio", 1_000_000)
    assert result is not None
    assert result.mime == "audio/ogg"


@respx.mock
async def test_non_ingestible_type_is_declined_not_sent() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(200, content=b"PK", headers={"content-type": "application/zip"})
    )
    assert await fetch_attachment(_URL, "file", 1_000_000) is None


@respx.mock
async def test_pdf_document_is_accepted() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        )
    )
    result = await fetch_attachment(_URL, "file", 1_000_000)
    assert result is not None
    assert result.mime == "application/pdf"


@respx.mock
async def test_oversized_file_is_rejected_on_its_declared_length() -> None:
    """Rejected from the headers, so a huge file costs one round trip and not
    its full transfer."""
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200,
            content=b"x" * 100,
            headers={"content-type": "video/mp4", "content-length": "99999999"},
        )
    )
    assert await fetch_attachment(_URL, "video", 1000) is None


@respx.mock
async def test_oversized_body_is_rejected_when_no_length_declared() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(200, content=b"x" * 500, headers={"content-type": "video/mp4"})
    )
    assert await fetch_attachment(_URL, "video", 100) is None


@respx.mock
async def test_http_error_returns_none_rather_than_raising() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(404))
    assert await fetch_attachment(_URL, "video", 1_000_000) is None


@respx.mock
async def test_transport_error_returns_none_rather_than_raising() -> None:
    respx.get(_URL).mock(side_effect=httpx.ConnectError("refused"))
    assert await fetch_attachment(_URL, "video", 1_000_000) is None


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def _media(file_type: str, size: int) -> FetchedMedia:
    return FetchedMedia(file_type=file_type, data=b"x" * size, mime=f"{file_type}/x")


def test_budget_drops_video_before_the_voice_note() -> None:
    kept = apply_budget([_media("video", 900), _media("audio", 50)], max_bytes=100)
    assert [m.file_type for m in kept] == ["audio"]


def test_budget_keeps_everything_when_it_fits() -> None:
    fetched = [_media("video", 10), _media("audio", 10)]
    assert apply_budget(fetched, max_bytes=1000) == fetched


def test_budget_drops_only_as_much_as_needed() -> None:
    kept = apply_budget(
        [_media("video", 500), _media("image", 40), _media("audio", 40)], max_bytes=100
    )
    assert {m.file_type for m in kept} == {"image", "audio"}


def test_budget_never_truncates_a_payload() -> None:
    """An attachment is whole or absent — half a video is worse than none."""
    kept = apply_budget([_media("video", 500)], max_bytes=100)
    assert kept == []


# ---------------------------------------------------------------------------
# End-to-end collection
# ---------------------------------------------------------------------------


async def test_flag_off_makes_no_chatwoot_call_at_all() -> None:
    context = _FakeContext([_raw(attachments=[{"file_type": "video", "data_url": _URL}])])
    parts = await collect_media_parts(context, "42", enabled=False, max_bytes=1000)
    assert parts == []
    assert context.calls == []


async def test_missing_client_is_not_an_error() -> None:
    assert await collect_media_parts(None, "42", enabled=True, max_bytes=1000) == []


async def test_chatwoot_failure_degrades_to_no_media() -> None:
    """The invariant: no media condition may turn a working draft into no draft."""
    context = _FakeContext(raises=True)
    assert await collect_media_parts(context, "42", enabled=True, max_bytes=1000) == []


async def test_conversation_without_attachments_returns_nothing() -> None:
    context = _FakeContext([_raw()])
    assert await collect_media_parts(context, "42", enabled=True, max_bytes=1000) == []


@respx.mock
async def test_happy_path_produces_an_inline_part() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(200, content=b"clip", headers={"content-type": "video/mp4"})
    )
    context = _FakeContext([_raw(attachments=[{"file_type": "video", "data_url": _URL}])])
    parts = await collect_media_parts(context, "42", enabled=True, max_bytes=1_000_000)
    assert len(parts) == 1
    assert parts[0].inline_data is not None
    assert parts[0].inline_data.mime_type == "video/mp4"


@respx.mock
async def test_one_failing_attachment_does_not_cost_the_others() -> None:
    good = "https://crm.example.test/photo.jpg"
    respx.get(_URL).mock(return_value=httpx.Response(500))
    respx.get(good).mock(
        return_value=httpx.Response(200, content=b"jpg", headers={"content-type": "image/jpeg"})
    )
    context = _FakeContext(
        [
            _raw(attachments=[{"file_type": "video", "data_url": _URL}]),
            _raw(attachments=[{"file_type": "image", "data_url": good}]),
        ]
    )
    parts = await collect_media_parts(context, "42", enabled=True, max_bytes=1_000_000)
    assert len(parts) == 1
    assert parts[0].inline_data.mime_type == "image/jpeg"


@pytest.mark.parametrize("kind", ["location", "contact"])
async def test_marker_only_kinds_never_reach_the_fetcher(kind: str) -> None:
    context = _FakeContext([_raw(attachments=[{"file_type": kind, "data_url": "x"}])])
    assert await collect_media_parts(context, "42", enabled=True, max_bytes=1000) == []
