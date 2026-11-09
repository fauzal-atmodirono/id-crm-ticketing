"""Fork patch 0093: the 0079 idle monitor never signs out a fresh login.

Live bug (proton, rc17, SESSION_IDLE_TIMEOUT_MINUTES=15): sign in -> first
dashboard load -> DELETE /auth/sign_out within a second. 0079's reload/reopen
bypass judged the bare proton_last_activity_ms stamp the PREVIOUS session left
behind (menu logout, closed tab and token expiry never clear it) and fired.

The node scripts reproduce that against 0079's idleMonitor.js (RED: the bug is
there) and check 0093's (GREEN). Only fork-created files are rebuilt here
(there is no upstream tree); store/utils/api.js is checked on the patch text.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCHES = FORK / "patches"
PATCH = PATCHES / "0093-idle-monitor-fresh-login.patch"
MON = "app/javascript/dashboard/composables/idleMonitor.js"
WIRE = "app/javascript/dashboard/composables/useProtonIdleTimeout.js"

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _apply(d, names, include):
    for n in names:
        subprocess.run(["git", "apply", f"--include={include}", str(PATCHES / n)], cwd=d, check=True)


@pytest.fixture(scope="module")
def trees():
    """(before, after): fork files after 0079+0090, and after 0093 too."""
    out = []
    for with_fix in (False, True):
        d = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        chain = ["0079-session-idle-timeout.patch", "0090-ui-review-followups.patch"]
        if with_fix:
            chain.append(PATCH.name)
        _apply(d, chain, "*composables/idleMonitor.js")
        _apply(d, chain, "*composables/useProtonIdleTimeout.js")
        out.append(d)
    yield out
    for d in out:
        shutil.rmtree(d, ignore_errors=True)


HARNESS = """
import assert from 'node:assert/strict';
import * as M from './idleMonitor.mjs';
const { createIdleMonitor, ACTIVITY_KEY } = M;
const mem = () => { const m = {}; return { m, getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: k => { delete m[k]; } }; };
const nop = () => 1;
let t = Date.UTC(2026, 10, 9, 1, 0), idle = 0;
const clock = () => t;
"""

# The live sequence: yesterday's session stamped activity, then ended without
# the monitor firing; today's sign-in loads the dashboard and installs it.
RED = HARNESS + """
const s = mem();
s.setItem(ACTIVITY_KEY, String(t - 8 * 3600e3));
createIdleMonitor({ timeoutMinutes: 15, now: clock, storage: s, onIdle: () => idle++, setIntervalFn: nop, clearIntervalFn: nop });
console.log(idle === 1 ? 'bug-reproduced' : 'no-bug');
"""

GREEN = HARNESS + """
const { sessionKey, isAuthPath } = M;
const auth = { 'access-token': 'SECRET-TOKEN', client: 'c-new', uid: 'a@x', expiry: '1790000000' };
const NEW = sessionKey(auth), OLD = sessionKey({ ...auth, client: 'c-old', expiry: '1789000000' });
const mk = (s, session, extra = {}) => createIdleMonitor({ timeoutMinutes: 15, now: clock, storage: s, session, onIdle: () => idle++, setIntervalFn: nop, clearIntervalFn: nop, ...extra });

// sessionKey: non-secret, per sign-in, null without a session
assert.match(NEW, /^[0-9a-f]{8}$/); assert.notEqual(NEW, OLD);
assert.ok(!NEW.includes('SECRET'));
assert.equal(sessionKey({ ...auth, 'access-token': 'other' }), NEW);
for (const bad of [null, false, {}, { uid: 'a@x' }, { client: 'c' }]) assert.equal(sessionKey(bad), null);

// isAuthPath mirrors DashboardController#set_application_pack
for (const p of ['/app/login', '/app/login/', '/app/auth/password/edit', '/app/auth/signup']) assert.equal(isAuthPath(p), true, p);
for (const p of ['/app/accounts/1/dashboard', '/app/accounts/1/conversations/5', '', undefined]) assert.equal(isAuthPath(p), false, String(p));

// 1. THE BUG: a bare 0079 stamp from hours ago + fresh login -> no sign-out
let s = mem(); s.setItem(ACTIVITY_KEY, String(t - 8 * 3600e3));
let m = mk(s, () => NEW);
assert.equal(idle, 0, 'fresh login signed out by a bare old stamp');
assert.equal(s.getItem(ACTIVITY_KEY), `${t}|${NEW}`);
// ...and it still times out normally afterwards
t += 14 * 60e3; assert.equal(m.check(), false);
t += 60e3; assert.equal(m.check(), true); assert.equal(idle, 1);

// 2. a stale stamp of the previous (bound) session -> no sign-out
idle = 0; s = mem(); s.setItem(ACTIVITY_KEY, `${t - 3600e3}|${OLD}`);
mk(s, () => NEW); assert.equal(idle, 0);

