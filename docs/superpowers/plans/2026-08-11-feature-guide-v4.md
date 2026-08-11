# PROTON CRM Feature Guide v4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce `PROTON - CRM Feature Guide v4.docx` — all 14 chapters re-verified against the live `proton` tenant, the newly-deployed features documented, real screenshots captured from the live tenant, and **no placeholder boxes anywhere**.

**Architecture:** A new `feature-guide-src-v4/` chapter tree, edited from a copy of v3, rendered by the existing `build_crm_feature_guide.py` through its `FG_SRC_DIR`/`FG_OUT`/`FG_COVER_*` env hooks. The only code change is one new `--no-placeholders` flag threaded through three functions. Every factual claim is settled by a probe against the running containers and recorded in an internal ledger. v3's source and output are never touched.

**Tech Stack:** Python 3 + python-docx (run from the backend's uv venv), `gcloud compute ssh` for tenant probes, Chrome via `mcp__claude-in-chrome__*` for screenshot capture, pytest for the builder tests.

## Global Constraints

- **v3 is frozen.** Do not modify `docs/client-materials/feature-guide-src-v3/` or `docs/client-materials/PROTON - CRM Feature Guide v3.docx`. v3 has shipped to the client.
- **The builder's default behaviour is frozen.** `python3 build_crm_feature_guide.py` with no arguments must keep producing byte-for-byte identical zip-member digests. `scripts/test_build_feature_guide_audiences.py::test_default_handbook_is_identical_to_the_baseline_build` enforces this; it must pass at every commit.
- **The live tenant is read-only.** Never send a message, save a setting, click a destructive control, or take any action that could fire escalation mail. This tenant mails real Devoteam addresses.
- **Tenant scope is `proton` only.** Never touch `default` or `wahchan`.
- **Never claim a feature exists without a probe.** The four sanctioned probes are in Task 3. An exact-path `/openapi.json` check is banned — list by prefix.
- **Branch:** `dev-yuda`. Never merge to `main`.
- Tenant URL: `http://proton.crm.34-50-103-151.nip.io`. VM: `gcloud compute ssh crm-ticketing --zone=asia-southeast2-a`. Do **not** pass `--tunnel-through-iap` — this account gets `4033: not authorized`.
- Run pytest from the backend venv: `cd backend/apps/backend && GEMINI_API_KEY=dummy GOOGLE_API_KEY=test-key uv run pytest <path> -q`. Without a `GEMINI_API_KEY` five modules fail at *collection* and it reads like a code regression.
- Run `rtk proxy <cmd>` when you need untruncated output from a build or a diff.

## File Structure

**Created:**
- `docs/client-materials/feature-guide-src-v4/` — 14 chapters + `OUTLINE.md`, copied from v3 then edited. One file per chapter, unchanged from v3's decomposition.
- `docs/client-materials/feature-guide-v4-verification.md` — internal ledger. Not a client deliverable.
- `docs/client-materials/PROTON - CRM Feature Guide v4.docx` — the deliverable.
- `scripts/test_build_feature_guide_screenshots.py` — tests for the new flag. A separate file from the audience tests because that file's subject is the audience filter and its load-bearing baseline comparison; screenshot rendering is a different responsibility.
- New PNGs under `docs/client-materials/feature-guide-assets/`.

**Modified:**
- `docs/client-materials/build_crm_feature_guide.py` — `--no-placeholders`, threaded `add_screenshot` → `process_chapter` → `build_handbook`, plus the module docstring and the build summary line.

**Untouched:** everything under `feature-guide-src-v3/`, the v3 `.docx`, `feature-guide-v3-pending.md` (read-only input; sections move *out* of it only in Task 11).

---

### Task 1: Pre-flight — restore the v3 `.docx` to its delivered state

The working tree has a rebuilt `PROTON - CRM Feature Guide v3.docx`. Its `word/document.xml` differs from the committed copy. The expected cause is bookmark ids: `add_bookmark` derives them from `hash()` of the bookmark name, which Python randomises per process, so any rebuild without `PYTHONHASHSEED=0` rewrites them. Confirm that is all it is, then discard, because Global Constraints freeze v3 and the Task 2 baseline test compares against it.

**Files:**
- Modify: `docs/client-materials/PROTON - CRM Feature Guide v3.docx` (discard working-tree change)

**Interfaces:**
- Consumes: nothing
- Produces: a clean working tree for `docs/client-materials/`

- [ ] **Step 1: Confirm the diff is bookmark ids only**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
python3 - <<'PY'
import subprocess, zipfile, io, re
raw = subprocess.run(
    ["git", "show", "HEAD:docs/client-materials/PROTON - CRM Feature Guide v3.docx"],
    capture_output=True).stdout
def doc(b):
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        return z.read("word/document.xml").decode("utf-8")
a, b = doc(raw), doc(open("docs/client-materials/PROTON - CRM Feature Guide v3.docx", "rb").read())
# Strip every bookmark id and every anchor, then compare what is left.
scrub = lambda s: re.sub(r'w:id="\d+"', 'w:id="X"', re.sub(r'_Toc\d+', '_TocX', s))
print("identical after scrubbing bookmark ids/anchors:", scrub(a) == scrub(b))
print("prose identical:", re.findall(r'<w:t[^>]*>([^<]*)</w:t>', a) ==
                          re.findall(r'<w:t[^>]*>([^<]*)</w:t>', b))
PY
```

Expected: both lines print `True`.

**If either prints `False`, STOP and report to the user.** That would mean the working-tree v3 carries real content edits nobody recorded, and discarding it would destroy them.

- [ ] **Step 2: Discard the rebuild**

```bash
git checkout -- "docs/client-materials/PROTON - CRM Feature Guide v3.docx"
git status --short docs/client-materials/
```

Expected: no output from `git status` for that path.

- [ ] **Step 3: Confirm the frozen baseline test passes before any change**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy GOOGLE_API_KEY=test-key uv run pytest \
  ../../../scripts/test_build_feature_guide_audiences.py -q
```

Expected: PASS. This is the "before" reading — if it is already failing, every later claim about not having broken it is worthless.

- [ ] **Step 4: No commit**

Nothing to commit — this task only restores tracked state. Do not commit.

---

### Task 2: `--no-placeholders` in the builder

`add_screenshot()`'s `else` branch draws a one-cell table, shades it `NOTE_SHADE`, borders it at `sz=8`, writes `"Screenshot: <caption>"` in italic and pads it with three empty paragraphs. That box is what the client is objecting to. The new flag makes the branch emit **nothing** — no table, no caption, and not the trailing spacer paragraph either, so the section reads as if the marker were never written.

**Files:**
- Create: `scripts/test_build_feature_guide_screenshots.py`
- Modify: `docs/client-materials/build_crm_feature_guide.py` — `add_screenshot` (line 353), `process_chapter` (line 527), `build_handbook` (line 1497), `main` (line 1591), module docstring (lines 24-28)

**Interfaces:**
- Consumes: nothing
- Produces: `build_crm_feature_guide.py --no-placeholders`, and the keyword `placeholders=True` on `add_screenshot(document, shot_id, caption, found, missing, placeholders=True)`, `process_chapter(document, text, use_bullet_style, use_number_style, stats, bookmarks=None, placeholders=True)` and `build_handbook(audience=None, placeholders=True)`. Task 13 and Task 15 invoke the flag.

- [ ] **Step 1: Write the failing test**

Create `scripts/test_build_feature_guide_screenshots.py`:

```python
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
    # asserting `"<w:tbl" not in xml` would start failing the day either
    # grows a table for reasons that have nothing to do with screenshots.
    assert clean.count("<w:tbl") == document_xml(default_out).count("<w:tbl") - 1, (
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy GOOGLE_API_KEY=test-key uv run pytest \
  ../../../scripts/test_build_feature_guide_screenshots.py -q
```

Expected: `test_default_build_still_draws_the_placeholder_box` PASSES (the default already does this). The other three FAIL — the first two because `--no-placeholders` is not a recognised argument, so argparse exits 2 and `run_build`'s `assert result.returncode == 0` trips.

- [ ] **Step 3: Thread the flag through `add_screenshot`**

In `docs/client-materials/build_crm_feature_guide.py`, replace the whole of `add_screenshot` (starting line 353) with:

```python
def add_screenshot(document, shot_id, caption, found, missing, placeholders=True):
    """Render the PNG for `shot_id`, or account for its absence.

    `placeholders=False` (the `--no-placeholders` build) emits *nothing* for
    a missing shot -- not the box, not the caption, and not the trailing
    spacer either, so the section reads as if the marker were never written.
    The default keeps the box, because v3 was delivered with it and has to
    stay reproducible. Either way the id lands in `missing`, so the build
    still reports what it could not find.
    """
    png_path = os.path.join(ASSETS_DIR, "%s.png" % shot_id)
    if os.path.exists(png_path):
        found.append(shot_id)
        document.add_picture(png_path, width=Inches(6))
        document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap = document.add_paragraph(style="normal")
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = cap.add_run(caption)
        run.italic = True
    else:
        missing.append(shot_id)
        if not placeholders:
            return
        table = document.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, sz=8)
        cell = table.cell(0, 0)
        set_cell_shading(cell, NOTE_SHADE)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run("Screenshot: %s" % caption)
        run.italic = True
        # give the placeholder some visible height/width
        cell.width = Inches(6)
        for _ in range(3):
            cell.add_paragraph("")
    document.add_paragraph(style="normal")
```

Note the early `return` sits **before** the shared trailing `document.add_paragraph(style="normal")`. That is deliberate: a suppressed marker must leave no spacer behind.

- [ ] **Step 4: Thread it through `process_chapter`**

Change the signature at line 527 and the call at line 549:

```python
def process_chapter(document, text, use_bullet_style, use_number_style, stats,
                    bookmarks=None, placeholders=True):
```

```python
        elif kind == "screenshot":
            _, shot_id, caption = block
            add_screenshot(document, shot_id, caption, stats["found"],
                           stats["missing"], placeholders=placeholders)
```

- [ ] **Step 5: Thread it through `build_handbook` and fix the summary line**

Change the signature at line 1497 to `def build_handbook(audience=None, placeholders=True):`, then the `process_chapter` call inside the chapter loop:

```python
        process_chapter(
            document, text, use_bullet_style, use_number_style, stats, bookmarks,
            placeholders=placeholders,
        )
```

and the summary at lines 1571-1574, which currently claims placeholders were rendered whether or not they were:

```python
    if stats["missing"]:
        print(
            "Screenshots missing (%s):"
            % ("rendered as placeholders" if placeholders else "omitted entirely")
        )
        for shot_id in stats["missing"]:
            print("  - %s" % shot_id)
```

- [ ] **Step 6: Add the CLI argument**

In `main` (line 1591), after the `--check` argument and before `args = parser.parse_args(argv)`:

```python
    parser.add_argument(
        "--no-placeholders",
        action="store_true",
        help="emit nothing at all where a screenshot's PNG is missing, "
        "instead of the bordered caption box. Used for the v4 edition; the "
        "default is left alone so a v3 rebuild stays reproducible.",
    )
```

and change the final return at line 1622:

```python
        return build_handbook(
            audience=args.audience, placeholders=not args.no_placeholders
        )
```

- [ ] **Step 7: Record it in the module docstring**

In the markdown-subset list (lines 24-28), replace the screenshot bullet with:

```
  - `[[SCREENSHOT: id | caption]]` on its own line -> the PNG at
    feature-guide-assets/<id>.png if present, else a bordered placeholder
    box, so the build never fails on a missing screenshot. Pass
    `--no-placeholders` (the v4 edition) to emit nothing at all instead.
```

- [ ] **Step 8: Run the new tests to verify they pass**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy GOOGLE_API_KEY=test-key uv run pytest \
  ../../../scripts/test_build_feature_guide_screenshots.py -q
```

Expected: 4 passed.

- [ ] **Step 9: Run the frozen baseline test**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy GOOGLE_API_KEY=test-key uv run pytest \
  ../../../scripts/test_build_feature_guide_audiences.py -q
```

Expected: PASS, unchanged from Task 1 Step 3. If the default-handbook comparison fails, the threading leaked into the default path — the most likely cause is dropping the shared trailing `add_paragraph` for *found* shots too.

- [ ] **Step 10: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add docs/client-materials/build_crm_feature_guide.py \
        scripts/test_build_feature_guide_screenshots.py
git commit -m "feat(feature-guide): --no-placeholders, for a guide with no empty boxes

v3 referenced 103 screenshots against 44 PNGs, so 59 markers rendered as a
shaded box with the caption in it. The client wants a real screenshot or
nothing. The default is untouched -- v3 has shipped and has to stay
reproducible, and the baseline comparison test enforces that."
```

---

### Task 3: The live-state snapshot

Every chapter task reads from this one file instead of re-probing the VM fourteen times. It is the evidence base the ledger cites.

**Files:**
- Create: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: `--no-placeholders` exists (Task 2), though this task does not use it
- Produces: `feature-guide-v4-verification.md` with a `## Live state, measured YYYY-MM-DD` section and an empty `## Ledger` table. Tasks 4-12 append rows to the ledger; Task 11 reads the Live state section to decide which held-back sections come back.

- [ ] **Step 1: Take the snapshot**

One SSH round trip. Do not split it into fourteen.

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command '
echo "=== chatwoot .git_sha ==="
sudo docker exec proton-chatwoot-rails cat /app/.git_sha
echo "=== chatwoot image ==="
sudo docker inspect -f "{{.Config.Image}}" proton-chatwoot-rails
echo "=== SPA feature list (rendered login page) ==="
sudo docker exec proton-chatwoot-rails sh -c \
  "wget -q -O - http://127.0.0.1:3000/app/login" | grep -o "features[^<]*" | head -1
echo "=== backend openapi paths ==="
sudo docker exec proton-backend python -c "
import urllib.request, json
d = json.load(urllib.request.urlopen(\"http://127.0.0.1:8080/openapi.json\"))
for p in sorted(d[\"paths\"]): print(p)
"
echo "=== agent env ==="
sudo docker exec proton-agent printenv | grep -E "_ENABLED|AGENT_MODE|_IDS=" | sort
echo "=== backend env ==="
sudo docker exec proton-backend printenv | grep -E "_ENABLED|_MODE|_IDS=" | sort
echo "=== rails env (x-chatwoot-env passthrough only) ==="
sudo docker exec proton-chatwoot-rails printenv | grep -E "_ENABLED|PROTON_FEATURES" | sort
' 2>&1 | tee /private/tmp/claude-501/-Users-yudaadipratama-Archive-id-crm-ticketing/9d1a1f1d-d0d7-4276-8df8-c6cc821391af/scratchpad/live-state.txt
```

Two gotchas already paid for on this box: the image has **`wget`, not `curl`**, and **`localhost` resolves to IPv6** while puma binds IPv4 — hence `127.0.0.1`.

- [ ] **Step 2: Note which BigQuery views exist**

The one claim in the guide that the container probes cannot settle. P8's eleven views were never created as of 2026-08-09; confirm whether that is still true.

```bash
gcloud config get-value project
bq ls --format=pretty 2>&1 | head -30
```

If `bq` is unavailable or the dataset cannot be listed, record exactly that in the snapshot — "could not be probed" is a legitimate and useful verdict, and it still means the AI Cost & Performance section stays out under Task 11's rule.

- [ ] **Step 3: Write the file**

Create `docs/client-materials/feature-guide-v4-verification.md`:

```markdown
# Feature Guide v4 — verification ledger (internal)

**Not a client deliverable.** Companion to `feature-guide-v3-pending.md`.

v3 was cut back on 2026-08-09 because it described software that was not
running. This file exists so v4's claims can be audited rather than trusted:
one row per checkable claim, naming the probe that settled it.

## Live state, measured 2026-08-11

| Probe | Value |
|---|---|
| `docker exec proton-chatwoot-rails cat /app/.git_sha` | *(paste)* |
| Chatwoot image | *(paste)* |
| Rendered `/app/login` feature list | *(paste)* |
| Backend `/openapi.json` path count | *(paste)* |
| BigQuery views (P8) | *(paste, or "could not be probed")* |

Full capture: see the `## Raw capture` section below.

## Sanctioned probes

1. **What the SPA ships** — the feature list from the rendered login page,
   read inside the container. Not the compose file, not the patch directory.
2. **What the backend serves** — `/openapi.json` listed **by prefix**. An
   exact-path check is banned: `/alerts/rules` reads as missing when the real
   paths are `/alerts/rules/{defaults,mine}`.
3. **Flag state** — `printenv` inside *both* `proton-agent` and
   `proton-chatwoot-rails`. They do not read the same source: the backend
   takes the tenant env file wholesale via `env_file:`, Rails gets only what
   the compose `x-chatwoot-env` block passes through, and the VM's compose
   file has been stale before.
4. **The browser** — for anything only a rendered page can settle.

## Ledger

| Chapter | Claim | Probe | Verdict | Date |
|---|---|---|---|---|

## Raw capture

*(paste the full output of the snapshot command)*
```

Fill every `*(paste)*` from Step 1 and Step 2 output. **No `*(paste)*` marker may survive into the commit.**

- [ ] **Step 4: Verify no placeholders remain**

```bash
grep -n "(paste" docs/client-materials/feature-guide-v4-verification.md
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add docs/client-materials/feature-guide-v4-verification.md
git commit -m "docs(feature-guide): the live-state snapshot v4 gets verified against

One SSH round trip, recorded once, so fourteen chapter passes read a file
instead of re-probing the box fourteen times."
```

---

### Task 4: Fork the v3 source into v4

**Files:**
- Create: `docs/client-materials/feature-guide-src-v4/*.md` (14 chapters + `OUTLINE.md`)

**Interfaces:**
- Consumes: `--no-placeholders` (Task 2)
- Produces: `feature-guide-src-v4/`, the tree every content task edits, and the build invocation Tasks 13 and 15 use.

- [ ] **Step 1: Copy the tree**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
cp -R feature-guide-src-v3 feature-guide-src-v4
ls feature-guide-src-v4/
```

Expected: `01-introduction.md` through `14-glossary.md` plus `OUTLINE.md` — 15 files.

- [ ] **Step 2: Prove it builds before a single edit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
FG_SRC_DIR=feature-guide-src-v4 \
FG_OUT='PROTON - CRM Feature Guide v4.docx' \
FG_COVER_SUBTITLE='Operator Handbook — Edition 4, August 2026' \
python3 build_crm_feature_guide.py --no-placeholders 2>&1 | tail -25
```

Expected: exit 0, `Chapters processed : 14`, `Screenshots found  : 44/103`, and `Screenshots missing (omitted entirely):` — that last string is the proof the flag reached the summary.

- [ ] **Step 3: Prove the output really has no placeholder boxes**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
python3 - <<'PY'
import zipfile
xml = zipfile.ZipFile("PROTON - CRM Feature Guide v4.docx").read("word/document.xml").decode()
print("'Screenshot:' labels found:", xml.count("Screenshot:"))
PY
```

Expected: `0`. Anything else means a placeholder survived.

- [ ] **Step 4: Confirm v3 is still untouched**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git status --short docs/client-materials/ | grep -v feature-guide-src-v4
```

Expected: one line only, for the new `PROTON - CRM Feature Guide v4.docx`. If `feature-guide-src-v3/` or the v3 `.docx` appears, something wrote to a frozen path — stop and investigate.

- [ ] **Step 5: Commit the source tree only**

The `.docx` is rebuilt at the end of each phase; committing an intermediate one just makes noise in a 12 MB binary.

```bash
git add docs/client-materials/feature-guide-src-v4
git commit -m "docs(feature-guide): fork the v3 chapters into a v4 source tree

Byte-identical to v3 at this commit, so every later diff in this tree is
exactly the v4 delta and nothing else."
```

---

### Task 5: Chapters 1, 3 and 8 — introduction, contacts, campaigns & help centre

The low-churn chapters: mostly stock Chatwoot surfaces, so the probe surface is small and this task establishes the working rhythm the next seven follow.

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/01-introduction.md`
- Modify: `docs/client-materials/feature-guide-src-v4/03-contacts.md`
- Modify: `docs/client-materials/feature-guide-src-v4/08-campaigns-helpcenter.md`
- Modify: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: `feature-guide-v4-verification.md`'s `## Live state` section (Task 3), `feature-guide-src-v4/` (Task 4)
- Produces: the ledger row format every later chapter task reuses:
  `| 01 | <claim, quoted or paraphrased> | <probe> | verified\|corrected\|removed | 2026-08-11 |`

- [ ] **Step 1: Read the three chapters and list every checkable claim**

A claim is checkable if it asserts a feature exists, a page is reachable, a flag is on, a route responds, or a number is a particular value. Prose about *why* something matters is not checkable — leave it alone.

- [ ] **Step 2: Settle each claim against the snapshot**

Use the Live state section. Only probe the VM again for something the snapshot genuinely does not cover, and when you do, add it to the snapshot's Raw capture section rather than leaving it in your scrollback.

- [ ] **Step 3: Correct the prose**

Rules, in order of preference: correct the claim to what is true; if it is no longer true at all, delete the claim; only if a whole section describes unreachable software, move that section to `feature-guide-v3-pending.md` with a `<!-- VERIFY-LIVE -->` comment saying what would have to be confirmed. Never soften a false claim into a vague one.

- [ ] **Step 4: Append ledger rows**

One row per claim, in the `## Ledger` table. Every claim gets a row, including the ones that came back `verified` — a ledger with only corrections cannot be distinguished from a ledger nobody finished.

- [ ] **Step 5: Rebuild to prove the source still parses**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
FG_SRC_DIR=feature-guide-src-v4 \
FG_OUT='PROTON - CRM Feature Guide v4.docx' \
FG_COVER_SUBTITLE='Operator Handbook — Edition 4, August 2026' \
python3 build_crm_feature_guide.py --no-placeholders 2>&1 | tail -12
```

Expected: exit 0 and `Chapters processed : 14`. A `TRAINING marker error` here means an edit broke an audience comment — fix it now, not at the end.

- [ ] **Step 6: Commit**

```bash
git add docs/client-materials/feature-guide-src-v4 \
        docs/client-materials/feature-guide-v4-verification.md
git commit -m "docs(feature-guide): re-verify chapters 1, 3 and 8 against the live tenant"
```

---

### Task 6: Chapter 2 — conversations

The biggest chapter (31 KB) and the one with the most new material. Patches `0055` (Translate), `0056`/`0063`/`0064` (FAQ composer strip, channel priorities), `0057` (inbound alerts) and `0065` (agent status) are all in the live image, and multimodal AI assist plus media-grounded KB retrieval landed in the backend.

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/02-conversations.md`
- Modify: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: the ledger row format (Task 5)
- Produces: chapter 2 sections for Translate, the FAQ suggestion strip and inbound alerts, which Task 12's channel playbooks cross-reference

- [ ] **Step 1: Re-verify the existing chapter as in Task 5 Steps 1-2**

- [ ] **Step 2: Add the newly-deployed conversation-surface features**

Each one needs both halves of its probe before it is written up — the SPA feature list *and* the backend route. This is the specific failure this project has already had: `0055`'s Translate button gates on `ai_assist`, **not** on `TRANSLATION_ENABLED`, so it renders regardless, and against a backend without `/assist/translate` every click returned "Translation failed. Please try again." A button that renders is not a feature that works.

- **Translate composer action** — needs `ai_assist` in the feature list *and* `/assist/translate` in the OpenAPI paths.
- **FAQ suggestion strip** — needs `faq_suggestion_popup` in the feature list *and* `/kb/suggest` in the paths.
- **Inbound alerts** — needs `inbound_alerts` in the feature list. Note `ALERT_RULES_ENABLED` is a *separate* surface (the per-agent rule store and preferences page); check the snapshot before claiming it, and if it is off, say plainly that alerts fire on the built-in defaults and per-agent rules are not enabled.
- **Multimodal AI assist** — "Suggest a reply" reads image, video and audio attachments, and when a conversation has media the KB search is led by terms extracted from it. Write it as agent-facing behaviour, not architecture.

- [ ] **Step 3: Correct, append ledger rows, rebuild, commit** — as Task 5 Steps 3-6, with message `docs(feature-guide): chapter 2 — translate, FAQ strip, alerts and multimodal assist`

---

### Task 7: Chapter 4 — knowledge

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/04-knowledge.md`
- Modify: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: the ledger row format (Task 5)
- Produces: the Knowledge Settings coverage Task 11's Administration chapter cross-references for assistant persona and lifecycle messages

- [ ] **Step 1: Re-verify as in Task 5 Steps 1-2.** The routes behind this chapter are the `/kb/*` prefix and `/assist/*`. List by prefix.

- [ ] **Step 2: Check the FAQ suggestion strip's authoring side** — `faq_suggestion_popup` is in the live feature list and `/kb/suggest` is served, so the operator-facing half of that loop belongs in this chapter as well as chapter 2.

- [ ] **Step 3: Re-check the lifecycle-message claims against the snapshot.** v3 already had one correction here (`eb7ab9e`). Two are easy to get wrong because they read as if they were on: `LIFECYCLE_DISCLAIMER_ENABLED` and `LIFECYCLE_SURVEY_ENABLED` were both **off** at last reading, while Chatwoot's *native* CSAT was on for all four inboxes. Whatever the snapshot says now, state which mechanism is doing the work.

- [ ] **Step 4: Correct, append ledger rows, rebuild, commit** — as Task 5 Steps 3-6, message `docs(feature-guide): re-verify chapter 4 against the live tenant`

---

### Task 8: Chapters 5 and 6 — cases and RSA

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/05-cases.md`
- Modify: `docs/client-materials/feature-guide-src-v4/06-rsa.md`
- Modify: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: the ledger row format (Task 5)
- Produces: the taxonomy figures Task 10's reporting chapter reuses

- [ ] **Step 1: Re-verify as in Task 5 Steps 1-2.** Routes: the `/admin/taxonomy/*` and `/admin/customer360*` prefixes.

- [ ] **Step 2: Add the case-taxonomy admin page.** Patch `1ac8379`/`0062` put the taxonomy behind an admin page, and `0062`/`0063` added dialog editors. v3 documented the taxonomy as env-var configuration; if the admin page is live, that instruction is now wrong, not merely incomplete.

- [ ] **Step 3: Re-count the taxonomy against the live tenant.** v3 quotes 3 case types, 8 divisions, 89 Level-1 subcategories, 246 Level-2 details and 4 vehicle models. These are exactly the kind of number that drifts. Settle them from the tenant's own custom-attribute definitions, not from `case-categorisation.json` — the JSON is the source of truth for what *should* be provisioned, and this chapter describes what an operator will actually see in the dropdown.

- [ ] **Step 4: Correct, append ledger rows, rebuild, commit** — as Task 5 Steps 3-6, message `docs(feature-guide): chapters 5 and 6 — the taxonomy admin page, and a re-count`

---

### Task 9: Chapter 7 — reports

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/07-reports.md`
- Modify: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: the BigQuery finding from Task 3 Step 2, the taxonomy figures from Task 8
- Produces: the reporting-coverage statement Task 11 relies on when deciding whether AI Cost & Performance can come back

- [ ] **Step 1: Re-verify as in Task 5 Steps 1-2.** Routes: the `/metrics/*` prefix.

- [ ] **Step 2: Settle the reporting gap honestly.** `/metrics/targets` was absent at last reading and P8's eleven BigQuery views were never created. If Task 3 confirms both are still true, this chapter must say what reporting *does* cover and stop there. Do not describe a report an operator cannot open.

- [ ] **Step 3: Correct, append ledger rows, rebuild, commit** — as Task 5 Steps 3-6, message `docs(feature-guide): re-verify chapter 7 against the live tenant`

---

### Task 10: Chapter 10 — AI behaviour

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/10-ai-behaviour.md`
- Modify: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: the agent/backend flag state from Task 3, the multimodal write-up from Task 6
- Produces: the escalation-behaviour statement Task 12's playbooks cross-reference

- [ ] **Step 1: Re-verify as in Task 5 Steps 1-2.**

- [ ] **Step 2: Check the four flags this chapter turns on, individually.** `AGENT_MODE` (suggest vs auto changes what an agent *sees*, so getting it wrong misleads every reader), `DEPT_SUGGESTION_ENABLED`, `ESCALATION_ALL_CHANNELS_ENABLED` (unset at last reading, which means `escalate` is still Email-only even though the multi-channel code shipped — chapter 12 has scenarios that depend on this being stated correctly) and the `LIFECYCLE_*` set.

- [ ] **Step 3: Add media-grounded retrieval as AI behaviour.** Chapter 6 covers the agent's view of it; here it belongs as a statement about how the assistant decides what to retrieve.

- [ ] **Step 4: Correct, append ledger rows, rebuild, commit** — as Task 5 Steps 3-6, message `docs(feature-guide): re-verify chapter 10 against the live tenant`

---

### Task 11: Chapter 9 — administration, and the three held-back sections

The chapter with the most surface (30 KB) and the one the whole v3 cut-back was about.

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/09-administration.md`
- Modify: `docs/client-materials/feature-guide-v3-pending.md`
- Modify: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: the full Live state section (Task 3), the reporting statement (Task 9)
- Produces: the Administration sections Task 12's scenarios link to

- [ ] **Step 1: Re-verify the existing chapter as in Task 5 Steps 1-2.** Routes: the `/admin/*`, `/routing/*` and `/alerts/*` prefixes.

- [ ] **Step 2: Decide each held-back section on its probe, one at a time**

`feature-guide-v3-pending.md` holds three sections. The rule: a section comes back **only** if every surface it describes passes its probe. A section that is half-reachable does not come back half-written — restore the reachable part and leave the rest in the pending file.

- **Agent Availability & Workforce Dashboard** (P6). Needs the workforce dashboard and agent status selector in the live SPA, *and* `/routing/status` + `/routing/presence` in the OpenAPI paths. Those two were absent at last reading, and absent **by design** — `build_status_router` is flag-gated. So check the flag state as well as the paths: absent routes here mean a flag is off, not that code is missing, and the section's fate turns on which.
- **AI Conversational Quality** (P7). Needs `0055`/`0056` in the image, which they now are, and the backend routes behind them.
- **AI Cost & Performance Measurement** (P8). Needs `/metrics/targets` *and* the eleven BigQuery views. Expected outcome: stays out. If it does, leave it in the pending file with its `<!-- VERIFY-LIVE -->` comments intact and say so in the ledger.

- [ ] **Step 3: Add the admin surfaces that shipped after v3**

The agent status selector (`0065` — one place to set status, replacing two that disagreed), the Agent Channel Priorities editor (`0063`/`0064`) and the case-taxonomy admin page if Task 8 did not already cover it from the operator side.

- [ ] **Step 4: Restore the OUTLINE rows for anything that came back**

`feature-guide-v3-pending.md` says restored sections need their `OUTLINE.md` rows back. Put them in `feature-guide-src-v4/OUTLINE.md`, ahead of `## Account settings`, matching the position the pending file specifies.

- [ ] **Step 5: Update the pending file's header table**

Its three-row table says what each section still needs. Rewrite it for what is still true after this task — a stale pending file is how the next edition repeats this mistake.

- [ ] **Step 6: Correct, append ledger rows, rebuild, commit** — as Task 5 Steps 3-6, message `docs(feature-guide): chapter 9 — restore what the tenant can now actually reach`

---

### Task 12: Chapters 11, 12, 13 and 14 — scenarios, playbooks, integrations, glossary

These four are downstream of every earlier decision: they narrate and cross-reference rather than introduce. Doing them last means they are corrected once, against settled facts.

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/11-scenarios.md`
- Modify: `docs/client-materials/feature-guide-src-v4/12-channel-playbooks.md`
- Modify: `docs/client-materials/feature-guide-src-v4/13-integrations.md`
- Modify: `docs/client-materials/feature-guide-src-v4/14-glossary.md`
- Modify: `docs/client-materials/feature-guide-v4-verification.md`

**Interfaces:**
- Consumes: every earlier chapter task's corrections
- Produces: a chapter set with no internal contradictions, ready for Task 13's build

- [ ] **Step 1: Re-verify as in Task 5 Steps 1-2.**

- [ ] **Step 2: Walk each of the 17 scenarios against the corrections made in Tasks 5-11.** A scenario is a promise that a sequence of steps works. If any step's feature changed, the scenario changed. Two known landmines: scenarios 12 and 13 assert the `escalate` label's channel limitation, which depends on `ESCALATION_ALL_CHANNELS_ENABLED` (Task 10 Step 2); scenario 17 asserts a particular Escalation Routing configuration, and at last reading `dept_aftersales`, `dept_cs` and `dept_technical` had labels but **no PIC**, so escalating to them emailed nobody, silently.

- [ ] **Step 3: Check chapter 13's integration claims by prefix.** DMS is a mock client (`DMS_MOCK_CLIENT_ENABLED` was unset); do not let the chapter imply a live dealer-management-system integration.

- [ ] **Step 4: Add glossary entries for anything Tasks 6-11 introduced** — at minimum the terms an operator will meet in the UI for Translate, the FAQ suggestion strip, inbound alerts and agent status.

- [ ] **Step 5: Correct, append ledger rows, rebuild, commit** — as Task 5 Steps 3-6, message `docs(feature-guide): chapters 11-14 — scenarios and playbooks, against settled facts`

---

### Task 13: Phase A build — a complete v4 without the sweep

**Files:**
- Create: `docs/client-materials/PROTON - CRM Feature Guide v4.docx`

**Interfaces:**
- Consumes: `feature-guide-src-v4/` as edited by Tasks 5-12, `--no-placeholders` (Task 2)
- Produces: the shippable Phase A document, and the missing-shot list Task 14 works from

- [ ] **Step 1: Build**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
FG_SRC_DIR=feature-guide-src-v4 \
FG_OUT='PROTON - CRM Feature Guide v4.docx' \
FG_COVER_SUBTITLE='Operator Handbook — Edition 4, August 2026' \
rtk proxy python3 build_crm_feature_guide.py --no-placeholders 2>&1 | tail -80
```

Save the `Screenshots missing (omitted entirely):` list — Task 14's shot list is exactly that list.

- [ ] **Step 2: Verify zero placeholders and a sane document**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
python3 - <<'PY'
import zipfile
z = zipfile.ZipFile("PROTON - CRM Feature Guide v4.docx")
xml = z.read("word/document.xml").decode()
print("'Screenshot:' labels :", xml.count("Screenshot:"))
print("images embedded      :", sum(1 for n in z.namelist() if n.startswith("word/media/")))
print("size MB              : %.1f" % (len(open("PROTON - CRM Feature Guide v4.docx","rb").read())/1e6))
PY
```

Expected: `0` labels, 44 images, and a size in the same order as v3's 12.5 MB.

- [ ] **Step 3: Confirm both test files still pass**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy GOOGLE_API_KEY=test-key uv run pytest \
  ../../../scripts/test_build_feature_guide_screenshots.py \
  ../../../scripts/test_build_feature_guide_audiences.py -q
```

Expected: all pass.

- [ ] **Step 4: Confirm v3 is still untouched**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git status --short "docs/client-materials/PROTON - CRM Feature Guide v3.docx" \
                   docs/client-materials/feature-guide-src-v3/
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add "docs/client-materials/PROTON - CRM Feature Guide v4.docx"
git commit -m "docs(feature-guide): build v4 — re-verified, and with no empty boxes

Phase A: all 14 chapters checked against the live tenant (.git_sha 0866fda,
image -rc6, 113 backend routes) with a ledger row per claim. The 59 markers
with no PNG behind them now render as nothing at all rather than a shaded
box. The capture sweep is Phase B."
```

- [ ] **Step 6: Show the user**

Report: chapters changed, claims verified/corrected/removed, which held-back sections came back, and the count of markers still without a PNG. **Stop here for review before Phase B.**

---

### Task 14: Phase B — the screenshot sweep

**Files:**
- Create: PNGs under `docs/client-materials/feature-guide-assets/`

**Interfaces:**
- Consumes: the missing-shot list from Task 13 Step 1
- Produces: PNGs named exactly `<shot_id>.png` for the ids the markers already use, so no marker text changes

- [ ] **Step 1: Load the browser tools in one call**

```
ToolSearch: select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__find
```

Then call `tabs_context_mcp` first and create a **new** tab. Never reuse a tab id from an earlier session.

- [ ] **Step 2: Confirm the session, and stop if it is not logged in**

Navigate to `http://proton.crm.34-50-103-151.nip.io/app/accounts/1/dashboard`. The user's Chrome profile is expected to already hold a session. If a login form appears instead, **do not type credentials** — ask the user to log in themselves and wait.

Rails takes 60-90s to become healthy after a recreate; if a page will not load, check that before concluding a feature is broken.

- [ ] **Step 3: Triage the missing list into three buckets**

- **Navigate-and-shoot** — a settings or admin page that exists regardless of data. Most `ch09-*`, plus `ch01-login`, `ch08-*`, `ch04-*`, `ch05-*`, `ch07-*`.
- **Find-the-state** — needs a conversation that already shows something (an SLA breach note, a department-suggestion note, escalation labels in order). Find one by searching the existing conversations. **Never create the state.**
- **Not a screenshot** — describes a sequence or a concept rather than a screen. Record it and move on; it drops out in Task 15.

Record the bucket for every id. An id nobody triaged is an id that silently disappears.

- [ ] **Step 4: Capture, in bucket order, committing every 10-15 shots**

Read-only, as Global Constraints require: no message sent, no setting saved, no destructive control clicked, nothing that could fire escalation mail. Match the existing assets — same browser width, same dark theme as `ch02-labels.png`.

Save each as `docs/client-materials/feature-guide-assets/<shot_id>.png`, using the id from the marker verbatim.

Commit in batches so a failure late in the sweep does not cost the whole thing:

```bash
git add docs/client-materials/feature-guide-assets/
git commit -m "docs(feature-guide): capture <area> screenshots from the live tenant"
```

- [ ] **Step 5: Stop and ask if the browser fights back**

If a page will not render, a control will not respond, or a tool errors, after 2-3 attempts: stop, report what was tried and what happened, and ask. Do not keep retrying and do not wander the tenant looking for something else to shoot.

---

### Task 15: Drop the empty markers and rebuild

**Files:**
- Modify: `docs/client-materials/feature-guide-src-v4/*.md` (delete unbacked marker lines)
- Modify: `docs/client-materials/feature-guide-v4-verification.md`
- Modify: `docs/client-materials/PROTON - CRM Feature Guide v4.docx`

**Interfaces:**
- Consumes: the PNGs from Task 14, the triage record from Task 14 Step 3
- Produces: the final deliverable

- [ ] **Step 1: List what is still unbacked**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
rtk proxy grep -oh '\[\[SCREENSHOT: *[a-z0-9-]*' feature-guide-src-v4/*.md \
  | sed 's/.*: *//' | sort -u > /tmp/v4-ref.txt
ls feature-guide-assets/*.png | xargs -n1 basename | sed 's/\.png$//' | sort > /tmp/v4-have.txt
comm -23 /tmp/v4-ref.txt /tmp/v4-have.txt
```

- [ ] **Step 2: Delete each unbacked marker line from its chapter**

Delete the whole `[[SCREENSHOT: ...]]` line and the blank line that follows it, so no double blank is left behind. A dangling marker in the source is how a placeholder comes back by accident in a later edition — the flag suppresses the box, it does not remove the reference.

- [ ] **Step 3: Record every deletion in the ledger**

Add a `## Screenshots not captured` section to `feature-guide-v4-verification.md`: shot id, its caption, and why — "not a single screen", "state does not exist on the tenant", "page would not render". This is the list whoever builds v5 starts from.

- [ ] **Step 4: Rebuild and prove the invariants**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
FG_SRC_DIR=feature-guide-src-v4 \
FG_OUT='PROTON - CRM Feature Guide v4.docx' \
FG_COVER_SUBTITLE='Operator Handbook — Edition 4, August 2026' \
rtk proxy python3 build_crm_feature_guide.py --no-placeholders 2>&1 | tail -30
```

Expected: `Screenshots found  : N/N` — found equals total, and **no** `Screenshots missing` block at all. That is success criterion 3 met: no marker in the source lacks a PNG.

- [ ] **Step 5: Final verification against all five success criteria**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/docs/client-materials
python3 - <<'PY'
import zipfile
z = zipfile.ZipFile("PROTON - CRM Feature Guide v4.docx")
xml = z.read("word/document.xml").decode()
print("criterion 1 — 'Screenshot:' labels:", xml.count("Screenshot:"), "(want 0)")
print("images embedded:", sum(1 for n in z.namelist() if n.startswith("word/media/")))
PY
cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend
GEMINI_API_KEY=dummy GOOGLE_API_KEY=test-key uv run pytest \
  ../../../scripts/test_build_feature_guide_screenshots.py \
  ../../../scripts/test_build_feature_guide_audiences.py -q
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git status --short "docs/client-materials/PROTON - CRM Feature Guide v3.docx" \
                   docs/client-materials/feature-guide-src-v3/
```

Expected: 0 labels; both test files pass (criterion 4); no output from `git status` (criterion 1's frozen-v3 half). Criterion 2 is the ledger having a row per claim — check it by eye. Criterion 5 is that nothing was written to the tenant — assert it only if Task 14 genuinely stayed read-only.

- [ ] **Step 6: Commit**

```bash
git add docs/client-materials/feature-guide-src-v4 \
        docs/client-materials/feature-guide-v4-verification.md \
        "docs/client-materials/PROTON - CRM Feature Guide v4.docx"
git commit -m "docs(feature-guide): v4 final — every marker has a screenshot behind it

Phase B: captured what the live tenant could show, and deleted the markers
for what it could not, so the source carries no reference the build has to
silently swallow. The ledger records each deletion and why."
```

- [ ] **Step 7: Update the project memory**

Append to `current-state-and-next.md`: v4 exists, what it covers, and the correction that the live tenant reached dev-yuda HEAD (`0866fda`, image `-rc6`, 113 backend routes) — the deploy memory still describes the `-rc1`/59-patch state. Add a pointer line to `MEMORY.md` only if a new memory file was created.
