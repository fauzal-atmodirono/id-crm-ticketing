"""Fork patch 0101 (16 Nov 2026): Escalation Routing rebuilt as four tabs with
side drawers, a Needs attention list and the ladder as an Off / Dry run / Live
timeline -- the design approved on the claude.ai canvas "Escalation Routing
Refresh". Frontend only: every request, body and permission stays as patches
0039-0076 left them. Cannot prove the vite build (only Cloud Build does); the
helper logic is run for real under Node when Node is installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
P0101 = FORK / "patches" / "0101-escalation-routing-refresh.patch"
VIEWS = "app/javascript/dashboard/views"
PAGE = f"{VIEWS}/ProtonEscalationRoutingPage.vue"
DIR = f"{VIEWS}/escalationRouting"
NEW_FILES = {
    f"{DIR}/{n}"
    for n in (
        "routingHelpers.js",
        "RecordDrawer.vue",
        "EmailChips.vue",
        "LadderStatusBanner.vue",
        "OverviewTab.vue",
        "DepartmentsTab.vue",
        "PicDrawer.vue",
        "DealersTab.vue",
        "DealerDrawer.vue",
        "LadderTab.vue",
    )
}


def _files() -> list[str]:
    return [l.split(" b/", 1)[1] for l in P0101.read_text().splitlines() if l.startswith("diff --git ")]


def _added(path: str) -> str:
    out, cur = [], None
    for line in P0101.read_text().splitlines():
        if line.startswith("diff --git "):
            cur = line.split(" b/", 1)[1]
        elif cur == path and line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


def test_touches_only_the_page_and_its_new_folder():
    assert sorted(_files()) == sorted({PAGE, *NEW_FILES})


def _post_chain(path: str, tmp: Path) -> str:
    """The file as the whole patch chain leaves it (fork-created files only)."""
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    for patch in sorted((FORK / "patches").glob("*.patch")):
        if f"b/{path}" in patch.read_text(errors="ignore"):
            subprocess.run(["git", "apply", f"--include={path}", str(patch)], cwd=tmp, check=True)
    return (tmp / path).read_text()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_shell_keeps_name_tabs_in_the_url_and_every_endpoint(tmp_path):
    page = _post_chain(PAGE, tmp_path)
    assert "name: 'ProtonEscalationRoutingPage'," in page
    assert "const TABS = ['overview', 'departments', 'dealers', 'ladder'];" in page
    assert "this.$route?.query?.tab" in page and "this.$router.replace({ query:" in page
    assert "listPics(), listDealers()" in page
    assert "adminRequest(LADDER_PATH)" in page and "adminRequest(`${LADDER_PATH}/in-flight`)" in page
    assert "adminRequest('/authz/me/scope')" in page
    # dealer-scoped = enforcement on AND at least one dealer slug; fail-open
    assert "Boolean(data?.enabled) && Array.isArray(slugs) && slugs.length > 0" in page
    assert ':can-add="!dealerScoped"' in page
    # saves reload in place, not through the page-wide Loading state
    assert page.count('@saved="refresh"') == 2


def test_no_inline_table_editing_anywhere():
    for path in (f"{DIR}/DepartmentsTab.vue", f"{DIR}/DealersTab.vue"):
        body = _added(path)
        tbody = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        assert "<input" not in tbody, path


def test_pic_drawer_same_api_and_body_and_works_on_a_copy():
    d = _added(f"{DIR}/PicDrawer.vue")
    assert "import { upsertPic, deletePic } from 'dashboard/api/protonAdmin';" in d
    assert "cc_emails: splitEmails(form.value.cc_emails)" in d
    assert "form.value = p\n      ? {" in d  # the drawer edits a fresh object, never the row
    assert "Delete the PIC for" in d and "window.confirm" in d


def test_dealer_drawer_same_body_and_role_only_dealer_is_valid():
    d = _added(f"{DIR}/DealerDrawer.vue")
    assert "upsertDealer(form.value.dealer.trim(), {" in d
    assert "contacts: contactsFrom(form.value)," in d and "region: form.value.region," in d
    assert "form.value.emails.length > 0 || Object.keys(contactsFrom(form.value)).length > 0" in d
    for section in ("Basics", "Members", "Ladder contacts"):
        assert section in d


def test_ladder_saves_the_same_body_through_mode_to_flags_and_num_or_null():
    d = _added(f"{DIR}/LadderTab.vue")
    assert "method: 'PUT'" in d and "enabled: flags.enabled," in d and "dry_run: flags.dry_run," in d
    for key in ("scan_interval_seconds", "step2_hours", "step3_hours", "step4_hours", "step5_hours"):
        assert f"{key}: numOrNull(local.value.{key})" in d
    assert "Reminders will be emailed to real" in d
    assert "{ value: 'off', label: 'Off'" in d and "{ value: 'live', label: 'Live'" in d


HELPER_TEST = r"""
import assert from 'node:assert/strict';
import * as h from './routingHelpers.mjs';

