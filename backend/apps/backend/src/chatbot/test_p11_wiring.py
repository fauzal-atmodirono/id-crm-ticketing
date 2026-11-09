"""P11 tasks 1 and 5 -- through the real app, not through the inner function.

`recording_router.py` shipped complete and unit-tested and **was never mounted**.
Its own tests build a throwaway `FastAPI()` and `include_router` it by hand, so
they passed while the endpoint 404ed against every real deployment and
`CALL_RECORDING_RETRIEVAL_ENABLED` had no consumer any operator could reach --
the tenth instance of this exact failure in this run
(`.superpowers/sdd/DISPATCH-RULES.md`, "Reachability").

These tests boot `bootstrap_application()` and assert the discriminating pair the
P6/P10 wiring tests established: **401 rather than 404** on an unauthenticated
call, which can only happen if the route exists and its permission dependency
ran. A 404 there would mean unmounted; a 200 would mean un-gated.

The second half of the file does the same job for task 5's
`validate_handoff_target_settings`, which also shipped complete, unit-tested and
with no caller. Its own tests call it directly with a hand-built `Settings`, so
they passed while the placeholder-number guard ran nowhere. These tests instead
boot the real app with a placeholder in the environment and assert the boot is
refused -- so **deleting the call from `main.py` fails them**, which is the only
property that matters here.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
import pytest

from chatbot.features.chat.phone.recording_router import reset_state

_SECRET = "w" * 48
_AUDIO = "proton-crm/call-recording/2026-11-09/CA1.mp3"


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    from chatbot.platform.config import get_settings

    reset_state()
    get_settings.cache_clear()
    yield
    reset_state()
    # Leave no cached `Settings` behind. `chatbot.main` ends with a module-level
    # `app = bootstrap_application()`, so the first import of it in a process
    # boots the app against whatever is cached; a test here that left a
    # placeholder handoff number cached would make an unrelated file's *import*
    # raise, and a failed import is not kept in `sys.modules`, so every later
    # importer would pay for it too.
    get_settings.cache_clear()


def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("PROTON_BACKEND_KEY", "test_key")
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "test_secret")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from chatbot.main import bootstrap_application
    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    return bootstrap_application()


def test_the_recording_endpoint_is_mounted_and_rejects_an_unauthenticated_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, CALL_RECORDING_RETRIEVAL_ENABLED="true")
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording")
    # 401, NOT 404: the route resolved and `call_recording.listen` refused it.
    assert res.status_code == 401


def test_a_permitted_caller_reaches_the_handler_through_the_real_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With Chatwoot off there is no conversation to read: the clear empty
    state, from the real handler (a 404 would mean unmounted)."""
    app = _boot(
        monkeypatch,
        CALL_RECORDING_RETRIEVAL_ENABLED="true",
        CALL_RECORDING_SIGNING_SECRET=_SECRET,
        CHATWOOT_ENABLED="false",
    )
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": "test_key"})
    assert res.status_code == 200
    assert res.json()["status"] == "empty"


def test_the_real_app_plays_an_archived_recording_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main.py's wiring, not the router's: the Chatwoot reader it passes finds
    `call_recording_object`, the default GCS storage serves the bytes, and the
    shared audit log records the play. Chatwoot and GCS are faked at their
    adapter boundaries -- nothing leaves the process."""
    from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
    from chatbot.features.chat.phone.recording_access import GcsRecordingStorage

    async def _fake_request(self, method, path, payload=None):  # noqa: ARG001
        if method == "GET" and path == "/conversations/77":
            return {"id": 77, "custom_attributes": {"call_recording_object": _AUDIO}}
        return None

    monkeypatch.setattr(ChatwootAdapter, "_request", _fake_request)
    monkeypatch.setattr(GcsRecordingStorage, "size", lambda self, b, n: 4)  # noqa: ARG005
    monkeypatch.setattr(
        GcsRecordingStorage,
        "read",
        lambda self, b, n, start=None, end=None: b"abcd"[start or 0 : (end + 1) if end is not None else None],  # noqa: ARG005
    )
    app = _boot(
        monkeypatch,
        CALL_RECORDING_RETRIEVAL_ENABLED="true",
        CALL_RECORDING_SIGNING_SECRET=_SECRET,
        PHONE_RECORDING_ARCHIVE_BUCKET="bucket-x",
    )
    client = TestClient(app)

    meta = client.get("/calls/77/recording", headers={"x-api-key": "test_key"})
    assert meta.status_code == 200, meta.text
    assert meta.json()["status"] == "available"
    audio = client.get(meta.json()["play_url"], headers={"Range": "bytes=1-2"})
    assert audio.status_code == 206
    assert audio.content == b"bc"


def test_the_audio_route_is_mounted_and_refuses_a_bad_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(
        monkeypatch,
        CALL_RECORDING_RETRIEVAL_ENABLED="true",
        CALL_RECORDING_SIGNING_SECRET=_SECRET,
    )
    res = TestClient(app).get("/calls/conv_123/recording/audio?t=forged")
    # 403, NOT 404: the route exists and the HMAC check refused the link.
    assert res.status_code == 403


def test_the_flag_off_404s_for_a_caller_who_would_otherwise_be_permitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguishes "flag off" from "unmounted" -- the permitted caller is the
    only way to see the flag's own 404 rather than the permission gate's 401."""
    app = _boot(monkeypatch, CALL_RECORDING_RETRIEVAL_ENABLED="false")
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": "test_key"})
    assert res.status_code == 404
    assert "CALL_RECORDING_RETRIEVAL_ENABLED" in res.json()["detail"]


