"""Per-assistant Scenario (playbook) CRUD endpoints.

Endpoints (all guarded by ``x-api-key``):
  GET    /kb/scenarios?assistant_id=…   — list all scenarios for an assistant
  POST   /kb/scenarios                  — create a scenario
  PUT    /kb/scenarios/{id}             — partial update a scenario
  DELETE /kb/scenarios/{id}             — delete a scenario

Validation rules enforced here:
- Each tool slug in ``tools`` must be a built-in name (in COPILOT_TOOLS) OR an
  existing custom-tool slug (``tools_store.get_custom``) — 422 on unknown slug.
- ``instruction`` length ≤ 4096 chars — 422 if exceeded.
- ≤ 20 scenarios per assistant — 409 if the cap would be breached on create.
- GET requires ``assistant_id`` query param — 422 if missing.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from chatbot.features.assist.copilot_tools import COPILOT_TOOLS
from chatbot.features.chat.adapters.scenarios_store import Scenario

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Built-in tool names accepted as valid tool slugs in scenarios.
_BUILTIN_NAMES: frozenset[str] = frozenset(t["name"] for t in COPILOT_TOOLS)

# Caps enforced on create.
_SCENARIOS_CAP = 20
_INSTRUCTION_MAX = 4096


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class ScenarioCreateRequest(BaseModel):
    assistant_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(default="")
    instruction: str = Field(default="")
    tools: list[str] = Field(default_factory=list)
    enabled: bool = True


class ScenarioUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    instruction: str | None = None
    tools: list[str] | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_kb_scenarios_router(  # noqa: PLR0915 — one builder, many route closures
    scenarios_store: Any,
    tools_store: Any,
    settings: Settings,
) -> APIRouter:
    """Build the /kb/scenarios router.

    *scenarios_store* implements ``ScenariosStorePort``.
    *tools_store* implements ``ToolsStorePort`` (used to validate custom-tool slugs).
    *settings* provides API key candidates for auth.
    """
    router = APIRouter(tags=["kb"])

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        candidates = [settings.faq_admin_api_key, settings.proton_backend_key]
        supplied = x_api_key.encode("utf-8")
        for key in candidates:
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
        raise HTTPException(status_code=401, detail="Unauthorized")

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    async def _validate_tools(tool_slugs: list[str]) -> None:
        """Raise 422 if any slug is neither a built-in name nor an existing custom tool."""
        for slug in tool_slugs:
            if slug in _BUILTIN_NAMES:
                continue
            # Check the custom-tool registry.
            ct = None
            if tools_store is not None:
                try:
                    ct = await tools_store.get_custom(slug)
                except Exception as exc:
                    _log.warning("kb_scenarios_tools_lookup_failed", slug=slug, error=str(exc))
            if ct is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown tool slug: {slug!r}. Must be a built-in name or an existing custom-tool slug.",
                )

    def _validate_instruction(instruction: str) -> None:
        if len(instruction) > _INSTRUCTION_MAX:
            raise HTTPException(
                status_code=422,
                detail=f"instruction length {len(instruction)} exceeds maximum of {_INSTRUCTION_MAX} characters",
            )

    # ------------------------------------------------------------------
    # GET /kb/scenarios
    # ------------------------------------------------------------------

    @router.get("/kb/scenarios")
    async def list_scenarios(
        assistant_id: str = Query(...),
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        try:
            scenarios = await scenarios_store.list_for_assistant(assistant_id)
        except Exception as exc:
            _log.error("kb_scenarios_list_failed", assistant_id=assistant_id, error=str(exc))
            scenarios = []
        return {"scenarios": [s.to_dict() for s in scenarios]}

    # ------------------------------------------------------------------
    # POST /kb/scenarios
    # ------------------------------------------------------------------

    @router.post("/kb/scenarios")
    async def create_scenario(
        payload: ScenarioCreateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)

        _validate_instruction(payload.instruction)
        await _validate_tools(payload.tools)

        # Enforce per-assistant cap.
        try:
            existing = await scenarios_store.list_for_assistant(payload.assistant_id)
        except Exception as exc:
            _log.error("kb_scenarios_cap_check_failed", error=str(exc))
            existing = []

        if len(existing) >= _SCENARIOS_CAP:
            raise HTTPException(
                status_code=409,
                detail=f"Scenario cap of {_SCENARIOS_CAP} reached for assistant {payload.assistant_id!r}",
            )

        scenario = Scenario(
            id=uuid.uuid4().hex,
            assistant_id=payload.assistant_id,
            title=payload.title,
            description=payload.description,
            instruction=payload.instruction,
            tools=list(payload.tools),
            enabled=payload.enabled,
            created_at=datetime.now(UTC).isoformat(),
        )
        scenario_id = await scenarios_store.create(scenario)
        return {"id": scenario_id}

    # ------------------------------------------------------------------
    # PUT /kb/scenarios/{id}
    # ------------------------------------------------------------------

    @router.put("/kb/scenarios/{scenario_id}")
    async def update_scenario(
        scenario_id: str,
        payload: ScenarioUpdateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)

        existing = await scenarios_store.get(scenario_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Scenario {scenario_id!r} not found")

        fields = payload.model_dump(exclude_none=True)

        if "instruction" in fields:
            _validate_instruction(fields["instruction"])
        if "tools" in fields:
            await _validate_tools(fields["tools"])

        await scenarios_store.update(scenario_id, fields)
        return {"id": scenario_id, "status": "ok"}

    # ------------------------------------------------------------------
    # DELETE /kb/scenarios/{id}
    # ------------------------------------------------------------------

    @router.delete("/kb/scenarios/{scenario_id}")
    async def delete_scenario(
        scenario_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)

        existing = await scenarios_store.get(scenario_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Scenario {scenario_id!r} not found")

        await scenarios_store.delete(scenario_id)
        return {"id": scenario_id, "status": "ok"}

    return router
