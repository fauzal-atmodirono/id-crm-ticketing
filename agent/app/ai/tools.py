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

RECORD_INVESTOR_PREFERENCE = types.FunctionDeclaration(
    name="record_investor_preference",
    description=(
        "Record what the customer just told you about how they invest: their "
        "goal, when they need the money, how they would react to a 20% drop, "
        "and how experienced they are. Use this only for answers the customer "
        "actually gave in this conversation -- never infer them. Supply only "
        "the fields they answered; the rest can be asked later. This records "
        "preferences for personalization only and does NOT change the "
        "customer's official risk profile."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "goal": types.Schema(
                type=types.Type.STRING,
                enum=[
                    "retirement",
                    "education",
                    "house",
                    "wealth_growth",
                    "emergency_fund",
                    "other",
                ],
                description="What the customer is investing for.",
            ),
            "horizon": types.Schema(
                type=types.Type.STRING,
                enum=["short", "medium", "long", "very_long"],
                description=(
                    "When they need the money: short <1y, medium 1-3y, "
                    "long 3-10y, very_long >10y."
                ),
            ),
            "drawdown_reaction": types.Schema(
                type=types.Type.STRING,
                enum=["sell_all", "sell_some", "hold", "buy_more"],
                description=(
                    "What they said they would do if their portfolio fell 20%."
                ),
            ),
            "experience": types.Schema(
                type=types.Type.STRING,
                enum=["beginner", "intermediate", "experienced"],
                description="How experienced an investor they say they are.",
            ),
        },
        required=[],
    ),
)

TOOLS = [
    types.Tool(
        function_declarations=[SEND_REPLY, ESCALATE_TO_TICKET, HANDOFF_TO_HUMAN]
    )
]

# The action space when a tenant has opted into investor profiling. A separate
# list rather than a fourth entry in TOOLS, because "default off" has to mean
# the model is never offered the tool -- appending would expose it to every
# existing tenant, whose contacts have nowhere to put the answers.
TOOLS_WITH_PROFILING = [
    types.Tool(
        function_declarations=[
            SEND_REPLY,
            ESCALATE_TO_TICKET,
            HANDOFF_TO_HUMAN,
            RECORD_INVESTOR_PREFERENCE,
        ]
    )
]
