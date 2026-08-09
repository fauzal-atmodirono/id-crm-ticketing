#!/usr/bin/env python3
"""PROTON CRM Feature Guide — operator handbook builder.

Renders the 14 hand-drafted markdown chapters in `feature-guide-src-v3/`
(01-introduction.md ... 14-glossary.md) into the client's Google Docs
template (`Google Docs template - Short version.docx`), producing
`PROTON - CRM Feature Guide v3.docx`.

`SRC_DIR`/`OUT`/`COVER_TITLE`/`COVER_SUBTITLE` are overridable via the
`FG_SRC_DIR`/`FG_OUT`/`FG_COVER_TITLE`/`FG_COVER_SUBTITLE` env vars, so a
further edition can be built without editing this file. The v1 and v2
sources and outputs live under `archive/`; to rebuild one, point
`FG_SRC_DIR`/`FG_OUT` at the archived paths.

The markdown subset implemented here is deliberately narrow — it's exactly
what the chapter files use, no more:
  - `#`.."####" headings -> Heading 1..4
  - paragraphs with inline **bold**, *italic*, `code` (code -> Courier New)
  - `-` bullets, one level of nesting -> List Bullet / List Bullet 2
    (falls back to a manual indent + bullet character if those styles
    aren't defined in the template — which is the case here)
  - `1.` numbered lists -> List Number (same fallback: manual "N. " prefix)
  - pipe tables with a `|---|` separator row -> a bordered table, bold header
  - `> ` blockquotes -> a left-bordered, lightly shaded, italic paragraph
  - `[[SCREENSHOT: id | caption]]` on its own line -> the PNG at
    feature-guide-assets/<id>.png if present, else a bordered placeholder
    box, so the build never fails on a missing screenshot
  - `<!-- ... -->` HTML comments (including inline trailing ones) are
    stripped entirely, whether they sit on their own line or trail real
    content

Plain stdlib + python-docx script, constants at the top, no CLI args —
matches the style of the build scripts in docs/proposals/.
"""
import glob
import os
import re

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE, "Google Docs template - Short version.docx")
ASSETS_DIR = os.path.join(BASE, "feature-guide-assets")

# SRC_DIR / OUT / COVER_TITLE / COVER_SUBTITLE are overridable by environment
# variable so a further edition living in its own source dir can be built
# without touching this script. The defaults track the CURRENT edition (v3);
# v1/v2 moved under archive/ on 2026-08-09 and are rebuilt by pointing
# FG_SRC_DIR/FG_OUT at those archived paths.
SRC_DIR = os.environ.get("FG_SRC_DIR", os.path.join(BASE, "feature-guide-src-v3"))
OUT = os.environ.get(
    "FG_OUT", os.path.join(BASE, "PROTON - CRM Feature Guide v3.docx")
)

COVER_TITLE = os.environ.get(
    "FG_COVER_TITLE", "PROTON e.MAS — CRM Feature Guide"
)
COVER_SUBTITLE = os.environ.get(
    "FG_COVER_SUBTITLE", "Operator Handbook — August 2026"
)

BORDER_COLOR = "4472C4"  # a muted blue, used for table borders and the
                          # blockquote/placeholder left accent
NOTE_SHADE = "F2F2F2"    # light grey shading for blockquote paragraphs

# ---------------------------------------------------------------------------
# Markdown line patterns
# ---------------------------------------------------------------------------
COMMENT_RE = re.compile(r"<!--.*?-->")
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
SCREENSHOT_RE = re.compile(r"^\[\[SCREENSHOT:\s*(.+?)\s*\|\s*(.+?)\]\]\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")
BULLET_RE = re.compile(r"^(\s*)-\s+(.*)$")
NUMBER_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
INLINE_RE = re.compile(
    r"(?P<bold>\*\*(?P<boldtext>.+?)\*\*)"
    r"|(?P<italic>\*(?P<italictext>.+?)\*)"
    r"|(?P<code>`(?P<codetext>.+?)`)"
)


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------
def style_exists(document, name):
    try:
        document.styles[name]
        return True
    except KeyError:
        return False


