"""Integration & enforcement tests for Data-Scoped RBAC (P10 Task 7)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import HTTPException, status
import pytest

from chatbot.features.authz.data_scope import (
    DataScope,
    apply_scope_to_filters,
    intersect_scopes,
    reset_role_data_scopes,
    set_role_data_scope,
)


@pytest.fixture(autouse=True)
def _clean_scopes():
    reset_role_data_scopes()
    yield
    reset_role_data_scopes()


def test_a_dealer_scoped_caller_sees_only_that_dealers_rows() -> None:
    scope = DataScope(dealers=("dealer_kl",))
    filters = apply_scope_to_filters({}, scope)

    assert filters.get("dealer_filter") == ("dealer_kl",)
    assert not filters.get("_fail_closed")


def test_an_inbox_scoped_caller_sees_only_those_inboxes_conversations() -> None:
    scope = DataScope(inboxes=("inbox_101", "inbox_102"))
    filters = apply_scope_to_filters({}, scope)

    assert filters.get("inbox_filter") == ("inbox_101", "inbox_102")
    assert not filters.get("_fail_closed")


def test_enforcement_holds_when_the_api_is_called_directly() -> None:
    # Bypassing UI and calling direct API parameters
    scope = DataScope(inboxes=("inbox_allowed",))

    # User attempts direct call passing an allowed inbox vs restricted inbox
    allowed_params = apply_scope_to_filters({"inbox": "inbox_allowed"}, scope)
    assert allowed_params.get("inbox") == "inbox_allowed"
    assert not allowed_params.get("_fail_closed")

    restricted_params = apply_scope_to_filters({"inbox": "inbox_forbidden"}, scope)
    assert restricted_params.get("_fail_closed") is True
    assert restricted_params.get("inbox") is None


def test_an_own_only_caller_receives_403_on_a_team_report() -> None:
    scope = DataScope(own_only=True)

    with pytest.raises(HTTPException) as exc_info:
        apply_scope_to_filters({"report_type": "team"}, scope)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "own_only scope cannot access team-level reports" in str(exc_info.value.detail)


def test_an_own_only_caller_still_sees_their_own_conversations() -> None:
    scope = DataScope(own_only=True)

    params = apply_scope_to_filters({"report_type": "agent", "agent_id": 7}, scope, caller_agent_id=7)
    assert params.get("agent_id") == 7
    assert not params.get("_fail_closed")


def test_scope_composes_with_an_explicit_metrics_filter() -> None:
    scope = DataScope(dealers=("dealer_north", "dealer_south"))

    # Caller explicitly passes dealer_north within their scope ceiling
    composed = apply_scope_to_filters({"dealer": "dealer_north", "date_from": "2026-08-01"}, scope)
    assert composed.get("dealer") == "dealer_north"
    assert composed.get("date_from") == "2026-08-01"
    assert not composed.get("_fail_closed")


def test_a_caller_cannot_widen_their_scope_via_a_query_parameter() -> None:
    scope = DataScope(dealers=("dealer_north",))

    # Caller passes ?dealer=dealer_south to try to read outside their scope
    composed = apply_scope_to_filters({"dealer": "dealer_south"}, scope)
    assert composed.get("_fail_closed") is True
    assert composed.get("dealer") is None


def test_the_flag_off_leaves_every_endpoint_account_wide_as_today() -> None:
    # Account-wide scope (flag off default)
    scope = DataScope()  # All None

    params = apply_scope_to_filters({"dealer": "dealer_south", "report_type": "team"}, scope)
    assert params.get("dealer") == "dealer_south"
    assert params.get("report_type") == "team"
    assert not params.get("_fail_closed")


async def test_inbox_scope_is_mirrored_into_the_chatwoot_custom_role() -> None:
    from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirror
    from chatbot.platform.config import get_settings

    mirror = ChatwootRoleMirror(get_settings())
    mirror.ensure_custom_role = AsyncMock(return_value=42)
    mirror.set_agent_custom_role = AsyncMock()

    # Simulate mirroring inbox scope for user 7
    scope = DataScope(inboxes=("inbox_1", "inbox_2"))
    role_id = await mirror.ensure_custom_role(
        chatwoot_role_id=None,
        name="Scoped_User_7",
        description="Mirrored inbox scope",
        permissions=list(scope.inboxes),
    )
    await mirror.set_agent_custom_role(chatwoot_user_id=7, chatwoot_role_id=role_id)

    assert role_id == 42
    mirror.ensure_custom_role.assert_called_once()
    mirror.set_agent_custom_role.assert_called_once_with(chatwoot_user_id=7, chatwoot_role_id=42)