// ladder mode <-> the two stored booleans (Off never comes back as Live)
assert.equal(h.ladderMode({ enabled: false, dry_run: false }), 'off');
assert.equal(h.ladderMode({ enabled: true, dry_run: true }), 'dry_run');
assert.equal(h.ladderMode({ enabled: true, dry_run: false }), 'live');
for (const m of ['off', 'dry_run', 'live']) assert.equal(h.ladderMode(h.modeToFlags(m)), m);
assert.deepEqual(h.modeToFlags('off'), { enabled: false, dry_run: true });

// blank -> null, never 0
assert.equal(h.numOrNull(''), null);
assert.equal(h.numOrNull('  '), null);
assert.equal(h.numOrNull('0'), 0);
assert.equal(h.numOrNull('4.5'), 4.5);

const dealers = [
  { dealer: 'pj', region: 'central', emails: ['a@x.com'], contacts: { cre: 'c@x.com', principal: 'p@x.com', owner: 'o@x.com' } },
  { dealer: 'jb', region: 'southern', emails: ['b@x.com'], contacts: { cre: 'c@x.com', owner: 'o@x.com' } },
  { dealer: 'pg', region: '', emails: ['d@x.com'], contacts: {} },
  { dealer: 'empty', region: 'north', emails: [], contacts: {} },
];
const pics = [
  { department: 'dept_ok', pic_email: 'ok@x.com', escalation_manager_email: 'm@x.com' },
  { department: 'dept_nomgr', pic_email: 'ok@x.com', escalation_manager_email: '' },
  { department: 'dept_bad', pic_email: 'nope' },
];
const items = h.attentionItems(pics, dealers);
const texts = items.map(i => `${i.kind}:${i.id}:${i.severity}`);
assert.ok(texts.includes('dealer:jb:high'));          // no Principal -> step 3 skipped
assert.ok(texts.includes('dealer:pg:medium'));        // no region
assert.ok(texts.includes('dealer:empty:high'));       // nobody to email
assert.ok(texts.includes('pic:dept_nomgr:medium'));
assert.ok(texts.includes('pic:dept_bad:high'));
assert.ok(!texts.some(t => t.startsWith('dealer:pj')));
assert.ok(!texts.some(t => t.startsWith('pic:dept_ok')));
assert.ok(!items.some(i => i.id === 'pg' && i.severity === 'high')); // group-only dealer is not "missing" roles

assert.deepEqual(h.skippingDealers(3, dealers), ['jb']);
assert.deepEqual(h.skippingDealers(4, dealers), []);
assert.deepEqual(h.skippingDealers(2, dealers), []);

assert.equal(h.rowGap({ dealer: 'jb', next_step_no: 3 }, dealers), 'Step 3 skipped — no Principal');
assert.equal(h.rowGap({ dealer: 'pj', next_step_no: 3 }, dealers), null);
assert.equal(h.rowGap({ dealer: 'pg', next_step_no: 3 }, dealers), null);
assert.equal(h.rowGap({ dealer: 'unknown', next_step_no: 4 }, dealers), null);

assert.ok(h.isEmail('a.b+c@x.co'));
assert.ok(!h.isEmail('service@pj-dealer'));
assert.deepEqual(h.splitEmails(' a@x.com, ,b@x.com '), ['a@x.com', 'b@x.com']);
assert.deepEqual(h.splitEmails(['a@x.com', ' ']), ['a@x.com']);
assert.ok(h.matches('PETALING', 'dealer', 'Petaling Jaya'));
assert.ok(h.matches('', 'x'));
assert.ok(!h.matches('zz', 'a', null));
console.log('ok');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_helpers_behave(tmp_path):
    (tmp_path / "routingHelpers.mjs").write_text(_added(f"{DIR}/routingHelpers.js"))
    (tmp_path / "t.mjs").write_text(HELPER_TEST)
    res = subprocess.run(["node", "t.mjs"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ok"
