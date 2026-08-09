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

Since P7 task 11a the provider also layers per-request media/tone sections on
top of the registered string, so the service here is seeded with both P7 flags
OFF -- which must return the registered string untouched. That makes this file
the flags-off equivalence check for the provider; the flags-on behaviour (and
what reaches the model on a real turn) lives in
test_turn_instruction_wiring.py.
"""

from chatbot.features.chat.prompts import AGENT_INSTRUCTION
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class _FakeCtx:
    """Mimics ReadonlyContext: exposes .session.id (the accessor used in the provider)."""

    def __init__(self, session_id: str) -> None:
        self.session = _FakeSession(session_id)
        self.state = {}
        self.user_content = None


def test_provider_returns_registered_instruction_then_falls_back(monkeypatch):
    svc = OrchestratorService.__new__(OrchestratorService)  # bypass heavy __init__
    svc._instruction_by_session = {"crm-42": "PERSONA-INSTRUCTION"}
    svc._assistant_by_session = {}
    svc._settings = get_settings().model_copy(
        update={
            "sentiment_classifier_enabled": False,
            "sentiment_tone_adjustment_enabled": False,
            "media_diagnosis_prompt_enabled": False,
        }
    )
    # session with a registered instruction:
    assert svc._chat_instruction_provider(_FakeCtx("crm-42")) == "PERSONA-INSTRUCTION"
    # unregistered session -> base:
    assert svc._chat_instruction_provider(_FakeCtx("crm-99")) == AGENT_INSTRUCTION
    # ctx without a resolvable session id -> base (fail-open):
    assert svc._chat_instruction_provider(object()) == AGENT_INSTRUCTION
