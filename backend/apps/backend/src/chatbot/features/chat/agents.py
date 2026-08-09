from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from google.adk.agents import Agent
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from chatbot.features.chat.case_taxonomy import build_case_taxonomy
from chatbot.features.chat.option_lists import build_option_list
from chatbot.features.chat.product_cleanup import clean_description, clean_title, dedupe_cards
from chatbot.features.chat.prompts import AGENT_INSTRUCTION, SUMMARIZER_INSTRUCTION

if TYPE_CHECKING:
    from chatbot.features.chat.ports import KnowledgePort, TicketingPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def build_ai_agent(  # noqa: PLR0915 — one builder, many tool closures
    settings: Settings,
    _ticketing_port: TicketingPort,
    knowledge_port: KnowledgePort,
    instruction_provider=None,
) -> Agent:
    """Builds the main conversational Support Agent with registered tools.

    instruction_provider: optional ADK InstructionProvider callable
    ``(ReadonlyContext) -> str``.  When supplied it governs the per-invocation
    instruction (e.g. a per-session persona string); when omitted the module-
    level AGENT_INSTRUCTION constant is used unchanged, preserving existing
    behaviour byte-for-byte.
    """
    case_taxonomy = build_case_taxonomy(settings)
    case_type_options = build_option_list(settings.case_type_options_json)
    vehicle_model_options = build_option_list(settings.vehicle_models_json)

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
        case_type: str,
        vehicle_model: str,
        sentiment: str = "neutral",
    ) -> str:
        """Classify the current ticket details.

        Args:
            tool_context: Context injected by the ADK runner.
            category: General category of the problem.
            subcategory: Precise subcategory matching the chosen category.
            priority: Priority tier (LOW, MEDIUM, HIGH, URGENT).
            sla_minutes: Targeted SLA duration in minutes.
            case_type: Whether this is an Inquiry, Complaint, or Feedback.
            vehicle_model: The customer's vehicle model, if mentioned.
            sentiment: The customer's emotional tone this turn -- one of
                "positive", "neutral", "negative", or "urgent" (safety-critical
                or otherwise needing immediate human attention). Defaults to
                "neutral" when omitted.
        """
        # P7 task 1: rides the EXISTING per-turn tool call rather than a second
        # Gemini round-trip -- see module docstring reasoning at the call site
        # (service.py's _resolve_sentiment). Gated on the flag so a tenant that
        # hasn't opted in gets a session_state that never contains "sentiment"
        # at all, matching pre-P7 behaviour byte-for-byte (rather than just
        # gating what's later read back out of it).
        #
        # P7 task 11a: "sentiment_at" stamps WHEN this was classified. Sentiment
        # lives in session state for the whole conversation, so tone adjustment
        # (service.py's _tone_sentiment) needs to tell "the customer is angry
        # right now" from "the customer was angry an hour ago" -- without a
        # stamp, a cheerful message resuming an old conversation would be
        # answered apologetically. Written in the same gated block, so a
        # disabled tenant's state gains neither key.
        if settings.sentiment_classifier_enabled:
            tool_context.state["sentiment"] = sentiment
            tool_context.state["sentiment_at"] = datetime.now(UTC).isoformat()

        tool_context.state["priority"] = priority
        tool_context.state["sla_minutes"] = sla_minutes

        if case_taxonomy.is_empty():
            # No taxonomy configured — pre-feature fallback: accept free text.
            tool_context.state["category"] = category
            tool_context.state["subcategory"] = subcategory
            written = True
        elif case_taxonomy.is_valid_category(category) and case_taxonomy.is_valid_subcategory(
            category, subcategory
        ):
            # Write the LABEL + flattened "<Label>: <Subcategory>" format, matching
            # what provision_case_taxonomy.py provisions as the Chatwoot List custom
            # attribute options — the LLM-facing contract (slug + bare subcategory)
            # is validated above and stays unchanged.
            label = case_taxonomy.label_for(category)
            tool_context.state["category"] = label
            tool_context.state["subcategory"] = f"{label}: {subcategory}"
            written = True
        else:
            written = False
            _log.warning(
                "classify_ticket_tool_invalid_category",
                category=category,
                subcategory=subcategory,
            )

        if case_type_options.is_empty() or case_type_options.is_valid(case_type):
            tool_context.state["case_type"] = case_type
            case_type_written = True
        else:
            case_type_written = False
            _log.warning("classify_ticket_tool_invalid_case_type", case_type=case_type)

        if vehicle_model_options.is_empty() or vehicle_model_options.is_valid(vehicle_model):
            tool_context.state["vehicle_model"] = vehicle_model
            vehicle_model_written = True
        else:
            vehicle_model_written = False
            _log.warning("classify_ticket_tool_invalid_vehicle_model", vehicle_model=vehicle_model)

        # Only echo case_type/vehicle_model in the response as recorded when they
        # actually passed validation and were written to state — otherwise the
        # message must say so explicitly, mirroring how category/subcategory
        # rejection is reported below (never claim a value was recorded when it
        # was silently dropped).
        type_fragment = (
            f"type={case_type}" if case_type_written else "case_type not recorded (invalid value)"
        )
        model_fragment = (
            f"model={vehicle_model}"
            if vehicle_model_written
            else "vehicle_model not recorded (invalid value)"
        )

        if written:
            return (
                f"[internal] ticket classified as {category} -> {subcategory} "
                f"({priority}, SLA {sla_minutes}m, {type_fragment}, {model_fragment})."
            )
        return (
            f"[internal] category '{category}' / subcategory '{subcategory}' is not a "
            f"valid taxonomy entry; not recorded ({priority}, SLA {sla_minutes}m)."
        )

    # Each of the three dimensions (category/subcategory taxonomy, case_type,
    # vehicle_model) is configured and validated independently, so the docstring
    # must reflect that: it's rebuilt whenever ANY of them is non-empty, and each
    # guidance block gates on its OWN emptiness rather than the taxonomy's.
    if (
        not case_taxonomy.is_empty()
        or not case_type_options.is_empty()
        or not vehicle_model_options.is_empty()
    ):
        if not case_taxonomy.is_empty():
            category_block = (
                f"    category: MUST be exactly one of: {', '.join(case_taxonomy.main_categories())}.\n"
                "    subcategory: MUST match one of the subcategories for the chosen category:\n"
                + "\n".join(
                    f"        {slug} -> {', '.join(case_taxonomy.subcategories_for(slug))}"
                    for slug in case_taxonomy.main_categories()
                )
                + "\n"
            )
        else:
            category_block = (
                "    category: General category of the problem.\n"
                "    subcategory: Precise subcategory matching the chosen category.\n"
            )

        classify_ticket_tool.__doc__ = (
            "Classify the current ticket details.\n\n"
            "Args:\n"
            "    tool_context: Context injected by the ADK runner.\n"
            + category_block
            + "    priority: Priority tier (LOW, MEDIUM, HIGH, URGENT).\n"
            "    sla_minutes: Targeted SLA duration in minutes."
            + (
                f"\n    case_type: MUST be exactly one of: {', '.join(case_type_options.options())}."
                if not case_type_options.is_empty()
                else ""
            )
            + (
                f"\n    vehicle_model: MUST be exactly one of: {', '.join(vehicle_model_options.options())}."
                if not vehicle_model_options.is_empty()
                else ""
            )
            + "\n    sentiment: The customer's emotional tone this turn -- one of "
            '"positive", "neutral", "negative", or "urgent" (safety-critical or '
            'otherwise needing immediate human attention). Defaults to "neutral" '
            "when omitted."
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
                "title": clean_title(a.title),
                "description": clean_description(a.content),
                "image_url": a.image_urls[0] if a.image_urls else None,
                "price": a.price,
                "url": a.url,
            }
            for a in articles
            if a.source_type == "model"
        ]
        # Vertex Search returns the same model page more than once; dedupe so the
        # carousel (and the WhatsApp text rendering) shows each model only once.
        tool_context.state["product_carousel"] = dedupe_cards(cards)
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
        instruction=instruction_provider or AGENT_INSTRUCTION,
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
