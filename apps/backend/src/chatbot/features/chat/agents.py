from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents import Agent
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from chatbot.features.chat.prompts import AGENT_INSTRUCTION, SUMMARIZER_INSTRUCTION

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort, TicketingPort
    from chatbot.platform.config import Settings


def build_ai_agent(
    settings: Settings, _ticketing_port: TicketingPort, knowledge_port: KnowledgePort
) -> Agent:
    """Builds the main conversational Support Agent with registered tools."""

    # Define tools inside a closure to inject the ports
    async def search_kb_tool(query: str) -> str:
        """Search the support Knowledge Base for articles matching the query.

        Args:
            query: The search term or topic to look up.
        """
        articles = await knowledge_port.search_kb(query, limit=2)
        if not articles:
            # Literal token the agent prompt branches on — see rule 2 in
            # AGENT_INSTRUCTION. Distinct from a tool execution error.
            return "NO_MATCHES: The Knowledge Base has no documents matching this query."

        results = []
        for art in articles:
            snippet = f"Title: {art.title}\nContent: {art.content}"
            if art.url:
                snippet += f"\nSource URL: {art.url}"
            results.append(snippet)
        return "\n\n---\n\n".join(results)

    async def classify_ticket_tool(
        tool_context: ToolContext,
        category: str,
        subcategory: str,
        priority: str,
        sla_minutes: int,
    ) -> str:
        """Classify the current ticket details.

        Args:
            tool_context: Context injected by the ADK runner.
            category: General category of the problem (e.g. Account, Technical).
            subcategory: Precise subcategory (e.g. Password Reset, System Crash).
            priority: Priority tier (LOW, MEDIUM, HIGH, URGENT).
            sla_minutes: Targeted SLA duration in minutes.
        """
        # Save classification state locally in the tool context
        tool_context.state["category"] = category
        tool_context.state["subcategory"] = subcategory
        tool_context.state["priority"] = priority
        tool_context.state["sla_minutes"] = sla_minutes
        
        return (
            f"[internal] ticket classified as {category} -> {subcategory} "
            f"({priority}, SLA {sla_minutes}m)."
        )

    async def emit_handoff_tool(
        tool_context: ToolContext,
        reason: str,
    ) -> str:
        """Hand off the conversation immediately to a human support agent.

        Args:
            tool_context: Context injected by the ADK runner.
            reason: Explanation why handoff is triggered (e.g. help_request, negative_sentiment).
        """
        tool_context.state["handoff_triggered"] = True
        tool_context.state["handoff_reason"] = reason
        return f"[internal] handoff triggered (Reason: {reason})."

    # Instantiate the ADK Agent. `classify_ticket_tool` is intentionally
    # excluded from `tools` for now — its result is stored only in
    # tool_context.state which nothing downstream consumes, and Gemini was
    # treating the classify call as the turn's terminal action and skipping
    # the customer-facing text reply. It stays defined above so we can
    # re-enable once we wire its state into the handoff payload.
    _ = classify_ticket_tool  # keep referenced for ruff
    return Agent(
        name="support_agent",
        model=settings.gemini_model,
        instruction=AGENT_INSTRUCTION,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.3,
        ),
        tools=[search_kb_tool, emit_handoff_tool],
    )


def build_summarizer_agent(settings: Settings) -> Agent:
    """Builds the Handoff Summarizer Agent to condense transcripts for human takeovers."""
    return Agent(
        name="handoff_summarizer",
        model=settings.gemini_model,
        instruction=SUMMARIZER_INSTRUCTION,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )
