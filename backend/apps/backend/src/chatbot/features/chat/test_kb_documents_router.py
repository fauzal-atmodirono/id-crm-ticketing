from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.kb_documents_router import build_kb_documents_router
from chatbot.platform.config import Settings

_BRANCH = (
    "projects/p/locations/global/collections/default_collection"
    "/dataStores/proton-kb/branches/default_branch"
)


class _FakeDoc:
    """Stand-in for a Discovery Engine Document (struct_data as plain dicts)."""

    def __init__(self, doc_id, struct_data=None, derived_struct_data=None):
        self.id = doc_id
        self.struct_data = struct_data or {}
        self.derived_struct_data = derived_struct_data or {}


def _client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.include_router(build_kb_documents_router(settings))
    return TestClient(app, raise_server_exceptions=False)


def _settings(**kw) -> Settings:
    base = {
        "faq_admin_api_key": "fk",
        "proton_backend_key": "pk",
        "vertex_search_project_id": "test-project",
        "vertex_search_location": "global",
        "vertex_search_data_store_id": "proton-kb",
    }
    base.update(kw)
    return Settings(**base)


# --- auth ---------------------------------------------------------------


def test_missing_key_rejected() -> None:
    c = _client(_settings(vertex_search_project_id=""))
    assert c.get("/kb/documents").status_code == 401


def test_wrong_key_rejected() -> None:
    c = _client(_settings(vertex_search_project_id=""))
    assert c.get("/kb/documents", headers={"x-api-key": "nope"}).status_code == 401


def test_proton_key_accepted() -> None:
    c = _client(_settings(vertex_search_project_id=""))
    r = c.get("/kb/documents", headers={"x-api-key": "pk"})
    assert r.status_code == 200
    assert r.json() == {"documents": []}


def test_faq_key_accepted() -> None:
    c = _client(_settings(vertex_search_project_id=""))
    assert c.get("/kb/documents", headers={"x-api-key": "fk"}).status_code == 200


# --- listing / mapping --------------------------------------------------


def test_unconfigured_project_skips_client() -> None:
    c = _client(_settings(vertex_search_project_id=""))
    with patch("google.cloud.discoveryengine_v1beta.DocumentServiceClient") as cls:
        r = c.get("/kb/documents", headers={"x-api-key": "pk"})
    assert r.status_code == 200
    assert r.json() == {"documents": []}
    cls.assert_not_called()


def test_lists_documents_from_struct_data() -> None:
    c = _client(_settings())
    docs = [
        _FakeDoc(
            "doc-1",
            struct_data={
                "title": "Warranty",
                "link": "https://proton.com/warranty",
                "body_excerpt": "The battery is covered for 8 years.",
            },
        )
    ]
    with patch("google.cloud.discoveryengine_v1beta.DocumentServiceClient") as cls:
        client = MagicMock()
        client.branch_path.return_value = _BRANCH
        client.list_documents.return_value = docs
        cls.return_value = client
        r = c.get("/kb/documents", headers={"x-api-key": "pk"})

    assert r.status_code == 200
    assert r.json() == {
        "documents": [
            {
                "id": "doc-1",
                "title": "Warranty",
                "uri": "https://proton.com/warranty",
                "snippet": "The battery is covered for 8 years.",
            }
        ]
    }
    client.list_documents.assert_called_once()


def test_lists_documents_from_derived_data() -> None:
    c = _client(_settings())
    docs = [
        _FakeDoc(
            "doc-2",
            derived_struct_data={
                "title": "Proton X70",
                "link": "https://proton.com/x70",
                "snippets": [{"snippet": "Premium SUV."}],
            },
        )
    ]
    with patch("google.cloud.discoveryengine_v1beta.DocumentServiceClient") as cls:
        client = MagicMock()
        client.branch_path.return_value = _BRANCH
        client.list_documents.return_value = docs
        cls.return_value = client
        r = c.get("/kb/documents", headers={"x-api-key": "pk"})

    assert r.status_code == 200
    doc = r.json()["documents"][0]
    assert doc == {
        "id": "doc-2",
        "title": "Proton X70",
        "uri": "https://proton.com/x70",
        "snippet": "Premium SUV.",
    }


def test_document_without_metadata_falls_back_to_id() -> None:
    c = _client(_settings())
    with patch("google.cloud.discoveryengine_v1beta.DocumentServiceClient") as cls:
        client = MagicMock()
        client.branch_path.return_value = _BRANCH
        client.list_documents.return_value = [_FakeDoc("bare-id")]
        cls.return_value = client
        r = c.get("/kb/documents", headers={"x-api-key": "pk"})

    assert r.json()["documents"][0] == {
        "id": "bare-id",
        "title": "bare-id",
        "uri": "",
        "snippet": "",
    }


def test_vertex_failure_returns_empty_not_500() -> None:
    c = _client(_settings())
    with patch("google.cloud.discoveryengine_v1beta.DocumentServiceClient") as cls:
        client = MagicMock()
        client.branch_path.return_value = _BRANCH
        client.list_documents.side_effect = RuntimeError("boom")
        cls.return_value = client
        r = c.get("/kb/documents", headers={"x-api-key": "pk"})

    assert r.status_code == 200
    assert r.json() == {"documents": []}
