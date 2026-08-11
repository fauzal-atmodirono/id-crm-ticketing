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

from chatbot.features.chat.phone.recording_router import register_recording, reset_recordings


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    reset_recordings()
    yield
    reset_recordings()


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
    app = _boot(monkeypatch, CALL_RECORDING_RETRIEVAL_ENABLED="true")
    register_recording("conv_123", "https://storage.example.invalid/audio/123.mp3")
    client = TestClient(app)

    res = client.get("/calls/conv_123/recording", headers={"x-api-key": "test_key"})
    assert res.status_code == 200
    assert res.json()["status"] == "available"


def test_the_flag_off_404s_for_a_caller_who_would_otherwise_be_permitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguishes "flag off" from "unmounted" -- the permitted caller is the
    only way to see the flag's own 404 rather than the permission gate's 401."""
    app = _boot(monkeypatch, CALL_RECORDING_RETRIEVAL_ENABLED="false")
    register_recording("conv_123", "https://storage.example.invalid/audio/123.mp3")
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
