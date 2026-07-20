"""Per-inbox assistant + mode assignment router.

GET  /kb/inboxes           — list all Chatwoot inboxes joined with their
                             effective assignments (stored override or tenant
                             defaults). Never raises: Chatwoot down → stored-
                             only rows; Firestore down → Chatwoot list with
                             default assignments.

PUT  /kb/inboxes/{inbox_id} — set {assistant_id, mode} for an inbox. Validates
                              that the assistant exists (404 if not) and that mode
                              ∈ {off, suggest, auto} (422 if not). Sending an
                              empty/null body field clears the assignment (reverts
                              the inbox to tenant defaults).

Auth: x-api-key (same constant-time check as the FAQ admin router).
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.chat.inbox_resolver import effective_assignment

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.assistants_store import AssistantsStorePort
    from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
    from chatbot.features.chat.adapters.inbox_assignment_store import InboxAssignmentStorePort
    from chatbot.features.chat.adapters.tenant_settings_store import TenantSettingsStorePort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_VALID_MODES = frozenset({"off", "suggest", "auto"})


class InboxAssignmentBody(BaseModel):
    """Request body for PUT /kb/inboxes/{inbox_id}.

    Both fields are optional: omitting / nulling assistant_id or mode triggers
    a revert (the entire assignment is cleared so the inbox falls back to tenant
    defaults). If both are present, both are validated and the assignment is
    stored.
    """

    assistant_id: str | None = None
    mode: str | None = None


async def _resolve_assistant_name(assistants_store: AssistantsStorePort, assistant_id: str) -> str:
    """Return the assistant name for the given id, or '' on any failure."""
    try:
        asst = await assistants_store.get(assistant_id)
        return asst.name if asst is not None else ""
    except Exception as exc:
        _log.debug("kb_inboxes_assistant_name_lookup_failed", assistant_id=assistant_id, error=str(exc))
        return ""


def build_kb_inboxes_router(
    assignment_store: InboxAssignmentStorePort,
    assistants_store: AssistantsStorePort,
    tenant_settings_store: TenantSettingsStorePort,
    chatwoot_adapter: ChatwootAdapter | None,
    settings: Settings,
) -> APIRouter:
    """Build the /kb/inboxes router.

    `chatwoot_adapter` may be None (e.g. when Chatwoot is not enabled) — the GET
    endpoint degrades to returning stored assignments only without an inbox name/
    channel_type.
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

    @router.get("/kb/inboxes")
    async def list_inboxes(
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Return all inboxes with their effective assignments.

        Fetches the Chatwoot inbox list (degrades to [] on any error) and the
        stored assignment overrides, then joins them. Inboxes with a stored
        override get source="override"; others get source="default" with the
        tenant default assistant + default_mode.
        """
        _authorize(x_api_key)

        # Fetch Chatwoot inboxes (best-effort; [] on any failure).
        chatwoot_inboxes: list[dict[str, Any]] = []
        if chatwoot_adapter is not None:
            try:
                chatwoot_inboxes = await chatwoot_adapter.list_inboxes()
            except Exception as exc:
                _log.warning("kb_inboxes_chatwoot_list_failed", error=str(exc))

        # Fetch stored assignments.
        try:
            stored = await assignment_store.get_all()
        except Exception as exc:
            _log.error("kb_inboxes_assignment_store_failed", error=str(exc))
            stored = {}

        # Build a set of inbox_ids already covered by Chatwoot list.
        chatwoot_ids: set[int] = {inbox["id"] for inbox in chatwoot_inboxes}

        # Build result rows: start from Chatwoot inboxes, then append stored-only
        # assignments not present in the Chatwoot list (covers Chatwoot-down case).
        rows: list[dict[str, Any]] = []

        for inbox in chatwoot_inboxes:
            inbox_id = inbox["id"]
            eff = await effective_assignment(
                assignment_store, assistants_store, tenant_settings_store, settings, inbox_id
            )
            name = await _resolve_assistant_name(assistants_store, eff["assistant_id"])
            rows.append(
                {
                    "inbox_id": inbox_id,
                    "name": inbox.get("name", ""),
                    "channel_type": inbox.get("channel_type", ""),
                    "assistant_id": eff["assistant_id"],
                    "assistant_name": name,
                    "mode": eff["mode"],
                    "source": eff["source"],
                }
            )

        # Stored assignments for inboxes not in the Chatwoot list (degrade path).
        for inbox_id, entry in stored.items():
            if inbox_id in chatwoot_ids:
                continue
            name = await _resolve_assistant_name(assistants_store, entry["assistant_id"])
            rows.append(
                {
                    "inbox_id": inbox_id,
                    "name": "",
                    "channel_type": "",
                    "assistant_id": entry["assistant_id"],
                    "assistant_name": name,
                    "mode": entry["mode"],
                    "source": "override",
                }
            )

        return {"inboxes": rows}

    @router.put("/kb/inboxes/{inbox_id}")
    async def set_inbox_assignment(
        inbox_id: int,
        body: InboxAssignmentBody,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Set or clear the assistant + mode for an inbox.

        If both assistant_id and mode are provided and valid, the assignment is
        stored. If either is None/missing, the entire assignment is cleared
        (reverts to tenant defaults). Validates:
        - assistant_id: must exist in the assistants store (404 if not found).
        - mode: must be in {off, suggest, auto} (422 if invalid).
        """
        _authorize(x_api_key)

        # Clear path: either field null/omitted → revert to defaults.
        if body.assistant_id is None or body.mode is None:
            await assignment_store.delete(inbox_id)
            return {"inbox_id": inbox_id, "cleared": True}

        # Validate mode.
        if body.mode not in _VALID_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"mode must be one of {sorted(_VALID_MODES)}, got {body.mode!r}",
            )

        # Validate assistant exists.
        assistant = await assistants_store.get(body.assistant_id)
        if assistant is None:
            raise HTTPException(
                status_code=404,
                detail=f"Assistant {body.assistant_id!r} not found",
            )

        await assignment_store.set(inbox_id, body.assistant_id, body.mode)
        return {
            "inbox_id": inbox_id,
            "assistant_id": body.assistant_id,
            "mode": body.mode,
        }

    return router
