"""Archiving a call to a bucket the tenant owns.

Twilio is otherwise the only copy: residency is Twilio's region, retention is
unenforceable (no delete adapter exists), and storage bills forever. These
tests pin the path layout, because a GCS lifecycle rule keyed on the date
prefix is what turns PHONE_RECORDING_RETENTION_DAYS into something real.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from chatbot.features.chat.phone.recording_archive import (
    archive_call,
    note_archive_locations,
    object_path,
)


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "phone_recording_archive_enabled": True,
            "phone_recording_archive_bucket": "lv-playground-genai",
            "phone_recording_archive_prefix": "proton-crm/call-recording",
            "phone_recording_archive_format": "mp3",
        }
    )


def test_object_path_is_date_partitioned_and_named_by_call_sid():
    when = datetime(2026, 8, 18, 23, 30, tzinfo=UTC)
    assert (
        object_path("proton-crm/call-recording", "CA123", when, "mp3")
        == "proton-crm/call-recording/2026-08-18/CA123.mp3"
    )
    assert (
        object_path("proton-crm/call-recording", "CA123", when, "txt")
        == "proton-crm/call-recording/2026-08-18/CA123.txt"
    )


def test_audio_and_transcript_share_one_date_folder():
    """`when` is passed in rather than read from the clock so a call archived
    either side of midnight UTC cannot be split across two prefixes and look
    like two separate calls."""
    when = datetime(2026, 8, 18, 23, 59, 59, tzinfo=UTC)
    a = object_path("p", "CA1", when, "mp3").rsplit("/", 1)[0]
    t = object_path("p", "CA1", when, "txt").rsplit("/", 1)[0]
    assert a == t


def test_prefix_slashes_are_normalised():
    when = datetime(2026, 8, 18, tzinfo=UTC)
    assert object_path("/p/q/", "CA1", when, "txt") == "p/q/2026-08-18/CA1.txt"


async def test_uploads_both_objects(settings):
    with patch(
        "chatbot.features.chat.phone.recording_archive._upload_sync",
        side_effect=lambda s, path, d, ct, **kw: f"gs://b/{path}",
    ) as up:
        out = await archive_call(
            settings, "CA9", b"audio", "CUSTOMER: hi", datetime(2026, 8, 18, tzinfo=UTC)
        )
    assert set(out) == {"audio", "transcript"}
    paths = [c.args[1] for c in up.call_args_list]
    assert "proton-crm/call-recording/2026-08-18/CA9.mp3" in paths
    assert "proton-crm/call-recording/2026-08-18/CA9.txt" in paths


async def test_partial_result_is_kept(settings):
    """A transcript that lands without its audio is still worth having."""
    with patch(
        "chatbot.features.chat.phone.recording_archive._upload_sync",
        side_effect=lambda s, path, d, ct, **kw: None if path.endswith(".mp3") else f"gs://b/{path}",
    ):
        out = await archive_call(
            settings, "CA9", b"audio", "text", datetime(2026, 8, 18, tzinfo=UTC)
        )
    assert set(out) == {"transcript"}


async def test_disabled_uploads_nothing(settings):
    off = settings.model_copy(update={"phone_recording_archive_enabled": False})
    with patch("chatbot.features.chat.phone.recording_archive._upload_sync") as up:
        assert await archive_call(off, "CA9", b"a", "t") == {}
        up.assert_not_called()


async def test_no_bucket_configured_uploads_nothing(settings):
    bad = settings.model_copy(update={"phone_recording_archive_bucket": ""})
    with patch("chatbot.features.chat.phone.recording_archive._upload_sync") as up:
        assert await archive_call(bad, "CA9", b"a", "t") == {}
        up.assert_not_called()


async def test_missing_call_sid_uploads_nothing(settings):
    """Without the SID there is no stable name, and a generated one would be
    unreachable from the conversation it belongs to."""
    with patch("chatbot.features.chat.phone.recording_archive._upload_sync") as up:
        assert await archive_call(settings, "", b"a", "t") == {}
        up.assert_not_called()


async def test_note_records_the_locations():
    port = AsyncMock()
    assert await note_archive_locations(port, "42", {"audio": "gs://b/a.mp3"})
    assert "gs://b/a.mp3" in port.append_conversation_comment.await_args.args[1]


async def test_note_failure_does_not_raise():
    port = AsyncMock()
    port.append_conversation_comment.side_effect = RuntimeError("chatwoot down")
    assert await note_archive_locations(port, "42", {"audio": "gs://b/a.mp3"}) is False


async def test_archived_objects_carry_their_conversation_as_metadata(settings):
    """The player trusts an object only when its GCS metadata names the
    conversation asking for it -- the binding is server-side, not a Chatwoot
    attribute or note an agent could edit."""
    with patch(
        "chatbot.features.chat.phone.recording_archive._upload_sync",
        side_effect=lambda s, path, d, ct, **kw: f"gs://b/{path}",
    ) as up:
        await archive_call(
            settings,
            "CA9",
            b"audio",
            "CUSTOMER: hi",
            datetime(2026, 8, 18, tzinfo=UTC),
            conversation_id="42",
        )
    for call in up.call_args_list:
        assert call.kwargs["metadata"] == {"conversation_id": "42", "call_sid": "CA9"}


def test_upload_sets_blob_metadata_before_writing(settings):
    from chatbot.features.chat.phone.recording_archive import _upload_sync

    class _Blob:
        metadata = None

        def upload_from_string(self, data, content_type):
            self.uploaded_with = dict(self.metadata or {})

    blob = _Blob()

    class _Bucket:
        def blob(self, path):
            return blob

    class _Client:
        def __init__(self, project=None):
            pass

        def bucket(self, name):
            return _Bucket()

    with patch("google.cloud.storage.Client", _Client):
        uri = _upload_sync(
            settings, "p/2026-08-18/CA9.mp3", b"x", "audio/mpeg",
            metadata={"conversation_id": "42", "call_sid": "CA9"},
        )
    assert uri == "gs://lv-playground-genai/p/2026-08-18/CA9.mp3"
    assert blob.uploaded_with == {"conversation_id": "42", "call_sid": "CA9"}
