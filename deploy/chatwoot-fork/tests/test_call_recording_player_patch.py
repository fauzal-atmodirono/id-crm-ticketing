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
COMPOSABLE = "app/javascript/dashboard/components/proton/useProtonCallRecording.js"
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
    """The panel and the shared composable it delegates to (also used by
    0095's in-note player), read as one unit."""
    return _added(PANEL) + "\n" + _added(COMPOSABLE)


@pytest.fixture(scope="module")
def panel_only() -> str:
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
        assert f"b/{PANEL}" not in text, p.name
        assert f"b/{COMPOSABLE}" not in text, p.name
        if p.name < P0094.name:
            assert f"b/{CONTACT_PANEL}" not in text, p.name
        elif f"b/{CONTACT_PANEL}" in text:
            # A later patch (0099's DMS card) may add to ContactPanel.vue,
            # but only add: 0094's lines must survive untouched.
            assert not [
                line for line in text.splitlines()
                if line.startswith("-") and not line.startswith("---")
            ], p.name


def test_contact_panel_mounts_the_player_under_contact_info():
    added = _added(CONTACT_PANEL)
    assert (
        "import ProtonCallRecordingPanel from "
        "'dashboard/components/proton/ProtonCallRecordingPanel.vue';"
    ) in added
    assert '<ProtonCallRecordingPanel :conversation-id="conversationId" />' in added


def test_panel_calls_the_backend_with_the_session_helper(panel):
    assert "import { adminRequest } from 'dashboard/api/protonAdmin';" in panel
    assert "`/calls/${encodeURIComponent(conversationId())}/recording`" in panel
    assert "adminRequest(`${recordingPath()}${noteQuery()}`)" in panel
    assert "adminRequest(`${recordingPath()}/retranscribe${noteQuery()}`, {" in panel
    # the sidebar panel asks for the latest call: no message id
    assert "useProtonCallRecording({ conversationId: () => props.conversationId })" in panel


def test_panel_renders_an_inline_player_that_does_not_preload(panel):
    assert "<audio" in panel
    assert "controls" in panel and 'preload="none"' in panel
    assert ':src="playUrl"' in panel
    # The backend returns a relative /calls/... path; it is served by the backend.
    assert "`${backendUrl || ''}${path}`" in panel


def test_panel_is_hidden_unless_a_recording_is_available(panel):
    assert "v-if=\"state === 'available' || state === 'deleted'\"" in panel
    # 403 no permission, 404 off, 503 unconfigured -> no player
    assert "const FINAL_STATUSES = [403, 404, 503];" in panel
    assert "if (FINAL_STATUSES.includes(e?.status)) {" in panel
    # ordinary chats never ask
    assert "attrs.value.call_recording_object" in panel
    assert "attrs.value.recording_sid" in panel


def test_panel_remints_an_expired_link_and_keeps_position(panel):
    assert "EXPIRY_MARGIN_MS" in panel
    assert '@play="onPlay"' in panel and '@error="onError"' in panel
    assert "fresh.currentTime = at;" in panel


def test_panel_offers_transcript_and_retranscribe(panel):
    # Review R44 item 4: the transcript token goes in a header, the text is
    # rendered inline -- never a tokenised URL in a new tab or in history.
    assert "headers: { 'x-recording-token': transcriptToken.value }" in panel
    assert "transcriptToken.value = res.transcript_token || '';" in panel
    assert 'v-if="transcriptOpen"' in panel and "{{ transcriptText }}" in panel
    assert "window.open" not in panel
    assert 'v-if="canRetranscribe"' in panel
    assert "e?.status === 429" in panel


def test_panel_reloads_when_the_recording_attribute_appears(panel_only):
    """The archive attribute lands minutes after the call; a conversation
    already open with recording_sid must pick it up."""
    assert "attrs.value.call_recording_object || ''" in panel_only
    assert "watch(\n  loadKey," in panel_only


def test_panel_does_not_remint_on_unrelated_attribute_updates(panel_only):
    """M2: the watcher keys on a string (conversation + recording object), not
    a fresh array, and only a conversation change resets/unmounts."""
    assert "const loadKey = computed(() =>" in panel_only
    assert "`${props.conversationId}|${attrs.value.call_recording_object || ''}`" in panel_only
    assert "watch(() => props.conversationId, reset);" in panel_only
    assert "() => [" not in panel_only


def test_panel_only_asks_for_agents_who_may_listen(panel_only):
    assert (
        "import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';"
        in panel_only
    )
    assert "hasPermission('call_recording.listen')" in panel_only
    assert "permitted.value && isRecordedCall.value" in panel_only


def test_a_failed_refresh_keeps_a_working_player(panel):
    assert "} else if (state.value === 'available') {\n        // A refresh failed: keep the player, say so." in panel
    assert "message.value = 'Could not load — try again';" in panel


def test_retranscribe_failure_does_not_tear_down_the_player(panel):
    """M1: only 403, or 404 before anything loaded, collapses the player."""
    assert "e?.status === 403 ||\n        (e?.status === 404 && state.value === 'idle')" in panel
    assert "[403, 404, 503].includes(e?.status)" not in panel


def test_starting_one_player_pauses_the_others(panel):
    assert "const PLAY_EVENT = 'proton-call-recording-play';" in panel
    assert "window.addEventListener(PLAY_EVENT, pauseIfOther);" in panel
    assert "onScopeDispose(() => window.removeEventListener(PLAY_EVENT, pauseIfOther));" in panel
    assert "if (audio && event.detail !== audio && !audio.paused) audio.pause();" in panel
    assert "  async function onPlay() {\n    announcePlay();" in panel


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_panel_script_parses(panel_only, tmp_path):
    script = panel_only.split("<script setup>", 1)[1].split("</script>", 1)[0]
    (tmp_path / "panel.mjs").write_text(script)
    subprocess.run(["node", "--check", str(tmp_path / "panel.mjs")], check=True)
    (tmp_path / "composable.mjs").write_text(_added(COMPOSABLE))
    subprocess.run(["node", "--check", str(tmp_path / "composable.mjs")], check=True)


def test_a_rate_limited_retranscribe_says_how_long_to_wait(panel):
    assert "Re-transcribe limit reached — try again in ${minutes} min" in panel
    assert "?.detail?.retry_after_seconds" in panel
    assert "Math.ceil(seconds / 60)" in panel


def test_a_new_call_in_the_same_conversation_drops_the_old_transcript(panel, panel_only):
    """Round 2: when call_recording_object changes, the previous call's
    transcript/open state/message must not show under the new recording."""
    assert "  function forgetTranscript() {\n    transcriptText.value = '';\n    transcriptOpen.value = false;\n    message.value = '';" in panel
    assert "    forgetTranscript();\n    load();" in panel_only
