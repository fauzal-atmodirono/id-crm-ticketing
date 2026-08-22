from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.tenant_config.custom_features import (
    CustomFeatureStoreUnavailable,
)
from chatbot.features.tenant_config.custom_features_router import (
    build_custom_features_router,
)
from chatbot.platform.config import Settings


class _FakeDocStore:
    def __init__(self, document: dict | None = None) -> None:
        self.document = dict(document or {})

    async def get_document(self) -> dict:
        return dict(self.document)

    async def get_all(self) -> dict[str, bool]:
        return dict(self.document.get("features") or {})

    async def set(self, key: str, enabled: bool) -> None:
        self.document.setdefault("features", {})[key] = enabled

    async def set_terms(self, profile, overrides) -> None:
        block = self.document.setdefault("terms", {})
        if profile is not None:
            block["profile"] = profile
        if overrides is not None:
            block["overrides"] = overrides


class _FakeValidator:
    def __init__(self, identity):
        self._identity = identity

    async def resolve_identity(self, *_args):
        return self._identity


def _client(store, identity) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_custom_features_router(store, _FakeValidator(identity), Settings())
    )
    return TestClient(app)


_SESSION = {
    "x-chatwoot-access-token": "tok",
    "x-chatwoot-client": "cli",
    "x-chatwoot-uid": "uid@x",
}


def test_unconfigured_tenant_reports_no_features() -> None:
    res = _client(_FakeDocStore(), (9, False)).get("/admin/custom-features", headers=_SESSION)
    assert res.status_code == 200
    assert res.json()["features"] == []


def test_read_reports_enabled_keys_and_superadmin_flag() -> None:
    store = _FakeDocStore({"features": {"knowledge": True, "cases": False}})
    res = _client(store, (1, False)).get("/admin/custom-features", headers=_SESSION)
    body = res.json()
    assert body["features"] == ["knowledge"]
    assert body["is_superadmin"] is True


def test_read_hides_the_registry_from_a_non_superadmin() -> None:
    """A tenant admin must not be able to enumerate which surfaces exist but
    are switched off — that is a product roadmap, and an upsell surface we
    deliberately do not put inside the customer's console."""
    res = _client(_FakeDocStore(), (9, False)).get("/admin/custom-features", headers=_SESSION)
    body = res.json()
    assert body["is_superadmin"] is False
    assert body["registry"] == []
    assert body["behavior"] == {}


def test_read_exposes_the_registry_to_a_superadmin() -> None:
    res = _client(_FakeDocStore(), (7, True)).get("/admin/custom-features", headers=_SESSION)
    body = res.json()
    assert len(body["registry"]) == 24
    assert body["registry"][0]["key"]
    assert body["registry"][0]["label"]
    assert "behavior_routing" in body["behavior"]


def test_write_is_refused_for_a_non_superadmin() -> None:
    res = _client(_FakeDocStore(), (9, False)).post(
        "/admin/custom-features", json={"key": "knowledge", "enabled": True}, headers=_SESSION
    )
    assert res.status_code == 403


def test_write_toggles_a_registered_key() -> None:
    store = _FakeDocStore()
    res = _client(store, (1, False)).post(
        "/admin/custom-features", json={"key": "knowledge", "enabled": True}, headers=_SESSION
    )
    assert res.status_code == 200
    assert store.document == {"features": {"knowledge": True}}


def test_write_rejects_an_unregistered_key_with_400() -> None:
    res = _client(_FakeDocStore(), (1, False)).post(
        "/admin/custom-features", json={"key": "nope", "enabled": True}, headers=_SESSION
    )
    assert res.status_code == 400


def test_write_rejects_a_behavior_key_with_409() -> None:
    """A real key that is simply not writable yet — distinct from one that
    does not exist, so the operator can tell "not yet" from "typo"."""
    res = _client(_FakeDocStore(), (1, False)).post(
        "/admin/custom-features",
        json={"key": "behavior_routing", "enabled": True},
        headers=_SESSION,
    )
    assert res.status_code == 409


def test_read_401s_without_a_session() -> None:
    res = _client(_FakeDocStore(), (1, False)).get("/admin/custom-features")
    assert res.status_code == 401


