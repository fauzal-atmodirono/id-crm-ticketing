"""2026-11-09 brief 3: main.py builds the HandoffNoteService only with
HANDOFF_NOTE_ENABLED, and hands it to the orchestrator (reachability: the
service's own tests inject it by hand)."""

from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.chat import handoff_note
from chatbot.features.chat.service import OrchestratorService
from chatbot.test_phone_caller_context_wiring import _boot, _clean_settings  # noqa: F401


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    seen: list[Any] = []
    real = OrchestratorService.__init__

    def spy(self: Any, *a: Any, **kw: Any) -> None:
        seen.append(kw.get("handoff_note_service"))
        real(self, *a, **kw)

    monkeypatch.setattr(OrchestratorService, "__init__", spy)
    return seen


def test_flag_on_builds_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture(monkeypatch)
    _boot(monkeypatch, HANDOFF_NOTE_ENABLED="true")
    assert isinstance(seen[-1], handoff_note.HandoffNoteService)


def test_flag_off_builds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture(monkeypatch)
    _boot(monkeypatch, HANDOFF_NOTE_ENABLED="false")
    assert seen[-1] is None
