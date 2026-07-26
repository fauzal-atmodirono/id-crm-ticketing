from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.kb_documents_router import build_kb_documents_router
from chatbot.features.chat.kb_repository import InMemoryKbRepository
from chatbot.platform.config import Settings


class _Embedder:
    async def embed(self, text): return [1.0, 0.0]


def _client(repo):
    s = Settings(faq_admin_api_key="fk", kb_chunk_size_tokens=200, kb_chunk_overlap_tokens=20)
    app = FastAPI()
    app.include_router(build_kb_documents_router(repo, _Embedder(), s))
    return TestClient(app, raise_server_exceptions=False)


def test_requires_api_key() -> None:
    c = _client(InMemoryKbRepository())
    assert c.get("/kb/documents").status_code == 401


def test_create_text_then_list_indexed() -> None:
    repo = InMemoryKbRepository()
    c = _client(repo)
    r = c.post("/kb/documents/text",
               json={"title": "Warranty", "body": "the warranty is five years"},
               headers={"x-api-key": "fk"})
    assert r.status_code == 200
    doc_id = r.json()["id"]

    listing = c.get("/kb/documents", headers={"x-api-key": "fk"}).json()
    assert listing["documents"][0]["id"] == doc_id
    # TestClient runs the BackgroundTask before returning, so it is already indexed
    assert listing["documents"][0]["status"] == "indexed"


def test_upload_file_and_delete() -> None:
    repo = InMemoryKbRepository()
    c = _client(repo)
    r = c.post("/kb/documents/file",
               files={"file": ("notes.txt", b"hello knowledge base", "text/plain")},
               headers={"x-api-key": "fk"})
    doc_id = r.json()["id"]
    assert c.delete(f"/kb/documents/{doc_id}", headers={"x-api-key": "fk"}).status_code == 200
    assert c.get("/kb/documents", headers={"x-api-key": "fk"}).json()["documents"] == []