def sanitize_page_margins(document):
    """The template's pgMar twip values are non-integers (a Google Docs
    export quirk, e.g. "1440.0000000000002"), which crashes python-docx's
    integer-only Twips parser the first time anything (like add_table)
    reads section.left_margin. Round them to whole twips in place — this
    doesn't perceptibly change the page setup, just makes it parseable."""
    body = document.element.body
    sectPr = body.find(qn("w:sectPr"))
    if sectPr is None:
        return
    pgMar = sectPr.find(qn("w:pgMar"))
    if pgMar is None:
        return
    for attr in ("top", "right", "bottom", "left", "header", "footer", "gutter"):
        key = qn("w:%s" % attr)
        val = pgMar.get(key)
        if val is not None:
            try:
                pgMar.set(key, str(int(round(float(val)))))
            except ValueError:
                pass


def clear_body(document):
    """Delete every existing body paragraph/table, keep page setup (sectPr)."""
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def set_cell_shading(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def set_table_borders(table, color=BORDER_COLOR, sz=4):
    tbl = table._tbl
    tblPr = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement("w:%s" % edge)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tblPr.append(borders)


def add_left_border_and_shade(paragraph, color=BORDER_COLOR, fill=NOTE_SHADE):
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), color)
    pBdr.append(left)
    pPr.append(pBdr)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pPr.append(shd)


def scan_headings(chapter_paths):
    """Collect every chapter (#) and section (##) heading, in document order,
    pairing each with the bookmark name the TOC will link to.

    Deliberately a second read of the same files rather than a hook inside
    process_chapter: the table of contents is written before any chapter is
    rendered, so the full list has to exist first. Reading a handful of small
    markdown files twice is cheaper than buffering the whole document.

    Returns [(level, text, bookmark_name)]. Bookmark names use Word's reserved
    `_Toc` prefix and are pure ASCII, because a heading's own text may contain
    em dashes, ampersands and non-breaking punctuation that are not legal in a
    bookmark name."""
    entries = []
    for path in chapter_paths:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = COMMENT_RE.sub("", raw).rstrip()
                m = HEADING_RE.match(line)
                if not m or len(m.group(1)) > 2:
                    continue
                entries.append(
                    (len(m.group(1)), m.group(2).strip(), "_Toc%05d" % len(entries))
                )
    return entries


def add_bookmark(paragraph, name, bookmark_id):
    """Wrap a paragraph in a bookmarkStart/bookmarkEnd pair so a TOC entry can
    link to it."""
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bookmark_id))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bookmark_id))
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def add_toc_entry(document, level, text, anchor):
    """One line of the table of contents: an internal hyperlink to `anchor`.

    A static, pre-rendered TOC rather than a Word `TOC` field. A field is only
    text once an application evaluates it — Word does that on F9, but Google
    Docs cannot evaluate Word fields at all, and on import it degrades the
    field into raw heading anchors (`?tab=t.0#heading=...`), which is what the
    reader then sees instead of a contents list. Real paragraphs with real
    internal links render identically in Word, Google Docs, LibreOffice and
    any PDF export, and need no "update field" step from the reader.

    The trade is page numbers: computing them needs a layout engine, which
    python-docx is not. Clickable entries are the better half of that trade
    for a document read on screen."""
    paragraph = document.add_paragraph(style="normal")
    fmt = paragraph.paragraph_format
    fmt.left_indent = Inches(0.0 if level == 1 else 0.35)
    fmt.space_after = Pt(2)

    link = OxmlElement("w:hyperlink")
    link.set(qn("w:anchor"), anchor)

    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    if level == 1:
        bold = OxmlElement("w:b")
        rPr.append(bold)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1155CC" if level == 2 else "000000")
    rPr.append(color)
    if level == 2:
        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        rPr.append(underline)
    run.append(rPr)

    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(t)

    link.append(run)
    paragraph._p.append(link)
    return paragraph


# ---------------------------------------------------------------------------
# Inline formatting (bold / italic / code)
# ---------------------------------------------------------------------------
def add_inline_runs(paragraph, text, base_italic=False, base_bold=False):
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            run = paragraph.add_run(text[pos : m.start()])
            run.bold = base_bold or None
            run.italic = base_italic or None
        if m.group("bold") is not None:
            run = paragraph.add_run(m.group("boldtext"))
            run.bold = True
            run.italic = base_italic or None
        elif m.group("italic") is not None:
            run = paragraph.add_run(m.group("italictext"))
            run.italic = True
            run.bold = base_bold or None
        elif m.group("code") is not None:
            run = paragraph.add_run(m.group("codetext"))
            run.font.name = "Courier New"
            run.bold = base_bold or None
            run.italic = base_italic or None
        pos = m.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        run.bold = base_bold or None
        run.italic = base_italic or None


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------
def add_screenshot(document, shot_id, caption, found, missing):
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


