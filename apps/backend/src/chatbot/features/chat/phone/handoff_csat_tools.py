"""Gemini Live function tools for phone handoff + CSAT (the phone channel runs
through Live, not handle_turn, so these are how the model triggers them)."""

from __future__ import annotations

from google.genai import types

REQUEST_HANDOFF_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="request_human_handoff",
            description=(
                "Call this when you cannot resolve the caller's issue, they explicitly ask "
                "for a human, or it is a complaint or sensitive matter. A support ticket with "
                "the conversation is created and a human specialist will follow up."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "reason": types.Schema(
                        type=types.Type.STRING, description="Short reason for the handoff."
                    ),
                    "summary": types.Schema(
                        type=types.Type.STRING,
                        description="A brief summary of what the caller needs help with.",
                    ),
                },
                required=["reason", "summary"],
            ),
        )
    ]
)

SUBMIT_CSAT_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="submit_csat",
            description=(
                "After the caller gives a 1 to 5 satisfaction rating at the end of a resolved "
                "call, call this with the number they said."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "score": types.Schema(
                        type=types.Type.INTEGER,
                        description="The caller's rating, an integer from 1 to 5.",
                    )
                },
                required=["score"],
            ),
        )
    ]
)


_CSAT_MIN = 1
_CSAT_MAX = 5


def parse_csat_score(args: dict[str, object]) -> int | None:
    """Validate the submit_csat `score` arg -> int 1-5, else None."""
    raw = args.get("score")
    try:
        score = int(raw)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return score if _CSAT_MIN <= score <= _CSAT_MAX else None
