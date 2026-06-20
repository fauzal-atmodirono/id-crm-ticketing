from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from google.cloud import discoveryengine_v1beta as discoveryengine

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.ports import KnowledgePort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class VertexAISearchAdapter(KnowledgePort):
    """Adapter for searching Vertex AI Search (Discovery Engine) unstructured website data store."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # The full resource name of the search engine branch
        self._serving_config_path = f"projects/{settings.vertex_search_project_id}/locations/{settings.vertex_search_location}/collections/default_collection/engines/{settings.vertex_search_engine_id}/servingConfigs/default_search"

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        """Search the Vertex AI Search engine for pages matching the query.

        Args:
            query: The search term or question.
            limit: Maximum search results to return.
        """
        _log.info("searching_vertex_ai_search", query=query, limit=limit)
        
        try:
            # Note: client init can be cached if needed, but creating it is lightweight.
            # We don't make async calls inside Google clients directly unless using an async stub, 
            # so we run it synchronously (or in a threadpool if it blocks the loop). 
            # FastAPI's route handler is async, but calling synchronous library methods works fine 
            # for low concurrency. To prevent blocking the main event loop, we can run it in a separate thread.
            import asyncio
            
            def run_search() -> list[KbArticle]:
                client = discoveryengine.SearchServiceClient()

                request = discoveryengine.SearchRequest(
                    serving_config=self._serving_config_path,
                    query=query,
                    page_size=limit,
                )

                response = client.search(request=request)
                articles = []

                for result in response.results:
                    doc = result.document
                    # We populated `struct_data` at ingest time with the real
                    # title + canonical URL. Vertex's `derived_struct_data`
                    # overrides `link` with the GCS URI, which is useless to
                    # the agent — prefer ours, fall back to Vertex's view.
                    struct_data = dict(doc.struct_data) if doc.struct_data else {}
                    derived_data = (
                        dict(doc.derived_struct_data) if doc.derived_struct_data else {}
                    )

                    title = (
                        struct_data.get("title")
                        or derived_data.get("title")
                        or doc.id
                        or "PROTON Page"
                    )
                    link = struct_data.get("link") or derived_data.get("link") or ""

                    # Snippets + extractive segments require Enterprise edition
                    # on the engine — this project is Standard tier. We pre-
                    # extracted a ~1.2 kB body excerpt at ingest time and
                    # stashed it in struct_data so the agent has real content
                    # to ground its reply in.
                    content = ""
                    excerpt = struct_data.get("body_excerpt")
                    if isinstance(excerpt, str):
                        content = excerpt
                    if not content:
                        snippets = derived_data.get("snippets", [])
                        if snippets and isinstance(snippets, list):
                            first = snippets[0]
                            if isinstance(first, dict):
                                content = first.get("snippet", "") or ""
                    if not content:
                        content = derived_data.get("snippet", "") or ""
                    if not content:
                        content = f"See: {title}"

                    # Vertex returns repeated fields as a protobuf
                    # RepeatedComposite (not a list), so accept any non-string
                    # iterable rather than checking `isinstance(..., list)`.
                    raw_images = struct_data.get("image_urls")
                    image_urls: list[str] = []
                    if raw_images is not None and not isinstance(raw_images, str | bytes):
                        try:
                            image_urls = [str(u) for u in raw_images]
                        except TypeError:
                            image_urls = []
                    articles.append(
                        KbArticle(
                            title=title,
                            content=content,
                            url=link,
                            source_type=(
                                str(struct_data["source_type"])
                                if struct_data.get("source_type") is not None
                                else None
                            ),
                            price=(
                                str(struct_data["price"])
                                if struct_data.get("price") is not None
                                else None
                            ),
                            image_urls=image_urls,
                            brochure_url=(
                                str(struct_data["brochure_url"])
                                if struct_data.get("brochure_url") is not None
                                else None
                            ),
                        )
                    )
                return articles

            # Execute run_search in the default event loop executor to prevent blocking the async event loop
            return await asyncio.to_thread(run_search)
            
        except Exception as e:
            _log.error("vertex_ai_search_failed", query=query, error=str(e))
            return []
