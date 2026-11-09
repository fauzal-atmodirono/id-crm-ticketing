"""Fork patch 0094 (call-recording player): the conversation sidebar plays a
call's archived recording through the backend's signed link.

The panel is a fork-created file; ContactPanel.vue is upstream and touched by
no other patch, so 0094's own hunks are checked against a tree holding just
the upstream lines they need as context (the full-chain apply against the
v4.15.1 checkout is the build-time proof). Cannot prove the vite build; only
Cloud Build does.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCHES = sorted((FORK / "patches").glob("*.patch"))
P0094 = FORK / "patches" / "0094-call-recording-player.patch"
PANEL = "app/javascript/dashboard/components/proton/ProtonCallRecordingPanel.vue"
CONTACT_PANEL = "app/javascript/dashboard/routes/dashboard/conversation/ContactPanel.vue"


def _added(path: str) -> str:
    """The lines 0094 adds to `path`, joined."""
    out, cur = [], None
    for line in P0094.read_text().splitlines():
        if line.startswith("diff --git "):
            cur = line.split(" b/", 1)[1]
        elif cur == path and line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


@pytest.fixture(scope="module")
def panel() -> str:
    return _added(PANEL)


def test_patch_is_additions_only():
    removed = [
        line
        for line in P0094.read_text().splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert removed == []


def test_no_earlier_patch_touches_the_files_0094_edits():
    """ContactPanel.vue is upstream-only before 0094, so its context lines
    are upstream's; a later patch editing it must keep 0094's lines intact."""
    for p in PATCHES:
        if p == P0094:
            continue
        text = p.read_text()
        assert f"b/{CONTACT_PANEL}" not in text, p.name
        assert f"b/{PANEL}" not in text, p.name


def test_contact_panel_mounts_the_player_under_contact_info():
    added = _added(CONTACT_PANEL)
    assert (
        "import ProtonCallRecordingPanel from "
        "'dashboard/components/proton/ProtonCallRecordingPanel.vue';"
    ) in added
    assert '<ProtonCallRecordingPanel :conversation-id="conversationId" />' in added


def test_panel_calls_the_backend_with_the_session_helper(panel):
    assert "import { adminRequest } from 'dashboard/api/protonAdmin';" in panel
    assert "`/calls/${encodeURIComponent(props.conversationId)}/recording`" in panel
    assert "adminRequest(`${recordingPath()}/retranscribe`, { method: 'POST' })" in panel


def test_panel_renders_an_inline_player_that_does_not_preload(panel):
    assert "<audio" in panel
    assert "controls" in panel and 'preload="none"' in panel
    assert ':src="playUrl"' in panel
    # The backend returns a relative /calls/... path; it is served by the backend.
    assert "`${backendUrl || ''}${path}`" in panel


def test_panel_is_hidden_unless_a_recording_is_available(panel):
    assert "v-if=\"state === 'available' || state === 'deleted'\"" in panel
    # every failure (403 no permission, 404 off, 503 unconfigured) -> hidden
    assert "if (mine === requestSeq) state.value = 'hidden';" in panel
    # ordinary chats never ask
    assert "attrs.value.call_recording_object" in panel
    assert "attrs.value.recording_sid" in panel


def test_panel_remints_an_expired_link_and_keeps_position(panel):
    assert "EXPIRY_MARGIN_MS" in panel
    assert '@play="onPlay"' in panel and '@error="onError"' in panel
    assert "fresh.currentTime = at;" in panel


def test_panel_offers_transcript_and_retranscribe(panel):
    assert "window.open(transcriptUrl.value, '_blank', 'noopener')" in panel
    assert 'v-if="canRetranscribe"' in panel
    assert "e?.status === 429" in panel


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_panel_script_parses(panel, tmp_path):
    script = panel.split("<script setup>", 1)[1].split("</script>", 1)[0]
    (tmp_path / "panel.mjs").write_text(script)
    subprocess.run(["node", "--check", str(tmp_path / "panel.mjs")], check=True)
