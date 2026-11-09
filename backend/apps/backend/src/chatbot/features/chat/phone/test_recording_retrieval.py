"""The in-CRM call-recording player's HTTP surface (recording_router.py).

Every external dependency is faked: Chatwoot reads are plain coroutines, the
object store is a dict with per-object metadata, the transcriber is a lambda,
the Chatwoot session validator and RBAC repository are stubs. No GCS, Gemini
or Chatwoot call can happen from here.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.data_scope import Scope
from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.phone import recording_router
from chatbot.features.chat.phone.recording_access import ObjectInfo, sign_token
from chatbot.features.chat.phone.recording_router import (
    RecordingTokenRedactor,
    build_recording_router,
    install_access_log_redaction,
    redact_recording_tokens,
)

SECRET = "k" * 48
PREFIX = "proton-crm/call-recording"
BUCKET = "bucket-x"
AUDIO = f"{PREFIX}/2026-11-09/CA1.mp3"
TEXT = f"{PREFIX}/2026-11-09/CA1.txt"
AUDIO_BYTES = bytes(range(256)) * 4  # 1024 distinguishable bytes
BOUND_42 = {"conversation_id": "42", "call_sid": "CA1"}
SESSION = {"x-chatwoot-access-token": "good", "x-chatwoot-client": "c", "x-chatwoot-uid": "u"}
OTHER = {**SESSION, "x-chatwoot-access-token": "other"}
NOTE = (
    "[Call archived to Cloud Storage]\n"
    f"- audio: gs://{BUCKET}/{AUDIO}\n- transcript: gs://{BUCKET}/{TEXT}"
)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {AUDIO: AUDIO_BYTES, TEXT: b"CUSTOMER: hello"}
        self.meta: dict[str, dict[str, str]] = {AUDIO: dict(BOUND_42), TEXT: dict(BOUND_42)}
        self.fail = False
        self.may_update = True
        self.reads: list[tuple[str, int | None, int | None]] = []

    def stat(self, bucket: str, name: str):
        assert bucket == BUCKET
        if self.fail:
            raise RuntimeError("gcs down")
        if name not in self.objects:
            return None
        return ObjectInfo(size=len(self.objects[name]), metadata=dict(self.meta.get(name, {})))

    def read(self, bucket: str, name: str, start=None, end=None) -> bytes:
        assert bucket == BUCKET
        self.reads.append((name, start, end))
        data = self.objects[name]
        return data if start is None and end is None else data[start : end + 1]

    def set_metadata(self, bucket, name, metadata) -> bool:  # noqa: ARG002
        if not self.may_update:
            return False
        self.meta.setdefault(name, {}).update(metadata)
        return True


class World:
    def __init__(self) -> None:
        self.conversations: dict[str, dict[str, Any]] = {
            "42": {
                "id": 42,
                "inbox_id": 5,
                "additional_attributes": {"session_id": "phone-CA1"},
                "custom_attributes": {
                    "call_recording_object": AUDIO,
                    "call_recording_transcript_object": TEXT,
                },
            }
        }
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.notes: list[tuple[str, str]] = []
        self.storage = FakeStorage()
        self.audit = InMemoryAuditLog()
        self.transcribed: list[tuple[bytes, str]] = []
        self.transcript_result: str | None = "CUSTOMER: saya nak tanya X70"

    async def read(self, cid: str):
        return self.conversations.get(cid)

    async def read_messages(self, cid: str, before=None):
        return {"payload": self.messages.get(cid, [])} if before is None else {"payload": []}

    async def post_note(self, cid: str, text: str):
        self.notes.append((cid, text))

    async def transcribe(self, _settings, audio: bytes, mime_type: str = "audio/mp3"):
        self.transcribed.append((audio, mime_type))
        return self.transcript_result

    async def backend(self):
        return ("user", 900)


class _Validator:
    async def resolve_identity(self, token, client, uid):  # noqa: ARG002
        return {"good": (7, False), "other": (8, False)}.get(token)

    def display_name(self, user_id: int) -> str:
        return f"Agent {user_id}"


class _Repo:
    def __init__(self, perms: set[str], scope: Scope) -> None:
        self.perms = perms
        self.scope = scope

    async def permissions_for_user(self, _uid):
        return self.perms

    async def effective_scope(self, _uid):
        return self.scope


class _FailingAudit(InMemoryAuditLog):
    def __init__(self) -> None:
        super().__init__()
        self.down = True

    async def append(self, entry) -> None:
        if self.down:
            raise RuntimeError("audit store down")
        await super().append(entry)


@pytest.fixture(autouse=True)
def _reset_router_state():
    recording_router.reset_state()
    yield
    recording_router.reset_state()


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "call_recording_retrieval_enabled": True,
            "call_recording_signing_secret": SECRET,
            "call_recording_url_ttl_seconds": 300,
            "call_recording_retranscribe_cooldown_seconds": 600,
            "call_recording_retranscribe_user_hourly_cap": 5,
            "call_recording_retranscribe_global_hourly_cap": 30,
            "phone_recording_archive_bucket": BUCKET,
            "phone_recording_archive_prefix": PREFIX,
            "phone_recording_transcription_enabled": True,
            "proton_backend_key": "valid_key",
            "rbac_enabled": True,
            "data_scoped_rbac_enabled": False,
        }
    )


@pytest.fixture
def world() -> World:
    return World()


def _client(settings, world: World, *, perms=None, scope=None, audit=None, **kw) -> TestClient:
    app = FastAPI()
    granted = perms if perms is not None else {"call_recording.listen"}
    app.include_router(
        build_recording_router(
            settings,
            authz_repo=_Repo(granted, scope or Scope()),
            validator=_Validator(),
            audit=audit if audit is not None else world.audit,
            chatwoot_read=world.read,
            chatwoot_messages=world.read_messages,
            post_note=world.post_note,
            backend_sender=world.backend,
            storage=world.storage,
            transcribe=world.transcribe,
            **kw,
        )
    )
    return TestClient(app)


def _meta(client: TestClient, cid: str = "42", headers=SESSION) -> dict:
    res = client.get(f"/calls/{cid}/recording", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _audio_path(client: TestClient, cid: str = "42") -> str:
    return _meta(client, cid)["play_url"]


async def _rows(audit, event_type: str):
    return await audit.list_filtered(event_type=event_type)


# --- gate and identity (R44) ---------------------------------------------------


def test_the_flag_off_returns_404_for_everyone(settings, world) -> None:
    client = _client(settings.model_copy(update={"call_recording_retrieval_enabled": False}), world)
    assert client.get("/calls/42/recording", headers=SESSION).status_code == 404
    assert client.get("/calls/42/recording").status_code == 404
    token, _ = sign_token(
        SECRET, conversation_id="42", object_name=AUDIO, kind="audio", user_id=7, ttl_seconds=60
    )
    assert client.get(f"/calls/42/recording/audio?t={token}").status_code == 404


def test_no_session_is_401(settings, world) -> None:
    assert _client(settings, world).get("/calls/42/recording").status_code == 401


def test_the_shared_key_is_never_enough(settings, world) -> None:
    key = {"x-api-key": "valid_key"}
    client = _client(settings, world)
    assert client.get("/calls/42/recording", headers=key).status_code == 401
    assert client.post("/calls/42/recording/retranscribe", headers=key).status_code == 401


def test_with_rbac_off_there_is_no_person_so_401(settings, world) -> None:
    client = _client(settings.model_copy(update={"rbac_enabled": False}), world)
    for headers in ({"x-api-key": "valid_key"}, SESSION):
        assert client.get("/calls/42/recording", headers=headers).status_code == 401
        assert client.post("/calls/42/recording/retranscribe", headers=headers).status_code == 401


def test_a_user_without_call_recording_listen_is_403(settings, world) -> None:
    client = _client(settings, world, perms={"cases.view"})
    assert client.get("/calls/42/recording", headers=SESSION).status_code == 403
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 403


def test_a_missing_signing_secret_is_503_not_a_weak_link(settings, world) -> None:
    client = _client(settings.model_copy(update={"call_recording_signing_secret": "short"}), world)
    res = client.get("/calls/42/recording", headers=SESSION)
    assert res.status_code == 503
    assert "CALL_RECORDING_SIGNING_SECRET" in res.json()["detail"]


@pytest.mark.parametrize("cid", ["conv_1", "42abc", "4 2"])
def test_a_non_numeric_conversation_id_is_refused(settings, world, cid) -> None:
    res = _client(settings, world).get(f"/calls/{cid}/recording", headers=SESSION)
    assert res.status_code == 422


# --- metadata ----------------------------------------------------------------


def test_a_permitted_person_gets_a_signed_backend_play_url(settings, world) -> None:
    res = _client(settings, world).get("/calls/42/recording", headers=SESSION)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "available"
    assert body["play_url"].startswith("/calls/42/recording/audio?t=")
    # The transcript credential travels separately, never in a URL.
    assert body["transcript_url"] == "/calls/42/recording/transcript"
    assert body["transcript_token"]
    assert body["expires_at"] and body["can_retranscribe"] is True
    assert "gs://" not in res.text and BUCKET not in res.text
    assert res.headers["cache-control"] == "no-store"


def test_no_recording_is_a_clear_empty_state(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {}
    body = _meta(_client(settings, world))
    assert body["status"] == "empty" and "play_url" not in body


def test_an_object_pruned_by_retention_is_deleted_state(settings, world) -> None:
    del world.storage.objects[AUDIO]
    body = _meta(_client(settings, world))
    assert body["status"] == "deleted" and "play_url" not in body


def test_storage_failure_is_502_not_deleted(settings, world) -> None:
    world.storage.fail = True
    assert _client(settings, world).get("/calls/42/recording", headers=SESSION).status_code == 502


def test_without_transcription_re_transcribe_is_not_offered(settings, world) -> None:
    off = settings.model_copy(update={"phone_recording_transcription_enabled": False})
    assert _meta(_client(off, world))["can_retranscribe"] is False


# --- conversation binding (R43) ---------------------------------------------------


def test_an_attribute_pointing_at_another_conversations_object_does_not_play(
    settings, world
) -> None:
    world.storage.meta[AUDIO] = {"conversation_id": "43", "call_sid": "CA1"}
    body = _meta(_client(settings, world))
    assert body["status"] == "empty" and "play_url" not in body


def test_a_backend_note_bound_elsewhere_is_404(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {}
    world.storage.meta[AUDIO] = {"conversation_id": "43"}
    world.messages["42"] = [
        {"id": 9, "private": True, "content": NOTE, "sender": {"type": "user", "id": 900}}
    ]
    assert _client(settings, world).get("/calls/42/recording", headers=SESSION).status_code == 404


def test_an_agent_authored_fake_note_is_ignored(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {}
    world.storage.meta = {}
    world.messages["42"] = [
        {"id": 9, "private": True, "content": NOTE, "sender": {"type": "user", "id": 5}}
    ]
    assert _meta(_client(settings, world))["status"] == "empty"
    assert world.storage.meta == {}


def test_a_legacy_backend_note_with_a_matching_sid_plays(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {"recording_sid": "RE1"}
    world.storage.meta = {}
    world.messages["42"] = [
        {"id": 9, "private": True, "content": NOTE, "sender_type": "User", "sender_id": 900}
    ]
    client = _client(settings, world)
    body = _meta(client)
    assert body["status"] == "available"
    # the first resolution bound the legacy objects to this conversation
    assert world.storage.meta[AUDIO]["conversation_id"] == "42"
    assert client.get(body["play_url"]).content == AUDIO_BYTES


def test_a_legacy_object_the_service_account_cannot_bind_is_refused(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {}
    world.storage.meta = {}
    world.storage.may_update = False
    world.messages["42"] = [
        {"id": 9, "private": True, "content": NOTE, "sender": {"type": "user", "id": 900}}
    ]
    assert _client(settings, world).get("/calls/42/recording", headers=SESSION).status_code == 404


def test_the_stream_rechecks_the_binding(settings, world) -> None:
    """A link minted while the object was ours stops working if the object's
    metadata no longer names this conversation."""
    client = _client(settings, world)
    path = _audio_path(client)
    world.storage.meta[AUDIO] = {"conversation_id": "43"}
    assert client.get(path).status_code == 404


# --- audio stream --------------------------------------------------------------


def test_the_audio_streams_whole_with_seek_headers(settings, world) -> None:
    client = _client(settings, world)
    res = client.get(_audio_path(client))
    assert res.status_code == 200
    assert res.content == AUDIO_BYTES
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.headers["accept-ranges"] == "bytes"
    assert res.headers["content-length"] == str(len(AUDIO_BYTES))
    assert "no-store" in res.headers["cache-control"]


async def test_head_returns_headers_without_bytes_or_an_audit_row(settings, world) -> None:
    client = _client(settings, world)
    res = client.head(_audio_path(client))
    assert res.status_code == 200
    assert res.content == b""
    assert res.headers["content-length"] == str(len(AUDIO_BYTES))
    assert res.headers["accept-ranges"] == "bytes"
    assert world.storage.reads == []
    assert await _rows(world.audit, "call_recording_play") == []


def test_a_range_request_gets_206_with_exactly_those_bytes(settings, world) -> None:
    client = _client(settings, world)
    res = client.get(_audio_path(client), headers={"Range": "bytes=100-199"})
    assert res.status_code == 206
    assert res.content == AUDIO_BYTES[100:200]
    assert res.headers["content-range"] == f"bytes 100-199/{len(AUDIO_BYTES)}"
    assert res.headers["content-length"] == "100"


def test_an_open_ended_and_a_suffix_range(settings, world) -> None:
    client = _client(settings, world)
    path = _audio_path(client)
    tail = client.get(path, headers={"Range": "bytes=1000-"})
    assert tail.status_code == 206 and tail.content == AUDIO_BYTES[1000:]
    suffix = client.get(path, headers={"Range": "bytes=-24"})
    assert suffix.status_code == 206 and suffix.content == AUDIO_BYTES[-24:]


def test_a_range_past_the_end_is_416(settings, world) -> None:
    client = _client(settings, world)
    res = client.get(_audio_path(client), headers={"Range": "bytes=5000-"})
    assert res.status_code == 416
    assert res.headers["content-range"] == f"bytes */{len(AUDIO_BYTES)}"


def test_a_large_range_is_read_in_chunks(settings, world, monkeypatch) -> None:
    monkeypatch.setattr(recording_router, "_CHUNK_BYTES", 300)
    client = _client(settings, world)
    assert client.get(_audio_path(client)).content == AUDIO_BYTES
    assert [(s, e) for _n, s, e in world.storage.reads] == [
        (0, 299),
        (300, 599),
        (600, 899),
        (900, 1023),
    ]


def test_an_expired_link_is_refused(settings, world) -> None:
    token, _ = sign_token(
        SECRET,
        conversation_id="42",
        object_name=AUDIO,
        kind="audio",
        user_id=7,
        ttl_seconds=60,
        now=1_000.0,
    )
    assert _client(settings, world).get(f"/calls/42/recording/audio?t={token}").status_code == 403


def test_a_tampered_link_is_refused(settings, world) -> None:
    client = _client(settings, world)
    path = _audio_path(client)
    forged = path[:-2] + ("AA" if not path.endswith("AA") else "BB")
    assert client.get(forged).status_code == 403


def test_a_link_cannot_be_replayed_on_another_conversation(settings, world) -> None:
    client = _client(settings, world)
    path = _audio_path(client, "42")
    assert client.get(path.replace("/calls/42/", "/calls/43/")).status_code == 403


def test_a_missing_token_is_refused(settings, world) -> None:
    assert _client(settings, world).get("/calls/42/recording/audio").status_code == 403


def test_a_token_for_an_object_outside_the_archive_is_refused(settings, world) -> None:
    token, _ = sign_token(
        SECRET,
        conversation_id="42",
        object_name="secrets/key.mp3",
        kind="audio",
        user_id=7,
        ttl_seconds=60,
    )
    assert _client(settings, world).get(f"/calls/42/recording/audio?t={token}").status_code == 403


# --- audit (R45: fail-closed, names the object) ----------------------------------


async def test_a_play_writes_one_audit_row_however_many_ranges_it_fetches(settings, world) -> None:
    client = _client(settings, world)
    path = _audio_path(client)
    client.get(path, headers={"Range": "bytes=0-99"})
    client.get(path, headers={"Range": "bytes=100-"})
    rows = await _rows(world.audit, "call_recording_play")
    assert len(rows) == 1
    row = rows[0]
    assert row.target == "conversation:42"
    assert row.actor_user_id == 7 and row.actor_name == "Agent 7"
    assert row.after == {"object": AUDIO}
    assert row.at


async def test_each_minted_link_played_is_a_new_audit_row(settings, world) -> None:
    client = _client(settings, world)
    client.get(_audio_path(client))
    client.get(_audio_path(client))
    assert len(await _rows(world.audit, "call_recording_play")) == 2


async def test_minting_alone_writes_no_play_row(settings, world) -> None:
    _audio_path(_client(settings, world))
    assert await _rows(world.audit, "call_recording_play") == []


async def test_a_refused_link_writes_no_audit_row(settings, world) -> None:
    _client(settings, world).get("/calls/42/recording/audio?t=garbage")
    assert await _rows(world.audit, "call_recording_play") == []


async def test_no_audio_is_served_without_its_audit_row(settings, world) -> None:
    audit = _FailingAudit()
    client = _client(settings, world, audit=audit)
    path = _audio_path(client)
    assert client.get(path).status_code == 503
    assert world.storage.reads == []
    # The link was released, so the play is audited once the store recovers.
    audit.down = False
    assert client.get(path).status_code == 200
    assert len(await _rows(audit, "call_recording_play")) == 1


# --- transcript ------------------------------------------------------------------


async def test_the_transcript_is_fetched_with_a_header_token_and_audited(settings, world) -> None:
    client = _client(settings, world)
    body = _meta(client)
    res = client.get(body["transcript_url"], headers={"X-Recording-Token": body["transcript_token"]})
    assert res.status_code == 200
    assert res.text == "CUSTOMER: hello"
    assert res.headers["content-type"].startswith("text/plain")
    assert "no-store" in res.headers["cache-control"]
    rows = await _rows(world.audit, "call_recording_transcript_view")
    assert len(rows) == 1 and rows[0].after == {"object": TEXT}


def test_an_audio_link_cannot_open_the_transcript(settings, world) -> None:
    client = _client(settings, world)
    token = _audio_path(client).split("t=", 1)[1]
    res = client.get("/calls/42/recording/transcript", headers={"X-Recording-Token": token})
    assert res.status_code == 403


def test_a_transcript_bound_elsewhere_is_not_offered(settings, world) -> None:
    world.storage.meta[TEXT] = {"conversation_id": "43"}
    body = _meta(_client(settings, world))
    assert body["status"] == "available" and "transcript_url" not in body


# --- re-transcribe -----------------------------------------------------------------


async def test_retranscribe_runs_the_transcriber_and_posts_a_note(settings, world) -> None:
    res = _client(settings, world).post("/calls/42/recording/retranscribe", headers=SESSION)
    assert res.status_code == 202 and res.json()["status"] == "queued"
    assert world.transcribed == [(AUDIO_BYTES, "audio/mp3")]
    cid, text = world.notes[0]
    assert cid == "42" and text.startswith("[Re-transcribed]") and "X70" in text
    rows = await _rows(world.audit, "call_recording_retranscribe")
    assert len(rows) == 1 and rows[0].actor_user_id == 7 and rows[0].after == {"object": AUDIO}


def test_a_wav_archive_is_retranscribed_as_wav(settings, world) -> None:
    wav = f"{PREFIX}/2026-11-09/CA1.wav"
    world.storage.objects[wav] = b"RIFF"
    world.storage.meta[wav] = dict(BOUND_42)
    world.conversations["42"]["custom_attributes"] = {"call_recording_object": wav}
    _client(settings, world).post("/calls/42/recording/retranscribe", headers=SESSION)
    assert world.transcribed == [(b"RIFF", "audio/wav")]


def _second_conversation(world: World, cid: str) -> None:
    obj = f"{PREFIX}/2026-11-09/CA{cid}.mp3"
    world.storage.objects[obj] = b"x"
    world.storage.meta[obj] = {"conversation_id": cid}
    world.conversations[cid] = {"id": int(cid), "custom_attributes": {"call_recording_object": obj}}


def test_retranscribe_is_rate_limited_per_conversation(settings, world) -> None:
    _second_conversation(world, "43")
    client = _client(settings, world)
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 202
    again = client.post("/calls/42/recording/retranscribe", headers=SESSION)
    assert again.status_code == 429 and int(again.headers["retry-after"]) > 0
    # The panel reads the wait from the body (adminRequest keeps no headers).
    assert again.json()["detail"]["retry_after_seconds"] == int(again.headers["retry-after"])
    assert client.post("/calls/43/recording/retranscribe", headers=SESSION).status_code == 202


def test_retranscribe_is_allowed_again_after_the_cooldown(settings, world) -> None:
    now = [1000.0]
    client = _client(settings, world, monotonic=lambda: now[0])
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 202
    now[0] += 601
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 202


def test_retranscribe_has_a_per_user_hourly_cap(settings, world) -> None:
    capped = settings.model_copy(update={"call_recording_retranscribe_user_hourly_cap": 2})
    for cid in ("43", "44", "45"):
        _second_conversation(world, cid)
    client = _client(capped, world)
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 202
    assert client.post("/calls/43/recording/retranscribe", headers=SESSION).status_code == 202
    res = client.post("/calls/44/recording/retranscribe", headers=SESSION)
    assert res.status_code == 429 and "Hourly" in res.json()["detail"]["message"]
    assert res.json()["detail"]["retry_after_seconds"] > 0
    # Another person is not limited by the first one's cap.
    assert client.post("/calls/45/recording/retranscribe", headers=OTHER).status_code == 202


def test_retranscribe_has_a_global_hourly_cap_that_rolls_off(settings, world) -> None:
    capped = settings.model_copy(update={"call_recording_retranscribe_global_hourly_cap": 1})
    _second_conversation(world, "43")
    now = [1000.0]
    client = _client(capped, world, monotonic=lambda: now[0])
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 202
    assert client.post("/calls/43/recording/retranscribe", headers=OTHER).status_code == 429
    now[0] += 3601
    assert client.post("/calls/43/recording/retranscribe", headers=OTHER).status_code == 202


def test_retranscribe_without_its_audit_row_is_503_and_frees_the_slot(settings, world) -> None:
    audit = _FailingAudit()
    client = _client(settings, world, audit=audit)
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 503
    assert world.transcribed == []
    audit.down = False
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 202


def test_a_failed_transcription_posts_nothing(settings, world) -> None:
    world.transcript_result = None
    client = _client(settings, world)
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 202
    assert world.notes == []


def test_retranscribe_without_a_recording_is_404(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {}
    res = _client(settings, world).post("/calls/42/recording/retranscribe", headers=SESSION)
    assert res.status_code == 404


def test_retranscribe_with_transcription_off_is_409(settings, world) -> None:
    off = settings.model_copy(update={"phone_recording_transcription_enabled": False})
    res = _client(off, world).post("/calls/42/recording/retranscribe", headers=SESSION)
    assert res.status_code == 409


# --- data scope ------------------------------------------------------------------


def test_scope_a_conversation_outside_the_callers_scope_is_404(settings, world) -> None:
    scoped = settings.model_copy(update={"data_scoped_rbac_enabled": True})
    client = _client(scoped, world, scope=Scope.build(inbox_ids=[99]))
    assert client.get("/calls/42/recording", headers=SESSION).status_code == 404
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 404


def test_scope_a_conversation_inside_the_callers_scope_plays(settings, world) -> None:
    scoped = settings.model_copy(update={"data_scoped_rbac_enabled": True})
    client = _client(scoped, world, scope=Scope.build(inbox_ids=[5]))
    assert _meta(client)["status"] == "available"


# --- access-log redaction ----------------------------------------------------------


def test_player_tokens_are_redacted_from_the_uvicorn_access_log(settings, world) -> None:
    _client(settings, world)  # building the router installs the filter
    logger = logging.getLogger("uvicorn.access")
    install_access_log_redaction()  # idempotent
    redactors = [f for f in logger.filters if isinstance(f, RecordingTokenRedactor)]
    assert len(redactors) == 1

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5", "GET", "/calls/42/recording/audio?t=SECRETTOKEN.SIG&x=1", "1.1", 206),
        None,
    )
    assert logger.filter(record)
    line = record.getMessage()
    assert "SECRETTOKEN" not in line
    assert "/calls/42/recording/audio?t=REDACTED&x=1" in line


def test_redaction_leaves_other_paths_alone() -> None:
    assert redact_recording_tokens("/search?t=abc") == "/search?t=abc"
    assert redact_recording_tokens("/calls/1/recording/transcript?t=abc") == (
        "/calls/1/recording/transcript?t=REDACTED"
    )


# --- concurrent first requests on one link (PDPA: audit before any byte) --------


class _GatedAudit(InMemoryAuditLog):
    """append() blocks until released, then succeeds or fails."""

    def __init__(self) -> None:
        super().__init__()
        import asyncio

        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.succeed = True
        self.calls = 0

    async def append(self, entry) -> None:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if not self.succeed:
            raise RuntimeError("audit store down")
        await super().append(entry)


async def _concurrent_plays(settings, world, succeed: bool):
    import asyncio

    import httpx

    audit = _GatedAudit()
    audit.succeed = succeed
    app = FastAPI()
    app.include_router(
        build_recording_router(
            settings,
            authz_repo=_Repo({"call_recording.listen"}, Scope()),
            validator=_Validator(),
            audit=audit,
            chatwoot_read=world.read,
            chatwoot_messages=world.read_messages,
            post_note=world.post_note,
            backend_sender=world.backend,
            storage=world.storage,
            transcribe=world.transcribe,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        meta = await client.get("/calls/42/recording", headers=SESSION)
        path = meta.json()["play_url"]
        first = asyncio.create_task(client.get(path, headers={"Range": "bytes=0-99"}))
        await audit.entered.wait()
        second = asyncio.create_task(client.get(path, headers={"Range": "bytes=100-199"}))
        await asyncio.sleep(0.05)
        # The second request is parked behind the first one's audit write.
        assert not second.done()
        assert world.storage.reads == []
        audit.release.set()
        return await first, await second, audit


async def test_concurrent_first_requests_wait_for_the_audit_row(settings, world) -> None:
    a, b, audit = await _concurrent_plays(settings, world, succeed=True)
    assert (a.status_code, b.status_code) == (206, 206)
    assert audit.calls == 1
    assert len(await _rows(audit, "call_recording_play")) == 1


async def test_concurrent_requests_all_fail_when_the_audit_row_fails(settings, world) -> None:
    a, b, audit = await _concurrent_plays(settings, world, succeed=False)
    assert (a.status_code, b.status_code) == (503, 503)
    assert world.storage.reads == []
    assert audit.calls == 1
