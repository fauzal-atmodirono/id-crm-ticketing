from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.routing.presence import AgentRecord, PresenceFetcher
from chatbot.features.routing.store import AgentPriority, ChannelPriorityStore

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.ports import AuditLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class _PriorityIn(BaseModel):
    agent_id: int
    channel_priorities: list[str]


class _PriorityUpdate(BaseModel):
    channel_priorities: list[str]


class _AgentOut(BaseModel):
    id: int
    name: str
    availability_status: str


class _PriorityOut(BaseModel):
    agent_id: int
    channel_priorities: list[str]


class _AssignIn(BaseModel):
    conversation_id: int
    # Task 8 (P6): an explicit agent_id names a supervisor's chosen agent
    # (reassignment). Absent (the default) preserves today's behaviour
    # exactly -- the router auto-picks via routing_svc.pick_agent.
    agent_id: int | None = None


def _require_api_key(settings: Settings):
    """Return a FastAPI dependency that 401s when the x-api-key header is wrong.

    Accepts any of: routing_admin_api_key, faq_admin_api_key, proton_backend_key.
    All comparisons are constant-time to prevent timing attacks.
    """

    def _check(x_api_key: str | None = Header(default=None)) -> None:
        if not x_api_key:
            raise HTTPException(status_code=401, detail="Missing or invalid API key")
        candidates = [
            settings.routing_admin_api_key,
            settings.faq_admin_api_key,
            settings.proton_backend_key,
        ]
        for key in candidates:
            if key and hmac.compare_digest(x_api_key, key):
                return
        raise HTTPException(status_code=401, detail="Missing or invalid API key")

    return _check


