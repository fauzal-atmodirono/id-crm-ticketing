"""Fork patches 0088-0090: fixes from the final whole-branch review of the
Proton demo-fix branch (I3, I4, I5, M4-M7). Content-level checks on each
patch's added/removed lines; the whole-chain apply against upstream v4.15.1
is run separately (see the fix report)."""
from pathlib import Path

PATCHES = Path(__file__).resolve().parent.parent / "patches"
COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.tenant.yml"


def _patch(prefix):
    hits = sorted(PATCHES.glob(f"{prefix}-*.patch"))
    assert len(hits) == 1, f"expected one patch {prefix}-*.patch, got {hits}"
    return hits[0].read_text()


def _section(text, name):
    return text.split(f"diff --git a/{name}", 1)[1].split("\ndiff --git", 1)[0]


def _added(text):
    return "\n".join(l[1:] for l in text.split("\n") if l.startswith("+") and not l.startswith("+++"))


def _post(text):
    """The post-image lines of a hunk section (context + added)."""
    return "\n".join(l[1:] for l in text.split("\n") if l[:1] in ("+", " "))


def _removed(text):
    return "\n".join(l[1:] for l in text.split("\n") if l.startswith("-") and not l.startswith("---"))


# ── 0088: PII masking follows PII_MASKING_ENABLED (I3, ruling R26) ─────────

def test_0088_layout_emits_the_flag_from_env():
    s = _section(_patch("0088"), "app/views/layouts/vueapp.html.erb")
    added = _added(s)
    assert "window.__PROTON_PII_MASKING__ = <%=" in added
    assert "ENV.fetch('PII_MASKING_ENABLED', '')" in added
    # same truthy set as patch 0058's flag mechanism (pydantic bool parsing)
    assert "%w[1 true t yes y on]" in added


def test_0088_flag_off_never_asks_and_never_masks():
    s = _section(_patch("0088"), "app/javascript/dashboard/composables/useProtonPiiMask.js")
    added, removed = _added(s), _removed(s)
    assert "window.__PROTON_PII_MASKING__ === true" in added
    assert "const masked = ref(true); // pending = masked" in removed
    assert "const masked = ref(piiFlagOn());" in added
    guard = _post(s).split("function ensureLoaded() {", 1)[1].split("adminRequest(", 1)[0]
    assert "if (!piiFlagOn()) {" in guard
    assert "masked.value = false;" in guard
    assert "return;" in guard


def test_0088_request_has_a_5s_abort_timeout():
    s = _section(_patch("0088"), "app/javascript/dashboard/composables/useProtonPiiMask.js")
    added = _added(s)
    assert "new AbortController()" in added
    assert "REQUEST_TIMEOUT_MS = 5000" in added
    assert "adminRequest('/authz/me/pii', { signal: ctrl.signal })" in added
    assert "clearTimeout(timeout)" in added


def test_0088_admin_request_forwards_an_optional_signal():
    s = _section(_patch("0088"), "app/javascript/dashboard/api/protonAdmin.js")
    added = _added(s)
    assert "{ method = 'GET', body, signal } = {}" in added
    assert "...(signal ? { signal } : {})" in added


def test_compose_forwards_pii_masking_to_rails():
    head = COMPOSE.read_text().split("\nservices:", 1)[0]
    assert "PII_MASKING_ENABLED: ${PII_MASKING_ENABLED:-false}" in head


# ── 0089: report auth + "not available" on a Wave 1 backend (I4, I5, M4) ──

def test_0089_weekly_download_sends_the_session_triplet_via_shared_helper():
    p = _patch("0089")
    metrics = _added(_section(p, "app/javascript/dashboard/api/protonMetrics.js"))
    assert "export function protonBackendHeaders()" in metrics
    assert "'x-chatwoot-access-token': authData['access-token']" in metrics
    assert "headers: protonBackendHeaders()," in metrics
    weekly = _section(p, "app/javascript/dashboard/routes/dashboard/settings/reports/ProtonWeeklyReport.vue")
    assert "{ headers: backendKey ? { 'x-api-key': backendKey } : {} }" in _removed(weekly)
    assert "{ headers: protonBackendHeaders() }" in _added(weekly)
    assert "protonBackendHeaders" in _added(weekly).split("<script", 1)[-1]


def test_0089_ai_performance_hides_on_404():
    s = _section(
        _patch("0089"),
        "app/javascript/dashboard/routes/dashboard/settings/reports/components/proton/ProtonAiPerformanceSection.vue",
    )
    added = _added(s)
    assert "e?.status === 404" in added
    assert "notAvailable.value = true;" in added
    assert '<div v-if="!notAvailable" class="flex flex-col gap-6">' in added


def test_0089_role_scope_card_hides_on_404():
    s = _section(_patch("0089"), "app/javascript/dashboard/components/widgets/ProtonRoleScopeEditor.vue")
    added = _added(s)
    assert "e?.status === 404" in added
    assert "this.notAvailable = true;" in added
    assert "notAvailable: false," in added
    assert 'v-if="!notAvailable"' in added


def test_0089_picker_chips_are_not_inside_a_label():
    s = _section(_patch("0089"), "app/javascript/dashboard/components/widgets/ProtonRoleScopeEditor.vue")
    removed, added = _removed(s), _added(s)
    # the Inboxes and Teams pickers: <label> wrapping <button> chips -> <div>
    assert removed.count('<label class="flex flex-col gap-1">') == 2
    assert removed.count("</label>") == 2
    assert added.count('<div class="flex flex-col gap-1">') == 2


# ── 0090: small UI fixes (M5, M6, M7) ─────────────────────────────────────

def test_0090_tamil_message_only_for_tamil_target():
    s = _section(_patch("0090"), "app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue")
    added = _added(s)
    assert "target === 'ta' &&" in added


def test_0090_ai_activity_alerts_once_per_failure_streak():
    s = _section(_patch("0090"), "app/javascript/dashboard/views/ProtonAiActivityPage.vue")
    added = _added(s)
    assert "const firstFailure = !this.errored;" in added
    assert "if (firstFailure) {" in added
    # errored is cleared only on success, so a streak alerts once
    assert "this.errored = false;" in _removed(s)


def test_0090_idle_signout_runs_upstream_logout_cleanup():
    s = _section(_patch("0090"), "app/javascript/dashboard/composables/useProtonIdleTimeout.js")
    added = _added(s)
    assert "from 'dashboard/store/utils/api'" in added
    assert "deleteIndexedDBOnLogout()" in added
    assert "clearCookiesOnLogout()" in added
    # our redirect still wins (upstream's goes to LOGOUT_REDIRECT_LINK or /)
    tail = _post(s).split("clearCookiesOnLogout();", 1)[1]
    assert "window.location.href = '/app/login';" in tail
