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

    async def book_test_drive_tool(
        tool_context: ToolContext,
        full_name: str,
        phone_number: str,
        email: str,
        preferred_model: str,
        preferred_dealer: str,
    ) -> str:
        """Register customer interest and book a test drive for a Proton vehicle.

        Args:
            tool_context: Context injected by the ADK runner.
            full_name: The customer's full name.
            phone_number: The customer's contact phone number.
            email: The customer's contact email address.
            preferred_model: The Proton model of interest (e.g. Saga, Persona, Iriz, X50, X70, X90, S70).
            preferred_dealer: Customer's preferred dealer location or city/state.
        """
        lead_data = {
            "customer_name": full_name,
            "customer_phone": phone_number,
            "customer_email": email,
            "preferred_model": preferred_model,
            "preferred_dealer": preferred_dealer,
        }
        tool_context.state["lead_captured"] = True
        tool_context.state["lead_details"] = lead_data
        return f"[internal] test drive registration processed for {full_name} ({preferred_model})."

    async def show_models_tool(tool_context: ToolContext, query: str) -> str:
        """Fetch Proton model cards to display as a visual carousel.

        Call this when the user asks to see, browse, or compare models, or
        asks which Proton car to buy. Returns an internal confirmation; the
        cards are rendered to the user automatically.

        Args:
            tool_context: Context injected by the ADK runner.
            query: The model/segment the user is interested in.
        """
        articles = await knowledge_port.search_kb(query, limit=6)
        cards = [
            {
                "title": a.title,
                "description": a.content[:200],
                "image_url": a.image_urls[0] if a.image_urls else None,
                "price": a.price,
                "url": a.url,
            }
            for a in articles
            if a.source_type == "model"
        ]
        tool_context.state["product_carousel"] = cards
        return f"[internal] prepared {len(cards)} model cards for the carousel."

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

    async def flag_for_ticket_tool(
        tool_context: ToolContext,
        reason: str,
    ) -> str:
        """Mark this conversation as needing a tracked support ticket.

        Call this when the conversation is actionable — a complaint, a
        service/warranty request, a sales lead, or an unresolved issue — even if
        the customer is NOT asking for a human yet.

        Args:
            tool_context: Context injected by the ADK runner.
            reason: Short reason (e.g. complaint, service_request, sales_lead).
        """
        tool_context.state["ticket_flagged"] = True
        tool_context.state["ticket_reason"] = reason
        return f"[internal] conversation flagged for ticket (Reason: {reason})."

    return Agent(
        name="support_agent",
        model=settings.gemini_model,
        instruction=AGENT_INSTRUCTION,
        generate_content_config=types.GenerateContentConfig(
            temperature=0.3,
        ),
        tools=[
            search_kb_tool,
            emit_handoff_tool,
            flag_for_ticket_tool,
            show_models_tool,
            classify_ticket_tool,
            book_test_drive_tool,
        ],
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
