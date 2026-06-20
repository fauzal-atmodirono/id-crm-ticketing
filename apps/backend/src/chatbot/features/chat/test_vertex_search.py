from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chatbot.features.chat.adapters.vertex_search import VertexAISearchAdapter
from chatbot.platform.config import Settings


@pytest.mark.asyncio
async def test_vertex_ai_search_adapter_success() -> None:
    settings = Settings(
        vertex_search_project_id="test-project",
        vertex_search_location="global",
        vertex_search_data_store_id="test-ds",
        vertex_search_engine_id="test-engine",
    )

    adapter = VertexAISearchAdapter(settings)

    # Mock result and search service client
    mock_search_result = MagicMock()
    mock_search_result.document.id = "doc-1"
    mock_search_result.document.derived_struct_data = {
        "title": "Proton X70",
        "link": "https://www.proton.com/models/x70",
        "snippets": [{"snippet": "The Proton X70 is a premium SUV."}],
    }

    mock_response = MagicMock()
    mock_response.results = [mock_search_result]

    with patch("google.cloud.discoveryengine_v1beta.SearchServiceClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.search.return_value = mock_response
        mock_client_cls.return_value = mock_client

        results = await adapter.search_kb("X70 spec", limit=1)

        assert len(results) == 1
        assert results[0].title == "Proton X70"
        assert results[0].url == "https://www.proton.com/models/x70"
        assert results[0].content == "The Proton X70 is a premium SUV."

        mock_client.search.assert_called_once()


@pytest.mark.asyncio
async def test_vertex_ai_search_adapter_failure_returns_empty() -> None:
    settings = Settings(
        vertex_search_project_id="test-project",
        vertex_search_location="global",
        vertex_search_data_store_id="test-ds",
        vertex_search_engine_id="test-engine",
    )

    adapter = VertexAISearchAdapter(settings)

    with patch("google.cloud.discoveryengine_v1beta.SearchServiceClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.search.side_effect = Exception("API connection error")
        mock_client_cls.return_value = mock_client

        results = await adapter.search_kb("X70 spec", limit=1)

        assert results == []


@pytest.mark.asyncio
async def test_search_kb_populates_enriched_fields() -> None:
    settings = Settings(
        vertex_search_project_id="test-project",
        vertex_search_location="global",
        vertex_search_data_store_id="test-ds",
        vertex_search_engine_id="test-engine",
    )

    adapter = VertexAISearchAdapter(settings)

    mock_doc = MagicMock()
    mock_doc.id = "models_x50"
    mock_doc.struct_data = {
        "title": "PROTON All-New X50",
        "link": "https://www.proton.com/models/all-new-x50",
        "source_type": "model",
        "price": "RM 89,800",
        "image_urls": ["https://img/x50.jpg"],
        "brochure_url": "https://img/x50.pdf",
        "body_excerpt": "Compact SUV.",
    }
    mock_doc.derived_struct_data = {}

    mock_result = MagicMock()
    mock_result.document = mock_doc

    mock_response = MagicMock()
    mock_response.results = [mock_result]

    with patch("google.cloud.discoveryengine_v1beta.SearchServiceClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.search.return_value = mock_response
        mock_client_cls.return_value = mock_client

        articles = await adapter.search_kb("x50", limit=1)

        assert len(articles) == 1
        art = articles[0]
        assert art.price == "RM 89,800"
        assert art.source_type == "model"
        assert art.image_urls == ["https://img/x50.jpg"]
        assert art.brochure_url == "https://img/x50.pdf"


class _RepeatedComposite:
    """Mimics Vertex's protobuf repeated field: iterable but NOT a list."""

    def __init__(self, items: list[str]) -> None:
        self._items = items

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._items)


@pytest.mark.asyncio
async def test_search_kb_handles_repeated_composite_image_urls() -> None:
    """Vertex returns repeated fields as RepeatedComposite, not list — the
    adapter must still extract image URLs (regression for the carousel)."""
    settings = Settings(
        vertex_search_project_id="test-project",
        vertex_search_location="global",
        vertex_search_data_store_id="test-ds",
        vertex_search_engine_id="test-engine",
    )
    adapter = VertexAISearchAdapter(settings)

    mock_doc = MagicMock()
    mock_doc.id = "models_x50"
    mock_doc.struct_data = {
        "title": "PROTON All-New X50",
        "link": "https://www.proton.com/models/all-new-x50",
        "source_type": "model",
        "price": "RM 89,800",
        "image_urls": _RepeatedComposite(
            ["https://img/x50-a.jpg", "https://img/x50-b.jpg"]
        ),
        "body_excerpt": "Compact SUV.",
    }
    mock_doc.derived_struct_data = {}
    mock_result = MagicMock()
    mock_result.document = mock_doc
    mock_response = MagicMock()
    mock_response.results = [mock_result]

    with patch("google.cloud.discoveryengine_v1beta.SearchServiceClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.search.return_value = mock_response
        mock_client_cls.return_value = mock_client

        articles = await adapter.search_kb("x50", limit=1)

        assert articles[0].image_urls == ["https://img/x50-a.jpg", "https://img/x50-b.jpg"]
