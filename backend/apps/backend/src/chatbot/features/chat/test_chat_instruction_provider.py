"""Tests for OrchestratorService._chat_instruction_provider.

Contract:
- A session with a registered instruction returns that instruction.
- An unregistered session falls back to AGENT_INSTRUCTION.
- A ctx whose session id cannot be read (AttributeError / any exception) falls
  back to AGENT_INSTRUCTION (fail-open).

The _FakeCtx mirrors the real ReadonlyContext.session.id accessor:
  ctx.session.id
(ReadonlyContext.session is a property returning self._invocation_context.session,
and Session.id is the pydantic field.  We expose it via a lightweight fake object
so the test validates the same attribute path used in the provider.)
"""
from chatbot.features.chat.prompts import AGENT_INSTRUCTION
from chatbot.features.chat.service import OrchestratorService


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class _FakeCtx:
    """Mimics ReadonlyContext: exposes .session.id (the accessor used in the provider)."""

    def __init__(self, session_id: str) -> None:
        self.session = _FakeSession(session_id)


def test_provider_returns_registered_instruction_then_falls_back(monkeypatch):
    svc = OrchestratorService.__new__(OrchestratorService)  # bypass heavy __init__
    svc._instruction_by_session = {"crm-42": "PERSONA-INSTRUCTION"}
    # session with a registered instruction:
    assert svc._chat_instruction_provider(_FakeCtx("crm-42")) == "PERSONA-INSTRUCTION"
    # unregistered session -> base:
    assert svc._chat_instruction_provider(_FakeCtx("crm-99")) == AGENT_INSTRUCTION
    # ctx without a resolvable session id -> base (fail-open):
    assert svc._chat_instruction_provider(object()) == AGENT_INSTRUCTION
