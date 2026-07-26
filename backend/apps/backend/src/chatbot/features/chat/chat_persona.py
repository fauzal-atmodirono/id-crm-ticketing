"""Compose the /chat/turn support-agent instruction from the base AGENT_INSTRUCTION
plus an operator-configured assistant persona.

AUGMENT, never replace: the base carries the essential tool-orchestration rules the
agent needs to function, so the operator persona is layered on top. An empty persona
returns the base verbatim (byte-identical default).
"""

from __future__ import annotations


def compose_chat_agent_instruction(base: str, assistant) -> str:
    if assistant is None:
        return base
    config = getattr(assistant, "config", None)
    if config is None:
        return base
    instructions = (getattr(config, "instructions", "") or "").strip()
    guardrails = [g for g in (getattr(config, "guardrails", []) or []) if str(g).strip()]
    language = (getattr(config, "language", "") or "").strip()
    if not instructions and not guardrails and not language:
        return base
    parts = [base]
    if instructions:
        parts.append(f"## Operator persona\n{instructions}")
    if guardrails:
        parts.append("## Guardrails\n" + "\n".join(f"- {g}" for g in guardrails))
    if language:
        parts.append(f"## Language\nAlways respond in {language}.")
    return "\n\n".join(parts)
