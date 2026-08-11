"""Patch 0002's assist payload, and the stack that sits on top of it.

`ReplyTopPanel.vue`'s assist payload lives in a Chatwoot fork patch, so there
is no importable module to exercise — these tests read the patch text and drive
`git apply` against the real post-0054 fixture.

Why the stacking test exists: on 2026-08-11 patch `0002` grew 13 lines in this
file (pre-rendered strings -> structured messages), and patches `0055` and
`0056` both patch the *same* file further down. Nothing in the suite would have
noticed if that had broken them — the fixture test passes whether or not the
fixture is refreshed, because `git apply` matches context rather than line
numbers. This file closes that gap.

What these CAN prove: that 0002's payload has the shape the backend expects,
that the fixture agrees with the patch, and that 0055/0056 still apply to the
result. What they CANNOT prove: that 0002 itself applies to real upstream (no
image here), or that vite compiles it. Cloud Build remains the only proof of
the latter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_FORK_ROOT = _REPO_ROOT / "deploy" / "chatwoot-fork"
_PATCHES = _FORK_ROOT / "patches"
_ASSIST_PATCH = _PATCHES / "0002-ai-assist-backend.patch"
_FIXTURE = _FORK_ROOT / "fixtures" / "ReplyTopPanel.post-0054.vue"
_VUE_PATH = "app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue"

pytestmark = pytest.mark.skipif(
    not _ASSIST_PATCH.is_file(), reason="fork patches not present in this checkout"
)

# The fixture is ground truth extracted from the pinned upstream image, not
# something this suite can regenerate. Tests that drive `git apply` need it;
# the ones that only read patch text do not, and must still run without it.
_needs_fixture = pytest.mark.skipif(
    not _FIXTURE.is_file(),
    reason=f"{_FIXTURE.name} not present; see deploy/chatwoot-fork/fixtures/README.md",
)


def _patch_text() -> str:
    return _ASSIST_PATCH.read_text()


# ---------------------------------------------------------------------------
# The payload shape
# ---------------------------------------------------------------------------


def test_payload_is_structured_not_pre_rendered() -> None:
    """The backend renders transcripts from one registry; a second label table
    in the SPA would be a second registry to drift."""
    text = _patch_text()
    assert "role: m.message_type === 0 ? 'customer' : 'agent'," in text
    assert "file_type: a.file_type || 'file'," in text


def test_old_pre_rendered_mapping_is_gone() -> None:
    assert "'Customer' : 'Agent'}: ${m.content}`" not in _patch_text()


def test_caption_less_messages_are_no_longer_filtered_out() -> None:
    """The exact line that made a caption-less voice note invisible to the AI."""
    text = _patch_text()
    assert ".filter(m => m.content && [0, 1].includes(m.message_type))" not in text
    assert ".filter(m => m.content || (m.attachments || []).length)" in text


def test_attachments_are_read_off_the_message() -> None:
    assert "(m.attachments || []).map(a => ({" in _patch_text()


@_needs_fixture
def test_fixture_agrees_with_the_patch() -> None:
    """A fixture that no longer reflects an earlier patch is a silent liar —
    see deploy/chatwoot-fork/fixtures/README.md."""
    fixture = _FIXTURE.read_text()
    assert "role: m.message_type === 0 ? 'customer' : 'agent'," in fixture
    assert "'Customer' : 'Agent'}: ${m.content}`" not in fixture


# ---------------------------------------------------------------------------
# The stack on top
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # Every argument is a hardcoded literal or a repo-local path (git plumbing
    # only), never untrusted input -- the same reasoning test_p7_task7's `run`
    # records for its S603 suppression.
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@_needs_fixture
@pytest.mark.parametrize(
    "patch_name",
    ["0055-translate-action.patch", "0056-faq-composer-apply.patch"],
)
def test_later_patches_still_apply_over_the_new_payload(tmp_path: Path, patch_name: str) -> None:
    """0055 and 0056 patch ReplyTopPanel.vue below the region 0002 changed.

    Parametrised rather than merged so a failure names which one broke. 0056
    stacks on 0055, so both are applied in order and only the named one is
    asserted on.
    """
    patch = _PATCHES / patch_name
    if not patch.is_file():
        pytest.skip(f"{patch_name} not present")

    target = tmp_path / _VUE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_FIXTURE.read_text())
    _git(["init", "-q", "."], tmp_path)

    for name in ["0055-translate-action.patch", "0056-faq-composer-apply.patch"]:
        result = _git(["apply", "--include=*ReplyTopPanel.vue", str(_PATCHES / name)], tmp_path)
        assert result.returncode == 0, (
            f"{name} failed to apply over patch 0002's ReplyTopPanel.vue payload.\n"
            f"An early patch changed this file's line count; refresh the fixture "
            f"and regenerate the failing patch.\n{result.stderr}"
        )
        if name == patch_name:
            break


@_needs_fixture
def test_stacked_result_keeps_the_structured_payload(tmp_path: Path) -> None:
    """Applying the later patches must not clobber what 0002 put there."""
    target = tmp_path / _VUE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_FIXTURE.read_text())
    _git(["init", "-q", "."], tmp_path)
    for name in ["0055-translate-action.patch", "0056-faq-composer-apply.patch"]:
        if (_PATCHES / name).is_file():
            _git(["apply", "--include=*ReplyTopPanel.vue", str(_PATCHES / name)], tmp_path)
    assert "role: m.message_type === 0 ? 'customer' : 'agent'," in target.read_text()
