#!/usr/bin/env python3
"""Dump a Google Doc's paragraph + table structure to plan rewrites.

Usage:
  # First, dump the doc JSON via the gws CLI. The 'Using keyring backend' status
  # line goes to STDERR, so a plain redirect is already clean JSON —
  # do NOT pipe through `tail -n +2`, it would eat the first line of the payload.
  gws docs documents get --params '{"documentId":"<DOC_ID>"}' --format json > /tmp/doc.json

  # Then inspect:
  python inspect_doc.py /tmp/doc.json
  python inspect_doc.py /tmp/doc.json --tables-only
  python inspect_doc.py /tmp/doc.json --search "Finnet"
  python inspect_doc.py /tmp/doc.json --search "{{ARCHITECTURE_DIAGRAM}}"

Inline images are printed as `[IMAGE objectId=… idx=N]`. `idx` is the single body
index the image occupies — delete it with
`deleteContentRange {"startIndex": N, "endIndex": N + 1}` (SKILL.md Phase D step 2).
"""

import argparse
import json
import sys


def cell_text(cell):
    parts = []
    for el in cell.get("content", []):
        if "paragraph" in el:
            for run in el["paragraph"].get("elements", []):
                tr = run.get("textRun", {})
                parts.append(tr.get("content", ""))
    return "".join(parts).rstrip("\n")


def paragraph_text(para_el):
    runs = para_el.get("paragraph", {}).get("elements", [])
    return "".join(r.get("textRun", {}).get("content", "") for r in runs)


def paragraph_images(para_el):
    """Return [(objectId, startIndex)] for every inline image in this paragraph.

    An inline image occupies exactly one body index, so its element's startIndex
    is the index to pass to deleteContentRange (start=idx, end=idx+1).
    """
    found = []
    for r in para_el.get("paragraph", {}).get("elements", []):
        obj = r.get("inlineObjectElement")
        if obj:
            found.append((obj.get("inlineObjectId", "?"), r.get("startIndex")))
    return found


def paragraph_render(para_el):
    """Text of a paragraph with inline images rendered as visible markers."""
    parts = []
    for r in para_el.get("paragraph", {}).get("elements", []):
        if "textRun" in r:
            parts.append(r["textRun"].get("content", ""))
        elif "inlineObjectElement" in r:
            obj = r["inlineObjectElement"]
            parts.append(
                f"[IMAGE objectId={obj.get('inlineObjectId', '?')} idx={r.get('startIndex')}]"
            )
    return "".join(parts)


def dump_paragraphs(content):
    for el in content:
        if "paragraph" in el:
            # Render images as markers so image-only paragraphs are never invisible.
            text = paragraph_render(el).replace("\n", "⏎")
            start = el.get("startIndex", 0)
            end = el.get("endIndex", 0)
            # Do not let the strip() filter suppress a paragraph that holds only
            # an inline image — locating the legacy diagram depends on it.
            if text.strip() or paragraph_images(el):
                print(f"  [{start}-{end}] {text}")


def dump_tables(content):
    tables = [el for el in content if "table" in el]
    print(f"Total tables: {len(tables)}\n")
    for ti, el in enumerate(tables):
        t = el["table"]
        rows = t["tableRows"]  # NOTE: tableRows is the array; rows is the count
        print(
            f"========== TABLE {ti} (rows={len(rows)} cols={t['columns']} "
            f"start={el.get('startIndex')}) =========="
        )
        for ri, row in enumerate(rows):
            for ci, cell in enumerate(row["tableCells"]):
                txt = cell_text(cell)
                if len(txt) > 200:
                    txt = txt[:200] + "..."
                print(f"  T{ti}R{ri}C{ci}: {txt}")
        print()


def search_doc(content, needle):
    """Find all places needle appears, returning (location, context)."""
    hits = []
    for el in content:
        if "paragraph" in el:
            text = paragraph_text(el)
            if needle in text:
                hits.append(
                    (f"para[{el.get('startIndex')}-{el.get('endIndex')}]", text.strip())
                )
        elif "table" in el:
            for ri, row in enumerate(el["table"]["tableRows"]):
                for ci, cell in enumerate(row["tableCells"]):
                    txt = cell_text(cell)
                    if needle in txt:
                        hits.append(
                            (
                                f"Table@{el.get('startIndex')} R{ri}C{ci}",
                                txt,
                            )
                        )
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc_json", help="Path to doc JSON dump (cleaned)")
    ap.add_argument("--tables-only", action="store_true", help="Show only tables")
    ap.add_argument("--paragraphs-only", action="store_true", help="Show only paragraphs")
    ap.add_argument(
        "--search",
        metavar="STRING",
        help="Search the doc for STRING; print all locations + occurrence count",
    )
    args = ap.parse_args()

    with open(args.doc_json) as f:
        try:
            d = json.load(f)
        except json.JSONDecodeError as e:
            print(
                f"Failed to parse {args.doc_json}: {e}\n"
                "Re-dump it — gws writes its status line to stderr, so a plain "
                "redirect of stdout is already clean JSON:\n"
                "  gws docs documents get --params '{\"documentId\":\"<ID>\"}' "
                "--format json > /tmp/doc.json\n"
                "Do NOT pipe through `tail -n +2`; that deletes the first line of "
                "the real payload.",
                file=sys.stderr,
            )
            sys.exit(1)

    content = d.get("body", {}).get("content", [])
    print(f"Doc has {len(content)} top-level elements\n")

    if args.search:
        hits = search_doc(content, args.search)
        print(f"=== Search '{args.search}': {len(hits)} hits ===")
        for loc, text in hits:
            snippet = text if len(text) < 200 else text[:200] + "..."
            print(f"  {loc}: {snippet}")
        return

    if not args.paragraphs_only:
        dump_tables(content)

    if not args.tables_only:
        print("========== PARAGRAPHS ==========")
        dump_paragraphs(content)


if __name__ == "__main__":
    main()
