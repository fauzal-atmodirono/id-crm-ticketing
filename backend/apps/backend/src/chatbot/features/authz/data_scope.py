"""P10 Task 6 -- Data-Scoped RBAC core logic & intersection semantics.

Implements DataScope(inboxes, teams, dealers, own_only) and intersection logic.

CRITICAL SECURITY INVARIANTS:
1. Intersection over Union: Scopes INVERT traditional additive RBAC. When a user
   holds multiple scoped roles, their DataScope is the INTERSECTION of the roles.
   Granting an extra role can ONLY narrow what they see, never broaden it.
2. None = Account-Wide, Empty Tuple () = Fail Closed:
   None means unrestricted account-wide access. An empty tuple () means ZERO
   access (fail closed). An empty scope intersection yields () and fails closed,
   preventing half-configured roles from leaking data.

## NOT ENFORCED ANYWHERE YET -- read this before citing this module

This is task 6 (the logic) without task 7 (the enforcement). Nothing outside this
file and its own tests imports it, so as shipped it changes no endpoint's
behaviour. Concretely, and tracked in
`docs/analysis/2026-08-09-blocked-work-register.md`:

- `apply_scope_to_filters()` has **no caller**. `features/metrics/query_adapter.py`
  and the admin routers were never modified, so no query is narrowed by a scope and
  the `_fail_closed` marker it sets is read by nobody.
- `resolve_user_data_scope()` has **no caller**, and there is no FastAPI dependency
  here despite the plan's interface naming one.
- `DATA_SCOPED_RBAC_ENABLED` has **no consumer at all** -- not in this module,
  which never takes a `Settings`. Flipping it on does nothing at present.
- Role scopes live in `_ROLE_DATA_SCOPES`, a module-level dict with no persistence
  and no admin surface, so an operator has no way to configure one.
- `features/authz/chatwoot_role_mirror.py` was not modified; inbox scope is not
  mirrored into a Chatwoot custom role.

The invariants above are genuinely proven by `test_data_scope.py`, which exercises
`intersect_scopes` directly. `test_scope_enforcement.py`'s names claim more than it
checks -- it calls `apply_scope_to_filters` by hand, so it cannot detect that no
request path reaches it. Do not report data-scoped RBAC as delivered on the
strength of either file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, status
import structlog

if TYPE_CHECKING:
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def _intersect_field(
    a: tuple[str, ...] | None, b: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    # Both are tuples: calculate intersection
    intersected = tuple(sorted(set(a) & set(b)))
    # An empty intersection is () [fail closed], NOT None [account-wide]
    return intersected


@dataclass(frozen=True)
class DataScope:
    inboxes: tuple[str, ...] | None = None
    teams: tuple[str, ...] | None = None
    dealers: tuple[str, ...] | None = None
    own_only: bool = False

    @property
    def is_account_wide(self) -> bool:
        return (
            self.inboxes is None
            and self.teams is None
            and self.dealers is None
            and not self.own_only
        )

    def is_empty(self) -> bool:
        """Check if any scope dimension is restricted to an empty set (fail closed)."""
        return (
            self.inboxes == ()
            or self.teams == ()
            or self.dealers == ()
        )


def intersect_scopes(a: DataScope, b: DataScope) -> DataScope:
    """Intersect two DataScopes.

    Adding a second role can NEVER widen access. If a user holds role A with
    scope A and role B with scope B, the resulting scope is intersect(A, B).
    """
    return DataScope(
        inboxes=_intersect_field(a.inboxes, b.inboxes),
        teams=_intersect_field(a.teams, b.teams),
        dealers=_intersect_field(a.dealers, b.dealers),
        own_only=a.own_only or b.own_only,
    )


# Storage for per-role DataScopes in-memory (or attached to authz repository)
_ROLE_DATA_SCOPES: dict[str, DataScope] = {}


def set_role_data_scope(role_id: str, scope: DataScope) -> None:
    _ROLE_DATA_SCOPES[role_id] = scope


def get_role_data_scope(role_id: str) -> DataScope:
    return _ROLE_DATA_SCOPES.get(role_id, DataScope())


def reset_role_data_scopes() -> None:
    _ROLE_DATA_SCOPES.clear()


async def resolve_user_data_scope(
    authz_repo: AuthzRepository, chatwoot_user_id: int
) -> DataScope:
    """Resolve a user's effective DataScope by intersecting the scopes of all assigned roles.

    If a user has no roles or no roles have a scope defined, returns account-wide DataScope().
    If a user has multiple roles, intersects their scopes.
    """
    async with authz_repo._sm() as session:
        from sqlalchemy import select
        from chatbot.features.authz.db import UserRole

        rows = await session.execute(
            select(UserRole.role_id).where(UserRole.chatwoot_user_id == chatwoot_user_id)
        )
        role_ids = [r[0] for r in rows.all()]

    if not role_ids:
        return DataScope()

    resolved_scope: DataScope | None = None
    for role_id in role_ids:
        role_scope = get_role_data_scope(role_id)
        if resolved_scope is None:
            resolved_scope = role_scope
        else:
            resolved_scope = intersect_scopes(resolved_scope, role_scope)

    return resolved_scope if resolved_scope is not None else DataScope()


def apply_scope_to_filters(
    query_params: dict[str, Any], scope: DataScope, caller_agent_id: int | None = None
) -> dict[str, Any]:
    """Compose user's DataScope with explicit query parameters.

    Query parameters can only NARROW access within the user's ceiling, never widen it.
    If scope.own_only is True and query requests a team report, raises HTTP 403.
    """
    if scope.is_account_wide:
        return dict(query_params)

    if scope.own_only:
        if query_params.get("report_type") == "team":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="own_only scope cannot access team-level reports",
            )

    result = dict(query_params)

    # Inbox scope composition
    if scope.inboxes is not None:
        param_inbox = query_params.get("inbox")
        if param_inbox is not None:
            if param_inbox not in scope.inboxes:
                result["inbox"] = None  # Narrowed out to empty
                result["_fail_closed"] = True
        else:
            result["inbox_filter"] = scope.inboxes

    # Dealer scope composition
    if scope.dealers is not None:
        param_dealer = query_params.get("dealer")
        if param_dealer is not None:
            if param_dealer not in scope.dealers:
                result["dealer"] = None
                result["_fail_closed"] = True
        else:
            result["dealer_filter"] = scope.dealers

    # Team scope composition
    if scope.teams is not None:
        param_team = query_params.get("team")
        if param_team is not None:
            if param_team not in scope.teams:
                result["team"] = None
                result["_fail_closed"] = True
        else:
            result["team_filter"] = scope.teams

    if scope.is_empty() or result.get("_fail_closed"):
        result["_fail_closed"] = True

    return result
