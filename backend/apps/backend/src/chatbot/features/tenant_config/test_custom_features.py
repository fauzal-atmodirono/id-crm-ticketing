from __future__ import annotations

import pytest

from chatbot.features.tenant_config.custom_features import (
    BEHAVIOR_FLAGS,
    CUSTOM_FEATURE_REGISTRY,
    enabled_features,
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
