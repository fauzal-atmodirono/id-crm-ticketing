from __future__ import annotations

import pytest

from chatbot.features.tenant_config.custom_features import (
    BEHAVIOR_FLAGS,
    CUSTOM_FEATURE_REGISTRY,
    CustomFeatureStore,
    CustomFeatureStoreUnavailable,
    enabled_features,
    stored_terms,
)
from chatbot.features.authz.seed import PERMISSION_REGISTRY


def test_an_unwritten_store_yields_no_features() -> None:
    """The whole point: a tenant nobody has configured opens as a blank CRM.
    "Starts empty" is a property of the data model, not a value someone has
    to remember to set."""
    assert enabled_features({}) == []


def test_only_registered_and_true_keys_are_enabled() -> None:
    stored = {"knowledge": True, "cases": False, "not_a_real_feature": True}
    assert enabled_features(stored) == ["knowledge"]


def test_registry_covers_every_expected_surface() -> None:
    assert len(CUSTOM_FEATURE_REGISTRY) == 24
    assert all(f.kind == "surface" for f in CUSTOM_FEATURE_REGISTRY.values())
    for key in ("knowledge", "cases", "workforce", "customer360", "roles_permissions"):
        assert key in CUSTOM_FEATURE_REGISTRY


def test_every_paired_permission_actually_exists() -> None:
    """A typo here is a page that no role can ever reach, and nothing else in
    the system would report it."""
    for feature in CUSTOM_FEATURE_REGISTRY.values():
        if feature.permission is not None:
            assert feature.permission in PERMISSION_REGISTRY, feature.key


def test_behavior_flags_name_real_settings_fields() -> None:
    from chatbot.platform.config import Settings

    for key, attr in BEHAVIOR_FLAGS.items():
        assert hasattr(Settings(), attr), f"{key} -> {attr}"


def test_surface_and_behavior_keys_do_not_collide() -> None:
    assert not (set(CUSTOM_FEATURE_REGISTRY) & set(BEHAVIOR_FLAGS))


class _BrokenDocRef:
    def get(self):  # pragma: no cover - executed via asyncio.to_thread
        raise RuntimeError("firestore unreachable")


async def test_get_all_raises_rather_than_returning_empty_on_a_store_failure() -> None:
    """A genuinely unreachable store must not look like a real, empty
    document. Both used to come back as `{}`, which made an outage
    indistinguishable from "this tenant has nothing switched on" -- the
    router turned that into a 200 the composable treated as success, and a
    Firestore blip permanently blanked a live tenant's CRM for the rest of
    the page session with no retry. Raising lets the router answer 503
    instead, only for the actual outage case."""
    from chatbot.platform.config import Settings

    store = CustomFeatureStore(Settings())
    store._doc_ref = _BrokenDocRef  # type: ignore[method-assign]

    with pytest.raises(CustomFeatureStoreUnavailable):
        await store.get_all()


def test_stored_terms_of_an_empty_document_is_unset() -> None:
    """Unset, NOT "generic" — the caller must be able to tell "nobody chose"
    from "somebody chose generic", because those resolve differently."""
    assert stored_terms({}) == (None, {})


def test_stored_terms_reads_profile_and_overrides() -> None:
    doc = {"terms": {"profile": "generic", "overrides": {"partner": {"singular": "Branch"}}}}
    profile, overrides = stored_terms(doc)
    assert profile == "generic"
    assert overrides == {"partner": {"singular": "Branch"}}


def test_stored_terms_tolerates_a_malformed_terms_block() -> None:
    assert stored_terms({"terms": "nonsense"}) == (None, {})


def test_features_and_terms_share_one_document() -> None:
    doc = {"features": {"knowledge": True}, "terms": {"profile": "generic"}}
    assert enabled_features(doc.get("features") or {}) == ["knowledge"]
    assert stored_terms(doc)[0] == "generic"


class _RecordingDocRef:
    """Captures the exact positional/keyword args `set_terms` hands the
    Firestore SDK, so the merge=True guarantee is a tripwire in CI rather than
    something a reviewer has to re-derive by reading the client's internals."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    def set(self, *args, **kwargs):  # pragma: no cover - executed via asyncio.to_thread
        self.calls.append((args, kwargs))


async def test_set_terms_merge_writes_only_the_terms_block() -> None:
    """A bare `.set()` on this document would drop the sibling `features`
    map -- the tenant's entire switchboard state. `merge=True` is what stops
    a vocabulary write from clobbering it, and the payload must contain
    exactly the fields given, nothing about `features`."""
    from chatbot.platform.config import Settings

    store = CustomFeatureStore(Settings())
    doc_ref = _RecordingDocRef()
    store._doc_ref = lambda: doc_ref  # type: ignore[method-assign]

    await store.set_terms("generic", {"partner": {"singular": "Branch"}})

    assert len(doc_ref.calls) == 1
    args, kwargs = doc_ref.calls[0]
    assert args == ({"terms": {"profile": "generic", "overrides": {"partner": {"singular": "Branch"}}}},)
    assert kwargs == {"merge": True}


async def test_set_terms_omits_a_field_that_was_not_given() -> None:
    """Setting only the profile must not write an `overrides` key at all --
    a `None` overrides here means "leave it alone", not "clear it"."""
    from chatbot.platform.config import Settings

    store = CustomFeatureStore(Settings())
    doc_ref = _RecordingDocRef()
    store._doc_ref = lambda: doc_ref  # type: ignore[method-assign]

    await store.set_terms("automotive", None)

    args, kwargs = doc_ref.calls[0]
    assert args == ({"terms": {"profile": "automotive"}},)
    assert kwargs == {"merge": True}


async def test_set_terms_with_an_empty_dict_overrides_clears_the_stored_block() -> None:
    """`overrides=None` (tested above) and `overrides={}` are NOT the same
    input. `None` omits the field and leaves the stored block alone; an
    explicit `{}` is a deliberate clear, because Firestore's `merge=True`
    replaces the whole `terms.overrides` leaf with whatever is given here.
    Pinning both so a later edit cannot quietly collapse the distinction by
    defaulting the missing case to `{}`."""
    from chatbot.platform.config import Settings

    store = CustomFeatureStore(Settings())
    doc_ref = _RecordingDocRef()
    store._doc_ref = lambda: doc_ref  # type: ignore[method-assign]

    await store.set_terms(None, {})

    args, kwargs = doc_ref.calls[0]
    assert args == ({"terms": {"overrides": {}}},)
    assert kwargs == {"merge": True}
