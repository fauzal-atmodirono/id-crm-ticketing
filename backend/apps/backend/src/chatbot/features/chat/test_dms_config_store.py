"""The credential must be settable and never retrievable through any public path."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from chatbot.features.chat.dms_config_store import (
    _COLLECTION,
    _DOC_ID,
    DmsConfig,
    DmsConfigStore,
    public_dict,
)
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
def fake_client() -> _FakeFirestoreClient:
    return _FakeFirestoreClient()


@pytest.fixture
def store(fake_client: _FakeFirestoreClient):
    settings = Settings(firestore_project_id="proj", firestore_database_id="db")
    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = fake_client
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


async def test_get_credential_returns_none_when_nothing_saved(store):
    assert await store.get_credential() is None


async def test_saving_with_none_credential_and_nothing_stored_omits_the_key(
    store, fake_client: _FakeFirestoreClient
):
    """save(config, credential=None) on a store that has never held a
    credential must not invent one — the accessor returns None, and the
    underlying document must not even have a `credential` key (as opposed to
    one holding a literal None), so a later regression that writes
    `credential: null` instead of omitting the field is caught here rather
    than only by the accessor's behaviour.
    """
    await store.save(CFG, credential=None)

    assert await store.get_credential() is None

    doc = fake_client._collections[_COLLECTION][_DOC_ID]
    assert "credential" not in doc


# --- the credential never reaches a log line, even on a Firestore failure ---


class _RaisingDoc:
    """A doc ref whose every operation fails with an exception whose message
    interpolates the offending value -- exactly what google-api-core's
    InvalidArgument/FailedPrecondition do.
    """

    def __init__(self, payload_repr: str) -> None:
        self._payload_repr = payload_repr

    def get(self) -> Any:
        raise RuntimeError(f"400 Invalid argument for document: {self._payload_repr}")

    def set(self, data: dict[str, Any]) -> None:
        raise RuntimeError(f"400 Invalid argument writing: {data}")


class _RaisingCollection:
    def __init__(self, payload_repr: str) -> None:
        self._payload_repr = payload_repr

    def document(self, key: str) -> _RaisingDoc:
        return _RaisingDoc(self._payload_repr)


class _RaisingFirestoreClient:
    def __init__(self, payload_repr: str = "") -> None:
        self._payload_repr = payload_repr

    def collection(self, name: str) -> _RaisingCollection:
        return _RaisingCollection(self._payload_repr)


@pytest.fixture
def raising_store():
    settings = Settings(firestore_project_id="proj", firestore_database_id="db")
    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _RaisingFirestoreClient("super-secret-key")
        yield DmsConfigStore(settings)


async def test_save_failure_log_never_contains_the_credential(raising_store):
    """`save()` is the one function where the credential is a live local
    inside the `try`. Logging `str(e)` there would put a Firestore error's
    interpolated payload -- which includes the document being written, hence
    the credential -- straight into the log. Assert against captured records,
    since the credential never reaches a return value on this path.
    """
    with capture_logs() as captured:
        await raising_store.save(CFG, credential="super-secret-key")

    assert captured
    for record in captured:
        assert "super-secret-key" not in repr(record)
    assert captured[0]["event"] == "dms_config_store_save_failed"
    assert captured[0]["error_type"] == "RuntimeError"
    assert "error" not in captured[0]


async def test_get_and_get_credential_failure_logs_never_contain_the_payload(raising_store):
    """Same guarantee for the two read paths -- these never hold the
    credential as a local, but a Firestore error message can still echo the
    stored document back at us.
    """
    with capture_logs() as captured:
        assert await raising_store.get() is None
        assert await raising_store.get_credential() is None

    events = {record["event"] for record in captured}
    assert events == {"dms_config_store_get_failed", "dms_config_store_get_credential_failed"}
    for record in captured:
        assert "super-secret-key" not in repr(record)
        assert "error" not in record
        assert record["error_type"] == "RuntimeError"


# --- one Firestore client, and a short-TTL config cache ---------------------


async def test_only_one_firestore_client_is_built_per_store(fake_client):
    """Customer 360 reaches `get()` on every lookup. Building a
    `firestore.Client` per call meant an ADC resolution plus a fresh gRPC
    channel on a path a human is waiting on.
    """
    settings = Settings(firestore_project_id="proj", firestore_database_id="db")
    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = fake_client
        store = DmsConfigStore(settings)

        # Constructing the store must not touch Firestore at all -- main.py
        # builds it unconditionally, including on tenants with no DMS.
        assert MockClient.call_count == 0

        await store.save(CFG, credential="k")
        await store.get()
        await store.get_credential()
        await store.get()

        assert MockClient.call_count == 1


async def test_get_is_cached_and_save_invalidates_it(fake_client):
    """Assert on returned values AND on how many times Firestore was actually
    read. Values alone don't pin the cache: two `get()`s legitimately return
    `None` either because the second one hit the cache, or because caching
    doesn't exist at all and both went to Firestore and both found nothing --
    the values can't tell those apart. `read_count` can: it only stays flat
    across the second `get()` if the cache actually short-circuited it.
    """
    settings = Settings(firestore_project_id="proj", firestore_database_id="db")
    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = fake_client
        store = DmsConfigStore(settings)

        read_count = 0
        real_get = _FakeDoc.get

        def _counting_get(self: _FakeDoc) -> MagicMock:
            nonlocal read_count
            read_count += 1
            return real_get(self)

        with patch.object(_FakeDoc, "get", _counting_get):
            # "No document" is the common tenant state and must be cached too
            # -- otherwise the majority case still pays a round trip per lookup.
            assert await store.get() is None
            assert await store.get() is None
            assert read_count == 1, (
                "second get() within the TTL must be served from cache, not a second Firestore read"
            )

            await store.save(CFG, credential="k")
            # save() invalidated, so this must be a fresh read, not the cached
            # None -- read_count must go up, not just the returned value change.
            reloaded = await store.get()
            assert read_count == 2, "save() must force the next get() to re-read"

        assert reloaded is not None
        assert reloaded.base_url == CFG.base_url


async def test_a_failed_get_is_not_cached():
    """A Firestore blip must not pin "not configured" for the whole TTL --
    and, going further than the returned value alone can prove, a *recovered*
    read must itself start being served from cache again. If the whole cache
    were deleted, the failure-then-recovery values here would look identical
    (both `get()`s would just hit Firestore directly), so the assertions
    below are on `good_read_count`, not on `recovered`/`again` alone.
    """
    settings = Settings(firestore_project_id="proj", firestore_database_id="db")
    good = _FakeFirestoreClient()
    good.collection(_COLLECTION)._store[_DOC_ID] = {"enabled": True, "base_url": "https://x"}

    good_read_count = 0
    real_get = _FakeDoc.get

    def _counting_get(self: _FakeDoc) -> MagicMock:
        nonlocal good_read_count
        good_read_count += 1
        return real_get(self)

    with patch("chatbot.features.chat.dms_config_store.firestore.Client", autospec=True) as Mock:
        Mock.return_value = _RaisingFirestoreClient()
        store = DmsConfigStore(settings)
        assert await store.get() is None

        # Swap the underlying client for a healthy one; a cached failure
        # would keep returning None here.
        store._firestore_client = good  # type: ignore[assignment]
        with patch.object(_FakeDoc, "get", _counting_get):
            recovered = await store.get()
            assert good_read_count == 1, "the failed attempt must not have poisoned this read"

            # The success itself must now be cached: a second get() within the
            # TTL must NOT hit Firestore again. This is what actually
            # distinguishes "caching works" from "caching doesn't exist" --
            # deleting the cache would make this assertion fail (read_count
            # would go to 2), even though `again == recovered` either way.
            again = await store.get()
            assert good_read_count == 1, "a cached success must not trigger a second read"

    assert recovered is not None
    assert recovered.enabled is True
    assert again is recovered
