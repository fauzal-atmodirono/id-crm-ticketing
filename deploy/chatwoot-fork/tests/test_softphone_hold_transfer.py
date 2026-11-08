"""Fork patch 0069 FIX-15 additions (RFP 2026_028 4.6): Hold / Resume /
Transfer on the softphone panel.

Applies 0069's three fork-created files to an empty tree (no upstream needed),
checks the content, and syntax-checks the JS under node. Cannot prove the vite
build; only Cloud Build does.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCH = FORK / "patches" / "0069-agent-softphone.patch"
API = "app/javascript/dashboard/api/protonVoice.js"
SP = "app/javascript/dashboard/composables/useProtonSoftphone.js"
PANEL = "app/javascript/dashboard/components-next/softphone/ProtonSoftphonePanel.vue"


@pytest.fixture(scope="module")
def tree():
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(
        ["git", "apply", f"--include={API}", f"--include={SP}", f"--include={PANEL}", str(PATCH)],
        cwd=d,
        check=True,
    )
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_api_has_the_call_control_endpoints(tree):
    src = (tree / API).read_text()
    for path in ("capabilities", "hold", "resume", "transfer-targets", "transfer"):
        assert f"'/voice/agent/call/{path}'" in src
    # The agent's own leg SID is what the backend checks ownership of.
    assert "call_sid: callSid" in src
    assert "target_agent_id: targetAgentId" in src


def test_panel_has_hold_resume_and_transfer_buttons(tree):
    src = (tree / PANEL).read_text()
    assert "{{ held ? 'Resume' : 'Hold' }}" in src
    assert "@click=\"openTransfer\"" in src and "Ring agent" in src
    assert "v-for=\"agent in targets\"" in src
    assert "status === 'in-call' && canHoldTransfer" in src
    # Transfer carries the case: the target's panel links the transcript.
    assert "incoming?.transcriptPath" in src


def test_original_controls_and_idle_hold_kept(tree):
    sp = (tree / SP).read_text()
    panel = (tree / PANEL).read_text()
    # Task 20 / 0079 idle hold untouched; held calls stay 'in-call'.
    assert "window.__PROTON_IDLE_HOLD__ = () => v === 'in-call' || v === 'ringing';" in sp
    assert re.search(r"return \{\s+status,\s+incoming,\s+elapsed,\s+muted,", sp)
    assert "{{ muted ? 'Unmute' : 'Mute' }}" in panel and "Hang up" in panel
    assert "params.get('transcript_path')" in sp and "call?.parameters?.CallSid" in sp


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_parses(tree, tmp_path):
    (tmp_path / "a.mjs").write_text((tree / SP).read_text())
    (tmp_path / "b.mjs").write_text((tree / API).read_text())
    panel = (tree / PANEL).read_text()
    script = panel.split("<script setup>", 1)[1].split("</script>", 1)[0]
    (tmp_path / "c.mjs").write_text(script)
    for f in ("a.mjs", "b.mjs", "c.mjs"):
        subprocess.run(["node", "--check", str(tmp_path / f)], check=True)
