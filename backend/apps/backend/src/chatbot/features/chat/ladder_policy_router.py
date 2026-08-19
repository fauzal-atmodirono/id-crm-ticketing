"""Escalation-ladder admin API — the switches, the timers, and what is in flight.

Gated behind `escalation.manage`, the same permission as the Escalation
Routing page it lives on: configuring which dealer roles exist and how long
the ladder waits before reaching the next one is one job, not two.

Three endpoints, and the split matters:

* `GET  /admin/escalation/ladder`          what is stored AND what is in
  force, so the page can show "inherited from env" distinctly from "set here";
* `PUT  /admin/escalation/ladder`          write the row (nulls included, so
  clearing a field means "go back to inheriting");
* `GET  /admin/escalation/ladder/in-flight` read-only ladder state per case.

The in-flight list is built by `escalation_ladder.describe_in_flight`, the
same module that runs the sweep, rather than by re-deriving state here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.escalation_ladder import describe_in_flight
from chatbot.features.chat.ladder_policy_db import LadderPolicyValues
from chatbot.features.metrics.sync import fetch_conversations

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.ladder_policy_repository import LadderPolicyRepository
    from chatbot.features.chat.pic_store import DealerStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# A rung cannot be due before the escalation that started it, and a year of
# working hours is not a timer anybody meant to set. Bounds are here rather
# than in the store so a bad value is refused at the edge with a 422 the page
# can show, instead of silently becoming a ladder that never fires.
_MAX_DELAY_WORKING_HOURS = 2000.0
_MIN_SCAN_SECONDS = 30
_MAX_SCAN_SECONDS = 86_400


class LadderPolicyBody(BaseModel):
    enabled: bool | None = None
    dry_run: bool | None = None
    scan_interval_seconds: int | None = Field(
        default=None, ge=_MIN_SCAN_SECONDS, le=_MAX_SCAN_SECONDS
    )
    step2_hours: float | None = Field(default=None, ge=0, le=_MAX_DELAY_WORKING_HOURS)
    step3_hours: float | None = Field(default=None, ge=0, le=_MAX_DELAY_WORKING_HOURS)
    step4_hours: float | None = Field(default=None, ge=0, le=_MAX_DELAY_WORKING_HOURS)
    step5_hours: float | None = Field(default=None, ge=0, le=_MAX_DELAY_WORKING_HOURS)

    def to_values(self) -> LadderPolicyValues:
        return LadderPolicyValues(**self.model_dump())


def _stored_dict(values: LadderPolicyValues) -> dict[str, Any]:
    return {
        "enabled": values.enabled,
        "dry_run": values.dry_run,
        "scan_interval_seconds": values.scan_interval_seconds,
        "step2_hours": values.step2_hours,
        "step3_hours": values.step3_hours,
        "step4_hours": values.step4_hours,
        "step5_hours": values.step5_hours,
    }


def build_ladder_policy_router(
    repo: LadderPolicyRepository,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
    dealer_store: DealerStore | None = None,
    fetch: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/admin/escalation", tags=["escalation-admin"])
    manage = require_permission(
        "escalation.manage", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("/ladder", dependencies=[Depends(manage)])
    async def get_ladder() -> dict[str, Any]:
        stored = await repo.get()
        effective = await repo.resolve(settings)
        return {
            "stored": _stored_dict(stored),
            "effective": {
                "enabled": effective.enabled,
                "dry_run": effective.dry_run,
                "scan_interval_seconds": effective.scan_interval_seconds,
                "delay_overrides": effective.delay_overrides,
                "from_store": effective.from_store,
            },
            # So the page can label a field "inherited from deployment" rather
            # than leaving an operator to guess where a number came from.
            "env_defaults": {
                "enabled": bool(getattr(settings, "escalation_policy_enabled", False)),
                "dry_run": bool(getattr(settings, "escalation_policy_dry_run", True)),
                "scan_interval_seconds": int(
                    getattr(settings, "escalation_policy_scan_interval_seconds", 300)
                ),
            },
        }

    @router.put("/ladder", dependencies=[Depends(manage)])
    async def put_ladder(body: LadderPolicyBody) -> dict[str, Any]:
        await repo.upsert(body.to_values())
        effective = await repo.resolve(settings)
        _log.info(
            "ladder_policy_updated",
            enabled=effective.enabled,
            dry_run=effective.dry_run,
            overrides=effective.delay_overrides,
        )
        return {"status": "ok", "effective": {
            "enabled": effective.enabled,
            "dry_run": effective.dry_run,
            "scan_interval_seconds": effective.scan_interval_seconds,
            "delay_overrides": effective.delay_overrides,
        }}

    @router.get("/ladder/in-flight", dependencies=[Depends(manage)])
    async def in_flight() -> dict[str, Any]:
        try:
            conversations = (fetch or fetch_conversations)(settings)
        except Exception as exc:
            # Chatwoot being unreachable is a reason to show an empty panel
            # with a note, not to 500 the settings page it sits on.
            _log.warning("ladder_in_flight_fetch_failed", error=str(exc))
            return {"cases": [], "error": "Could not reach Chatwoot to list escalated cases."}

        rows = await describe_in_flight(
            conversations, settings=settings, dealer_store=dealer_store, policy_repo=repo
        )
        return {"cases": rows}

    return router
