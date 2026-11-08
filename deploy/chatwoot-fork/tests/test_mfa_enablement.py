"""FIX-25 (RFP 2026_028 7.3): MFA enablement is config-only.

Upstream v4.15.1 renders the two-factor card in the PROFILE page
(profile/Index.vue -> MfaSettingsCard, route profile_settings_mfa) whenever
Chatwoot.mfa_enabled? is true, i.e. the three ActiveRecord encryption env vars
are set. Patch 0032 only removes the unrelated sidebar entry for the
enterprise Security (SAML/SSO) settings page, so it never hid MFA and needs no
change. These tests pin that: 0032 stays a single sidebar removal, no patch in
the series touches the MFA UI/gating, and the compose/env wiring is in place.
Cannot prove the live login flow; that needs the image and an enrolled user.
"""
import re
from pathlib import Path

FORK = Path(__file__).resolve().parent.parent
ROOT = FORK.parent.parent
PATCHES = sorted((FORK / "patches").glob("*.patch"))
P0032 = FORK / "patches" / "0032-hide-security-settings-nav.patch"
KEYS = (
    "ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY",
    "ACTIVE_RECORD_ENCRYPTION_DETERMINISTIC_KEY",
    "ACTIVE_RECORD_ENCRYPTION_KEY_DERIVATION_SALT",
)


def test_0032_only_removes_the_security_sidebar_entry():
    text = P0032.read_text()
    files = re.findall(r"^diff --git a/(\S+)", text, re.M)
    assert files == ["app/javascript/dashboard/components-next/sidebar/Sidebar.vue"]
    removed = [l for l in text.splitlines() if l.startswith("-") and not l.startswith("---")]
    assert any("security_settings_index" in l for l in removed)
    assert not any(re.search(r"mfa|two.?factor|profile", l, re.I) for l in removed)
    assert not [l for l in text.splitlines() if l.startswith("+") and not l.startswith("+++")]


def test_no_patch_hides_or_gates_the_mfa_ui():
    for p in PATCHES:
        removed = [
            l for l in p.read_text().splitlines()
            if l.startswith("-") and not l.startswith("---")
        ]
        for l in removed:
            assert not re.search(
                r"MfaSettingsCard|isMfaEnabled|profile_settings_mfa|MfaVerification", l
            ), f"{p.name} removes MFA code: {l}"


def test_compose_passes_all_three_keys_to_the_chatwoot_env_anchor():
    text = (ROOT / "deploy/docker-compose.tenant.yml").read_text()
    anchor = text.split("x-chatwoot-env: &chatwoot-env", 1)[1].split("\nnetworks:", 1)[0]
    for k in KEYS:
        assert f"  {k}: ${{{k}:-}}" in anchor


def test_example_env_documents_keys_with_no_values():
    text = (ROOT / "deploy/tenants/example.env").read_text()
    for k in KEYS:
        assert re.search(rf"^{k}=$", text, re.M), k
    assert "db:encryption:init" in text
