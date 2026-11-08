"""Data-scoped permissions (Wave 2 Task 28, FIX-48; RFP 2026_028 4.83).

RBAC already answers *which functions* a user may use (`require_permission`).
This module answers *which rows* they may see on the surfaces this backend
owns. A **scope** is attached to a role and names up to four dimensions:

- ``inbox_ids``    -- Chatwoot inbox ids (conversation ``inbox_id``)
- ``team_ids``     -- Chatwoot team ids (conversation ``team_id`` / ``meta.team.id``)
- ``dealer_slugs`` -- dealer slugs (``dealer_<slug>`` labels, the
  ``purchased_from_dealer`` case field, the escalation dealer registry, the
  ``dealer`` column of the reporting views, an RSA incident's ``purchased_from``)
- ``departments``  -- taxonomy department (``dept_<x>`` labels, the
  ``department`` column of the reporting views, a PIC routing record)

## The rules, and why each one is the way it is

1. **Empty = unrestricted.** A dimension with no values does not restrict; a
   role with no scope row does not restrict. So switching
   ``DATA_SCOPED_RBAC_ENABLED`` on changes nothing for anybody until an
   operator actually configures a scope on a role -- the same
   default-preserving shape as every other flag here.
2. **Administrator and platform superadmin are always unrestricted**, whatever
   other roles they hold. A scope cannot be saved on the ``administrator``
   role at all (the router 400s), so a tenant cannot accidentally confine the
   people who configure scopes.
3. **Roles that carry no scope are neutral.** Every user holds the seeded
   ``agent`` role; if "unscoped role = see everything" won a union, any scope
   on a second role would be erased by the default one. So only roles that
   *have* a scope take part in the combination.
4. **Across scoped roles, values union per dimension; a dimension restricted
   by any scoped role stays restricted.** Two dealer roles (``kl`` and
   ``penang``) give ``{kl, penang}`` -- granting a second dealer role widens
   to that dealer, which is what an operator expects. A role restricting only
   inboxes combined with one restricting only dealers yields *both*
   restrictions (rows must match the inbox AND the dealer). That is narrower
   than a true row-level union of the two roles; it is the deliberately safe
   side of the trade (under-grant, never over-grant) and is documented to
   Proton as "configure related roles on the same dimensions".
   (P10's unwired draft of this module intersected values across roles; that
   made "give her the KL and the Penang dealer roles" mean "she sees
   neither", which no operator would predict.)
5. **A row that lacks a restricted dimension is hidden.** A dealer-scoped
   user does not see a conversation that names no dealer. Data that cannot
   be attributed is not presumed to be theirs. Configuration records keyed
   by one dimension (a dealer's routing row, a department's PIC row) are
   checked on that one dimension only -- see `Scope.allows`.
6. **Aggregates that cannot be filtered on a restricted dimension are
   refused (403), never served unfiltered.** See `ScopeNotApplicable`.
7. **Fail closed.** With the flag on, a scope that cannot be read (database
   down, repository missing) resolves to `DENY_ALL` -- lists come back empty,
   reports 403 -- never to "everything". Startup is never affected: the
   ``role_scopes`` table is created by the same idempotent ``create_all`` as
   the rest of RBAC.

## What this does NOT govern

Chatwoot's own conversation lists (the inbox views, and the fork's Cases list,
which reads Chatwoot's ``/conversations`` directly with the agent's own
session) are governed by **Chatwoot inbox membership** and the native custom
role mirror, not by this module. To confine an agent's Chatwoot inbox views,
make their Chatwoot inbox membership match their inbox scope. Data scope
governs the data this backend serves with its own (admin-level) Chatwoot
token or from its own stores: Customer 360, case fields, reports, the
escalation-routing admin, RSA incidents and the AI activity monitor.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import Depends, Header, HTTPException, Request

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

DIMENSIONS: tuple[str, ...] = ("inbox_ids", "team_ids", "dealer_slugs", "departments")
# Roles that are always unrestricted, and on which a scope may not be saved.
UNSCOPABLE_ROLE_IDS: frozenset[str] = frozenset({"administrator"})

_DEALER_PREFIX = "dealer_"
_DEPT_PREFIX = "dept_"


def _norm_text(value: Any, prefix: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    text = text.removeprefix(prefix)
    text = text.replace(" ", "_").replace("-", "_")
    return text or None


def normalise_dealer(value: Any) -> str | None:
    """`Dealer_KL`, `dealer_kl`, `dealer kl` and `kl` all compare as `kl`."""
    return _norm_text(value, _DEALER_PREFIX)


def normalise_department(value: Any) -> str | None:
    return _norm_text(value, _DEPT_PREFIX)


def _norm_id(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


_NORMALISERS: dict[str, Callable[[Any], Any]] = {
    "inbox_ids": _norm_id,
    "team_ids": _norm_id,
    "dealer_slugs": normalise_dealer,
    "departments": normalise_department,
}


def _norm_set(dimension: str, values: Any) -> frozenset:
    if values is None:
        return frozenset()
    if isinstance(values, str | int):
        values = [values]
    norm = _NORMALISERS[dimension]
    return frozenset(v for v in (norm(x) for x in values) if v is not None)


@dataclass(frozen=True)
class Scope:
    """One role's scope, or a user's effective scope. Empty dimension = no
    restriction on it; `deny_all` is the fail-closed marker."""

    inbox_ids: frozenset[int] = field(default_factory=frozenset)
    team_ids: frozenset[int] = field(default_factory=frozenset)
    dealer_slugs: frozenset[str] = field(default_factory=frozenset)
    departments: frozenset[str] = field(default_factory=frozenset)
    deny_all: bool = False

    @classmethod
    def build(
        cls,
        *,
        inbox_ids: Any = None,
        team_ids: Any = None,
        dealer_slugs: Any = None,
        departments: Any = None,
    ) -> Scope:
        """Normalising constructor -- use this for anything from outside."""
        return cls(
            inbox_ids=_norm_set("inbox_ids", inbox_ids),
            team_ids=_norm_set("team_ids", team_ids),
            dealer_slugs=_norm_set("dealer_slugs", dealer_slugs),
            departments=_norm_set("departments", departments),
        )

    @property
    def unrestricted(self) -> bool:
        return not self.deny_all and not any(getattr(self, d) for d in DIMENSIONS)

    @property
    def restricted_dimensions(self) -> tuple[str, ...]:
        return tuple(d for d in DIMENSIONS if getattr(self, d))

    def allows(self, dimension: str, value: Any) -> bool:
        """Is this single key (a dealer slug, a department) inside the scope
        on its own dimension? For config records keyed by one dimension."""
        if self.deny_all:
            return False
        allowed = getattr(self, dimension)
        if not allowed:
            return True
        return _NORMALISERS[dimension](value) in allowed

    def permits(self, dims: dict[str, Any]) -> bool:
        """Row check. `dims` maps dimension -> the row's value(s) for it; a
        restricted dimension absent from `dims` (or empty on the row) hides
        the row (rule 5)."""
        if self.deny_all:
            return False
        for dimension in self.restricted_dimensions:
            row_values = _norm_set(dimension, dims.get(dimension))
            if not row_values & getattr(self, dimension):
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "inbox_ids": sorted(self.inbox_ids),
            "team_ids": sorted(self.team_ids),
            "dealer_slugs": sorted(self.dealer_slugs),
            "departments": sorted(self.departments),
            "unrestricted": self.unrestricted,
            "deny_all": self.deny_all,
        }

    def to_json_columns(self) -> dict[str, str]:
        return {d: json.dumps(sorted(getattr(self, d))) for d in DIMENSIONS}

    @classmethod
    def from_json_columns(cls, row: Any) -> Scope:
        return cls.build(**{d: json.loads(getattr(row, d) or "[]") for d in DIMENSIONS})


UNRESTRICTED = Scope()
DENY_ALL = Scope(deny_all=True)


def combine_role_scopes(scopes: Iterable[Scope]) -> Scope:
    """A user's effective scope from the scopes of their *scoped* roles
    (rules 3-4). No scoped role -> UNRESTRICTED."""
    merged: dict[str, set] = {d: set() for d in DIMENSIONS}
    for scope in scopes:
        if scope.deny_all:
            return DENY_ALL
        for d in DIMENSIONS:
            merged[d] |= getattr(scope, d)
    return Scope(**{d: frozenset(v) for d, v in merged.items()})


# --- applying a scope ------------------------------------------------------


def apply_scope(
    rows: Iterable[Any], scope: Scope, dims: Callable[[Any], dict[str, Any]]
) -> list[Any]:
    """Keep the rows `scope` permits. `dims(row)` returns the row's dimension
    values; a dimension it leaves out is absent (hidden when restricted).
    Unrestricted -> every row, order unchanged."""
    rows = list(rows)
    if scope.unrestricted:
        return rows
    if scope.deny_all:
        return []
    return [r for r in rows if scope.permits(dims(r))]


def conversation_dimensions(conv: dict[str, Any]) -> dict[str, Any]:
    """Dimension values of a Chatwoot conversation payload."""
    meta = conv.get("meta") or {}
    team = meta.get("team") or {}
    attrs = conv.get("custom_attributes") or {}
    labels = [str(lb) for lb in (conv.get("labels") or [])]
    dealers: list[Any] = [lb for lb in labels if lb.lower().startswith(_DEALER_PREFIX)]
    dealers += [attrs[k] for k in ("purchased_from_dealer", "dealer") if attrs.get(k)]
    departments: list[Any] = [lb for lb in labels if lb.lower().startswith(_DEPT_PREFIX)]
    if attrs.get("department"):
        departments.append(attrs["department"])
    team_id = conv.get("team_id")
    if team_id is None and isinstance(team, dict):
        team_id = team.get("id")
    return {
        "inbox_ids": conv.get("inbox_id"),
        "team_ids": team_id,
        "dealer_slugs": dealers,
        "departments": departments,
    }


class ScopeNotApplicable(HTTPException):
    """An aggregate that cannot be narrowed to the caller's scope (rule 6)."""

    def __init__(self, what: str, dimensions: Iterable[str] = ()) -> None:
        dims = ", ".join(dimensions) or "your data scope"
        super().__init__(
            status_code=403,
            detail=(
                f"{what} cannot be filtered by {dims}, so it is not available "
                "to a data-scoped user. Ask an administrator for a report "
                "within your scope."
            ),
        )


