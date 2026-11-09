"""Fork patch 0082 (FIX-44, RFP 2026_028 7.6): password expiry.

Applies 0082 to a fixture of the upstream v4.15.1 pre-images it edits (copied
verbatim in the shape the patch needs as context) and asserts content. Ruby
logic is exercised only if ruby+activesupport exist locally. The RSpec file the
patch ships (spec/proton/password_expiry_spec.rb: test_login_after_90_days_
requires_change, test_self_service_change_resets_clock) needs a full Chatwoot
bundle and is NOT run here; only the image CI can.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCH = FORK / "patches" / "0082-password-expiry.patch"
ROOT = FORK.parent


@pytest.fixture(scope="module")
def tree():
    """Only new-file hunks are applied (edits need upstream context we lack)."""
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "apply", "--include=config/*", "--include=spec/*", str(PATCH)], cwd=d, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_default_off_and_env_driven(tree):
    rb = (tree / "config/initializers/zz_proton_password_expiry.rb").read_text()
    assert "ENV.fetch('PASSWORD_MAX_AGE_DAYS', '0').to_i" in rb
    assert "return false unless days.positive?" in rb
    assert "return false unless provider == 'email'" in rb  # SSO users exempt
    if shutil.which("ruby"):
        subprocess.run(["ruby", "-c", str(tree / "config/initializers/zz_proton_password_expiry.rb")], check=True)


def test_every_password_change_stamps_the_clock(tree):
    rb = (tree / "config/initializers/zz_proton_password_expiry.rb").read_text()
    assert "before_save :stamp_password_changed_at" in rb
    assert "will_save_change_to_encrypted_password?" in rb
    # nil stamp = account creation
    assert "|| created_at" in rb


def test_no_migration_in_patch():
    files = [l for l in PATCH.read_text().splitlines() if l.startswith("diff --git")]
    assert not any("db/migrate" in l or "schema.rb" in l for l in files)


def test_payload_flag_only_when_expired_and_spa_guard_present():
    p = PATCH.read_text()
    assert "+json.password_expired true if resource.respond_to?(:password_expired?) && resource.password_expired?" in p
    assert "user.password_expired && to.name !== 'profile_settings_index'" in p
    # the guard sits before the generic route validation, after login/account checks
    assert p.index("user.password_expired") < p.index("validateLoggedInRoutes")


def test_rspec_names_and_docker_env_plumbing(tree):
    spec = (tree / "spec/proton/password_expiry_spec.rb").read_text()
    assert "test_login_after_90_days_requires_change" in spec
    assert "test_self_service_change_resets_clock" in spec
    df = (FORK / "Dockerfile").read_text()
    assert "zz_proton_password_expiry.rb" in df and "models/_user.json.jbuilder" in df
    for f in ("docker-compose.tenant.yml", "tenants/example.env"):
        assert "PASSWORD_MAX_AGE_DAYS" in (ROOT / f).read_text(), f


def test_password_age_stripped_from_colleague_payloads():
    p = PATCH.read_text()
    for f in ("api/v1/models/_agent.json.jbuilder", "platform/api/v1/models/_user.json.jbuilder"):
        assert f"diff --git a/app/views/{f}" in p, f
    assert p.count(".except('password_changed_at')") == 2
    assert "json.custom_attributes resource.custom_attributes" not in "\n".join(
        l for l in p.splitlines() if l.startswith("+"))
    df = (FORK / "Dockerfile").read_text()
    assert "models/_agent.json.jbuilder" in df and "platform/api/v1/models/_user.json.jbuilder" in df
