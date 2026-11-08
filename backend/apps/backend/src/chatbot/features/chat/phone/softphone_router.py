"""Endpoints the CRM's softphone panel calls: get a token, say "still here",
say "gone".

Mounted from `main.py`. Modelled on `recording_router.py` (a `build_*_router`
factory taking settings) rather than added to `ChatRouter`, which is already
~1900 lines and has no authz collaborators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from chatbot.features.authz.deps import require_permission_with_identity
from chatbot.features.chat.phone.agent_token import agent_identity, mint_agent_voice_token

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.phone.hold_transfer import HoldTransferController
    from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class CallActionRequest(BaseModel):
    """`call_sid` is the agent's OWN leg (the browser's `call.parameters.
    CallSid`). Ownership is checked server-side; see HoldTransferController.
    _authorise."""

    call_sid: str


class TransferRequest(CallActionRequest):
    target_agent_id: int
    mode: str = "cold"  # cold | warm


def build_softphone_router(
    settings: Settings,
    registry: SoftphoneRegistry,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    hold_transfer: HoldTransferController | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/voice/agent", tags=["voice-softphone"])

    identity_dep = require_permission_with_identity(
        "voice.answer", repo=repo, validator=validator, settings=settings
    )

    def _check_enabled() -> None:
        if not settings.phone_agent_softphone_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent softphone is disabled (PHONE_AGENT_SOFTPHONE_ENABLED=false)",
            )

    @router.post("/token")
    async def agent_token(agent_id: int = Depends(identity_dep)) -> dict[str, str]:
        """Mint a Voice token for the CALLER'S OWN identity.

        Note there is no request model: this endpoint deliberately reads
        nothing from the body. Any `identity` a client sends is ignored,
        because honouring it would let an authenticated agent register as a
        colleague and intercept their transferred calls.
        """
        _check_enabled()
        _log.info("softphone_token_issued", agent_id=agent_id)
        return {
            "token": mint_agent_voice_token(settings, agent_id),
            "identity": agent_identity(agent_id),
        }

    @router.post("/heartbeat")
    async def agent_heartbeat(agent_id: int = Depends(identity_dep)) -> dict[str, Any]:
        _check_enabled()
        await registry.heartbeat(agent_id)
        return {"status": "ok"}

    @router.post("/unregister")
    async def agent_unregister(agent_id: int = Depends(identity_dep)) -> dict[str, Any]:
        _check_enabled()
        await registry.unregister(agent_id)
        return {"status": "ok"}

    # --- FIX-15: hold / resume / transfer -------------------------------
    # Same per-agent identity gate as the token: the agent acting must be the
    # agent on the leg, so the shared-secret path is refused here too.

    def _hold_transfer() -> HoldTransferController:
        _check_enabled()
        if hold_transfer is None or not settings.phone_hold_transfer_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Hold/transfer is disabled (PHONE_HOLD_TRANSFER_ENABLED=false)",
            )
        return hold_transfer

    async def _run(coro: Any) -> dict[str, Any]:
        from chatbot.features.chat.phone.hold_transfer import HoldTransferError  # noqa: PLC0415

        try:
            return await coro
        except HoldTransferError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    @router.post("/call/capabilities")
    async def call_capabilities(_agent_id: int = Depends(identity_dep)) -> dict[str, bool]:
        """Lets the panel hide Hold/Transfer on tenants without the flag."""
        _check_enabled()
        return {
            "hold_transfer": bool(
                hold_transfer is not None and settings.phone_hold_transfer_enabled
            )
        }

    @router.post("/call/hold")
    async def call_hold(
        body: CallActionRequest, agent_id: int = Depends(identity_dep)
    ) -> dict[str, Any]:
        return await _run(_hold_transfer().hold(agent_id, body.call_sid))

    @router.post("/call/resume")
    async def call_resume(
        body: CallActionRequest, agent_id: int = Depends(identity_dep)
    ) -> dict[str, Any]:
        return await _run(_hold_transfer().resume(agent_id, body.call_sid))

    @router.post("/call/transfer-targets")
    async def call_transfer_targets(agent_id: int = Depends(identity_dep)) -> dict[str, Any]:
        return {"agents": await _hold_transfer().transfer_targets(agent_id)}

    @router.post("/call/transfer")
    async def call_transfer(
        body: TransferRequest, agent_id: int = Depends(identity_dep)
    ) -> dict[str, Any]:
        return await _run(
            _hold_transfer().transfer(agent_id, body.call_sid, body.target_agent_id, body.mode)
        )

    return router