def require_unrestricted(scope: Scope, what: str) -> None:
    """403 unless `scope` is unrestricted -- for aggregates with no way to
    narrow them."""
    if not scope.unrestricted:
        raise ScopeNotApplicable(what, scope.restricted_dimensions or ("data scope",))


# --- the per-request scope, for layers that never see the request ----------

_current_scope: ContextVar[Scope] = ContextVar("data_scope", default=UNRESTRICTED)


def current_scope() -> Scope:
    """The scope of the request being served. Outside a request (scheduled
    reports, background jobs) this is UNRESTRICTED: those are system reads."""
    return _current_scope.get()


def bind_scope(scope: Scope) -> None:
    """Set the request's scope from inside a FastAPI dependency.

    No reset is needed: each request runs in its own task, so its context is
    a copy that dies with it, and an async dependency is awaited in that same
    context, so the endpoint (and `asyncio.to_thread` beneath it) sees the
    value. Code outside a request never calls this and sees UNRESTRICTED.
    """
    _current_scope.set(scope)


@contextlib.contextmanager
def use_scope(scope: Scope) -> Iterator[None]:
    """Make `scope` visible to `current_scope()` for the block -- including
    inside `asyncio.to_thread`, which copies the context."""
    token = _current_scope.set(scope)
    try:
        yield
    finally:
        _current_scope.reset(token)