// 3. the reopen bypass still works for the SAME session (tab reopened late)
s = mem(); s.setItem(ACTIVITY_KEY, `${t - 16 * 60e3}|${NEW}`);
mk(s, () => NEW); assert.equal(idle, 1); assert.equal(s.getItem(ACTIVITY_KEY), null);
// ...and a recent same-session stamp does not fire
idle = 0; s = mem(); s.setItem(ACTIVITY_KEY, `${t - 60e3}|${NEW}`);
mk(s, () => NEW); assert.equal(idle, 0);

// 4. no live session (logged out / login page): never arms, never fires
for (const none of [() => null, () => '', () => { throw new Error('x'); }]) {
  s = mem(); s.setItem(ACTIVITY_KEY, String(t - 8 * 3600e3));
  m = mk(s, none);
  assert.equal(m.enabled, false); assert.equal(idle, 0);
  t += 60 * 60e3; assert.equal(m.check(), false); assert.equal(idle, 0);
}

// 5. cross-tab: same-session activity keeps alive; another session's does not
idle = 0; s = mem(); m = mk(s, () => NEW);
t += 14 * 60e3; s.setItem(ACTIVITY_KEY, `${t}|${NEW}`);
t += 14 * 60e3; assert.equal(m.check(), false);
s.setItem(ACTIVITY_KEY, `${t + 60e3}|${OLD}`);
t += 2 * 60e3; assert.equal(m.check(), true); assert.equal(idle, 1);

// 6. a new sign-in under an open tab (cookie changed): window restarts, no fire
idle = 0; s = mem(); let cur = OLD; m = mk(s, () => cur);
t += 30 * 60e3; cur = NEW;
assert.equal(m.check(), false); assert.equal(idle, 0);
assert.equal(s.getItem(ACTIVITY_KEY), `${t}|${NEW}`);
t += 15 * 60e3; assert.equal(m.check(), true); assert.equal(idle, 1);

// 7. session gone under the tab (logged out elsewhere): stop, never fire
idle = 0; s = mem(); cur = NEW; let stopped = 0;
m = mk(s, () => cur, { clearIntervalFn: () => stopped++ });
cur = null; t += 60 * 60e3;
assert.equal(m.check(), false); assert.equal(m.check(), false);
assert.equal(idle, 0); assert.equal(stopped, 1);

// 8. calls still hold the session
idle = 0; s = mem(); let held = true; m = mk(s, () => NEW, { hold: () => held });
t += 60 * 60e3; assert.equal(m.check(), false); held = false;
t += 15 * 60e3; assert.equal(m.check(), true); assert.equal(idle, 1);

// 9. without `session` (0079 callers) behaviour is unchanged
idle = 0; s = mem(); s.setItem(ACTIVITY_KEY, String(t - 16 * 60e3));
mk(s, undefined); assert.equal(idle, 1);
idle = 0; s = mem(); m = mk(s, undefined); assert.equal(s.getItem(ACTIVITY_KEY), String(t));
t += 15 * 60e3; assert.equal(m.check(), true); assert.equal(idle, 1);
console.log('ok');
"""


def _node(tree, script, tmp_path):
    (tmp_path / "idleMonitor.mjs").write_text((tree / MON).read_text())
    (tmp_path / "t.mjs").write_text(script)
    return subprocess.run(["node", "t.mjs"], cwd=tmp_path, capture_output=True, text=True)


@needs_node
def test_red_0079_signs_out_a_fresh_login(trees, tmp_path):
    before, _ = trees
    r = _node(before, RED, tmp_path)
    assert r.returncode == 0 and "bug-reproduced" in r.stdout, r.stdout + r.stderr


@needs_node
def test_green_0093_never_signs_out_a_fresh_login(trees, tmp_path):
    _, after = trees
    r = _node(after, GREEN, tmp_path)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout + r.stderr


def test_wiring_skips_auth_pages_and_binds_the_session(trees):
    _, after = trees
    js = (after / WIRE).read_text()
    assert "import { createIdleMonitor, isAuthPath, sessionKey } from './idleMonitor';" in js
    guard = js.index("if (isAuthPath(window.location && window.location.pathname)) return;")
    cookie = js.index("if (!Auth.hasAuthCookie()) return;")
    armed = js.index("installed = true;")
    assert guard < armed and cookie < armed
    assert "Auth.hasAuthCookie() ? sessionKey(Auth.getAuthData()) : null" in js
    # 0090's cleanup is kept
    assert "clearCookiesOnLogout();" in js and "deleteIndexedDBOnLogout();" in js


def test_sign_in_drops_the_old_stamp():
    text = PATCH.read_text()
    s = text.split("diff --git a/app/javascript/dashboard/store/utils/api.js", 1)[1]
    assert "+import { ACTIVITY_KEY } from 'dashboard/composables/idleMonitor';" in s
    # inside setAuthCredentials, right after the 0092 cookie write
    body = s.split("expires: expiryDate,", 1)[1].split("setUser(response.data.data, expiryDate);", 1)[0]
    assert "+    window.localStorage.removeItem(ACTIVITY_KEY);" in body


def test_patch_touches_exactly_three_files():
    text = PATCH.read_text()
    files = [ln.split(" b/", 1)[1] for ln in text.splitlines() if ln.startswith("diff --git ")]
    assert files == [MON, WIRE, "app/javascript/dashboard/store/utils/api.js"]
