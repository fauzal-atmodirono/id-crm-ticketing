"""Tests for the live-FAQ admin CRUD router: x-api-key auth + create returns id."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.faq_admin_router import build_faq_admin_router
from chatbot.features.chat.ports import LiveFaqEntry
from chatbot.platform.config import Settings

_KEY = "secret-faq-key"


class FakeLiveFaqStore:
    def __init__(self) -> None:
        self.entries: dict[str, LiveFaqEntry] = {}
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.deletes: list[str] = []
        self._counter = 0

    async def create(self, entry: LiveFaqEntry) -> str:
        self._counter += 1
        entry_id = f"faq-{self._counter}"
        self.entries[entry_id] = LiveFaqEntry(
            id=entry_id,
            question=entry.question,
            answer=entry.answer,
            keywords=entry.keywords,
            tags=entry.tags,
            active=entry.active,
        )
        return entry_id

    async def update(self, entry_id: str, fields: dict[str, Any]) -> None:
        self.updates.append((entry_id, fields))

    async def delete(self, entry_id: str) -> None:
        self.deletes.append(entry_id)

    async def list_all(self) -> list[LiveFaqEntry]:
        return list(self.entries.values())

    async def list_active(self) -> list[LiveFaqEntry]:
        return [e for e in self.entries.values() if e.active]

    async def search(
        self, query_embedding: list[float], limit: int
    ) -> list[tuple[LiveFaqEntry, float]]:
        return []


def _client(store: FakeLiveFaqStore) -> TestClient:
    settings = Settings(faq_admin_api_key=_KEY)
    app = FastAPI()
    app.include_router(build_faq_admin_router(store, settings))
    return TestClient(app)


def test_create_requires_api_key() -> None:
    store = FakeLiveFaqStore()
    r = _client(store).post("/kb/faq", json={"question": "Q", "answer": "A"})
    assert r.status_code == 401
    assert store.entries == {}


def test_create_returns_id_with_valid_key() -> None:
    store = FakeLiveFaqStore()
    r = _client(store).post(
        "/kb/faq",
        json={"question": "How to reset?", "answer": "Hold the button.", "tags": ["howto"]},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "faq-1"
    assert store.entries["faq-1"].question == "How to reset?"
    assert store.entries["faq-1"].tags == ["howto"]


def test_update_requires_api_key() -> None:
    store = FakeLiveFaqStore()
    r = _client(store).put("/kb/faq/faq-1", json={"answer": "new"})
    assert r.status_code == 401
    assert store.updates == []


def test_update_with_valid_key_passes_only_set_fields() -> None:
    store = FakeLiveFaqStore()
    r = _client(store).put(
        "/kb/faq/faq-1",
        json={"answer": "updated answer"},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    assert store.updates == [("faq-1", {"answer": "updated answer"})]


def test_delete_requires_api_key() -> None:
    store = FakeLiveFaqStore()
    r = _client(store).delete("/kb/faq/faq-1")
    assert r.status_code == 401
    assert store.deletes == []


def test_delete_with_valid_key() -> None:
    store = FakeLiveFaqStore()
    r = _client(store).delete("/kb/faq/faq-1", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    assert store.deletes == ["faq-1"]


def test_list_requires_api_key() -> None:
    store = FakeLiveFaqStore()
    r = _client(store).get("/kb/faq")
    assert r.status_code == 401


def test_list_returns_all_entries_with_key() -> None:
    store = FakeLiveFaqStore()
    client = _client(store)
    client.post("/kb/faq", json={"question": "Q1", "answer": "A1"}, headers={"x-api-key": _KEY})
    r = client.get("/kb/faq", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["question"] == "Q1"
    assert "embedding" not in entries[0]


def test_empty_configured_key_rejects_even_matching_header() -> None:
    store = FakeLiveFaqStore()
    settings = Settings(faq_admin_api_key="")
    app = FastAPI()
    app.include_router(build_faq_admin_router(store, settings))
    client = TestClient(app)
    r = client.post("/kb/faq", json={"question": "Q", "answer": "A"}, headers={"x-api-key": ""})
    assert r.status_code == 401


def test_store_unavailable_returns_503_after_auth() -> None:
    settings = Settings(faq_admin_api_key=_KEY)
    app = FastAPI()
    app.include_router(build_faq_admin_router(None, settings))
    client = TestClient(app)
    r = client.post("/kb/faq", json={"question": "Q", "answer": "A"}, headers={"x-api-key": _KEY})
    assert r.status_code == 503


def test_bulk_create_requires_api_key() -> None:
    store = FakeLiveFaqStore()
    client = _client(store)
    csv_data = b"question,answer,keywords,tags\nWhat is X?,X is Y,,\n"
    r = client.post("/kb/faq/bulk", files={"file": ("test.csv", csv_data)})
    assert r.status_code == 401
    assert store.entries == {}


def test_bulk_create_with_valid_csv() -> None:
    store = FakeLiveFaqStore()
    client = _client(store)
    csv_data = b"question,answer,keywords,tags\nWhat is X?,X is Y,xyz;abc,tag1;tag2\nWhat is Y?,Y is Z,,tag3\n"
    r = client.post(
        "/kb/faq/bulk",
        files={"file": ("test.csv", csv_data)},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2
    assert body["errors"] == []
    assert len(store.entries) == 2
    # Check first entry
    assert store.entries["faq-1"].question == "What is X?"
    assert store.entries["faq-1"].answer == "X is Y"
    assert store.entries["faq-1"].keywords == ["xyz", "abc"]
    assert store.entries["faq-1"].tags == ["tag1", "tag2"]
    # Check second entry
    assert store.entries["faq-2"].question == "What is Y?"
    assert store.entries["faq-2"].answer == "Y is Z"
    assert store.entries["faq-2"].keywords == []
    assert store.entries["faq-2"].tags == ["tag3"]


def test_bulk_create_skips_invalid_rows() -> None:
    store = FakeLiveFaqStore()
    client = _client(store)
    csv_data = b"question,answer,keywords,tags\nWhat is X?,X is Y,,\n,Missing question,\n\nY is Z,,\nWhat is Z?,Z is W,,\n"
    r = client.post(
        "/kb/faq/bulk",
        files={"file": ("test.csv", csv_data)},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2  # rows 2 and 5 succeeded
    assert len(body["errors"]) == 2  # rows 3 and 4 failed
    assert body["errors"][0]["row"] == 3
    assert body["errors"][0]["reason"] == "question and answer are required"
    assert body["errors"][1]["row"] == 4
    assert body["errors"][1]["reason"] == "question and answer are required"
    # Only rows 2 and 5 should be in entries
    assert len(store.entries) == 2
    assert store.entries["faq-1"].question == "What is X?"
    assert store.entries["faq-2"].question == "What is Z?"


def test_bulk_create_rejects_oversized_file() -> None:
    store = FakeLiveFaqStore()
    settings = Settings(faq_admin_api_key=_KEY, kb_max_upload_bytes=10)
    app = FastAPI()
    app.include_router(build_faq_admin_router(store, settings))
    client = TestClient(app)
    csv_data = b"question,answer,keywords,tags\n" + b"X" * 100  # 100+ bytes, exceeds limit
    r = client.post(
        "/kb/faq/bulk",
        files={"file": ("test.csv", csv_data)},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 413
    assert store.entries == {}
