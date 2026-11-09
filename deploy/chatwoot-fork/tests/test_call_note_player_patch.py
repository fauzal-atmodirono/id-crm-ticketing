"""Fork patch 0095 (call note player): a backend "[Call archived to Cloud
Storage]" private note renders, inside its own bubble, as a call-recording
player for THAT call -- never as its raw gs:// lines.

A conversation can hold several calls (callback, retried call); the sidebar
panel (0094) only plays the latest. Each archive note mints its own link via
`GET /calls/{id}/recording?message_id=<note id>`, lazily: nothing is asked of
the backend until the agent presses Play / View transcript / Re-transcribe,
so a long timeline with many call notes fires no request on load.

Message.vue is upstream and touched by no other patch; 0095 only inserts into
it. The fetch / refresh / keep-position / transcript logic is the shared
composable 0094 ships (useProtonCallRecording.js), not a copy.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCHES = sorted((FORK / "patches").glob("*.patch"))
P0095 = FORK / "patches" / "0095-call-note-player.patch"
P0094 = FORK / "patches" / "0094-call-recording-player.patch"
BUBBLE = "app/javascript/dashboard/components/proton/ProtonCallNoteBubble.vue"
COMPOSABLE = "app/javascript/dashboard/components/proton/useProtonCallRecording.js"
MESSAGE = "app/javascript/dashboard/components-next/message/Message.vue"


def _added(patch: Path, path: str) -> str:
    out, cur = [], None
    for line in patch.read_text().splitlines():
        if line.startswith("diff --git "):
            cur = line.split(" b/", 1)[1]
        elif cur == path and line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


@pytest.fixture(scope="module")
def bubble() -> str:
    return _added(P0095, BUBBLE)


@pytest.fixture(scope="module")
def composable() -> str:
    return _added(P0094, COMPOSABLE)


def test_patch_is_additions_only():
    removed = [
        line
        for line in P0095.read_text().splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert removed == []


def test_no_other_patch_touches_the_message_component_or_the_bubble():
    for p in PATCHES:
        if p == P0095:
            continue
        text = p.read_text()
        assert f"b/{MESSAGE}" not in text, p.name
        assert f"b/{BUBBLE}" not in text, p.name


def test_message_routes_private_archive_notes_to_the_call_bubble():
    added = _added(P0095, MESSAGE)
    assert (
        "import ProtonCallNoteBubble from "
        "'dashboard/components/proton/ProtonCallNoteBubble.vue';"
    ) in added
    assert (
        "import { isProtonCallArchiveNote } from "
        "'dashboard/components/proton/useProtonCallRecording.js';"
    ) in added
    assert "if (props.private && isProtonCallArchiveNote(props.content)) {" in added
    assert "return ProtonCallNoteBubble;" in added
    # inserted as the FIRST branch of componentToRender (hunk context)
    text = P0095.read_text()
    assert " const componentToRender = computed(() => {\n+  // 0095" in text


def test_note_detection_matches_the_backend_header(composable):
    assert "const ARCHIVE_NOTE_HEADER = '[Call archived to Cloud Storage]';" in composable
    assert "export function isProtonCallArchiveNote(content)" in composable
    assert "content.trimStart().startsWith(ARCHIVE_NOTE_HEADER)" in composable


def test_the_bubble_never_renders_gs_lines(bubble):
    # every rendered line goes through visibleText, which drops the header
    # and every "- audio|transcript: gs://..." line
    assert "const GS_LINE = /^\\s*-\\s*(audio|transcript)\\s*:\\s*gs:\\/\\//i;" in bubble
    assert "!GS_LINE.test(line)" in bubble
    assert '<FormattedContent v-if="visibleText" :content="visibleText" />' in bubble
    template = bubble.split("<template>", 1)[1]
    assert "gs://" not in template
    # the only note text rendered is visibleText (never the raw `content`)
    assert "{{ content" not in template and ':content="content"' not in template


def test_the_bubble_has_play_and_transcript_controls(bubble):
    assert "▶ Play recording" in bubble
    assert "📄 View transcript" in bubble
    assert "Re-transcribe" in bubble
    assert "📞 Call recording" in bubble


def test_the_bubble_mints_its_own_note_lazily(bubble):
    # one note -> one link: the message id goes to the backend
    assert "messageId: () => id.value" in bubble
    # nothing on mount: no immediate watcher, no onMounted, load only in handlers
    assert "immediate" not in bubble and "onMounted" not in bubble
    assert "async function playRecording() {" in bubble
    assert "if (state.value === 'idle') await load();" in bubble


def test_the_bubble_never_autoplays(bubble):
    template = bubble.split("<template>", 1)[1]
    assert "autoplay" not in template
    assert 'preload="none"' in bubble and "controls" in bubble
    assert "<audio" in bubble


def test_the_bubble_keeps_position_through_the_shared_composable(bubble, composable):
    assert "useProtonCallRecording" in bubble
    assert '@play="onPlay"' in bubble and '@error="onError"' in bubble
    assert "fresh.currentTime = at;" in composable
    # no second copy of the fetch / refresh code in the bubble
    assert "adminRequest" not in bubble and "fetch(" not in bubble
    assert "EXPIRY_MARGIN_MS" not in bubble


def test_the_composable_sends_message_id(composable):
    assert "`?message_id=${encodeURIComponent(m)}`" in composable
    assert "`${recordingPath()}/retranscribe${noteQuery()}`" in composable


def test_the_bubble_degrades_without_permission_or_after_retention(bubble):
    assert "📞 Call recording archived" in bubble
    assert "📞 Call recording deleted under the retention policy" in bubble
    assert "v-if=\"state === 'unavailable'\"" in bubble
    assert "v-else-if=\"state === 'deleted'\"" in bubble


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_composable_and_bubble_scripts_parse(bubble, composable, tmp_path):
    (tmp_path / "c.mjs").write_text(composable)
    subprocess.run(["node", "--check", str(tmp_path / "c.mjs")], check=True)
    script = bubble.split("<script setup>", 1)[1].split("</script>", 1)[0]
    (tmp_path / "b.mjs").write_text(script)
    subprocess.run(["node", "--check", str(tmp_path / "b.mjs")], check=True)
