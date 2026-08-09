"""P9 task 1/6 -- the alert-rule router: admin defaults, self-service overrides.

Two audiences, two permissions, and the split is the whole design decision
here (same shape as `features/routing/status_router.py`'s `presence.set_own_status`
/ `workforce.manage` split, and `features/chat/pic_admin_router.py`'s
admin-only routing config):

``GET  /alerts/rules/defaults``       -- the account-level default per event
``PUT  /alerts/rules/defaults/{event}`` -- edit an account-level default
``GET  /alerts/rules/mine``            -- what will actually fire for ME
``PUT  /alerts/rules/mine/{event}``    -- override one event for MYSELF
``DELETE /alerts/rules/mine/{event}``  -- revert MYSELF to the account default

**Setting your own alert preferences is not an admin action; editing the
account-wide defaults is.** Tolerance for interruption genuinely varies by
person -- that is `AlertRuleStore`'s whole reason for having a per-agent
override layer at all -- so gating an agent's own preferences behind an
admin permission would leave that layer unreachable by anyone, the exact
mistake ruling D5 and the `presence.set_own_status`/`translation.use`
decisions already had to correct on other packages. `GET/PUT/DELETE
.../mine*` therefore require only `alerts.set_own_preferences`, which the
default `agent` role carries. `PUT /defaults/{event}` requires
`alerts.manage`, which only `administrator` carries: one agent quietly
turning down the account-wide `sla_breach` default would go unnoticed until
the whole team missed one, so that lever is admin-only.

`GET /alerts/rules/mine` is not merely a preferences-page read: it is the
endpoint the fork's alert module (task 2/3) is designed to call before
deciding whether to raise a modality for an event, per this package's own
plan (`docs/superpowers/plans/2026-08-08-rfp-p9-notification-alerting.md`,
task 2's "Consumes: ... `GET /alerts/rules/mine`"). Its shape is therefore
fixed by that consumer, not just by this router's own tests.

**This router has no "set another agent's preferences" capability at all.**
Unlike `status_router`'s `POST /status`, there is no admin path here that
targets a different `agent_id` -- account-level defaults are the only lever
an operator gets, which is a deliberate, narrower surface: an admin who
wants to enforce something for one agent edits the account default (which
that agent's own override can still beat) rather than reaching into a
colleague's personal preferences.

**The flag is enforced here, not only at the mount site**, matching
`status_router`'s own reasoning: with `alert_rules_enabled` off, every
endpoint answers `{"disabled": true}` and touches the store for nothing, so
a direct caller gets the same guarantee the wiring gets.

**Not wired into `main.py` by this task** -- `build_rules_router` is a
factory for a later wiring step to call, matching `build_status_router`'s
own contract. See this task's report for why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.alerts.rules_store import (
    EVENTS,
    AlertRule,
    AlertRuleStore,
    InvalidAlertRule,
    build_alert_rule_store,
)
from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# The permission an agent needs to read/write their OWN overrides. Registered
# in `features/authz/seed.py` and granted to the default `agent` role.
SET_OWN_PREFERENCES_PERMISSION = "alerts.set_own_preferences"

# The permission an operator needs to edit the account-level defaults.
# Admin-only, the `workforce.manage`/`escalation.manage` counterpart.
MANAGE_PERMISSION = "alerts.manage"


class AlertRuleBody(BaseModel):
    """One rule, minus its `event` (which is the path parameter). `Literal`
    on `scope`/`modalities`, not `str`: an unrecognised value here would
    otherwise be accepted, stored, and then silently fire nothing on the
    fork side -- a 422 at the point of the typo is the honest place for it,
    the same reasoning `status_router.StatusUpsertBody.native` documents.
    """

    scope: Literal["mine", "my_inbox", "my_team", "all"]
    modalities: list[Literal["sound", "desktop", "toast"]]
    enabled: bool = True


def _rule_dict(rule: AlertRule) -> dict[str, Any]:
    return {
        "event": rule.event,
        "scope": rule.scope,
        "modalities": list(rule.modalities),
        "enabled": rule.enabled,
    }


def _rule_from_body(event: str, body: AlertRuleBody) -> AlertRule:
    return AlertRule(
        event=event, scope=body.scope, modalities=tuple(body.modalities), enabled=body.enabled
    )


async def _caller_agent_id(
    settings: Settings,
    authz_repo: AuthzRepository | None,
    validator: TokenValidator | None,
    requested: int | None,
    access_token: str | None,
    client: str | None,
    uid: str | None,
) -> int:
    """Whose alert preferences this request acts on.

    With RBAC on, the caller's own Chatwoot session resolves to a user id and
    that is the only identity this router will ever act on for a `/mine`
    endpoint -- an `agent_id` query parameter, if one is even sent, is never
    consulted, so there is no way to point a `/mine` call at a colleague by
    supplying their id (this router simply has no such capability -- see the
    module docstring). With RBAC off there is no verifiable caller identity
    at all (`require_permission`'s fallback is a shared secret held by
    whoever configured the tenant), so the caller must name `agent_id`
    explicitly -- the same honest position `status_router._target_agent_id`
    reaches on its own non-RBAC path, rather than trusting a header anyone
    could set.
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
        return requested

    if validator is None or authz_repo is None:  # pragma: no cover
        # Unreachable through `require_permission`, which 401s first when RBAC
        # is on without a repo/validator. Belt and braces against an
        # unauthenticated read/write of alert preferences.
        _log.error("alert_rules_rbac_misconfigured")
        raise HTTPException(status_code=401, detail="RBAC is enabled but not configured")

    if not access_token or not client or not uid:
        raise HTTPException(status_code=401, detail="Missing Chatwoot access token")
    caller_id = await validator.resolve_user_id(access_token, client, uid)
    if caller_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return caller_id


