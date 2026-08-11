"""Tests for `--no-placeholders` (Feature Guide v4).

The v3 guide referenced 103 screenshots and only 44 PNGs existed, so 59
markers rendered as a shaded, bordered box containing the caption. The
client asked for real screenshots where they can be had and *nothing*
where they cannot. This file pins both halves of that: the default build
still draws the box, and `--no-placeholders` emits nothing at all.

Placed in `scripts/` rather than under `backend/apps/backend/src/` so the
backend suite's own count is unaffected -- the same reasoning, and the
same location, as `scripts/test_build_feature_guide_audiences.py`. Run it
with the backend venv, which has python-docx:

    cd backend/apps/backend
    GEMINI_API_KEY=dummy GOOGLE_API_KEY=test-key uv run pytest \\
      ../../../scripts/test_build_feature_guide_screenshots.py -q
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_MATERIALS = REPO_ROOT / "docs" / "client-materials"
BUILDER = CLIENT_MATERIALS / "build_crm_feature_guide.py"

# A caption whose words cannot occur anywhere else in the template or the
# chapter, so finding it in document.xml means the placeholder rendered.
CAPTION = "Zarquon calibration panel"
CHAPTER = """# Test Chapter

## A Section

Some prose.

[[SCREENSHOT: ch99-does-not-exist | %s]]

Closing prose.
""" % CAPTION


def run_build(out_path, src_dir, extra_args=()):
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["FG_OUT"] = str(out_path)
    env["FG_SRC_DIR"] = str(src_dir)
    result = subprocess.run(
        [sys.executable, str(BUILDER), *extra_args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BUILDER.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def document_xml(path):
    with zipfile.ZipFile(path) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def one_chapter_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "01-test.md").write_text(CHAPTER, encoding="utf-8")
    return src


def test_default_build_still_draws_the_placeholder_box(tmp_path):
    """The default must not change -- v3 has to stay reproducible."""
    src = one_chapter_source(tmp_path)
    out = tmp_path / "default.docx"
    run_build(out, src)
    xml = document_xml(out)
    assert CAPTION in xml, "the caption should render inside the placeholder"
    assert "Screenshot:" in xml, "the placeholder carries a 'Screenshot:' label"


def test_no_placeholders_emits_nothing_for_a_missing_screenshot(tmp_path):
    src = one_chapter_source(tmp_path)
    default_out = tmp_path / "default.docx"
    clean_out = tmp_path / "clean.docx"
    run_build(default_out, src)
    run_build(clean_out, src, extra_args=("--no-placeholders",))

    clean = document_xml(clean_out)
    assert CAPTION not in clean, "no caption text may survive"
    assert "Screenshot:" not in clean, "no 'Screenshot:' label may survive"

    # Relative, not absolute: the cover and TOC are paragraphs today, but
    # asserting `"<w:tbl>" not in xml` would start failing the day either
    # grows a table for reasons that have nothing to do with screenshots.
    assert clean.count("<w:tbl>") == document_xml(default_out).count("<w:tbl>") - 1, (
        "suppressing the placeholder should remove exactly one table"
    )


def test_no_placeholders_leaves_the_surrounding_prose_alone(tmp_path):
    """Suppression must remove the marker, not the paragraphs around it."""
    src = one_chapter_source(tmp_path)
    out = tmp_path / "clean.docx"
    run_build(out, src, extra_args=("--no-placeholders",))
    xml = document_xml(out)
    assert "Some prose." in xml
    assert "Closing prose." in xml


def test_missing_shots_are_still_reported_on_stdout(tmp_path):
    """Suppressing the box must not suppress the build's own warning --
    that report is how Task 15 knows which markers to delete."""
    src = one_chapter_source(tmp_path)
    out = tmp_path / "clean.docx"
    result = run_build(out, src, extra_args=("--no-placeholders",))
    assert "ch99-does-not-exist" in result.stdout
    assert "Screenshots found  : 0/1" in result.stdout