def build_routing_router(
    settings: Settings,
    store: ChannelPriorityStore,
    presence: PresenceFetcher,
    routing_svc,
    assigner,
    audit: AuditLogPort | None = None,
    authz_repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
) -> APIRouter:
    """Build and return the routing config FastAPI router."""
    router = APIRouter(tags=["routing"])
    auth = _require_api_key(settings)

    @router.get("/routing/agents", response_model=list[_AgentOut])
    async def list_agents() -> list[AgentRecord]:
        return await presence.fetch_agents()

    @router.get("/routing/priorities", response_model=list[_PriorityOut])
    async def list_priorities() -> list[AgentPriority]:
        return await store.list_all()

    @router.post(
        "/routing/priorities",
        response_model=_PriorityOut,
        dependencies=[Depends(auth)],
    )
    async def create_priority(body: _PriorityIn) -> AgentPriority:
        await store.set(body.agent_id, body.channel_priorities)
        return AgentPriority(agent_id=body.agent_id, channel_priorities=body.channel_priorities)

    @router.put(
        "/routing/priorities/{agent_id}",
        response_model=_PriorityOut,
        dependencies=[Depends(auth)],
    )
    async def update_priority(agent_id: int, body: _PriorityUpdate) -> AgentPriority:
        await store.set(agent_id, body.channel_priorities)
        return AgentPriority(agent_id=agent_id, channel_priorities=body.channel_priorities)

    @router.delete("/routing/priorities/{agent_id}", dependencies=[Depends(auth)])
    async def delete_priority(agent_id: int) -> dict[str, str]:
        await store.delete(agent_id)
        return {"status": "deleted", "agent_id": str(agent_id)}

    async def _reassign(
        conversation_id: int,
        agent_id: int,
        *,
        x_api_key: str | None,
        x_chatwoot_access_token: str | None,
        x_chatwoot_client: str | None,
        x_chatwoot_uid: str | None,
    ) -> dict[str, Any]:
        """Supervisor-facing reassignment: an explicit `agent_id`, as
        opposed to the router auto-picking one. This is checked against
        `routing.reassign` HERE, inside the handler, rather than as a
        route-level `dependencies=[...]` entry on `/routing/assign`'s
        decorator. A route-level dependency runs for every request to this
        endpoint -- including the agent_id-absent auto-pick call the live
        handoff path depends on -- so gating it behind an RBAC permission
        would break that path in production. Only the branch that
        represents a human supervisor actually choosing an agent needs the
        extra gate; the decorator's `Depends(auth)` (the pre-existing
        shared-secret check) still runs for both branches, unchanged.

        Also note: `settings.routing_enabled` is deliberately never
        consulted here -- see the comment at this function's call site in
        `assign_conversation` for why that switch does not apply to an
        explicit reassignment.
        """
        # Review finding 4: require_permission's non-RBAC fallback
        # (features/authz/deps.py's `_shared_secret_check`) only accepts
        # faq_admin_api_key / proton_backend_key -- NOT
        # routing_admin_api_key -- unlike this router's own
        # `_require_api_key` (bound to `auth` above, already run
        # unconditionally by the route decorator's `Depends(auth)`), which
        # accepts all three. Widening deps.py's candidate list would
        # silently grant routing_admin_api_key access to the pic-admin,
        # customer360, and dms-admin routers too -- a security change
        # nobody asked for -- so a tenant configured with only
        # ROUTING_ADMIN_API_KEY must not be locked out of reassignment
        # instead. Fix lives here: when RBAC is off, authorise this branch
        # with this router's own key-check (`auth`, a cheap, idempotent
        # re-check of a request that already passed it once) rather than
        # routing it through require_permission's narrower fallback. When
        # RBAC is on, require_permission's Chatwoot-identity +
        # permission-set check is the real gate, unchanged.
        if settings.rbac_enabled:
            permission_check = require_permission(
                "routing.reassign", repo=authz_repo, validator=validator, settings=settings
            )
            await permission_check(
                x_api_key=x_api_key,
                x_chatwoot_access_token=x_chatwoot_access_token,
                x_chatwoot_client=x_chatwoot_client,
                x_chatwoot_uid=x_chatwoot_uid,
            )
        else:
            auth(x_api_key=x_api_key)

        # fetch_agents() is fail-open (returns [] when Chatwoot is
        # unreachable) -- that's the right default for presence *reads*
        # elsewhere in this module, but validation of a reassignment target
        # must NOT inherit it: silently assigning to an id nobody could
        # confirm exists is worse than refusing a reassignment during a
        # Chatwoot blip. An empty/no-match result is therefore treated the
        # same as "unknown agent_id" below, not as "skip validation".
        agents = await presence.fetch_agents()
        target = next((a for a in agents if a.id == agent_id), None)
        if target is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown agent_id: {agent_id} (not found in the Chatwoot agent list)",
            )

        # "Routable" here is deliberately narrow (availability_status ==
        # "online") and standalone -- a richer, custom-status notion of
        # routable may exist elsewhere but is not imported here. A
        # non-routable target is still a legitimate assignment (e.g. a team
        # leader assigning someone due back from lunch in five minutes):
        # warn, never refuse -- refusing would push supervisors back to the
        # stock Chatwoot dropdown and lose this endpoint's audit trail.
        warning: str | None = None
        if target.availability_status != "online":
            warning = (
                f"Agent {target.id} ({target.name}) is not currently routable "
                f"(availability_status={target.availability_status}); "
                "assignment proceeds."
            )
            _log.warning(
                "routing_reassign_non_routable_target",
                conversation_id=conversation_id,
                agent_id=agent_id,
                availability_status=target.availability_status,
            )

        await assigner.assign(conversation_id, agent_id)

        # Review finding 3: x-chatwoot-uid is a client-supplied header, not
        # by itself a verified identity. On the RBAC-off path (the
        # default), the `auth(...)` check just above never inspects the
        # Chatwoot header triplet at all -- so trusting x_chatwoot_uid here
        # would let anyone holding the shared secret forge
        # `x-chatwoot-uid: <team leader's id>` and have the audit row
        # implicate that person. It only becomes trustworthy once RBAC has
        # actually authenticated it: on the `if settings.rbac_enabled`
        # branch above, require_permission resolved this exact header
        # triplet against Chatwoot's own /profile endpoint
        # (features/authz/identity.py::TokenValidator.resolve_user_id)
        # before granting `routing.reassign` -- so reaching here with
        # rbac_enabled True means x_chatwoot_uid is Chatwoot-confirmed, not
        # merely client-claimed. Otherwise: an honest, clearly
        # non-personal label -- never blank, never a guess.
        actor = x_chatwoot_uid if (settings.rbac_enabled and x_chatwoot_uid) else "api-key"
        if audit is not None:
            remark = f"Supervisor reassignment to agent {agent_id}"
            if warning is not None:
                remark = f"{remark} ({warning})"
            try:
                await audit.append(
                    AuditEntry(
                        ticket_id=str(conversation_id),
                        session_id=f"chatwoot-conv-{conversation_id}",
                        actor=actor,
                        from_state="",
                        to_state=f"assigned:{agent_id}",
                        at=datetime.now(UTC).isoformat(),
                        remark=remark,
                    )
                )
            except Exception:
                # Review finding 2: assigner.assign() above has already
                # succeeded by this point -- the reassignment is a real,
                # completed side effect. FirestoreAuditLog.append does not
                # swallow its own exceptions, so an unguarded call here
                # would turn a transient Firestore blip into an HTTP 500 on
                # a request whose work is already done; a supervisor
                # retrying on that 500 would double-assign. Every
                # audit/alert leg elsewhere in this codebase is
                # independent and best-effort (see e.g.
                # agent/app/services/sync.py's escalation/notification
                # helpers), so this one follows suit: log loudly -- a
                # silently-dropped audit row is its own defect -- but never
                # fail a request whose side effect already landed.
                _log.warning(
                    "routing_reassign_audit_failed",
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    exc_info=True,
                )

        result: dict[str, Any] = {"assigned_agent_id": agent_id}
        if warning is not None:
            result["warning"] = warning
        return result

    @router.post("/routing/assign", dependencies=[Depends(auth)])
    async def assign_conversation(
        body: _AssignIn,
        x_api_key: str | None = Header(default=None),
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> dict:
        # Review finding 1: an explicit agent_id is a human supervisor
        # naming who does the work, not the engine choosing. routing_enabled
        # gates automatic agent SELECTION (the auto-pick branch below) --
        # this endpoint's own spec wording for the agent_id-supplied path is
        # "bypassing selection", and bypassing selection means bypassing the
        # engine, not asking the engine's on/off switch for permission.
        # routing_enabled defaults off and P6 deliberately keeps it off, so
        # gating this branch on it too would mean reassignment can never be
        # used by any tenant -- checked here, first, so it can never be
        # short-circuited by the `disabled` early return below.
        #
        # Do NOT "tidy" this by hoisting the `if not settings.routing_enabled`
        # check back above this branch -- that reads like a simplification
        # but silently kills the feature again. The early return below MUST
        # stay exactly as-is for the auto-pick (agent_id-absent) path: that
        # byte-identical `disabled: True` response is what the package's
        # "routing_enabled remains default-off" constraint protects, and the
        # live handoff flow depends on this exact shape when routing is off.
        if body.agent_id is not None:
            return await _reassign(
                body.conversation_id,
                body.agent_id,
                x_api_key=x_api_key,
                x_chatwoot_access_token=x_chatwoot_access_token,
                x_chatwoot_client=x_chatwoot_client,
                x_chatwoot_uid=x_chatwoot_uid,
            )

        if not settings.routing_enabled:
            return {"assigned_agent_id": None, "disabled": True}

        channel = await assigner.resolve_channel(body.conversation_id)
        agent_id = await routing_svc.pick_agent(channel)
        if agent_id is not None:
            await assigner.assign(body.conversation_id, agent_id)
        return {"assigned_agent_id": agent_id, "channel": channel}

    return router
