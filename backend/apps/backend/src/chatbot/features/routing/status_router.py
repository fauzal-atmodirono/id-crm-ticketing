"""P6 fix (review-final C1) -- the write path the plan forgot.

P6 built a status catalogue, a mirroring writer (`CustomStatusStore`), an
absence sweeper and a dashboard, and then shipped no way to *select* a
status. `set_status` had exactly two callers, both inside the After-Call-Work
controller; `add`/`list_all` had none. So in production every presence event
came from the poller as a raw native value, and requirements 4.12 (duration
tracking per named status), 4.13/4.14 (the 10-minute/1-hour absence alerts)
and 4.17 (operator-extensible statuses) were unreachable end to end while
their unit tests passed. This router is the missing half.

Four endpoints, and the permission split between them is the design
decision worth reading:

``GET  /routing/presence/statuses``      -- the catalogue an agent picks from
``GET  /routing/presence/status``        -- the agent's own current status
``POST /routing/presence/status``        -- set a status
``PUT  /routing/presence/statuses/{key}`` -- add or edit a catalogue entry

**Setting your own status is not an admin action; editing the catalogue is.**
Gating an agent's own status behind an admin permission would make the
feature unusable by the only people who use it -- the same mistake ruling D5
had to correct on the reassignment path, where an operator-facing switch had
been placed in front of a human's explicit instruction. So `POST` and the
`GET` that feeds its picker require `presence.set_own_status`, which the
default `agent` role carries; `PUT` requires `workforce.manage`, which only
`administrator` carries.

**An agent may set only their own status.** Setting a colleague's requires
`workforce.manage` too, because it is not a small thing: it removes that
colleague from routing and starts an absence-alert clock running against
their name, both of which they would have to notice to undo. When RBAC is
on, "their own" is verified -- the caller's Chatwoot session triplet is
resolved to a user id by the same `TokenValidator` `require_permission`
already used, and a body naming a different `agent_id` is refused unless the
caller holds `workforce.manage`. When RBAC is off there is **no verifiable
caller identity at all** (the only credential is a shared secret held by
whoever configured the tenant), so the request must name its `agent_id`
explicitly and this router cannot tell self from other -- the same honest
position `features/routing/router.py` reached about the audited actor on its
non-RBAC path, rather than trusting a header anyone could set.

Two smaller decisions:

* **The flag is enforced here, not only at the mount site.** With
  `presence_custom_statuses_enabled` off, every endpoint answers
  ``{"disabled": true}`` and writes nothing, so a direct caller gets the same
  guarantee the wiring gets -- the shape `sweep_presence_thresholds` already
  uses for its own flag.
* **No delete.** The catalogue can be added to and edited, never deleted:
  status keys are referenced by immutable `PresenceEvent` history, and
  deleting `lunch` would silently turn every past lunch on the dashboard
  into an unlabelled key. `PUT` can retire a status by making it
  `routable=False`; history stays readable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from chatbot.features.authz.deps import require_permission
from chatbot.features.routing.custom_status import (
    SEED_STATUSES,
    SYSTEM_MANAGED_KEYS,
    CustomStatus,
    build_custom_status_store,
)
from chatbot.features.routing.presence_store import build_presence_event_store

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.routing.custom_status import CustomStatusStore
    from chatbot.features.routing.presence_store import PresenceEvent
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# The permission an agent needs to set their OWN status. Registered in
# `features/authz/seed.py` and -- unlike almost every other key there --
# granted to the default `agent` role, because that is who uses it.
SET_OWN_STATUS_PERMISSION = "presence.set_own_status"

# The permission an operator needs to edit the catalogue or to set SOMEONE
# ELSE's status. Admin-only, the `workforce.view` write counterpart.
MANAGE_PERMISSION = "workforce.manage"


class SetStatusBody(BaseModel):
    """`agent_id` omitted means "me", which is the agent-facing case.

    It is optional rather than required so the common call carries no
    identity claim at all: with RBAC on, an absent `agent_id` cannot be
    wrong, whereas a client that always sends one could send someone else's
    by accident and would then be relying on the server's check to catch it.
    """

    key: str = Field(min_length=1)
    agent_id: int | None = None


class StatusUpsertBody(BaseModel):
    """One catalogue entry, minus its key (which is the path parameter).

    `native` is a `Literal`, not a `str`: it is PATCHed straight onto
    Chatwoot's fixed three-value availability enum, so anything else would be
    accepted here, stored, and then fail at the Chatwoot write on every
    selection -- a 422 at the point of the typo is the honest place for it.
    """

    label: str = Field(min_length=1)
    color: str = Field(default="#94a3b8", min_length=1)
    routable: bool = False
    native: Literal["online", "busy", "offline"] = "busy"
    counts_as_unavailable: bool = False


class _PresenceLog(Protocol):
    """The two `PresenceEventStore` reads the "what am I right now" endpoint
    needs, kept structural like every other collaborator in this package."""

    async def latest(self, agent_id: int) -> PresenceEvent | None: ...

    async def elapsed_in_current_status(self, agent_id: int, now: datetime) -> timedelta | None: ...


def _status_dict(status: CustomStatus, *, stored: bool) -> dict[str, Any]:
    """One catalogue row for the API.

    `stored` distinguishes a real Firestore document from the shipped
    default `CustomStatusStore.get` falls back to, so the admin page can say
    which entries have actually been written on this tenant rather than
    implying the seed has run when it has not. `system_managed` marks the
    entries an agent-facing picker must not offer (see `SYSTEM_MANAGED_KEYS`).
    """
    return {
        "key": status.key,
        "label": status.label,
        "color": status.color,
        "routable": status.routable,
        "native": status.native,
        "counts_as_unavailable": status.counts_as_unavailable,
        "stored": stored,
        "system_managed": status.key in SYSTEM_MANAGED_KEYS,
    }


async def _effective_catalogue(statuses: CustomStatusStore) -> list[dict[str, Any]]:
    """The catalogue as the system will actually resolve it: stored documents
    first, with the shipped `SEED_STATUSES` filling in anything never written.

    This mirrors `CustomStatusStore.get`'s own resolution order, so the page
    an operator reads and the lookup the sweeper performs can never disagree
    -- a catalogue page that showed nothing on an unseeded tenant while the
    sweeper happily resolved nine statuses would be its own bug report.
    Seeded keys keep their declaration order (Available first, which is what
    a picker wants at the top); operator-added keys follow, sorted, so the
    list is stable between requests.
    """
    stored = {status.key: status for status in await statuses.list_all()}
    rows = [
        _status_dict(stored.get(default.key) or default, stored=default.key in stored)
        for default in SEED_STATUSES
    ]
    extra = sorted(key for key in stored if key not in {s.key for s in SEED_STATUSES})
    rows.extend(_status_dict(stored[key], stored=True) for key in extra)
    return rows


async def _target_agent_id(
    settings: Settings,
    authz_repo: AuthzRepository | None,
    validator: TokenValidator | None,
    requested: int | None,
    access_token: str | None,
    client: str | None,
    uid: str | None,
) -> tuple[int, str]:
    """Whose status is being read/set, and the `PresenceEvent.source` to
    record for a write.

    Returns `"agent"` for a self-service change and `"admin"` for an operator
    changing someone else's -- the two values `PresenceEvent.source` already
    documents, so the dashboard and any later audit can tell them apart.

    See the module docstring for the authorisation reasoning. The
    `permissions_for_user` re-read here is not a second round trip in
    practice: `require_permission` has already resolved and cached this same
    triplet on the route dependency.
    """
    if not settings.rbac_enabled:
        if requested is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "agent_id is required when RBAC is disabled: there is no "
                    "verifiable caller identity to infer it from"
                ),
            )
        return requested, "admin"

    if validator is None or authz_repo is None:  # pragma: no cover
        # Unreachable through `require_permission`, which 401s first when RBAC
        # is on without a repo/validator. Belt and braces, because falling
        # through here would mean an unauthenticated write.
        _log.error("presence_status_rbac_misconfigured")
        raise HTTPException(status_code=401, detail="RBAC is enabled but not configured")

    if not access_token or not client or not uid:
        raise HTTPException(status_code=401, detail="Missing Chatwoot access token")
    caller_id = await validator.resolve_user_id(access_token, client, uid)
    if caller_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if requested is None or requested == caller_id:
        return caller_id, "agent"

    perms = await authz_repo.permissions_for_user(caller_id)
    if MANAGE_PERMISSION not in perms:
        raise HTTPException(status_code=403, detail=f"Missing permission: {MANAGE_PERMISSION}")
    return requested, "admin"


async def _current_status_payload(
    statuses: CustomStatusStore,
    presence: _PresenceLog,
    agent_id: int,
    now: datetime,
) -> dict[str, Any]:
    """One agent's current status, for the picker to highlight.

    `key`/`elapsed_minutes` are `null`, never a fabricated `available`/`0`,
    for an agent with no presence history -- the same no-events-is-not-zero
    rule `PresenceEventStore.latest` and the workforce dashboard already hold.
    A wrong "you are Available" here would be read as a statement about
    routing eligibility that no transition backs.

    `key` is the raw logged value rather than the resolved one, because that
    is what the agent actually selected (or what the poller observed); a
    picker highlighting a different button than the one that was pressed
    would read as the click having failed. `label`/`color` come from
    `resolve`, so a native value logged by the poller still renders as
    something a human can read.
    """
    latest = await presence.latest(agent_id)
    if latest is None:
        return {
            "agent_id": agent_id,
            "key": None,
            "label": None,
            "color": None,
            "since": None,
            "elapsed_minutes": None,
        }
    resolved = await statuses.resolve(latest.status)
    elapsed = await presence.elapsed_in_current_status(agent_id, now)
    return {
        "agent_id": agent_id,
        "key": latest.status,
        "label": resolved.label if resolved is not None else None,
        "color": resolved.color if resolved is not None else None,
        "since": latest.at.isoformat(),
        "elapsed_minutes": None if elapsed is None else int(elapsed.total_seconds() // 60),
    }


def build_status_router(
    settings: Settings,
    authz_repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    *,
    status_store: CustomStatusStore | None = None,
    presence_store: _PresenceLog | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> APIRouter:
    """Build the `/routing/presence` status-selection + catalogue router.

    Every collaborator is `None`-defaulted, matching
    `build_workforce_router`: a later wiring step mounts this and passes the
    real ones. `authz_repo`/`validator` follow `require_permission`'s own
    `| None` contract -- with `rbac_enabled` off they are unused and the
    endpoints fall back to the shared-secret `x-api-key` check.

    Kept in its own module rather than added to `features/routing/router.py`
    because that file was owned by a concurrent task; it mounts under the
    same `/routing` prefix either way.
    """
    router = APIRouter(prefix="/routing/presence", tags=["presence-status"])
    set_own = require_permission(
        SET_OWN_STATUS_PERMISSION, repo=authz_repo, validator=validator, settings=settings
    )
    manage = require_permission(
        MANAGE_PERMISSION, repo=authz_repo, validator=validator, settings=settings
    )
    statuses: CustomStatusStore = status_store or build_custom_status_store(settings)
    presence: _PresenceLog = presence_store or build_presence_event_store(settings)
    clock: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))

    def _disabled() -> dict[str, Any]:
        """The flag-off answer. 200 with `disabled: true`, matching
        `/routing/assign`'s own disabled shape, so a UI can render "not
        enabled on this tenant" instead of guessing at a 404."""
        return {
            "disabled": True,
            "reason": "PRESENCE_CUSTOM_STATUSES_ENABLED is off on this tenant",
        }

    @router.get("/statuses", dependencies=[Depends(set_own)])
    async def list_statuses() -> dict[str, Any]:
        """The catalogue, for an agent's picker and the admin page alike.

        Gated on `presence.set_own_status` rather than on `workforce.manage`
        even though it is a read shared with an admin surface: an agent who
        may choose a status must be able to see the choices, and a read of a
        list of status names is not privileged information. `PUT` below is
        where the admin boundary sits.
        """
        if not settings.presence_custom_statuses_enabled:
            return {**_disabled(), "statuses": []}
        return {"statuses": await _effective_catalogue(statuses)}

    @router.get("/status", dependencies=[Depends(set_own)])
    async def current_status(
        agent_id: int | None = None,
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """What the agent is currently set to, so a picker can show it.

        Without this an agent's own status page could only remember what it
        just posted and would go blank on a refresh -- and "no idea" rendered
        next to a list of buttons invites double-setting.

        `key`/`elapsed_minutes` are `null`, never a fabricated `available`/`0`,
        for an agent with no presence history: that is the same
        no-events-is-not-zero rule `PresenceEventStore.latest` and the
        workforce dashboard both hold, and a wrong "you are Available" here
        would be read as a statement about routing.

        Reading someone else's needs `workforce.manage`, on the same helper
        (and therefore the same reasoning) as setting it.
        """
        if not settings.presence_custom_statuses_enabled:
            return _disabled()
        target, _ = await _target_agent_id(
            settings,
            authz_repo,
            validator,
            agent_id,
            x_chatwoot_access_token,
            x_chatwoot_client,
            x_chatwoot_uid,
        )
        return await _current_status_payload(statuses, presence, target, clock())

    @router.post("/status", dependencies=[Depends(set_own)])
    async def set_status(
        body: SetStatusBody,
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Set an agent's status: the one call that makes 4.12/4.13/4.14 real.

        Reports failure honestly rather than 200-ing a write that did not
        happen (the class of defect finding I5 hit on the reassignment path):
        an unknown key is a 400 *before* anything is attempted, and a failed
        Chatwoot mirror is a 502 -- `set_status` writes the native status
        first and appends the presence event only on success, so a 502 here
        means nothing was recorded, and the dashboard and the router cannot
        end up disagreeing.
        """
        if not settings.presence_custom_statuses_enabled:
            return _disabled()

        if body.key in SYSTEM_MANAGED_KEYS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{body.key}' is set by the system, not chosen: "
                    "After-Call Work is entered at call end and times out, and "
                    "Offline is Chatwoot's own availability control"
                ),
            )
        if await statuses.get(body.key) is None:
            raise HTTPException(status_code=400, detail=f"Unknown status key: {body.key}")

        agent_id, source = await _target_agent_id(
            settings,
            authz_repo,
            validator,
            body.agent_id,
            x_chatwoot_access_token,
            x_chatwoot_client,
            x_chatwoot_uid,
        )
        ok = await statuses.set_status(agent_id, body.key, source=source)
        if not ok:
            # The native Chatwoot write failed (the key was checked above),
            # so no presence event was appended either.
            raise HTTPException(status_code=502, detail="Chatwoot rejected the availability change")
        _log.info("presence_status_set", agent_id=agent_id, key=body.key, source=source)
        return {"agent_id": agent_id, "key": body.key, "source": source, "status": "ok"}

    @router.put("/statuses/{key}", dependencies=[Depends(manage)])
    async def upsert_status(key: str, body: StatusUpsertBody) -> dict[str, Any]:
        """Add or edit one catalogue entry -- §4.17's "an administrator can
        add a ninth status without a software release", which until now was
        true only of somebody hand-editing Firestore.

        `add` is a create-or-overwrite `set`, so this is both paths. Editing a
        seeded entry writes a real document, which then wins over the shipped
        default on every later read (see `CustomStatusStore.get`).
        """
        if not settings.presence_custom_statuses_enabled:
            return _disabled()
        status = CustomStatus(
            key=key,
            label=body.label,
            color=body.color,
            routable=body.routable,
            native=body.native,
            counts_as_unavailable=body.counts_as_unavailable,
        )
        if not await statuses.add(status):
            raise HTTPException(status_code=502, detail="The catalogue write failed")
        return {"status": "ok", "entry": _status_dict(status, stored=True)}

    return router
