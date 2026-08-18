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

from chatbot.features.authz.deps import require_permission_with_identity
from chatbot.features.chat.phone.agent_token import agent_identity, mint_agent_voice_token

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def build_softphone_router(
    settings: Settings,
    registry: SoftphoneRegistry,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
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

    return router