# Reporting views: the dimension -> column each view carries. Derived from the
# view definitions in metrics/bigquery_schema.py. A view absent here, or a
# restricted dimension it has no column for, cannot be scoped -> 403. Inbox
# and team have no column in any reporting view.
SCOPE_COLUMNS: dict[str, str] = {"dealer_slugs": "dealer", "departments": "department"}
SCOPABLE_VIEW_COLUMNS: dict[str, frozenset[str]] = {
    "v_dept_pic_performance": frozenset({"department"}),
    "v_reopen_rate": frozenset({"dealer", "department"}),
    "v_dealer_escalation": frozenset({"dealer"}),
    "v_dealer_escalation_slowest_cases": frozenset({"dealer"}),
    "v_case_aging": frozenset({"dealer"}),
    "v_first_response_by_dealer": frozenset({"dealer"}),
}


def scope_predicates(view: str, scope: Scope) -> tuple[str, dict[str, str]]:
    """`(sql_fragment, params)` narrowing `view` to `scope`, or raise
    `ScopeNotApplicable`. Placeholders only; values are bound, never pasted."""
    if scope.unrestricted:
        return "", {}
    if scope.deny_all:
        raise ScopeNotApplicable(f"Report {view}", ("data scope (unreadable)",))
    columns = SCOPABLE_VIEW_COLUMNS.get(view, frozenset())
    clauses: list[str] = []
    params: dict[str, str] = {}
    for dimension in scope.restricted_dimensions:
        column = SCOPE_COLUMNS.get(dimension)
        if column is None or column not in columns:
            raise ScopeNotApplicable(f"Report {view}", (dimension,))
        names = []
        for i, value in enumerate(sorted(getattr(scope, dimension))):
            name = f"scope_{column}_{i}"
            names.append(f"@{name}")
            params[name] = str(value)
        clauses.append(f"LOWER({column}) IN ({', '.join(names)})")
    return " AND ".join(clauses), params


