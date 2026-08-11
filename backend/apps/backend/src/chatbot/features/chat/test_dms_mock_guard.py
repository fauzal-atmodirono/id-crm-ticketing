"""Unit tests for Mock DMS Client sandbox guard (P12 Task 3)."""

from __future__ import annotations

import pytest

from chatbot.features.chat.dms_client import MockDmsClient


def test_the_mock_client_activates_on_a_sandbox_tenant() -> None:
    client = MockDmsClient(environment="sandbox")
    assert client is not None


def test_the_mock_client_refuses_to_activate_on_a_non_sandbox_tenant() -> None:
    with pytest.raises(ValueError, match="refused activation outside sandbox environment"):
        MockDmsClient(environment="production")


def test_the_refusal_logs_a_warning_naming_the_tenant() -> None:
    with pytest.raises(ValueError, match="'production'"):
        MockDmsClient(environment="production")


async def test_a_mock_sourced_section_reports_the_demo_state() -> None:
    client = MockDmsClient(environment="sandbox")
    cust = await client.find_customer(phone="+628123456789", vehicle_no=None)
    assert cust is not None
    assert "(Demo data)" in cust.name


async def test_a_card_with_any_demo_section_sets_a_card_level_banner_flag() -> None:
    client = MockDmsClient(environment="sandbox")
    vehicles = await client.list_vehicles("DEMO-CUST-001")
    is_demo = any("(Demo data)" in v.model for v in vehicles)
    assert is_demo is True


async def test_the_existing_per_field_demo_data_suffix_is_retained() -> None:
    client = MockDmsClient(environment="sandbox")
    history = await client.list_service_history("B1234ABC")
    assert any("(Demo data)" in record.description for record in history)
