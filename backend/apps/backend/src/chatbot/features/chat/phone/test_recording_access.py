"""The pieces the call-recording player stands on: the signed link, finding a
call's archived object, and HTTP Range parsing. Router-level behaviour is in
test_recording_retrieval.py."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone.recording_access import (
    ArchivedRecording,
    InvalidRecordingToken,
    RangeNotSatisfiable,
    find_archived_recording,
    parse_archive_note,
    parse_range,
    sign_token,
    valid_object_name,
    verify_token,
)

SECRET = "s" * 40
PREFIX = "proton-crm/call-recording"
BUCKET = "bucket-x"
AUDIO = f"{PREFIX}/2026-11-09/CA1.mp3"
TEXT = f"{PREFIX}/2026-11-09/CA1.txt"


# --- token -----------------------------------------------------------------


def _mint(**over):
    kw = {
        "conversation_id": "42",
        "object_name": AUDIO,
        "kind": "audio",
        "user_id": 7,
        "ttl_seconds": 300,
        "now": 1_000_000.0,
    }
    kw.update(over)
    return sign_token(SECRET, **kw)


def test_a_fresh_token_verifies_and_carries_its_binding() -> None:
    token, expires = _mint()
    claims = verify_token(SECRET, token, conversation_id="42", kind="audio", now=1_000_100.0)
    assert claims.object_name == AUDIO
    assert claims.user_id == 7
    assert claims.conversation_id == "42"
    assert expires == 1_000_300.0
    assert claims.nonce


def test_an_expired_token_is_refused() -> None:
    token, _ = _mint()
    with pytest.raises(InvalidRecordingToken):
        verify_token(SECRET, token, conversation_id="42", kind="audio", now=1_000_301.0)


def test_a_token_for_another_conversation_is_refused() -> None:
    token, _ = _mint()
    with pytest.raises(InvalidRecordingToken):
        verify_token(SECRET, token, conversation_id="43", kind="audio", now=1_000_001.0)


def test_a_transcript_token_cannot_fetch_audio() -> None:
    token, _ = _mint(kind="transcript", object_name=TEXT)
    with pytest.raises(InvalidRecordingToken):
        verify_token(SECRET, token, conversation_id="42", kind="audio", now=1_000_001.0)


def test_a_tampered_payload_is_refused() -> None:
    import base64
    import json

    token, _ = _mint()
    body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["o"] = f"{PREFIX}/2026-11-09/CA_OTHER.mp3"
    forged = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + "." + sig
    )
    with pytest.raises(InvalidRecordingToken):
        verify_token(SECRET, forged, conversation_id="42", kind="audio", now=1_000_001.0)


def test_a_token_signed_with_another_secret_is_refused() -> None:
    token, _ = sign_token(
        "x" * 40,
        conversation_id="42",
        object_name=AUDIO,
        kind="audio",
        user_id=7,
        ttl_seconds=300,
        now=1_000_000.0,
    )
    with pytest.raises(InvalidRecordingToken):
        verify_token(SECRET, token, conversation_id="42", kind="audio", now=1_000_001.0)


@pytest.mark.parametrize("junk", ["", "abc", "a.b.c", "!!!.???", "e30.AAAA"])
def test_garbage_is_refused_not_raised_as_something_else(junk: str) -> None:
    with pytest.raises(InvalidRecordingToken):
        verify_token(SECRET, junk, conversation_id="42", kind="audio", now=1.0)


def test_ttl_is_capped_at_five_minutes() -> None:
    _, expires = _mint(ttl_seconds=3600)
    assert expires - 1_000_000.0 <= 300


# --- object names ------------------------------------------------------------


@pytest.mark.parametrize(
    "name,ok",
    [
        (AUDIO, True),
        (f"{PREFIX}/2026-11-09/CA1.wav", True),
        (TEXT, False),  # audio kind requires an audio suffix
        (f"other-prefix/2026-11-09/CA1.mp3", False),
        (f"{PREFIX}/../secrets/CA1.mp3", False),
        (f"/{AUDIO}", False),
        (f"{PREFIX}/2026-11-09/CA 1.mp3", False),
        ("", False),
    ],
)
def test_only_archive_shaped_audio_objects_are_accepted(name: str, ok: bool) -> None:
    assert valid_object_name(name, PREFIX, kind="audio") is ok


def test_transcript_objects_need_a_txt_suffix() -> None:
    assert valid_object_name(TEXT, PREFIX, kind="transcript")
    assert not valid_object_name(AUDIO, PREFIX, kind="transcript")


# --- the archive note (older calls) ------------------------------------------


NOTE = (
    "[Call archived to Cloud Storage]\n"
    f"- audio: gs://{BUCKET}/{AUDIO}\n"
    f"- transcript: gs://{BUCKET}/{TEXT}"
)


def test_the_archive_note_is_parsed() -> None:
    assert parse_archive_note(NOTE, BUCKET, PREFIX) == ArchivedRecording(
        audio_object=AUDIO, transcript_object=TEXT, source="note"
    )


def test_a_note_naming_another_bucket_is_ignored() -> None:
    assert parse_archive_note(NOTE.replace(BUCKET, "evil-bucket"), BUCKET, PREFIX) is None


def test_a_note_without_the_header_is_ignored() -> None:
    assert parse_archive_note(f"- audio: gs://{BUCKET}/{AUDIO}", BUCKET, PREFIX) is None


def test_a_transcript_only_note_has_no_audio_to_play() -> None:
    note = f"[Call archived to Cloud Storage]\n- transcript: gs://{BUCKET}/{TEXT}"
    assert parse_archive_note(note, BUCKET, PREFIX) is None


# --- locating ----------------------------------------------------------------


async def _no_messages(_cid, _before=None):
    return []


async def test_the_custom_attribute_wins() -> None:
    conv = {
        "custom_attributes": {
            "call_recording_object": AUDIO,
            "call_recording_transcript_object": TEXT,
        }
    }
    rec = await find_archived_recording("42", conv, _no_messages, bucket=BUCKET, prefix=PREFIX)
    assert rec == ArchivedRecording(audio_object=AUDIO, transcript_object=TEXT, source="attribute")


async def test_an_attribute_outside_the_prefix_is_not_trusted() -> None:
    conv = {"custom_attributes": {"call_recording_object": "elsewhere/CA1.mp3"}}
    rec = await find_archived_recording("42", conv, _no_messages, bucket=BUCKET, prefix=PREFIX)
    assert rec is None


async def test_older_calls_are_found_from_their_private_note() -> None:
    async def _messages(_cid, before=None):
        if before is None:
            return [
                {"id": 10, "private": False, "content": "hello"},
                {"id": 11, "private": True, "content": NOTE},
                {"id": 12, "private": True, "content": "later note"},
            ]
        return []

    rec = await find_archived_recording("42", {"custom_attributes": {}}, _messages, bucket=BUCKET, prefix=PREFIX)
    assert rec is not None and rec.audio_object == AUDIO and rec.source == "note"


async def test_a_public_message_shaped_like_the_note_is_ignored() -> None:
    """A customer can type anything into a public message; only a private note
    (which only staff and this backend can write) is a location record."""

    async def _messages(_cid, before=None):
        return [{"id": 11, "private": False, "content": NOTE}] if before is None else []

    rec = await find_archived_recording("42", {}, _messages, bucket=BUCKET, prefix=PREFIX)
    assert rec is None


async def test_note_search_pages_back_through_history() -> None:
    pages = {
        None: [{"id": i, "private": False, "content": "x"} for i in range(100, 120)],
        100: [{"id": 50, "private": True, "content": NOTE}],
    }
    asked: list = []

    async def _messages(_cid, before=None):
        asked.append(before)
        return pages.get(before, [])

    rec = await find_archived_recording("42", {}, _messages, bucket=BUCKET, prefix=PREFIX)
    assert rec is not None and asked == [None, 100]


async def test_a_failing_message_read_is_no_recording_not_an_error() -> None:
    async def _boom(_cid, before=None):
        raise RuntimeError("chatwoot down")

    assert await find_archived_recording("42", {}, _boom, bucket=BUCKET, prefix=PREFIX) is None


# --- Range -------------------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, None),
        ("", None),
        ("bytes=0-99", (0, 99)),
        ("bytes=100-", (100, 999)),
        ("bytes=-100", (900, 999)),
        ("bytes=900-5000", (900, 999)),
        ("bytes=0-0", (0, 0)),
        ("bytes=0-1,5-9", None),  # multi-range: serve the whole object
        ("items=0-9", None),
        ("bytes=abc-", None),  # malformed: ignored, per RFC 9110
        ("bytes=5-2", None),
    ],
)
def test_range_parsing(header, expected) -> None:
    assert parse_range(header, 1000) == expected


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=1000-2000", "bytes=-0"])
def test_unsatisfiable_ranges(header) -> None:
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, 1000)
