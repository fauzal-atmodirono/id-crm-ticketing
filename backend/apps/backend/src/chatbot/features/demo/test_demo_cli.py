"""`python -m chatbot.features.demo.seed` (the VM entry point)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from chatbot.features.demo import seed as cli
from chatbot.features.demo.conftest import FakeChatwoot
from chatbot.features.demo.crm import DemoCrmClient
from chatbot.features.demo.seeder import DemoSeeder
from chatbot.platform.config import Settings


def _factory(settings: Settings, fake: FakeChatwoot) -> Any:
    @asynccontextmanager
    async def factory() -> Any:
        crm = DemoCrmClient(settings, transport=httpx.MockTransport(fake.handler))
        try:
            yield DemoSeeder(settings, crm)
        finally:
            await crm.aclose()

    return factory


def test_refuses_when_the_flag_is_off(
    settings: Settings, fake: FakeChatwoot, capsys: pytest.CaptureFixture[str]
) -> None:
    off = settings.model_copy(update={"demo_console_enabled": False})
    assert cli.main(["--yes"], settings=off, seeder_factory=_factory(off, fake)) == 2
    assert "DEMO_CONSOLE_ENABLED" in capsys.readouterr().err
    assert fake.calls == []


def test_seed_needs_confirmation(
    settings: Settings, fake: FakeChatwoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": "no")
    assert cli.main([], settings=settings, seeder_factory=_factory(settings, fake)) == 1
    assert fake.mutations() == []


def test_seed_with_yes(
    settings: Settings, fake: FakeChatwoot, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--yes"], settings=settings, seeder_factory=_factory(settings, fake)) == 0
    assert len(fake.contacts) == 4
    out = capsys.readouterr().out
    assert "account 7" in out and "inbox 42" in out
    assert "test-token" not in out


def test_reset_with_yes(settings: Settings, fake: FakeChatwoot) -> None:
    factory = _factory(settings, fake)
    assert cli.main(["--yes"], settings=settings, seeder_factory=factory) == 0
    first = set(fake.contacts)
    assert cli.main(["--reset", "--yes"], settings=settings, seeder_factory=factory) == 0
    assert len(fake.contacts) == 4
    assert not first & set(fake.contacts)


def test_status_is_read_only(settings: Settings, fake: FakeChatwoot) -> None:
    assert cli.main(["--status"], settings=settings, seeder_factory=_factory(settings, fake)) == 0
    assert fake.mutations() == []


def test_safety_refusal_exits_nonzero(settings: Settings, fake: FakeChatwoot) -> None:
    fake.inbox_channel = "Channel::Whatsapp"
    assert cli.main(["--yes"], settings=settings, seeder_factory=_factory(settings, fake)) == 3
    assert fake.mutations() == []


def test_no_stdin_is_not_confirmed(
    settings: Settings, fake: FakeChatwoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _eof(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert cli.main(["--reset"], settings=settings, seeder_factory=_factory(settings, fake)) == 1
    assert fake.mutations() == []