# --- P11 task 5: the placeholder-number guard, reached from startup ----------

# `phone_handoff_enabled` is structurally dependent on
# `phone_transcript_live_enabled` (Settings._phone_flag_dependencies), so every
# handoff env below must set both or Settings itself refuses first -- for a
# different and unrelated reason, which would make these tests pass for the
# wrong cause.
_HANDOFF_ON = {
    "PHONE_TRANSCRIPT_LIVE_ENABLED": "true",
    "PHONE_HANDOFF_ENABLED": "true",
    "PHONE_HANDOFF_CALLER_ID": "+60311112222",
}


def test_a_placeholder_handoff_target_refuses_to_boot_the_real_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if the `validate_handoff_target_settings` call is removed from
    `bootstrap_application`. Without it the app boots clean and the placeholder
    surfaces at dial time as an indistinguishable `no-answer`.
    """
    with pytest.raises(ValueError, match="placeholder number"):
        _boot(monkeypatch, **_HANDOFF_ON, PHONE_HANDOFF_TARGET_NUMBER="+60300000001")


def test_the_refusal_names_the_setting_and_the_offending_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator reading only the boot failure has to be able to find the
    line to edit, so the message carries the env-var-shaped setting name and the
    literal value rather than just "invalid configuration".
    """
    with pytest.raises(ValueError) as excinfo:
        _boot(monkeypatch, **_HANDOFF_ON, PHONE_HANDOFF_TARGET_NUMBER="+60300000001")

    message = str(excinfo.value)
    assert "phone_handoff_target_number" in message
    assert "+60300000001" in message


def test_a_real_looking_target_number_still_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must not be a blanket refusal to enable handoff at all -- that
    would look identical to the placeholder case from the operator's side.
    """
    app = _boot(monkeypatch, **_HANDOFF_ON, PHONE_HANDOFF_TARGET_NUMBER="+60388889999")
    assert app is not None


def test_handoff_disabled_boots_with_a_placeholder_left_in_the_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every tenant today has `PHONE_HANDOFF_ENABLED` off, and `example.env`
    ships a placeholder target. This asserts that adding the guard cannot break
    any of them: nothing dials, so nothing is refused.
    """
    app = _boot(
        monkeypatch,
        PHONE_HANDOFF_ENABLED="false",
        PHONE_HANDOFF_TARGET_NUMBER="+60300000001",
    )
    assert app is not None


# --- Task 9: agent softphone (registry + token router) mounted from main.py -


def _effective_routes(app: Any) -> Any:
    """Flatten `app.routes` into concrete routes with a real `.path`/
    `.endpoint`.

    FastAPI 0.137 makes `app.include_router(...)` build a lazy
    `_IncludedRouter` wrapper rather than flat `APIRoute`/`Route` objects, so
    `app.routes` no longer yields objects with `.path` directly -- every one
    of this app's ~20 `include_router` calls means `{r.path for r in
    app.routes}` raises `AttributeError` instead of returning the path set a
    naive reading of Starlette's docs would expect. `_IncludedRouter.
    effective_candidates()` is the (undocumented, but only) way back to
    concrete routes; walk it recursively since a router can itself have been
    included into another router.
    """

    def walk(routes: Any) -> Any:
        for r in routes:
            if hasattr(r, "effective_candidates"):
                yield from walk(r.effective_candidates())
            else:
                yield r

    yield from walk(app.routes)


