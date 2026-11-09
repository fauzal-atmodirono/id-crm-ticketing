"""The pieces the call-recording player stands on: the signed link, finding a
call's archived object, and HTTP Range parsing. Router-level behaviour is in
test_recording_retrieval.py."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone.recording_access import (
    ArchivedRecording,
    InvalidRecordingToken,
    ObjectInfo,
    RangeNotSatisfiable,
    message_sender,
    object_bound_to,
    parse_archive_note,
    parse_range,
    resolve_recording,
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


# --- resolving (R43: the binding is GCS metadata, not Chatwoot) -------------

BACKEND = ("user", 900)


class Store:
    def __init__(self, objects: dict[str, dict] | None = None, may_update: bool = True) -> None:
        self.objects = {k: dict(v) for k, v in (objects or {}).items()}
        self.may_update = may_update
        self.patched: list[tuple[str, dict]] = []

    def stat(self, bucket, name):
        assert bucket == BUCKET
        o = self.objects.get(name)
        return None if o is None else ObjectInfo(size=10, metadata=dict(o))

    def read(self, bucket, name, start=None, end=None):
        return b"x" * 10

    def set_metadata(self, bucket, name, metadata):
        if not self.may_update or name not in self.objects:
            return False
        self.objects[name].update(metadata)
        self.patched.append((name, dict(metadata)))
        return True


async def _backend():
    return BACKEND


def _note(sender_type="user", sender_id=900, private=True, content=NOTE, mid=11):
    return {
        "id": mid,
        "private": private,
        "content": content,
        "sender": {"type": sender_type, "id": sender_id},
    }


def _reader(messages):
    async def _read(_cid, before=None):
        return {"payload": messages} if before is None else {"payload": []}

    return _read


async def _no_messages(_cid, _before=None):
    return []


def _conv(attrs=None, session="phone-CA1"):
    return {"custom_attributes": attrs or {}, "additional_attributes": {"session_id": session}}


async def _resolve(conv, store, messages=None, backend=_backend, cid="42"):
    return await resolve_recording(
        cid,
        conv,
        _reader(messages or []),
        store,
        backend,
        bucket=BUCKET,
        prefix=PREFIX,
    )


async def test_an_attribute_whose_object_is_bound_here_plays() -> None:
    store = Store({AUDIO: {"conversation_id": "42"}, TEXT: {"conversation_id": "42"}})
    res = await _resolve(
        _conv({"call_recording_object": AUDIO, "call_recording_transcript_object": TEXT}), store
    )
    assert res.status == "available"
    assert res.recording == ArchivedRecording(AUDIO, TEXT, "attribute")


async def test_an_attribute_pointing_at_another_conversations_object_is_refused() -> None:
    """An agent can edit custom attributes; pointing conversation 42 at
    conversation 43's recording must not play it."""
    store = Store({AUDIO: {"conversation_id": "43"}})
    res = await _resolve(_conv({"call_recording_object": AUDIO}), store)
    assert res.status == "mismatch"


async def test_an_attribute_to_an_unbound_object_is_only_a_hint() -> None:
    """No metadata + no backend note -> nothing proves it is ours."""
    store = Store({AUDIO: {}})
    res = await _resolve(_conv({"call_recording_object": AUDIO}), store)
    assert res.status == "empty"
    assert store.patched == []


async def test_a_transcript_bound_elsewhere_is_dropped() -> None:
    store = Store({AUDIO: {"conversation_id": "42"}, TEXT: {"conversation_id": "43"}})
    res = await _resolve(
        _conv({"call_recording_object": AUDIO, "call_recording_transcript_object": TEXT}), store
    )
    assert res.status == "available" and res.recording.transcript_object == ""


async def test_an_attribute_outside_the_prefix_is_not_trusted() -> None:
    res = await _resolve(_conv({"call_recording_object": "elsewhere/CA1.mp3"}), Store())
    assert res.status == "empty"


async def test_a_legacy_backend_note_with_a_matching_sid_plays_and_backfills() -> None:
    store = Store({AUDIO: {}, TEXT: {}})
    res = await _resolve(_conv(), store, [_note()])
    assert res.status == "available" and res.recording.source == "note"
    assert res.recording.transcript_object == TEXT
    assert store.objects[AUDIO] == {"conversation_id": "42", "call_sid": "CA1"}
    assert store.objects[TEXT]["conversation_id"] == "42"


