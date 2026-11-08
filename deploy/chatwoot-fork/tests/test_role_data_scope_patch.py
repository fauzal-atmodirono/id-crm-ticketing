"""Fork patch 0084 (Wave 2 task 28, FIX-48, RFP 2026_028 4.83): per-role data
scope editor + reports forwarding the session. Content-level checks on the
patch's added/removed lines; the chain-apply against upstream v4.15.1 is run
by the SDD task."""
from pathlib import Path

PATCH = Path(__file__).resolve().parent.parent / "patches" / "0084-role-data-scope-editor.patch"
LINES = PATCH.read_text().split("\n")
ADDED = "\n".join(l[1:] for l in LINES if l.startswith("+") and not l.startswith("+++"))
REMOVED = [l for l in LINES if l.startswith("-") and not l.startswith("---")]


def test_editor_is_mounted_on_the_roles_page():
    assert "import ProtonRoleScopeEditor from" in ADDED
    assert "components: { ProtonRoleScopeEditor }" in ADDED
    assert '<ProtonRoleScopeEditor :key="selectedRoleId" :role-id="selectedRoleId" />' in ADDED


def test_editor_talks_to_the_backend_scope_route():
    # backend: features/authz/router.py GET/PUT /authz/roles/{role_id}/scope
    assert "/authz/roles/${encodeURIComponent(roleId)}/scope" in ADDED
    assert "method: 'PUT'" in ADDED
    for dim in ("inbox_ids", "team_ids", "dealer_slugs", "departments"):
        assert dim in ADDED, dim


def test_reports_forward_the_chatwoot_session():
    assert "import Auth from './auth';" in ADDED
    assert "'x-chatwoot-access-token': authData['access-token']" in ADDED


def test_roles_page_edit_is_additions_only():
    # Only protonMetrics.js may lose lines (the old fetch block is rewritten);
    # 0059's page must be additions only so later edits to it stay safe.
    page_section = PATCH.read_text().split("ProtonRolesPermissionsPage.vue", 1)[1]
    assert not [l for l in page_section.split("\n") if l.startswith("-") and not l.startswith("---")]


def test_no_raw_html_is_injected():
    assert "v-html" not in ADDED
