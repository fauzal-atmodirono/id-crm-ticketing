"""Twilio call control. Every method is fail-open: a Twilio outage must degrade
the feature, never drop the live call it is attached to."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone.call_control import CallControl
from chatbot.platform.config import Settings


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
