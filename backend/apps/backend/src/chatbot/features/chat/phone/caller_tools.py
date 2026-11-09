"""Gemini Live function tools for caller identification (2026-11-09).

Offered only with ``phone_caller_context_enabled``; see ``caller_context.py``
for what the bridge does with them. Same shape as ``handoff_csat_tools.py``.
"""

from __future__ import annotations

from google.genai import types

SAVE_CALLER_DETAILS_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="save_caller_details",
            description=(
                "Silently record what the caller told you about themselves. Call it as soon "
                "as you learn any of these; pass only what you learned, and call it again "
                "when you learn more. Do not mention it to the caller."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "name": types.Schema(
                        type=types.Type.STRING, description="The caller's name as they said it."
                    ),
                    "vehicle_registration": types.Schema(
                        type=types.Type.STRING,
                        description="Car registration (plate) number, e.g. 'WXY 1234'.",
                    ),
                    "vehicle_model": types.Schema(
                        type=types.Type.STRING, description="Car model, e.g. 'Proton X50'."
                    ),
                    "dealer": types.Schema(
                        type=types.Type.STRING,
                        description="The Proton dealer or service centre they use, as they said it.",
                    ),
                },
            ),
        )
    ]
)

LOOKUP_CALLER_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="lookup_caller",
            description=(
                "Check the mobile number and name the caller just told you against our "
                "records (only when their number could not be detected). Ask for BOTH "
                "first. The answer is only match or no_match; follow its `guidance`."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "mobile_number": types.Schema(
                        type=types.Type.STRING,
                        description="The mobile number exactly as the caller said it, digits only.",
                    ),
                    "caller_name": types.Schema(
                        type=types.Type.STRING,
                        description="The caller's name exactly as THEY said it; never a guess.",
                    ),
                },
                required=["mobile_number", "caller_name"],
            ),
        )
    ]
)
