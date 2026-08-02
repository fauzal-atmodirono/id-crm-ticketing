# chatwoot-config/test_provision_features.py
from provision_features import DISABLE, ENABLE


def test_enterprise_sla_audit_roles_flags_are_disabled():
    for flag in ("sla", "audit_logs", "custom_roles"):
        assert flag in DISABLE, f"{flag} must be in DISABLE — Phase 2 replaces it with our own surface"
        assert flag not in ENABLE


def test_disable_branding_still_enabled():
    assert "disable_branding" in ENABLE
