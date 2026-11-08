"""Fork patch 0079 (FIX-18, RFP 2026_028 7.5): idle timeout + session cap.

Applies 0079 to a fixture that holds only what the patch needs as context
(useProtonConfig.js as patch 0001 creates it; the post-0058 tail of
vueapp.html.erb) -- there is no upstream tree here -- then checks the content
and runs the pure idle monitor under node. Cannot prove the vite/Rails build;
only Cloud Build does.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCH = FORK / "patches" / "0079-session-idle-timeout.patch"
CFG = "app/javascript/dashboard/composables/useProtonConfig.js"
LAYOUT = "app/views/layouts/vueapp.html.erb"

LAYOUT_FIXTURE = """    <script>
      window.__PROTON_CONFIG__ = {
        backendUrl: "<%= ENV.fetch('PROTON_BACKEND_URL', '') %>",
        backendKey: "<%= ENV.fetch('PROTON_BACKEND_KEY', '') %>",
        features: "<%= proton_features.join(',') %>".split(',').filter(Boolean)
      };
    </script>
    <% if @global_config['CLOUD_ANALYTICS_TOKEN'].present? %>
    <script>
      window.analyticsConfig = {
"""


@pytest.fixture(scope="module")
def tree():
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / CFG).parent.mkdir(parents=True)
    # Pre-image of useProtonConfig.js: exactly what 0001 adds.
    one = (FORK / "patches" / "0001-runtime-config.patch").read_text()
    subprocess.run(["git", "apply", "--include=*useProtonConfig.js", str(FORK / "patches" / "0001-runtime-config.patch")],
                   cwd=d, check=True)
    assert one
    (d / LAYOUT).parent.mkdir(parents=True)
    (d / LAYOUT).write_text(LAYOUT_FIXTURE)
    subprocess.run(["git", "apply", str(PATCH)], cwd=d, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_initializer_is_env_driven_and_default_preserving(tree):
    rb = (tree / "config/initializers/zz_proton_session.rb").read_text()
    assert "ENV.fetch('SESSION_TOKEN_LIFESPAN_HOURS', '')" in rb
    # Only overrides when positive: unset leaves upstream's 2 months alone.
    assert "if hours.positive?" in rb
    assert "DeviseTokenAuth.token_lifespan = hours.hours" in rb
    if shutil.which("ruby"):
        subprocess.run(["ruby", "-c", str(tree / "config/initializers/zz_proton_session.rb")], check=True)


def test_layout_exposes_idle_minutes_default_off(tree):
    erb = (tree / LAYOUT).read_text()
    assert "SESSION_IDLE_TIMEOUT_MINUTES', '0'" in erb
    assert "window.__PROTON_IDLE_TIMEOUT_MINUTES__" in erb
    # Existing config block untouched.
    assert "window.__PROTON_CONFIG__" in erb and "window.analyticsConfig" in erb


def test_config_module_arms_the_watcher(tree):
    js = (tree / CFG).read_text()
    assert "installProtonIdleTimeout();" in js
    assert "export function useProtonConfig" in js


def test_signout_clears_session():
    js = (FORK / "patches" / "0079-session-idle-timeout.patch").read_text()
    assert "/auth/sign_out" in js and "Cookies.remove('cw_d_session_info')" in js


def test_env_plumbing_present():
    root = FORK.parent
    for f in ("docker-compose.tenant.yml", "tenants/example.env"):
        t = (root / f).read_text()
        assert "SESSION_IDLE_TIMEOUT_MINUTES" in t and "SESSION_TOKEN_LIFESPAN_HOURS" in t, f
    assert "zz_proton_session.rb" in (FORK / "Dockerfile").read_text()


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_idle_monitor_logout_behaviour(tree, tmp_path):
    src = (tree / "app/javascript/dashboard/composables/idleMonitor.js").read_text()
    (tmp_path / "idleMonitor.mjs").write_text(src)
    (tmp_path / "t.mjs").write_text("""
import assert from 'node:assert/strict';
import { createIdleMonitor, ACTIVITY_KEY } from './idleMonitor.mjs';
const mem = () => { const m = {}; return { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = v; } }; };
let t = 1000000, idle = 0;
const clock = () => t;
const nop = () => 1;
// off when 0 / garbage
for (const v of [0, '', undefined, 'x', -5]) {
  const m = createIdleMonitor({ timeoutMinutes: v, onIdle: () => idle++, setIntervalFn: nop, clearIntervalFn: nop });
  assert.equal(m.enabled, false); assert.equal(m.check(), false);
}
// fires after timeout, once
let s = mem();
let m = createIdleMonitor({ timeoutMinutes: 10, now: clock, storage: s, onIdle: () => idle++, setIntervalFn: nop, clearIntervalFn: nop });
t += 9 * 60000; assert.equal(m.check(), false);
t += 60000; assert.equal(m.check(), true); assert.equal(m.check(), false); assert.equal(idle, 1);
// activity resets the clock
idle = 0; s = mem(); t = 5e6;
m = createIdleMonitor({ timeoutMinutes: 10, now: clock, storage: s, onIdle: () => idle++, setIntervalFn: nop, clearIntervalFn: nop });
t += 9 * 60000; m.touch(); t += 9 * 60000; assert.equal(m.check(), false);
// another tab's activity (shared storage) keeps this tab alive
t += 2 * 60000; s.setItem(ACTIVITY_KEY, String(t - 1000)); assert.equal(m.check(), false); assert.equal(idle, 0);
// throwing storage does not break it
const bad = { getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); } };
m = createIdleMonitor({ timeoutMinutes: 1, now: clock, storage: bad, onIdle: () => idle++, setIntervalFn: nop, clearIntervalFn: nop });
t += 61000; assert.equal(m.check(), true);
console.log('ok');
""")
    r = subprocess.run(["node", "t.mjs"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout + r.stderr