# --- resolution ------------------------------------------------------------


async def effective_scope(repo: AuthzRepository, user_id: int) -> Scope:
    """A user's effective scope (rules 1-4). Raises on a storage failure --
    the dependency below turns that into DENY_ALL."""
    return await repo.effective_scope(user_id)


def build_scope_dependency(
    settings: Settings,
    *,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
):
    """FastAPI dependency -> the caller's `Scope`, resolved once per request
    (FastAPI caches a dependency's value within one request).

    - Flag off: UNRESTRICTED without touching the session -- byte-identical
      behaviour for every tenant that has not opted in.
    - Flag on: a Chatwoot session is required (401 without). The shared
      x-api-key identifies a service that every browser holds, so it can
      never carry a person's scope. Superadmin / administrator: UNRESTRICTED.
      Any failure to read the scope: DENY_ALL (rule 7).

    `repo`/`validator` default to `app.state.authz_repo` /
    `app.state.authz_validator`, so routers mounted before the RBAC block
    (the metrics routers) can use it without changing their factories.
    """
    from chatbot.features.authz.deps import (  # noqa: PLC0415 -- deps imports nothing from here; keeps this module importable alone
        _resolve_session,
        is_platform_superadmin,
    )

    async def _scope(
        request: Request,
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> Scope:
        if not getattr(settings, "data_scoped_rbac_enabled", False):
            return UNRESTRICTED
        state = request.app.state
        the_repo = repo if repo is not None else getattr(state, "authz_repo", None)
        the_validator = (
            validator if validator is not None else getattr(state, "authz_validator", None)
        )
        if not getattr(settings, "rbac_enabled", False):
            # Settings refuses this combination at boot; a hand-built settings
            # object must still fail closed rather than open.
            _log.error("data_scope_without_rbac")
            return DENY_ALL
        user_id, is_super = await _resolve_session(
            x_chatwoot_access_token,
            x_chatwoot_client,
            x_chatwoot_uid,
            validator=the_validator,
            repo=None,
            needs_repo=False,
            missing_detail="Chatwoot session required for data-scoped access",
        )
        if is_platform_superadmin(user_id, is_super):
            return UNRESTRICTED
        if the_repo is None:
            _log.error("data_scope_repo_missing", user_id=user_id)
            return DENY_ALL
        try:
            return await effective_scope(the_repo, user_id)
        except Exception as exc:
            _log.error(
                "data_scope_read_failed", user_id=user_id, error_type=type(exc).__name__
            )
            return DENY_ALL

    return _scope


def build_report_scope_dependency(settings: Settings, port: Any):
    """For reporting routes whose reads go through a scope-aware port: binds
    the scope for the adapter (`current_scope()`), and refuses a restricted
    scope outright when the port cannot apply it (`supports_data_scope`
    unset -- e.g. the canned mock port)."""
    resolve = build_scope_dependency(settings)

    async def _dep(scope: Scope = Depends(resolve)) -> Scope:
        if not scope.unrestricted and not getattr(port, "supports_data_scope", False):
            raise ScopeNotApplicable("This report source", scope.restricted_dimensions)
        bind_scope(scope)
        return scope

    return _dep


def build_unscoped_only_dependency(settings: Settings, what: str):
    """For aggregates with no scope dimension at all: 403 for a restricted
    scope (rule 6), a no-op otherwise -- and, flag off, a no-op that does not
    even look at the session."""
    resolve = build_scope_dependency(settings)

    async def _dep(scope: Scope = Depends(resolve)) -> None:
        require_unrestricted(scope, what)

    return _dep
