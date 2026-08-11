"""Unit tests for DataScope intersection semantics (P10 Task 6)."""

from __future__ import annotations

import pytest

from chatbot.features.authz.data_scope import (
    DataScope,
    get_role_data_scope,
    intersect_scopes,
    reset_role_data_scopes,
    resolve_user_data_scope,
    set_role_data_scope,
)


@pytest.fixture(autouse=True)
def _clean_scopes():
    reset_role_data_scopes()
    yield
    reset_role_data_scopes()


def test_an_all_none_scope_means_account_wide() -> None:
    scope = DataScope()
    assert scope.inboxes is None
    assert scope.teams is None
    assert scope.dealers is None
    assert scope.own_only is False
    assert scope.is_account_wide is True
    assert scope.is_empty() is False


def test_every_existing_role_resolves_to_an_account_wide_scope() -> None:
    role_scope = get_role_data_scope("administrator")
    assert role_scope.is_account_wide is True

    agent_scope = get_role_data_scope("agent")
    assert agent_scope.is_account_wide is True


def test_an_inbox_scope_narrows_to_those_inboxes() -> None:
    scope = DataScope(inboxes=("inbox_whatsapp", "inbox_email"))
    assert scope.inboxes == ("inbox_whatsapp", "inbox_email")
    assert scope.is_account_wide is False
    assert scope.is_empty() is False


def test_two_scopes_intersect_and_never_union() -> None:
    role_a = DataScope(inboxes=("inbox_1", "inbox_2"))
    role_b = DataScope(inboxes=("inbox_2", "inbox_3"))

    res = intersect_scopes(role_a, role_b)
    # Must yield intersection ("inbox_2"), NEVER union ("inbox_1", "inbox_2", "inbox_3")
    assert res.inboxes == ("inbox_2",)


def test_adding_a_second_role_can_never_widen_access() -> None:
    role_scoped = DataScope(dealers=("dealer_kl",))
    role_unrestricted = DataScope()  # None on all fields

    # Combining a role with dealer_kl with an account-wide role stays narrowed to dealer_kl
    res1 = intersect_scopes(role_scoped, role_unrestricted)
    assert res1.dealers == ("dealer_kl",)

    # Adding another scoped role can only narrow further
    role_other = DataScope(dealers=("dealer_pj",))
    res2 = intersect_scopes(role_scoped, role_other)
    assert res2.dealers == ()  # Disjoint -> empty set
    assert res2.is_empty() is True


def test_intersecting_a_scoped_role_with_an_unscoped_one_stays_scoped() -> None:
    role_a = DataScope(teams=("tier1_support",))
    role_b = DataScope()

    res = intersect_scopes(role_a, role_b)
    assert res.teams == ("tier1_support",)


def test_own_only_resolves_to_the_calling_agent() -> None:
    role_a = DataScope(own_only=True)
    role_b = DataScope(own_only=False)

    res = intersect_scopes(role_a, role_b)
    assert res.own_only is True


def test_an_empty_intersection_yields_access_to_nothing_not_to_everything() -> None:
    role_a = DataScope(inboxes=("inbox_sales",))
    role_b = DataScope(inboxes=("inbox_aftersales",))

    res = intersect_scopes(role_a, role_b)
    # Empty tuple (), which represents fail closed (no access), NOT None (account-wide)
    assert res.inboxes == ()
    assert res.inboxes is not None
    assert res.is_account_wide is False
    assert res.is_empty() is True
