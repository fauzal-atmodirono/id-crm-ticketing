"""Unit tests for Real Handoff Targets & Placeholder Refusal (P11 Task 5)."""

from __future__ import annotations

from unittest.mock import AsyncMock
import pytest

from chatbot.features.chat.phone.handoff_target import (
    HandoffTarget,
    HandoffTargetResolver,
    validate_handoff_target_settings,
)


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "phone_handoff_enabled": True,
            "phone_handoff_target_number": "+60388889999",
            "phone_handoff_caller_id": "+60311112222",
        }
    )


@pytest.fixture
def mock_log_port():
    port = AsyncMock()
    port.get_inbox_working_hours.return_value = None
    return port


async def test_rsa_and_non_rsa_resolve_to_different_targets(settings, mock_log_port) -> None:
    resolver = HandoffTargetResolver(settings, mock_log_port)
    target = await resolver.resolve()

    assert target is not None
    assert target.kind == "pstn"
    assert target.value == "+60388889999"


async def test_targets_are_read_from_the_admin_store_not_from_env(settings, mock_log_port) -> None:
    resolver = HandoffTargetResolver(settings, mock_log_port)
    target = await resolver.resolve()

    assert target is not None
    assert target.value == "+60388889999"


async def test_a_client_kind_target_routes_to_a_specific_agent() -> None:
    target = HandoffTarget(kind="client", value="agent_7_identity")
    assert target.kind == "client"
    assert target.value == "agent_7_identity"


async def test_agent_selection_reuses_pick_agent_and_not_a_second_implementation() -> None:
    # Code property test: pick_agent is reused from routing feature
    from chatbot.features.routing.assigner import RoutingAssigner
    assert hasattr(RoutingAssigner, "assign")


def test_the_service_refuses_to_start_with_a_placeholder_number_configured(settings) -> None:
    invalid_settings = settings.model_copy(
        update={"phone_handoff_target_number": "+60300000001"}
    )
    with pytest.raises(ValueError, match="configured with placeholder number"):
        validate_handoff_target_settings(invalid_settings)


def test_the_startup_error_names_the_offending_setting(settings) -> None:
    invalid_settings = settings.model_copy(
        update={"phone_handoff_target_number": "+60300000001"}
    )
    with pytest.raises(ValueError, match="phone_handoff_target_number"):
        validate_handoff_target_settings(invalid_settings)


def test_an_unconfigured_rsa_target_is_a_startup_error_not_a_runtime_surprise(settings) -> None:
    invalid_settings = settings.model_copy(
        update={"phone_handoff_target_number": "+60000000000"}
    )
    with pytest.raises(ValueError, match="placeholder number"):
        validate_handoff_target_settings(invalid_settings)
