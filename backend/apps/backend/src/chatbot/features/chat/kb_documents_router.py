"""Read-only listing of the tenant's Vertex AI Search data store documents.

`GET /kb/documents` returns the documents indexed in the Discovery Engine data
store (the bulk KB that grounds Suggest/Copilot), so agents and admins can see
the grounding corpus from inside the CRM. Guarded by the same `x-api-key` the
FAQ admin router uses (constant-time against `faq_admin_api_key` /
`proton_backend_key`). It never raises for the "can't reach Vertex" case —
it returns an empty list, mirroring the read-only search adapter — so a mis-set
or unreachable data store degrades to an empty page instead of a 500.
"""

from __future__ import annotations

import asyncio
import hmac
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException
from google.cloud import discoveryengine_v1beta as discoveryengine

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Cap the listing so a large data store can't produce an unbounded response.
_MAX_DOCUMENTS = 200


def _document_dict(doc: Any) -> dict[str, str]:
    """Map a Discovery Engine Document to ``{id, title, uri, snippet}``.

    Field placement varies by how the store was ingested: our JSONL import puts
    the real title/link in ``struct_data``, while a website crawl leaves them in
    ``derived_struct_data``. We check both, with the same precedence the search
    adapter (``vertex_search.py``) uses.
    """
    struct_data = dict(doc.struct_data) if getattr(doc, "struct_data", None) else {}
    derived_data = (
        dict(doc.derived_struct_data) if getattr(doc, "derived_struct_data", None) else {}
    )

    title = (
        struct_data.get("title")
        or derived_data.get("title")
        or getattr(doc, "id", "")
        or "Document"
    )
    uri = struct_data.get("link") or derived_data.get("link") or ""

    snippet = ""
    excerpt = struct_data.get("body_excerpt")
    if isinstance(excerpt, str):
        snippet = excerpt
    if not snippet:
        snippets = derived_data.get("snippets", [])
        if isinstance(snippets, list) and snippets and isinstance(snippets[0], dict):
            snippet = snippets[0].get("snippet", "") or ""
    if not snippet:
        snippet = derived_data.get("snippet", "") or ""

    return {
        "id": str(getattr(doc, "id", "") or ""),
        "title": str(title),
        "uri": str(uri),
        "snippet": str(snippet),
    }


def build_kb_documents_router(settings: Settings) -> APIRouter:
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

    def _list_documents() -> list[dict[str, str]]:
        # Unconfigured data store → nothing to list (don't hit Vertex/ADC).
        if not settings.vertex_search_project_id:
            return []
        client = discoveryengine.DocumentServiceClient()
        parent = client.branch_path(
            settings.vertex_search_project_id,
            settings.vertex_search_location,
            settings.vertex_search_data_store_id,
            "default_branch",
        )
        request = discoveryengine.ListDocumentsRequest(parent=parent, page_size=100)
        documents: list[dict[str, str]] = []
        # The pager transparently walks pages; cap the total we materialize.
        for doc in client.list_documents(request=request):
            documents.append(_document_dict(doc))
            if len(documents) >= _MAX_DOCUMENTS:
                break
        return documents

    @router.get("/kb/documents")
    async def list_documents(
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        try:
            # Discovery Engine's client is sync; keep it off the event loop.
            documents = await asyncio.to_thread(_list_documents)
        except Exception as exc:
            _log.error("kb_documents_list_failed", error=str(exc))
            documents = []
        return {"documents": documents}

    return router
