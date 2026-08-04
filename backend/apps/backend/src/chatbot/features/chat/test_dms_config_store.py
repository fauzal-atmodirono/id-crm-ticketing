"""The credential must be settable and never retrievable through any public path."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chatbot.features.chat.dms_config_store import DmsConfig, DmsConfigStore, public_dict
from chatbot.platform.config import Settings

CFG = DmsConfig(
    enabled=True,
    provider_label="Proton DMS",
    base_url="https://dms.example.com",
    auth_type="api_key_header",
    extra_header_name="X-Tenant",
    extra_header_value="proton",
    timeout_seconds=10.0,
    retries=2,
)


class _FakeDoc:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store = store
        self._key = key

    def get(self) -> MagicMock:
        snap = MagicMock()
        data = self._store.get(self._key)
        snap.exists = data is not None
        snap.to_dict.return_value = data or {}
        return snap

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._key] = data

    def delete(self) -> None:
        self._store.pop(self._key, None)


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> _FakeDoc:
        return _FakeDoc(self._store, key)

    def stream(self) -> list[MagicMock]:
        snaps = []
        for data in self._store.values():
            snap = MagicMock()
            snap.to_dict.return_value = data
            snaps.append(snap)
        return snaps


class _FakeFirestoreClient:
    """In-memory stand-in for google.cloud.firestore.Client, keyed by
    collection name. Same double used by test_pic_admin_router.py.
    """

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))


@pytest.fixture
def store():
    settings = Settings(firestore_project_id="proj", firestore_database_id="db")
    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()
        yield DmsConfigStore(settings)


def test_public_dict_has_no_credential_key_at_all():
    d = public_dict(CFG)
    assert "credential" not in d
    assert "api_key" not in d
    assert "secret" not in d


def test_public_dict_does_not_contain_the_secret_value_anywhere():
    d = public_dict(CFG)
    assert "super-secret-key" not in repr(d)


async def test_saved_credential_is_retrievable_only_through_the_private_accessor(store):
    await store.save(CFG, credential="super-secret-key")
    assert await store.get_credential() == "super-secret-key"
    assert "super-secret-key" not in repr(public_dict(await store.get()))


async def test_saving_with_none_credential_preserves_the_existing_one(store):
    await store.save(CFG, credential="super-secret-key")
    await store.save(CFG, credential=None)
    assert await store.get_credential() == "super-secret-key"


async def test_config_absent_returns_none(store):
    assert await store.get() is None
