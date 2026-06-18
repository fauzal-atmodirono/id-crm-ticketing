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
