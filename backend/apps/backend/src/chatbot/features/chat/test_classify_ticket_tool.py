from __future__ import annotations

from types import SimpleNamespace

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
)
from chatbot.features.chat.agents import build_ai_agent
from chatbot.platform.config import get_settings


def _find_tool(agent: object, name: str) -> object:
    for tool in agent.tools:  # type: ignore[attr-defined]
        func = getattr(tool, "func", tool)
        if getattr(func, "__name__", "") == name:
            return func
    raise AssertionError(f"tool {name} not registered")


def _classify_tool(taxonomy_json: str) -> object:
    settings = get_settings()
    settings = settings.model_copy(update={"case_taxonomy_json": taxonomy_json})
    agent = build_ai_agent(settings, InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter())
    return _find_tool(agent, "classify_ticket_tool")


def _classify_tool_with_overrides(**settings_overrides: str) -> object:
    settings = get_settings().model_copy(update=settings_overrides)
    agent = build_ai_agent(settings, InMemoryTicketingAdapter(), InMemoryKnowledgeAdapter())
    return _find_tool(agent, "classify_ticket_tool")


VALID = '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking"]}}'
CASE_TYPES = '{"options": ["Inquiry", "Complaint"]}'
MODELS = '{"options": ["e.MAS 5", "e.MAS 7"]}'


@pytest.mark.asyncio
async def test_valid_category_and_subcategory_written() -> None:
    tool = _classify_tool(VALID)
    ctx = SimpleNamespace(state={})

    await tool(
        ctx,
        category="sales",
        subcategory="Test Drive Booking",
        priority="HIGH",
        sla_minutes=60,
        case_type="Inquiry",
        vehicle_model="e.MAS 5",
    )  # type: ignore[operator]

    assert ctx.state["category"] == "Sales"
    assert ctx.state["subcategory"] == "Sales: Test Drive Booking"
    assert ctx.state["priority"] == "HIGH"
    assert ctx.state["sla_minutes"] == 60


@pytest.mark.asyncio
async def test_invalid_category_not_written_but_priority_still_is() -> None:
    tool = _classify_tool(VALID)
    ctx = SimpleNamespace(state={})

    await tool(
        ctx,
        category="not_a_real_category",
        subcategory="x",
        priority="LOW",
        sla_minutes=30,
        case_type="Inquiry",
        vehicle_model="e.MAS 5",
    )  # type: ignore[operator]

    assert "category" not in ctx.state
    assert "subcategory" not in ctx.state
    assert ctx.state["priority"] == "LOW"
    assert ctx.state["sla_minutes"] == 30


@pytest.mark.asyncio
async def test_valid_category_invalid_subcategory_neither_written() -> None:
    tool = _classify_tool(VALID)
    ctx = SimpleNamespace(state={})

    await tool(
        ctx,
        category="sales",
        subcategory="Not A Real Sub",
        priority="LOW",
        sla_minutes=30,
        case_type="Inquiry",
        vehicle_model="e.MAS 5",
    )  # type: ignore[operator]

    assert "category" not in ctx.state
    assert "subcategory" not in ctx.state


@pytest.mark.asyncio
async def test_empty_taxonomy_falls_back_to_accepting_free_text() -> None:
    tool = _classify_tool("")
    ctx = SimpleNamespace(state={})

    await tool(
        ctx,
        category="Anything",
        subcategory="Whatever",
        priority="LOW",
        sla_minutes=30,
        case_type="Inquiry",
        vehicle_model="e.MAS 5",
    )  # type: ignore[operator]

    assert ctx.state["category"] == "Anything"
    assert ctx.state["subcategory"] == "Whatever"


@pytest.mark.asyncio
async def test_invalid_category_return_string_does_not_claim_success() -> None:
    tool = _classify_tool(VALID)
    ctx = SimpleNamespace(state={})

    result = await tool(
        ctx,
        category="not_a_real_category",
        subcategory="x",
        priority="LOW",
        sla_minutes=30,
        case_type="Inquiry",
        vehicle_model="e.MAS 5",
    )  # type: ignore[operator]

    assert "not_a_real_category" in result
    assert "not recorded" in result
    assert "classified as" not in result


@pytest.mark.asyncio
async def test_written_category_matches_taxonomy_label_format() -> None:
    """Contract test guarding against Finding 1's exact bug class: the value
    written to state must be a taxonomy LABEL (the format the Chatwoot List
    custom attribute / provisioning script expects), not the raw slug."""
    from chatbot.features.chat.case_taxonomy import build_case_taxonomy
    from chatbot.platform.config import get_settings

    settings = get_settings().model_copy(update={"case_taxonomy_json": VALID})
    case_taxonomy = build_case_taxonomy(settings)

    tool = _classify_tool(VALID)
    ctx = SimpleNamespace(state={})

    await tool(
        ctx,
        category="sales",
        subcategory="Test Drive Booking",
        priority="HIGH",
        sla_minutes=60,
        case_type="Inquiry",
        vehicle_model="e.MAS 5",
    )  # type: ignore[operator]

    assert ctx.state["category"] in [
        case_taxonomy.label_for(slug) for slug in case_taxonomy.main_categories()
    ]


@pytest.mark.asyncio
async def test_valid_case_type_and_vehicle_model_written() -> None:
    tool = _classify_tool_with_overrides(
        case_taxonomy_json=VALID, case_type_options_json=CASE_TYPES, vehicle_models_json=MODELS
    )
    ctx = SimpleNamespace(state={})

    await tool(
        ctx,
        category="sales",
        subcategory="Test Drive Booking",
        priority="HIGH",
        sla_minutes=60,
        case_type="Inquiry",
        vehicle_model="e.MAS 7",
    )  # type: ignore[operator]

    assert ctx.state["case_type"] == "Inquiry"
    assert ctx.state["vehicle_model"] == "e.MAS 7"


@pytest.mark.asyncio
async def test_invalid_case_type_and_vehicle_model_not_written() -> None:
    tool = _classify_tool_with_overrides(
        case_taxonomy_json=VALID, case_type_options_json=CASE_TYPES, vehicle_models_json=MODELS
    )
    ctx = SimpleNamespace(state={})

    await tool(
        ctx,
        category="sales",
        subcategory="Test Drive Booking",
        priority="LOW",
        sla_minutes=30,
        case_type="Not A Real Type",
        vehicle_model="Not A Real Model",
    )  # type: ignore[operator]

    assert "case_type" not in ctx.state
    assert "vehicle_model" not in ctx.state


@pytest.mark.asyncio
async def test_empty_option_lists_fall_back_to_accepting_free_text() -> None:
    tool = _classify_tool_with_overrides(
        case_taxonomy_json=VALID, case_type_options_json="", vehicle_models_json=""
    )
    ctx = SimpleNamespace(state={})

    await tool(
        ctx,
        category="sales",
        subcategory="Test Drive Booking",
        priority="LOW",
        sla_minutes=30,
        case_type="Anything",
        vehicle_model="Whatever",
    )  # type: ignore[operator]

    assert ctx.state["case_type"] == "Anything"
    assert ctx.state["vehicle_model"] == "Whatever"