def test_a_read_that_could_not_reach_the_store_reports_503_not_200() -> None:
    """The read-path counterpart of the write-path test below. A 200 with an
    empty `features` list is indistinguishable from "this tenant has nothing
    switched on" -- that used to be the response for BOTH a real empty store
    and an unreachable one, which permanently blanked a live tenant's CRM on
    a Firestore blip with no error and no self-heal. A 503 here is what makes
    the SPA's adminRequest() throw and hit useCustomFeatures.js's existing
    `.catch()` retry path instead of its success path."""

    class _BrokenStore(_FakeDocStore):
        async def get_document(self) -> dict:
            raise CustomFeatureStoreUnavailable("firestore unavailable")

    res = _client(_BrokenStore(), (1, False)).get(
        "/admin/custom-features", headers=_SESSION
    )
    assert res.status_code == 503


def test_a_write_that_did_not_persist_reports_503_not_200() -> None:
    """A 200 on a dropped write tells the superadmin the tenant's product
    changed when it did not. They would go looking for the bug in the SPA."""

    class _BrokenStore(_FakeDocStore):
        async def set(self, key: str, enabled: bool) -> None:
            raise RuntimeError("firestore unavailable")

    res = _client(_BrokenStore(), (1, False)).post(
        "/admin/custom-features", json={"key": "knowledge", "enabled": True}, headers=_SESSION
    )
    assert res.status_code == 503


def test_unconfigured_tenant_gets_automotive_terms() -> None:
    """Proton's situation. Nothing stored, nothing in env — it must read
    Dealer, not Partner."""
    res = _client(_FakeDocStore(), (9, False)).get("/admin/custom-features", headers=_SESSION)
    body = res.json()
    assert body["term_profile"] == "automotive"
    assert body["terms"]["partner"]["singular"] == "Dealer"


def test_stored_generic_profile_is_served() -> None:
    store = _FakeDocStore({"terms": {"profile": "generic"}})
    body = _client(store, (9, False)).get("/admin/custom-features", headers=_SESSION).json()
    assert body["term_profile"] == "generic"
    assert body["terms"]["partner"]["singular"] == "Partner"
    assert body["terms"]["field_incident"]["singular"] == "Field Incident"


def test_overrides_are_applied_to_the_served_terms() -> None:
    store = _FakeDocStore(
        {"terms": {"profile": "generic", "overrides": {"partner": {"singular": "Branch"}}}}
    )
    body = _client(store, (9, False)).get("/admin/custom-features", headers=_SESSION).json()
    assert body["terms"]["partner"]["singular"] == "Branch"


def test_terms_are_served_to_a_non_superadmin() -> None:
    """Unlike the registry, vocabulary is not privileged — every agent's UI
    needs it to render at all."""
    body = _client(_FakeDocStore(), (9, False)).get(
        "/admin/custom-features", headers=_SESSION
    ).json()
    assert body["terms"]
    assert body["is_superadmin"] is False


def test_superadmin_sees_the_selectable_profiles() -> None:
    body = _client(_FakeDocStore(), (1, False)).get(
        "/admin/custom-features", headers=_SESSION
    ).json()
    assert sorted(body["term_profiles"]) == ["automotive", "generic"]


def test_setting_the_profile_requires_superadmin() -> None:
    res = _client(_FakeDocStore(), (9, False)).post(
        "/admin/custom-features/terms", json={"profile": "generic"}, headers=_SESSION
    )
    assert res.status_code == 403


def test_superadmin_sets_the_profile() -> None:
    store = _FakeDocStore()
    res = _client(store, (1, False)).post(
        "/admin/custom-features/terms", json={"profile": "generic"}, headers=_SESSION
    )
    assert res.status_code == 200
    assert store.document["terms"]["profile"] == "generic"


def test_unknown_profile_is_rejected() -> None:
    res = _client(_FakeDocStore(), (1, False)).post(
        "/admin/custom-features/terms", json={"profile": "banking"}, headers=_SESSION
    )
    assert res.status_code == 400


def test_override_of_an_unknown_noun_is_rejected() -> None:
    """The list is closed. Accepting arbitrary keys is how a capped dictionary
    becomes an uncapped one."""
    res = _client(_FakeDocStore(), (1, False)).post(
        "/admin/custom-features/terms",
        json={"profile": "generic", "overrides": {"nonsense": {"singular": "X"}}},
        headers=_SESSION,
    )
    assert res.status_code == 400
