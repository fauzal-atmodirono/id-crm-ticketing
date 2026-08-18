"""Twilio call control. Every method is fail-open: a Twilio outage must degrade
the feature, never drop the live call it is attached to."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chatbot.features.chat.phone.call_control import CallControl
from chatbot.platform.config import Settings, get_settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, twilio_account_sid="AC1", twilio_auth_token="tok")


class _FakeCalls:
    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.updated: list[tuple[str, str]] = []
        self.recorded: list[str] = []

    def __call__(self, call_sid: str):
        self.sid = call_sid
        return self

    def update(self, twiml: str):
        if self.raises:
            raise self.raises
        self.updated.append((self.sid, twiml))
        return object()

    @property
    def recordings(self):
        return self

    def create(self, **kwargs):
        if self.raises:
            raise self.raises
        self.recorded.append(self.sid)
        return type("R", (), {"sid": "RE123"})()


class _FakeTwilio:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls = _FakeCalls(raises)


async def test_redirect_updates_the_call_with_twiml(settings):
    fake = _FakeTwilio()
    cc = CallControl(settings, client=fake)
    ok = await cc.redirect("CA123", "<Response/>")
    assert ok is True
    assert fake.calls.updated == [("CA123", "<Response/>")]


async def test_redirect_returns_false_on_twilio_error(settings):
    cc = CallControl(settings, client=_FakeTwilio(raises=RuntimeError("boom")))
    assert await cc.redirect("CA123", "<Response/>") is False


async def test_start_recording_returns_sid(settings):
    fake = _FakeTwilio()
    cc = CallControl(settings, client=fake)
    assert await cc.start_recording("CA123", "https://x/cb") == "RE123"
    assert fake.calls.recorded == ["CA123"]


async def test_start_recording_returns_none_on_error(settings):
    cc = CallControl(settings, client=_FakeTwilio(raises=RuntimeError("boom")))
    assert await cc.start_recording("CA123", "https://x/cb") is None


async def test_client_construction_failure_is_fail_open(monkeypatch, settings):
    """If Client(sid, token) itself raises — bad creds, a broken SDK install,
    a future constructor that validates — that must not propagate out of
    redirect/start_recording and drop the live call."""

    class _RaisingClient:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr("twilio.rest.Client", _RaisingClient)
    cc = CallControl(settings)  # no injected client -> forces lazy construction
    assert await cc.redirect("CA123", "<Response/>") is False
    assert await cc.start_recording("CA123", "https://x/cb") is None


async def test_lazily_constructed_client_gets_a_bounded_http_timeout(monkeypatch, settings):
    """Review fix (Important 3, round 2): without an SDK-level timeout, a
    blackholed Twilio API call hangs the underlying to_thread worker
    indefinitely -- bridge.py's asyncio.wait_for only cancels our AWAIT of
    that thread, not the thread itself. Pin that the lazily-constructed
    real client is built with a TwilioHttpClient carrying a timeout."""
    captured: dict[str, object] = {}

    class _FakeHttpClient:
        def __init__(self, *, timeout=None) -> None:
            captured["timeout"] = timeout

    class _FakeClient:
        def __init__(self, sid, token, http_client=None) -> None:
            captured["sid"] = sid
            captured["token"] = token
            captured["http_client"] = http_client

    monkeypatch.setattr("twilio.http.http_client.TwilioHttpClient", _FakeHttpClient)
    monkeypatch.setattr("twilio.rest.Client", _FakeClient)
    cc = CallControl(settings)  # no injected client -> forces lazy construction

    client = cc._twilio()

    assert isinstance(client, _FakeClient)
    assert isinstance(captured["http_client"], _FakeHttpClient)
    timeout = captured["timeout"]
    assert isinstance(timeout, (int, float))
    assert timeout <= 5.0  # shorter than bridge.py's own bound


# --- Task 10: mapping a winning <Dial> leg back to whoever answered --------


async def test_fetch_call_to_returns_the_dialed_endpoint() -> None:
    client = MagicMock()
    client.calls.return_value.fetch.return_value = MagicMock(to="client:agent_17")

    cc = CallControl(get_settings(), client=client)
    assert await cc.fetch_call_to("CA-child") == "client:agent_17"


async def test_fetch_call_to_is_fail_open() -> None:
    """Same invariant as every other method here: a Twilio blip degrades the
    feature (ACW falls back to the assignee), it never raises."""
    client = MagicMock()
    client.calls.return_value.fetch.side_effect = RuntimeError("twilio down")

    assert await CallControl(get_settings(), client=client).fetch_call_to("CA-child") is None


async def test_fetch_call_to_unconfigured_returns_none() -> None:
    unconfigured = get_settings().model_copy(
        update={"twilio_account_sid": "", "twilio_auth_token": ""}
    )
    assert await CallControl(unconfigured).fetch_call_to("CA-child") is None