async def test_an_agent_authored_fake_note_is_ignored() -> None:
    store = Store({AUDIO: {}})
    res = await _resolve(_conv(), store, [_note(sender_id=5)])
    assert res.status == "empty"
    assert store.patched == []


async def test_a_note_from_another_bot_is_ignored() -> None:
    res = await _resolve(_conv(), Store({AUDIO: {}}), [_note(sender_type="agent_bot")])
    assert res.status == "empty"


async def test_a_public_message_shaped_like_the_note_is_ignored() -> None:
    res = await _resolve(_conv(), Store({AUDIO: {}}), [_note(private=False)])
    assert res.status == "empty"


async def test_a_legacy_note_for_another_calls_sid_is_refused() -> None:
    store = Store({AUDIO: {}})
    res = await _resolve(_conv(session="phone-CA_OTHER"), store, [_note()])
    assert res.status == "mismatch"
    assert store.patched == []


async def test_a_legacy_object_that_cannot_be_backfilled_is_refused() -> None:
    res = await _resolve(_conv(), Store({AUDIO: {}}, may_update=False), [_note()])
    assert res.status == "unverifiable"


async def test_an_unknown_backend_identity_disables_the_legacy_path() -> None:
    async def _unknown():
        return None

    res = await _resolve(_conv(), Store({AUDIO: {}}), [_note()], backend=_unknown)
    assert res.status == "empty"


async def test_a_note_whose_object_is_bound_elsewhere_is_refused() -> None:
    res = await _resolve(_conv(), Store({AUDIO: {"conversation_id": "43"}}), [_note()])
    assert res.status == "mismatch"


async def test_a_missing_object_is_deleted() -> None:
    res = await _resolve(_conv(), Store(), [_note()])
    assert res.status == "deleted"


async def test_note_search_pages_back_through_history() -> None:
    pages = {
        None: [{"id": i, "private": False, "content": "x"} for i in range(100, 120)],
        100: [_note(mid=50)],
    }
    asked: list = []

    async def _messages(_cid, before=None):
        asked.append(before)
        return pages.get(before, [])

    res = await resolve_recording(
        "42", _conv(), _messages, Store({AUDIO: {}}), _backend, bucket=BUCKET, prefix=PREFIX
    )
    assert res.status == "available" and asked == [None, 100]


async def test_a_failing_message_read_is_no_recording_not_an_error() -> None:
    async def _boom(_cid, before=None):
        raise RuntimeError("chatwoot down")

    res = await resolve_recording(
        "42", _conv(), _boom, Store({AUDIO: {}}), _backend, bucket=BUCKET, prefix=PREFIX
    )
    assert res.status == "empty"


def test_sender_is_read_from_either_chatwoot_shape() -> None:
    assert message_sender({"sender": {"type": "user", "id": 9}}) == ("user", 9)
    assert message_sender({"sender_type": "User", "sender_id": 9}) == ("user", 9)
    assert message_sender({"sender_type": "AgentBot", "sender_id": 3}) == ("agent_bot", 3)
    assert message_sender({}) is None


async def test_the_stream_recheck_refuses_an_object_bound_elsewhere() -> None:
    store = Store({AUDIO: {"conversation_id": "43"}})
    assert await object_bound_to(store, BUCKET, AUDIO, "42") is None
    assert await object_bound_to(store, BUCKET, AUDIO, "43") is not None


async def test_api_token_identity_resolves_the_backend_user(respx_mock) -> None:
    import httpx

    from chatbot.features.chat.phone.recording_access import ApiTokenIdentity
    from chatbot.platform.config import get_settings

    settings = get_settings().model_copy(
        update={
            "chatwoot_api_url": "http://chatwoot.invalid",
            "chatwoot_api_token": "tok",
            "chatwoot_enabled": True,
        }
    )
    route = respx_mock.get("http://chatwoot.invalid/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 900})
    )
    ident = ApiTokenIdentity(settings)
    assert await ident() == ("user", 900)
    assert await ident() == ("user", 900)
    assert route.call_count == 1


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
