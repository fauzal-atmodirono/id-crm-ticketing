"""TDD: Task 8 — media diagnosis prompting.

The bot already receives whatever photo or video a customer attaches — see
`test_chat_turn_video.py` / `test_chat_turn_media_not_replayed.py` for how an
image/video becomes a Gemini ``Part`` on the turn that carried it. Nothing
ever asked the model to actually diagnose what it sees. `build_agent_instruction`
adds a bounded diagnostic instruction — confidence statement required, at most
one follow-up question — only when `media_diagnosis_prompt_enabled` is True
AND this specific turn carries an image or video.

Bounded on purpose: a model told to "ask follow-up questions" asks several,
and a customer who sent one photo of a dented door must not be interrogated.
Test six and seven are the ones that matter most in production; the rest are
the wiring/safety scaffolding around them.
"""

from __future__ import annotations

from chatbot.features.chat.adapters.assistants_store import Assistant, AssistantConfig
from chatbot.features.chat.prompts import AGENT_INSTRUCTION, build_agent_instruction


def _assistant(**cfg: object) -> Assistant:
    return Assistant(
        id="test-id",
        name="A",
        description="",
        product_name="",
        config=AssistantConfig(**cfg),  # type: ignore[arg-type]
        enabled=True,
        is_default=False,
        created_at="2026-01-01T00:00:00+00:00",
    )


async def test_the_diagnosis_instruction_is_present_when_an_image_is_attached() -> None:
    out = build_agent_instruction(media_diagnosis_prompt_enabled=True, has_image=True)
    assert out != AGENT_INSTRUCTION
    assert out.startswith(AGENT_INSTRUCTION)


async def test_the_diagnosis_instruction_is_absent_when_no_media_is_attached() -> None:
    out = build_agent_instruction(
        media_diagnosis_prompt_enabled=True, has_image=False, has_video=False
    )
    assert out == AGENT_INSTRUCTION


async def test_the_instruction_is_operator_editable() -> None:
    custom = "Always mention our nearest authorised service centre when diagnosing damage."
    out = build_agent_instruction(
        media_diagnosis_prompt_enabled=True,
        has_image=True,
        assistant=_assistant(media_diagnosis_instruction=custom),
    )
    assert custom in out
    assert out.startswith(AGENT_INSTRUCTION)


async def test_a_video_attachment_gets_the_same_instruction() -> None:
    image_out = build_agent_instruction(media_diagnosis_prompt_enabled=True, has_image=True)
    video_out = build_agent_instruction(media_diagnosis_prompt_enabled=True, has_video=True)
    assert video_out == image_out
    assert video_out != AGENT_INSTRUCTION


async def test_the_flag_off_reproduces_todays_generic_instruction_exactly() -> None:
    out = build_agent_instruction(
        media_diagnosis_prompt_enabled=False,
        has_image=True,
        has_video=True,
        assistant=_assistant(media_diagnosis_instruction="a custom override"),
    )
    assert out == AGENT_INSTRUCTION


async def test_the_instruction_asks_for_a_confidence_statement() -> None:
    out = build_agent_instruction(media_diagnosis_prompt_enabled=True, has_image=True)
    assert "confidence" in out.lower()


async def test_the_instruction_asks_for_at_most_one_follow_up_question() -> None:
    out = build_agent_instruction(media_diagnosis_prompt_enabled=True, has_image=True)
    lowered = out.lower()
    assert "at most one follow-up question" in lowered
    # A plural, unbounded phrasing anywhere would invite the interrogation this
    # instruction exists to prevent.
    assert "follow-up questions" not in lowered
