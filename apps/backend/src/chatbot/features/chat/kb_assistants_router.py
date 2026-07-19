"""CRUD router for the multi-assistant Knowledge console.

`GET /kb/assistants`, `POST /kb/assistants`, `GET /kb/assistants/{id}`,
`PUT /kb/assistants/{id}`, `DELETE /kb/assistants/{id}`.

Auth: `x-api-key` compared constant-time against `faq_admin_api_key` or
`proton_backend_key` (same pattern as `faq_admin_router` and
`kb_documents_router`).

Never raises on reads (get/list degrade to 404/[] rather than 500).
`DELETE` on the default assistant returns 409; on a non-existent id returns 404.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.assistants_store import AssistantsStorePort
    from chatbot.features.chat.adapters.inbox_assignment_store import InboxAssignmentStorePort
    from chatbot.features.chat.adapters.scenarios_store import ScenariosStorePort
    from chatbot.platform.config import Settings


class _CreateRequest(BaseModel):
    name: str
    description: str = ""
    product_name: str = ""


class _UpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    product_name: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    config: dict[str, Any] | None = None


def build_kb_assistants_router(
    store: AssistantsStorePort,
    settings: Settings,
    scenarios_store: ScenariosStorePort | None = None,
    assignment_store: InboxAssignmentStorePort | None = None,
) -> APIRouter:
    """Build and return the /kb/assistants APIRouter.

    `store` is injected (InMemory in tests, Firestore in prod) so the router
    has no knowledge of persistence details.

    `scenarios_store` and `assignment_store` are optional; when provided, a
    DELETE call cascades and removes all scenarios and inbox assignments that
    reference the deleted assistant so no orphan data lingers.
    """
    router = APIRouter(tags=["kb"])

    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        candidates = [settings.faq_admin_api_key, settings.proton_backend_key]
        supplied = x_api_key.encode("utf-8")
        for key in candidates:
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
        raise HTTPException(status_code=401, detail="Unauthorized")

    @router.get("/kb/assistants")
    async def list_assistants(
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        # Trigger default-seed on first call so the store is never empty.
        await store.get_default()
        assistants = await store.list_all()
        return {"assistants": [a.to_dict() for a in assistants]}

    @router.post("/kb/assistants")
    async def create_assistant(
        payload: _CreateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        from chatbot.features.chat.adapters.assistants_store import (  # noqa: PLC0415
            Assistant,
            AssistantConfig,
            _new_id,
            _now,
        )

        assistant = Assistant(
            id=_new_id(),
            name=payload.name,
            description=payload.description,
            product_name=payload.product_name,
            config=AssistantConfig(),
            enabled=True,
            is_default=False,
            created_at=_now(),
        )
        aid = await store.create(assistant)
        return {"id": aid}

    @router.get("/kb/assistants/{assistant_id}")
    async def get_assistant(
        assistant_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        assistant = await store.get(assistant_id)
        if assistant is None:
            raise HTTPException(status_code=404, detail="Assistant not found")
        return assistant.to_dict()

    @router.put("/kb/assistants/{assistant_id}")
    async def update_assistant(
        assistant_id: str,
        payload: _UpdateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        existing = await store.get(assistant_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Assistant not found")
        fields = payload.model_dump(exclude_none=True)
        await store.update(assistant_id, fields)
        return {"id": assistant_id, "status": "ok"}

    @router.delete("/kb/assistants/{assistant_id}")
    async def delete_assistant(
        assistant_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        try:
            await store.delete(assistant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Assistant not found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail="Cannot delete the default assistant"
            ) from exc
        # Cascade: remove all scenarios and inbox assignments referencing this assistant.
        if scenarios_store is not None:
            await scenarios_store.delete_for_assistant(assistant_id)
        if assignment_store is not None:
            await assignment_store.delete_for_assistant(assistant_id)
        return {"id": assistant_id, "status": "ok"}

    return router
