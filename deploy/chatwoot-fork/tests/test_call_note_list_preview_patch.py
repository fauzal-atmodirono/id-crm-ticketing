"""Fork patch 0096: in the conversation list (and the reply-to quote) a
call-archive note previews as "📞 Call recording", never as its raw gs://
lines. Additions only, on upstream files no other patch edits.

Known gaps, left on purpose (internal; only agents see them): message search
results and the context menu's "Copy" still carry the raw note text.
"""
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCHES = sorted((FORK / "patches").glob("*.patch"))
P0096 = FORK / "patches" / "0096-call-note-list-preview.patch"
P0094 = FORK / "patches" / "0094-call-recording-player.patch"
COMPOSABLE = "app/javascript/dashboard/components/proton/useProtonCallRecording.js"
D = "app/javascript/dashboard/"
FILES = [
    D + "components/widgets/conversation/MessagePreview.vue",
    D + "components-next/Conversation/ConversationCard/MessagePreview.vue",
    D + "components-next/Conversation/ConversationCard/CardMessagePreview.vue",
    D + "components-next/Conversation/ConversationCard/CardMessagePreviewWithMeta.vue",
]
IMPORT = "} from 'dashboard/components/proton/useProtonCallRecording.js';"


def _added(patch: Path, path: str) -> str:
    out, cur = [], None
    for line in patch.read_text().splitlines():
        if line.startswith("diff --git "):
            cur = line.split(" b/", 1)[1]
        elif cur == path and line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


def test_patch_is_additions_only():
    text = P0096.read_text()
    assert [ln for ln in text.splitlines() if ln.startswith("-") and not ln.startswith("---")] == []


def test_no_other_patch_touches_the_preview_files():
    for p in PATCHES:
        if p == P0096:
            continue
        for f in FILES:
            assert f"b/{f}" not in p.read_text(), (p.name, f)


def test_the_preview_label_lives_in_the_shared_composable():
    assert "export const PROTON_CALL_NOTE_PREVIEW = '📞 Call recording';" in _added(
        P0094, COMPOSABLE
    )


@pytest.mark.parametrize("path", FILES)
def test_every_list_preview_swaps_archive_notes_for_the_label(path):
    added = _added(P0096, path)
    assert IMPORT in added
    assert "return PROTON_CALL_NOTE_PREVIEW;" in added
    assert "isProtonCallArchiveNote(" in added


def test_card_previews_keep_the_email_subject_first():
    for path in FILES[2:]:
        assert (
            "if (!subject && isProtonCallArchiveNote(lastNonActivityMessage?.content)) {"
            in _added(P0096, path)
        )