# ---------------------------------------------------------------------------
# Markdown block parsing
# ---------------------------------------------------------------------------
def split_into_blocks(text):
    cleaned = []
    for line in text.split("\n"):
        cleaned.append(COMMENT_RE.sub("", line).rstrip())

    blocks = []
    current = []
    for line in cleaned:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def classify_block(block):
    first = block[0]

    m = HEADING_RE.match(first)
    if m and len(block) == 1:
        return ("heading", len(m.group(1)), m.group(2).strip())

    m = SCREENSHOT_RE.match(first)
    if m and len(block) == 1:
        return ("screenshot", m.group(1).strip(), m.group(2).strip())

    if first.lstrip().startswith("|") and len(block) >= 2 and TABLE_SEP_RE.match(
        block[1]
    ):
        rows = [block[0]] + block[2:]
        parsed_rows = []
        for row in rows:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            parsed_rows.append(cells)
        return ("table", parsed_rows)

    if all(line.lstrip().startswith(">") for line in block):
        quote_lines = [re.sub(r"^\s*>\s?", "", line) for line in block]
        return ("blockquote", " ".join(l.strip() for l in quote_lines))

    if BULLET_RE.match(first) or NUMBER_RE.match(first):
        items = []
        for line in block:
            bm = BULLET_RE.match(line)
            nm = NUMBER_RE.match(line)
            if bm is not None:
                indent, rest = bm.group(1), bm.group(2)
                level = 1 if len(indent) > 0 else 0
                items.append({"kind": "bullet", "level": level, "text": rest})
            elif nm is not None:
                indent, num, rest = nm.group(1), nm.group(2), nm.group(3)
                items.append(
                    {"kind": "number", "level": 0, "num": num, "text": rest}
                )
            else:
                # continuation line of the previous item's wrapped text
                if items:
                    items[-1]["text"] += " " + line.strip()
        return ("list", items)

    text = " ".join(line.strip() for line in block)
    return ("paragraph", text)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_heading(document, level, text, bookmarks=None):
    heading = document.add_paragraph(style="Heading %d" % level)
    add_inline_runs(heading, text)
    # Anchor for the matching table-of-contents entry. Looked up by
    # (level, text) and popped, rather than by position, so that a heading
    # the block parser and the TOC pre-scan disagree about can only lose its
    # link — never silently hand its bookmark to a different heading.
    if bookmarks:
        pending = bookmarks.get((level, text))
        if pending:
            name = pending.pop(0)
            add_bookmark(heading, name, abs(hash(name)) % 900000 + 1000)
    return heading


def render_paragraph(document, text):
    p = document.add_paragraph(style="normal")
    add_inline_runs(p, text)


def render_blockquote(document, text):
    p = document.add_paragraph(style="normal")
    add_left_border_and_shade(p)
    p.paragraph_format.left_indent = Inches(0.3)
    add_inline_runs(p, text, base_italic=True)


def render_list(document, items, use_bullet_style, use_number_style):
    for item in items:
        level = item.get("level", 0)
        if item["kind"] == "bullet":
            style_name = "List Bullet" if level == 0 else "List Bullet 2"
            if use_bullet_style:
                p = document.add_paragraph(style=style_name)
                add_inline_runs(p, item["text"])
            else:
                p = document.add_paragraph(style="normal")
                p.paragraph_format.left_indent = Inches(0.25 * (level + 1))
                bullet_char = "•" if level == 0 else "◦"
                run = p.add_run("%s  " % bullet_char)
                add_inline_runs(p, item["text"])
        else:  # number
            if use_number_style:
                p = document.add_paragraph(style="List Number")
                add_inline_runs(p, item["text"])
            else:
                p = document.add_paragraph(style="normal")
                p.paragraph_format.left_indent = Inches(0.25)
                p.add_run("%s.  " % item["num"])
                add_inline_runs(p, item["text"])


