"""Function declarations the Gemini orchestrator can choose between.

Exactly one of these is invoked per `gemini.decide` call (`tool_config` forces
function calling in `gemini.py`), so together they define the AI layer's
entire action space for a Chatwoot conversation: reply, escalate, or hand off
to a human without doing either.
"""

from google.genai import types

SEND_REPLY = types.FunctionDeclaration(
    name="send_reply",
    description=(
        "Reply directly to the customer in this conversation. Use this when "
        "you can answer the customer's question or resolve their request "
        "using only what's in the conversation so far."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "text": types.Schema(
                type=types.Type.STRING,
                description="The reply text to send to the customer.",
            ),
        },
        required=["text"],
    ),
)

ESCALATE_TO_TICKET = types.FunctionDeclaration(
    name="escalate_to_ticket",
    description=(
        "Escalate this conversation to a support ticket for a human "
        "specialist to handle. Use this when the request needs work only a "
        "human can do (e.g. a refund, an account change, a bug investigation)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reason": types.Schema(
                type=types.Type.STRING,
                description="Why this conversation needs to become a ticket.",
            ),
            "priority": types.Schema(
                type=types.Type.STRING,
                enum=["low", "normal", "high"],
                description="How urgently a human should look at this ticket.",
            ),
            "summary": types.Schema(
                type=types.Type.STRING,
                description="A short summary of the issue, for the ticket title/body.",
            ),
        },
        required=["reason", "priority", "summary"],
    ),
)

HANDOFF_TO_HUMAN = types.FunctionDeclaration(
    name="handoff_to_human",
    description=(
        "Hand this conversation off to a human agent without sending a reply "
        "or opening a ticket. Use this whenever you're unsure, the customer's "
        "intent is unclear, or you don't have enough grounded information to "
        "safely reply or escalate."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reason": types.Schema(
                type=types.Type.STRING,
                description="Why a human needs to take over.",
            ),
        },
        required=["reason"],
    ),
)

TOOLS = [
    types.Tool(
        function_declarations=[SEND_REPLY, ESCALATE_TO_TICKET, HANDOFF_TO_HUMAN]
    )
]