def build_rules_router(
    settings: Settings,
    authz_repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    *,
    store: AlertRuleStore | None = None,
) -> APIRouter:
    """Build the `/alerts/rules` router.

    Every collaborator is `None`-defaulted, matching `build_status_router`:
    a later wiring step mounts this and passes the real `authz_repo`/
    `validator`. With `rbac_enabled` off they are unused and every endpoint
    falls back to the shared-secret `x-api-key` check `require_permission`
    already implements.
    """
    router = APIRouter(prefix="/alerts/rules", tags=["alert-rules"])
    set_own = require_permission(
        SET_OWN_PREFERENCES_PERMISSION, repo=authz_repo, validator=validator, settings=settings
    )
    manage = require_permission(
        MANAGE_PERMISSION, repo=authz_repo, validator=validator, settings=settings
    )
    rules: AlertRuleStore = store or build_alert_rule_store(settings)

    def _disabled() -> dict[str, Any]:
        return {"disabled": True, "reason": "ALERT_RULES_ENABLED is off on this tenant"}

    @router.get("/defaults", dependencies=[Depends(set_own)])
    async def get_defaults() -> dict[str, Any]:
        """The effective account-level default for every event: a stored
        document wins, the built-in default fills any event never
        configured. Readable by any agent (not admin-gated) -- knowing what
        you inherit is not privileged information, the same boundary
        `status_router.list_statuses` draws for its own catalogue read.
        """
        if not settings.alert_rules_enabled:
            return {**_disabled(), "defaults": {}}
        effective = await rules.list_account_rules()
        return {"defaults": {event: _rule_dict(rule) for event, rule in effective.items()}}

    @router.put("/defaults/{event}", dependencies=[Depends(manage)])
    async def set_default(event: str, body: AlertRuleBody) -> dict[str, Any]:
        """Edit the account-wide default for one event. `alerts.manage` only."""
        if not settings.alert_rules_enabled:
            return _disabled()
        if event not in EVENTS:
            raise HTTPException(status_code=400, detail=f"Unknown event: {event}")
        rule = _rule_from_body(event, body)
        try:
            rule.validate()
        except InvalidAlertRule as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not await rules.set_account_rule(rule):
            raise HTTPException(status_code=502, detail="The rule store write failed")
        return {"status": "ok", "rule": _rule_dict(rule)}

    @router.get("/mine", dependencies=[Depends(set_own)])
    async def get_mine(
        agent_id: int | None = None,
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """What will actually fire for the calling agent, per event -- the
        resolved answer (override, else account default, else built-in),
        not just their raw overrides. This is the endpoint the fork's alert
        module calls before deciding whether to raise a modality; see the
        module docstring.
        """
        if not settings.alert_rules_enabled:
            return {**_disabled(), "rules": {}}
        caller = await _caller_agent_id(
            settings,
            authz_repo,
            validator,
            agent_id,
            x_chatwoot_access_token,
            x_chatwoot_client,
            x_chatwoot_uid,
        )
        resolved = {event: await rules.resolve(caller, event) for event in EVENTS}
        return {
            "agent_id": caller,
            "rules": {event: _rule_dict(rule) for event, rule in resolved.items()},
        }

    @router.put("/mine/{event}", dependencies=[Depends(set_own)])
    async def set_mine(
        event: str,
        body: AlertRuleBody,
        agent_id: int | None = None,
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Set the calling agent's own override for one event."""
        if not settings.alert_rules_enabled:
            return _disabled()
        if event not in EVENTS:
            raise HTTPException(status_code=400, detail=f"Unknown event: {event}")
        caller = await _caller_agent_id(
            settings,
            authz_repo,
            validator,
            agent_id,
            x_chatwoot_access_token,
            x_chatwoot_client,
            x_chatwoot_uid,
        )
        rule = _rule_from_body(event, body)
        try:
            rule.validate()
        except InvalidAlertRule as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not await rules.set_agent_override(caller, rule):
            raise HTTPException(status_code=502, detail="The rule store write failed")
        return {"agent_id": caller, "status": "ok", "rule": _rule_dict(rule)}

    @router.delete("/mine/{event}", dependencies=[Depends(set_own)])
    async def reset_mine(
        event: str,
        agent_id: int | None = None,
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Delete the calling agent's override for one event, reverting them
        to whatever the account default resolves to. Returns the resolved
        rule after the reset, not just a bare "ok", so the preferences page
        can repaint the row without a second round trip.
        """
        if not settings.alert_rules_enabled:
            return _disabled()
        if event not in EVENTS:
            raise HTTPException(status_code=400, detail=f"Unknown event: {event}")
        caller = await _caller_agent_id(
            settings,
            authz_repo,
            validator,
            agent_id,
            x_chatwoot_access_token,
            x_chatwoot_client,
            x_chatwoot_uid,
        )
        if not await rules.clear_agent_override(caller, event):
            raise HTTPException(status_code=502, detail="The rule store write failed")
        resolved = await rules.resolve(caller, event)
        return {"agent_id": caller, "status": "ok", "rule": _rule_dict(resolved)}

    return router
