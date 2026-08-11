"""Tests for the feature guide's audience filter (P14 task 4, §2.3.3).

The load-bearing test is `test_default_handbook_is_identical_to_the_baseline_build`.
Everything else in this file is about the three curricula; that one is about
the 12 MB client deliverable the audience filter was bolted onto, and it is
the reason the change is safe to make at all: it extracts the generator as it
stood *before* the filter existed (`BASELINE_REV`), builds the default
handbook with both, and compares.

Two things about that comparison are worth knowing, because both were
measured rather than assumed:

1. **A .docx is a zip, and python-docx stamps each member with the current
   local time**, so two builds of identical content never have identical
   file bytes. The comparison is therefore per-member payload equality
   across the full member list — every byte of content, none of the clock.
2. **Bookmark ids come from `hash()` of the bookmark name**, which Python
   randomises per process, so `word/document.xml` differs between two runs
   of the *same* generator unless `PYTHONHASHSEED` is fixed. Both builds run
   in a subprocess with `PYTHONHASHSEED=0`. (A latent wart, left alone here:
   fixing it would change the committed handbook's bytes, which is exactly
   what this test exists to prevent this change from doing.)

Placed in `scripts/` rather than under `backend/apps/backend/src/` so the
backend suite's own count is unaffected — the same reasoning, and the same
location, as `scripts/test_generate_config_doc.py`. Run it with the backend
venv, which has python-docx:

    cd backend/apps/backend
    GOOGLE_API_KEY=test-key uv run pytest ../../../scripts/test_build_feature_guide_audiences.py -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_MATERIALS = REPO_ROOT / "docs" / "client-materials"
BUILDER = CLIENT_MATERIALS / "build_crm_feature_guide.py"
SRC_DIR = CLIENT_MATERIALS / "feature-guide-src-v3"

# The last commit that touched the builder before the audience filter landed.
# Pinned by sha on purpose: "HEAD~1" would silently start comparing against
# the wrong thing the moment anyone else commits.
BASELINE_REV = "cf0473d"


def load_builder():
    """Import the builder by path (its directory is not a package)."""
    spec = importlib.util.spec_from_file_location("fg_builder", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load_builder()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def docx_member_digests(path):
    """{member name: sha256 of its bytes} — content without the zip clock."""
    with zipfile.ZipFile(path) as zf:
        return {
            name: hashlib.sha256(zf.read(name)).hexdigest()
            for name in sorted(zf.namelist())
        }


def run_build(script, out_path, src_dir=None, extra_args=()):
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    env["FG_OUT"] = str(out_path)
    if src_dir is not None:
        env["FG_SRC_DIR"] = str(src_dir)
    result = subprocess.run(
        [sys.executable, str(script), *extra_args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(script.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def write_chapter(directory, name, text):
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The safety argument for the whole change
# ---------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_default_handbook_is_identical_to_the_baseline_build(tmp_path):
    baseline_source = subprocess.run(
        ["git", "show", f"{BASELINE_REV}:docs/client-materials/build_crm_feature_guide.py"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert baseline_source.returncode == 0, baseline_source.stderr

    # The baseline script resolves the template, the screenshots and the
    # chapter source relative to its own directory, so it has to run from a
    # directory that has all three. Symlinks rather than copies: the assets
    # are 12 MB and nothing writes to them.
    workdir = tmp_path / "baseline"
    workdir.mkdir()
    baseline_script = workdir / "build_crm_feature_guide.py"
    baseline_script.write_text(baseline_source.stdout, encoding="utf-8")
    for entry in ("Google Docs template - Short version.docx", "feature-guide-assets"):
        os.symlink(CLIENT_MATERIALS / entry, workdir / entry)

    before = tmp_path / "before.docx"
    after = tmp_path / "after.docx"
    run_build(baseline_script, before, src_dir=SRC_DIR)
    run_build(BUILDER, after)

    assert docx_member_digests(before) == docx_member_digests(after), (
        "the default handbook build changed; the audience filter must be "
        "invisible to it"
    )


def test_training_markers_are_invisible_to_the_default_build(tmp_path):
    """The second half of the same argument, and the durable half.

    The markers are HTML comments, which the builder already strips. This
    rebuilds the handbook from a marker-free copy of the source and compares,
    so the claim keeps being checked even after the baseline generator has
    drifted out of usefulness."""
    stripped = tmp_path / "src"
    stripped.mkdir()
    for chapter in sorted(SRC_DIR.glob("*.md")):
        lines = chapter.read_text(encoding="utf-8").split("\n")
        kept = [l for l in lines if builder.TRAINING_RE.match(l) is None]
        assert len(kept) < len(lines) or chapter.name == "OUTLINE.md"
        (stripped / chapter.name).write_text("\n".join(kept), encoding="utf-8")

    tagged = tmp_path / "tagged.docx"
    untagged = tmp_path / "untagged.docx"
    run_build(BUILDER, tagged)
    run_build(BUILDER, untagged, src_dir=stripped)

    assert docx_member_digests(tagged) == docx_member_digests(untagged)


# ---------------------------------------------------------------------------
# A typo must fail loudly, not thin a document
# ---------------------------------------------------------------------------
def test_a_misspelled_audience_is_an_error_naming_the_valid_names():
    with pytest.raises(builder.TrainingTagError) as exc:
        builder.parse_chapter_sections(
            "# C\n\n## S\n<!-- TRAINING: audience=agnet -->\n", "07-x.md"
        )
    message = str(exc.value)
    assert "07-x.md:4" in message
    assert "agnet" in message
    for name in builder.AUDIENCES:
        assert name in message


def test_an_unknown_token_is_an_error():
    with pytest.raises(builder.TrainingTagError, match="unknown token"):
        builder.parse_chapter_sections(
            "# C\n\n## S\n<!-- TRAINING: audience=agent, excercise -->\n", "x.md"
        )


def test_a_marker_with_no_audience_is_an_error():
    with pytest.raises(builder.TrainingTagError, match="no 'audience='"):
        builder.parse_chapter_sections(
            "# C\n\n## S\n<!-- TRAINING: exercise -->\n", "x.md"
        )


def test_two_markers_on_one_section_is_an_error():
    with pytest.raises(builder.TrainingTagError, match="already has a TRAINING"):
        builder.parse_chapter_sections(
            "# C\n\n## S\n<!-- TRAINING: audience=agent -->\n"
            "<!-- TRAINING: audience=admin -->\n",
            "x.md",
        )


def test_a_chapter_level_marker_may_not_carry_the_exercise_flag():
    with pytest.raises(builder.TrainingTagError, match="per-section flag"):
        builder.parse_chapter_sections(
            "# C\n<!-- TRAINING: audience=agent, exercise -->\n\n## S\n", "x.md"
        )


def test_the_typo_stops_the_plain_handbook_build_too(tmp_path, monkeypatch):
    """Validation is unconditional, so the nearest build reports the typo.

    Driven through `main()` rather than the parser, because the parser raising
    proves nothing about whether anyone calls it."""
    src = tmp_path / "src"
    src.mkdir()
    write_chapter(src, "01-x.md", "# C\n\n## S\n<!-- TRAINING: audience=nope -->\n")
    monkeypatch.setattr(builder, "SRC_DIR", str(src))
    assert builder.main([]) == 2


# ---------------------------------------------------------------------------
# Untagged content must still reach a cohort
# ---------------------------------------------------------------------------
def test_an_untagged_section_falls_back_to_the_widest_curriculum():
    _, _, sections = builder.parse_chapter_sections("# C\n\n## S\n\nBody.\n", "x.md")
    assert sections[0]["audience"] == builder.FALLBACK_AUDIENCE
    assert sections[0]["audience_source"] == "fallback"
    assert builder.sees(sections[0]["audience"], "admin")


def test_a_section_inherits_its_chapter_marker():
    _, _, sections = builder.parse_chapter_sections(
        "# C\n<!-- TRAINING: audience=agent -->\n\n## S\n\nBody.\n", "x.md"
    )
    assert (sections[0]["audience"], sections[0]["audience_source"]) == (
        "agent",
        "chapter",
    )


def test_a_fallback_section_is_named_in_the_coverage_report(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    write_chapter(
        src,
        "01-x.md",
        "# Chapter\n\n## Tagged\n<!-- TRAINING: audience=agent -->\n\n"
        "### How to use it\n\n1. Do a thing.\n\n## Forgotten\n\nBody.\n",
    )
    monkeypatch.setattr(builder, "SRC_DIR", str(src))
    coverage = builder.build_curricula()["tag-coverage.md"]
    assert "Sections with no marker of their own or their chapter's: 1" in coverage
    assert "- `01-x.md` § Forgotten" in coverage
    # ...and it is in the admin curriculum rather than nowhere.
    assert "Forgotten" in builder.build_curricula()["admin/facilitator-deck.md"]
    assert "Forgotten" not in builder.build_curricula()["agent/facilitator-deck.md"]


def test_every_section_of_the_real_handbook_is_tagged():
    """A new chapter section added without a marker shows up here.

    The fallback keeps it visible to somebody; this keeps it from being
    quietly *left* to the fallback."""
    untagged = [
        (chapter["file"], section["title"])
        for chapter in builder.load_curriculum_model()
        for section in chapter["sections"]
        if section["audience_source"] == "fallback"
    ]
    assert untagged == []


# ---------------------------------------------------------------------------
# The filter itself
# ---------------------------------------------------------------------------
def test_the_audiences_are_cumulative():
    assert builder.sees("agent", "agent")
    assert builder.sees("agent", "supervisor")
    assert builder.sees("agent", "admin")
    assert not builder.sees("supervisor", "agent")
    assert not builder.sees("admin", "supervisor")
    assert builder.sees("admin", "admin")


def test_each_curriculum_is_a_subset_of_the_next():
    chapters = builder.load_curriculum_model()
    titles = {
        audience: {
            (c["file"], s["title"])
            for c, s in builder.audience_sections(chapters, audience)
        }
        for audience in builder.AUDIENCES
    }
    assert titles["agent"] < titles["supervisor"] < titles["admin"]
    assert len(titles["admin"]) == sum(len(c["sections"]) for c in chapters)


def test_filtering_drops_only_more_senior_sections():
    for audience in builder.AUDIENCES:
        for path in builder.chapter_source_paths():
            filtered = builder.read_chapter(path, audience)
            if not filtered.strip():
                continue
            _, _, sections = builder.parse_chapter_sections(filtered, path)
            assert sections, path
            for section in sections:
                assert builder.sees(section["audience"], audience), (
                    path,
                    section["title"],
                    audience,
                )


def test_a_chapter_with_nothing_for_the_audience_is_dropped_entirely(tmp_path):
    admin_only = tmp_path / "09-admin-only.md"
    admin_only.write_text(
        "# Administration\n<!-- TRAINING: audience=admin -->\n\n## Roles\n\nBody.\n",
        encoding="utf-8",
    )
    assert builder.read_chapter(str(admin_only), "admin").strip()
    assert builder.read_chapter(str(admin_only), "agent") == ""


def test_the_unfiltered_read_returns_the_file_verbatim():
    for path in builder.chapter_source_paths():
        with open(path, encoding="utf-8") as f:
            assert builder.read_chapter(path) == f.read()


# ---------------------------------------------------------------------------
# The curricula
# ---------------------------------------------------------------------------
def test_the_committed_curricula_are_current():
    """The `--check` convention, mirroring scripts/generate-config-doc.py."""
    result = subprocess.run(
        [sys.executable, str(BUILDER), "--check"],
        capture_output=True,
        text=True,
        cwd=str(BUILDER.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_curriculum_file_is_generated_for_every_audience():
    files = builder.build_curricula()
    for audience in builder.AUDIENCES:
        for name in ("facilitator-deck.md", "exercises.md", "competency-checklist.md"):
            assert os.path.join(audience, name) in files
    assert "tag-coverage.md" in files


def test_an_exercise_flag_without_documented_steps_is_an_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    write_chapter(
        src,
        "01-x.md",
        "# Chapter\n\n## Prose only\n<!-- TRAINING: audience=agent, exercise -->\n\n"
        "Nothing to carry out here.\n",
    )
    monkeypatch.setattr(builder, "SRC_DIR", str(src))
    with pytest.raises(builder.TrainingTagError, match="documents no steps"):
        builder.build_curricula()


def test_exercise_ids_are_unique_within_a_curriculum():
    chapters = builder.load_curriculum_model()
    for audience in builder.AUDIENCES:
        ids = builder.exercise_ids(chapters, audience)
        assert len(set(ids.values())) == len(ids)
        assert all(
            value.startswith(builder.AUDIENCE_EXERCISE_PREFIX[audience])
            for value in ids.values()
        )


def test_every_generated_file_carries_the_do_not_edit_banner():
    for name, content in builder.build_curricula().items():
        assert content.startswith("<!-- GENERATED FILE"), name
        assert "do not edit" in content.lower(), name


def test_no_generated_file_contains_a_timestamp():
    """Bytes that change every run cannot be committed with a drift check."""
    for name, content in builder.build_curricula().items():
        assert "2026-08-1" not in content, name  # today's date in any form
        assert "Generated on" not in content, name


def test_the_derived_length_is_reported_against_the_design_target_not_scaled():
    """The rule must not be tuned to agree with the spec's hours.

    It currently disagrees with all three, which is a finding about the topic
    list, not a bug in the arithmetic — so this asserts the disagreement is
    *stated* rather than that it does not exist."""
    chapters = builder.load_curriculum_model()
    for audience in builder.AUDIENCES:
        pairs = builder.audience_sections(chapters, audience)
        derived = sum(builder.slide_minutes(s) for _, s in pairs)
        deck = builder.render_deck(chapters, audience)
        assert builder.duration_text(derived) in deck
        assert builder.duration_text(builder.AUDIENCE_TARGET_MINUTES[audience]) in deck
        assert "derived by rule" in deck
        assert "not measured" in deck


def test_the_exercise_sets_say_they_have_not_been_dry_run():
    """No sandbox tenant exists here; a curriculum must not imply one does."""
    for audience in builder.AUDIENCES:
        content = builder.build_curricula()[os.path.join(audience, "exercises.md")]
        assert "NOT YET DRY-RUN" in content
        assert "owed, not verified" in content


def test_each_curriculum_records_what_it_cannot_teach():
    for audience in builder.AUDIENCES:
        gaps = builder.gaps_for(audience)
        assert gaps, audience
        deck = builder.build_curricula()[
            os.path.join(audience, "facilitator-deck.md")
        ]
        for gap in gaps:
            assert gap["topic"] in deck


def test_data_scopes_are_recorded_as_deliberately_not_taught():
    """R16: the flag restricts nothing, so teaching it would teach a control
    that does not exist. The reason has to survive a regeneration."""
    admin_gaps = {gap["topic"]: gap for gap in builder.gaps_for("admin")}
    scopes = admin_gaps["Data scopes (row-level data access)"]
    assert "restricts nothing" in scopes["why"]
    assert "Deliberately not taught" in scopes["why"]