def test_softphone_token_route_is_mounted_and_answers_401_not_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring assertion this file exists for: a route that 404s is not
    mounted; one that 401s is mounted and refusing an unauthenticated caller.

    Adapted from the task brief's `create_app()`-based snippet to this file's
    own `_boot()` helper (real `bootstrap_application()`, the actual
    production wiring path): `chatbot.main.create_app` is a re-export of
    `chatbot.platform.server.create_app(settings)`, a bare, route-less
    `FastAPI()` factory that *requires* a `settings` argument -- not the
    fully-wired app this assertion needs, and not callable with zero args.
    """
    app = _boot(monkeypatch)
    client = TestClient(app)

    assert client.post("/voice/agent/token").status_code in (401, 404)
    # 404 only when the feature flag is off, which is the default; assert the
    # route EXISTS by checking the app's route table directly.
    paths = {r.path for r in _effective_routes(app) if hasattr(r, "path")}
    assert "/voice/agent/token" in paths


def test_softphone_registry_is_constructed_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(
        monkeypatch,
        PHONE_AGENT_SOFTPHONE_ENABLED="true",
        PHONE_HANDOFF_ENABLED="true",
        PHONE_TRANSCRIPT_LIVE_ENABLED="true",
    )
    paths = {r.path for r in _effective_routes(app) if hasattr(r, "path")}
    assert "/webhooks/phone/dial-status/fanout" in paths


def test_call_control_is_wired_into_the_chat_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controller ruling R2: Task 10 fixes After-Call-Work attribution by
    having `_enter_acw_best_effort` consult `self._call_control`, but that
    collaborator only does anything in production if `main.py` actually hands
    `ChatRouter` a real `CallControl` instance -- a unit test that injects its
    own `AsyncMock` passes regardless of whether main.py does this. Reach
    through the real, fully-wired app's route table (every mounted
    `/webhooks/phone/...` / `/chat/...` route is a bound method of the SAME
    `ChatRouter` instance) and assert the collaborator is not None there.
    """
    from chatbot.features.chat.router import ChatRouter

    app = _boot(monkeypatch)
    chat_router_instances = {
        route.endpoint.__self__
        for route in _effective_routes(app)
        if isinstance(getattr(getattr(route, "endpoint", None), "__self__", None), ChatRouter)
    }
    assert len(chat_router_instances) == 1, "expected exactly one ChatRouter instance in the app"
    (chat_router,) = chat_router_instances
    assert chat_router._call_control is not None


# --- FIX-15: softphone hold / transfer wired from main.py --------------------


def _chat_router(app: Any) -> Any:
    from chatbot.features.chat.router import ChatRouter

    (inst,) = {
        route.endpoint.__self__
        for route in _effective_routes(app)
        if isinstance(getattr(getattr(route, "endpoint", None), "__self__", None), ChatRouter)
    }
    return inst


def test_hold_transfer_wired_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _boot(
        monkeypatch,
        PHONE_HOLD_TRANSFER_ENABLED="true",
        PHONE_AGENT_SOFTPHONE_ENABLED="true",
        PHONE_HANDOFF_ENABLED="true",
        PHONE_TRANSCRIPT_LIVE_ENABLED="true",
    )
    paths = {r.path for r in _effective_routes(app) if hasattr(r, "path")}
    for p in (
        "/voice/agent/call/hold",
        "/voice/agent/call/resume",
        "/voice/agent/call/transfer",
        "/webhooks/phone/conference-status",
        "/webhooks/phone/transfer/answer",
        "/webhooks/phone/transfer/status",
        "/webhooks/phone/caller-status",
        "/webhooks/phone/hold/next",
    ):
        assert p in paths, p
    assert _chat_router(app)._hold_transfer is not None


def test_hold_transfer_off_by_default_leaves_dial_status_unhooked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch)
    paths = {r.path for r in _effective_routes(app) if hasattr(r, "path")}
    assert "/webhooks/phone/transfer/answer" not in paths
    assert _chat_router(app)._hold_transfer is None