def render_table(document, rows):
    n_cols = max(len(r) for r in rows)
    table = document.add_table(rows=len(rows), cols=n_cols)
    set_table_borders(table)
    for r_idx, row in enumerate(rows):
        for c_idx in range(n_cols):
            text = row[c_idx] if c_idx < len(row) else ""
            cell = table.cell(r_idx, c_idx)
            cell.paragraphs[0].text = ""
            p = cell.paragraphs[0]
            add_inline_runs(p, text, base_bold=(r_idx == 0))
            if r_idx == 0:
                set_cell_shading(cell, NOTE_SHADE)
    document.add_paragraph(style="normal")
    return n_cols


# ---------------------------------------------------------------------------
# Chapter processing
# ---------------------------------------------------------------------------
def process_chapter(document, path, use_bullet_style, use_number_style, stats,
                    bookmarks=None):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = [classify_block(b) for b in split_into_blocks(text)]

    for block in blocks:
        kind = block[0]
        if kind == "heading":
            _, level, heading_text = block
            render_heading(document, level, heading_text, bookmarks)
            if level == 2:
                stats["sections"] += 1
        elif kind == "paragraph":
            render_paragraph(document, block[1])
        elif kind == "blockquote":
            render_blockquote(document, block[1])
        elif kind == "list":
            render_list(document, block[1], use_bullet_style, use_number_style)
        elif kind == "table":
            n_cols = render_table(document, block[1])
            stats["tables"] += 1
        elif kind == "screenshot":
            _, shot_id, caption = block
            add_screenshot(document, shot_id, caption, stats["found"], stats["missing"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    os.makedirs(ASSETS_DIR, exist_ok=True)
    gitkeep = os.path.join(ASSETS_DIR, ".gitkeep")
    if not os.path.exists(gitkeep):
        open(gitkeep, "w").close()

    document = Document(TEMPLATE)
    sanitize_page_margins(document)
    clear_body(document)

    use_bullet_style = style_exists(document, "List Bullet") and style_exists(
        document, "List Bullet 2"
    )
    use_number_style = style_exists(document, "List Number")

    # --- Cover page ---
    title_p = document.add_paragraph(COVER_TITLE, style="Title")
    subtitle_p = document.add_paragraph(COVER_SUBTITLE, style="Subtitle")
    document.add_page_break()

    # --- Chapters (resolved first: the TOC is written before them) ---
    chapter_paths = sorted(
        p
        for p in glob.glob(os.path.join(SRC_DIR, "*.md"))
        if re.match(r"^\d{2}-.*\.md$", os.path.basename(p))
    )
    toc_entries = scan_headings(chapter_paths)
    bookmarks = {}
    for level, text, name in toc_entries:
        bookmarks.setdefault((level, text), []).append(name)

    # --- Table of contents ---
    # Deliberately NOT "Heading 1"/"Heading 2": those are reserved for the
    # 14 chapter titles and their sections, and this label would otherwise
    # appear as an entry in its own list.
    toc_heading = document.add_paragraph(style="normal")
    toc_run = toc_heading.add_run("Table of Contents")
    toc_run.bold = True
    toc_run.font.size = Pt(20)
    for level, text, name in toc_entries:
        add_toc_entry(document, level, text, name)
    document.add_page_break()

    stats = {"sections": 0, "tables": 0, "found": [], "missing": []}

    for i, path in enumerate(chapter_paths):
        if i > 0:
            document.add_page_break()
        process_chapter(
            document, path, use_bullet_style, use_number_style, stats, bookmarks
        )

    document.save(OUT)

    print("PROTON CRM Feature Guide build summary")
    print("=" * 40)
    print("Chapters processed : %d" % len(chapter_paths))
    for path in chapter_paths:
        print("  - %s" % os.path.basename(path))
    print("Sections (##)      : %d" % stats["sections"])
    print("TOC entries        : %d" % len(toc_entries))
    unlinked = sum(len(v) for v in bookmarks.values())
    print("TOC entries unlinked: %d" % unlinked)
    print("Tables rendered    : %d" % stats["tables"])
    print(
        "Screenshots found  : %d/%d"
        % (len(stats["found"]), len(stats["found"]) + len(stats["missing"]))
    )
    if stats["missing"]:
        print("Screenshots missing (rendered as placeholders):")
        for shot_id in stats["missing"]:
            print("  - %s" % shot_id)
    print("Output: %s" % OUT)


if __name__ == "__main__":
    main()
