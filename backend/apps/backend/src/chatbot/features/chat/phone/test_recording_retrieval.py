"""The in-CRM call-recording player's HTTP surface (recording_router.py).

Every external dependency is faked: Chatwoot reads are plain coroutines, the
object store is a dict, the transcriber is a lambda. No GCS, Gemini or
Chatwoot call can happen from here.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.data_scope import Scope
from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.phone import recording_router
from chatbot.features.chat.phone.recording_access import sign_token
from chatbot.features.chat.phone.recording_router import build_recording_router

SECRET = "k" * 48
PREFIX = "proton-crm/call-recording"
BUCKET = "bucket-x"
AUDIO = f"{PREFIX}/2026-11-09/CA1.mp3"
TEXT = f"{PREFIX}/2026-11-09/CA1.txt"
AUDIO_BYTES = bytes(range(256)) * 4  # 1024 distinguishable bytes
KEY = {"x-api-key": "valid_key"}


class FakeStorage:
    def __init__(self, objects: dict[str, bytes] | None = None, fail: bool = False) -> None:
        self.objects = dict(objects or {})
        self.fail = fail
        self.reads: list[tuple[str, int | None, int | None]] = []

    def size(self, bucket: str, name: str) -> int | None:
        assert bucket == BUCKET
        if self.fail:
            raise RuntimeError("gcs down")
        data = self.objects.get(name)
        return None if data is None else len(data)

    def read(self, bucket: str, name: str, start=None, end=None) -> bytes:
        assert bucket == BUCKET
        self.reads.append((name, start, end))
        data = self.objects[name]
        if start is None and end is None:
            return data
        return data[start : end + 1]


class World:
    """One conversation store + collaborators, rebuilt per test."""

    def __init__(self) -> None:
        self.conversations: dict[str, dict[str, Any]] = {
            "42": {
                "id": 42,
                "inbox_id": 5,
                "custom_attributes": {
                    "call_recording_object": AUDIO,
                    "call_recording_transcript_object": TEXT,
                },
            }
        }
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.notes: list[tuple[str, str]] = []
        self.storage = FakeStorage({AUDIO: AUDIO_BYTES, TEXT: b"CUSTOMER: hello"})
        self.audit = InMemoryAuditLog()
        self.transcribed: list[bytes] = []
        self.transcript_result: str | None = "CUSTOMER: saya nak tanya X70"

    async def read(self, cid: str):
        return self.conversations.get(cid)

    async def read_messages(self, cid: str, before=None):
        return {"payload": self.messages.get(cid, [])} if before is None else {"payload": []}

    async def post_note(self, cid: str, text: str):
        self.notes.append((cid, text))

    async def transcribe(self, _settings, audio: bytes):
        self.transcribed.append(audio)
        return self.transcript_result


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
            "phone_recording_archive_bucket": BUCKET,
            "phone_recording_archive_prefix": PREFIX,
            "phone_recording_transcription_enabled": True,
            "proton_backend_key": "valid_key",
            "rbac_enabled": False,
            "data_scoped_rbac_enabled": False,
        }
    )


@pytest.fixture
def world() -> World:
    return World()


def _client(settings, world: World, **kw) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_recording_router(
            settings,
            audit=world.audit,
            chatwoot_read=world.read,
            chatwoot_messages=world.read_messages,
            post_note=world.post_note,
            storage=world.storage,
            transcribe=world.transcribe,
            **kw,
        )
    )
    return TestClient(app)


def _audio_path(client: TestClient, cid: str = "42") -> str:
    res = client.get(f"/calls/{cid}/recording", headers=KEY)
    assert res.status_code == 200, res.text
    return res.json()["play_url"]


# --- gate --------------------------------------------------------------------


def test_the_flag_off_returns_404(settings, world) -> None:
    client = _client(settings.model_copy(update={"call_recording_retrieval_enabled": False}), world)
    assert client.get("/calls/42/recording", headers=KEY).status_code == 404
    token, _ = sign_token(
        SECRET, conversation_id="42", object_name=AUDIO, kind="audio", user_id=None, ttl_seconds=60
    )
    assert client.get(f"/calls/42/recording/audio?t={token}").status_code == 404


def test_an_unauthenticated_caller_is_rejected(settings, world) -> None:
    res = _client(settings, world).get("/calls/42/recording")
    assert res.status_code == 401


def test_a_missing_signing_secret_is_503_not_a_weak_link(settings, world) -> None:
    client = _client(settings.model_copy(update={"call_recording_signing_secret": "short"}), world)
    res = client.get("/calls/42/recording", headers=KEY)
    assert res.status_code == 503
    assert "CALL_RECORDING_SIGNING_SECRET" in res.json()["detail"]


# --- metadata ----------------------------------------------------------------


def test_a_permitted_caller_gets_a_signed_backend_play_url(settings, world) -> None:
    res = _client(settings, world).get("/calls/42/recording", headers=KEY)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "available"
    assert body["play_url"].startswith("/calls/42/recording/audio?t=")
    assert body["transcript_url"].startswith("/calls/42/recording/transcript?t=")
    assert body["expires_at"]
    assert body["can_retranscribe"] is True
    # No bucket, gs:// URI or object name leaks to the browser.
    assert "gs://" not in res.text and BUCKET not in res.text and "CA1" not in res.text
    assert res.headers["cache-control"] == "no-store"


def test_no_recording_is_a_clear_empty_state(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {}
    body = _client(settings, world).get("/calls/42/recording", headers=KEY).json()
    assert body["status"] == "empty"
    assert "play_url" not in body


def test_an_object_pruned_by_retention_is_deleted_state(settings, world) -> None:
    del world.storage.objects[AUDIO]
    body = _client(settings, world).get("/calls/42/recording", headers=KEY).json()
    assert body["status"] == "deleted"
    assert "play_url" not in body


def test_storage_failure_is_502_not_deleted(settings, world) -> None:
    world.storage.fail = True
    res = _client(settings, world).get("/calls/42/recording", headers=KEY)
    assert res.status_code == 502


def test_an_older_call_is_found_from_its_archive_note(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {"recording_sid": "RE1"}
    world.messages["42"] = [
        {
            "id": 9,
            "private": True,
            "content": (
                "[Call archived to Cloud Storage]\n"
                f"- audio: gs://{BUCKET}/{AUDIO}\n- transcript: gs://{BUCKET}/{TEXT}"
            ),
        }
    ]
    client = _client(settings, world)
    body = client.get("/calls/42/recording", headers=KEY).json()
    assert body["status"] == "available"
    assert client.get(body["play_url"]).content == AUDIO_BYTES


def test_without_transcription_re_transcribe_is_not_offered(settings, world) -> None:
    off = settings.model_copy(update={"phone_recording_transcription_enabled": False})
    body = _client(off, world).get("/calls/42/recording", headers=KEY).json()
    assert body["can_retranscribe"] is False


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


def test_a_range_request_gets_206_with_exactly_those_bytes(settings, world) -> None:
    client = _client(settings, world)
    path = _audio_path(client)
    res = client.get(path, headers={"Range": "bytes=100-199"})
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
    res = client.get(_audio_path(client))
    assert res.content == AUDIO_BYTES
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
        user_id=None,
        ttl_seconds=60,
        now=1_000.0,
    )
    res = _client(settings, world).get(f"/calls/42/recording/audio?t={token}")
    assert res.status_code == 403


def test_a_tampered_link_is_refused(settings, world) -> None:
    client = _client(settings, world)
    path = _audio_path(client)
    forged = path[:-2] + ("AA" if not path.endswith("AA") else "BB")
    assert client.get(forged).status_code == 403


def test_a_link_cannot_be_replayed_on_another_conversation(settings, world) -> None:
    world.conversations["43"] = dict(world.conversations["42"], id=43)
    client = _client(settings, world)
    path = _audio_path(client, "42")
    assert client.get(path.replace("/calls/42/", "/calls/43/")).status_code == 403


def test_a_missing_token_is_refused(settings, world) -> None:
    assert _client(settings, world).get("/calls/42/recording/audio").status_code == 403


def test_a_token_signed_for_an_object_outside_the_archive_is_refused(settings, world) -> None:
    world.storage.objects["secrets/key.mp3"] = b"nope"
    token, _ = sign_token(
        SECRET,
        conversation_id="42",
        object_name="secrets/key.mp3",
        kind="audio",
        user_id=None,
        ttl_seconds=60,
    )
    assert _client(settings, world).get(f"/calls/42/recording/audio?t={token}").status_code == 403


# --- audit ---------------------------------------------------------------------


async def _audit_rows(world: World, event_type: str):
    return await world.audit.list_filtered(event_type=event_type)


async def test_a_play_writes_one_audit_row_however_many_ranges_it_fetches(settings, world) -> None:
    client = _client(settings, world)
    path = _audio_path(client)
    client.get(path, headers={"Range": "bytes=0-99"})
    client.get(path, headers={"Range": "bytes=100-"})
    rows = await _audit_rows(world, "call_recording_play")
    assert len(rows) == 1
    row = rows[0]
    assert row.target == "conversation:42"
    assert row.actor_name == "admin-api-key"  # RBAC off: the shared key, honestly named
    assert row.at


async def test_each_new_link_played_is_a_new_audit_row(settings, world) -> None:
    client = _client(settings, world)
    client.get(_audio_path(client))
    client.get(_audio_path(client))
    assert len(await _audit_rows(world, "call_recording_play")) == 2


async def test_minting_alone_writes_no_play_row(settings, world) -> None:
    client = _client(settings, world)
    _audio_path(client)
    assert await _audit_rows(world, "call_recording_play") == []


async def test_a_refused_link_writes_no_audit_row(settings, world) -> None:
    client = _client(settings, world)
    client.get("/calls/42/recording/audio?t=garbage")
    assert await _audit_rows(world, "call_recording_play") == []


# --- transcript ------------------------------------------------------------------


async def test_the_transcript_link_serves_text_and_is_audited(settings, world) -> None:
    client = _client(settings, world)
    url = client.get("/calls/42/recording", headers=KEY).json()["transcript_url"]
    res = client.get(url)
    assert res.status_code == 200
    assert res.text == "CUSTOMER: hello"
    assert res.headers["content-type"].startswith("text/plain")
    assert "no-store" in res.headers["cache-control"]
    assert len(await _audit_rows(world, "call_recording_transcript_view")) == 1


def test_an_audio_link_cannot_open_the_transcript(settings, world) -> None:
    client = _client(settings, world)
    token = _audio_path(client).split("t=", 1)[1]
    assert client.get(f"/calls/42/recording/transcript?t={token}").status_code == 403


# --- re-transcribe -----------------------------------------------------------------


async def test_retranscribe_runs_the_transcriber_and_posts_a_note(settings, world) -> None:
    client = _client(settings, world)
    res = client.post("/calls/42/recording/retranscribe", headers=KEY)
    assert res.status_code == 202
    assert res.json()["status"] == "queued"
    assert world.transcribed == [AUDIO_BYTES]
    assert len(world.notes) == 1
    cid, text = world.notes[0]
    assert cid == "42"
    assert text.startswith("[Re-transcribed]")
    assert "X70" in text
    assert len(await _audit_rows(world, "call_recording_retranscribe")) == 1


def test_retranscribe_is_rate_limited_per_conversation(settings, world) -> None:
    world.conversations["43"] = dict(world.conversations["42"], id=43)
    client = _client(settings, world)
    assert client.post("/calls/42/recording/retranscribe", headers=KEY).status_code == 202
    again = client.post("/calls/42/recording/retranscribe", headers=KEY)
    assert again.status_code == 429
    assert int(again.headers["retry-after"]) > 0
    # Another conversation is not throttled by the first.
    assert client.post("/calls/43/recording/retranscribe", headers=KEY).status_code == 202


def test_retranscribe_is_allowed_again_after_the_cooldown(settings, world) -> None:
    now = [1000.0]
    client = _client(settings, world, monotonic=lambda: now[0])
    assert client.post("/calls/42/recording/retranscribe", headers=KEY).status_code == 202
    now[0] += 601
    assert client.post("/calls/42/recording/retranscribe", headers=KEY).status_code == 202


def test_a_failed_transcription_posts_nothing(settings, world) -> None:
    world.transcript_result = None
    client = _client(settings, world)
    assert client.post("/calls/42/recording/retranscribe", headers=KEY).status_code == 202
    assert world.notes == []


def test_retranscribe_without_a_recording_is_404(settings, world) -> None:
    world.conversations["42"]["custom_attributes"] = {}
    res = _client(settings, world).post("/calls/42/recording/retranscribe", headers=KEY)
    assert res.status_code == 404


def test_retranscribe_with_transcription_off_is_409(settings, world) -> None:
    off = settings.model_copy(update={"phone_recording_transcription_enabled": False})
    res = _client(off, world).post("/calls/42/recording/retranscribe", headers=KEY)
    assert res.status_code == 409


def test_retranscribe_needs_auth(settings, world) -> None:
    assert _client(settings, world).post("/calls/42/recording/retranscribe").status_code == 401


# --- RBAC + data scope -------------------------------------------------------------


class _Validator:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    async def resolve_identity(self, token, client, uid):  # noqa: ARG002
        return (self.user_id, False) if token == "good" else None

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


SESSION = {"x-chatwoot-access-token": "good", "x-chatwoot-client": "c", "x-chatwoot-uid": "u"}


def _rbac_client(settings, world, *, perms, scope=None, data_scoped=False) -> TestClient:
    rbac = settings.model_copy(
        update={"rbac_enabled": True, "data_scoped_rbac_enabled": data_scoped}
    )
    return _client(
        rbac,
        world,
        authz_repo=_Repo(perms, scope or Scope()),
        validator=_Validator(7),
    )


def test_rbac_a_user_without_call_recording_listen_is_403(settings, world) -> None:
    client = _rbac_client(settings, world, perms={"cases.view"})
    assert client.get("/calls/42/recording", headers=SESSION).status_code == 403
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 403


async def test_rbac_the_audit_row_names_the_real_listener(settings, world) -> None:
    client = _rbac_client(settings, world, perms={"call_recording.listen"})
    res = client.get("/calls/42/recording", headers=SESSION)
    assert res.status_code == 200
    client.get(res.json()["play_url"])
    rows = await _audit_rows(world, "call_recording_play")
    assert len(rows) == 1
    assert rows[0].actor_user_id == 7
    assert rows[0].actor_name == "Agent 7"


def test_scope_a_conversation_outside_the_callers_scope_is_404(settings, world) -> None:
    client = _rbac_client(
        settings,
        world,
        perms={"call_recording.listen"},
        scope=Scope.build(inbox_ids=[99]),
        data_scoped=True,
    )
    assert client.get("/calls/42/recording", headers=SESSION).status_code == 404
    assert client.post("/calls/42/recording/retranscribe", headers=SESSION).status_code == 404


def test_scope_a_conversation_inside_the_callers_scope_plays(settings, world) -> None:
    client = _rbac_client(
        settings,
        world,
        perms={"call_recording.listen"},
        scope=Scope.build(inbox_ids=[5]),
        data_scoped=True,
    )
    res = client.get("/calls/42/recording", headers=SESSION)
    assert res.status_code == 200 and res.json()["status"] == "available"
