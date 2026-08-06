#!/usr/bin/env python3
"""Scan a Google Doc for residual template strings + check required content is present.

Usage:
  python verify_residuals.py <DOC_ID> --raw \
    --residuals Finnet Fincx Tableau '{{' '}}' \
    --required '<CLIENT_SHORT_NAME>' BigQuery \
    --allow-prefix '{{TBD'

  # --residuals     strings that must not appear at all
  # --required      strings that must appear at least once
  # --allow-prefix  repeatable; matches starting with this prefix are NOT failures.
  #                 They are subtracted from the residual count and reported
  #                 separately as "open items". Use '{{TBD' so deliberate
  #                 {{TBD — …}} placeholders do not fail the mandated '{{' scan.
  # --raw           also grep the serialized Docs API JSON, which covers hyperlink
  #                 URLs, smart-chip title/uri metadata and image alt-text — none of
  #                 which appear in the flattened body text.

Exits 1 if any residual term has a disallowed count > 0 OR any required term has
count == 0. Deliberate --allow-prefix matches never cause a non-zero exit.
"""

import argparse
import json
import subprocess
import sys


def fetch_doc(doc_id: str) -> dict:
    proc = subprocess.run(
        [
            "gws",
            "docs",
            "documents",
            "get",
            "--params",
            json.dumps({"documentId": doc_id}),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"gws CLI failed:\n{proc.stderr}")
    out = proc.stdout
    if not out.lstrip().startswith("{"):
        out = out[out.index("{"):]
    return json.loads(out)


def all_text(d: dict) -> str:
    parts = []

    def walk(elements):
        for el in elements:
            if "paragraph" in el:
                for run in el["paragraph"].get("elements", []):
                    parts.append(run.get("textRun", {}).get("content", ""))
            elif "table" in el:
                for row in el["table"]["tableRows"]:
                    for cell in row["tableCells"]:
                        walk(cell.get("content", []))

    walk(d.get("body", {}).get("content", []))
    return "".join(parts)


def raw_text(d: dict) -> str:
    """The serialized Docs API JSON.

    all_text() only sees paragraph textRuns and table cells. Hyperlink URLs live in
    textStyle.link.url, smart chips in richLink.richLinkProperties (title + uri), and
    image alt-text in inlineObjects[*].imageProperties — none of which it can reach.
    Grepping the serialized document covers all of them.
    """
    return json.dumps(d, ensure_ascii=False)


def raw_scannable(term: str) -> bool:
    """False for terms that are pure JSON structural noise in the raw dump.

    json.dumps never emits '{{' (a '{' is always followed by '"'), so scanning for
    '{{' against raw JSON is meaningful. But '}}' closes every nested object and
    would match hundreds of times, so it is skipped in raw mode instead of
    reported as a fake failure.
    """
    return bool(term.strip("}")) or not term


def allowed_spans(text: str, prefixes: list[str]) -> list[tuple[int, int]]:
    """Character spans that --allow-prefix marks as deliberate, not defects.

    A prefix that opens with '{{' (e.g. '{{TBD') extends its span to the closing
    '}}', so the whole '{{TBD — … }}' placeholder is covered — including its
    closing braces, which a '}}' residual scan would otherwise flag.
    """
    spans: list[tuple[int, int]] = []
    for p in prefixes:
        if not p:
            continue
        start = 0
        while True:
            i = text.find(p, start)
            if i < 0:
                break
            if p.startswith("{{"):
                j = text.find("}}", i + len(p))
                end = j + 2 if j >= 0 else i + len(p)
            else:
                end = i + len(p)
            spans.append((i, end))
            start = i + len(p)
    return spans


def count_term(text: str, term: str, spans: list[tuple[int, int]]) -> tuple[int, int]:
    """Return (disallowed_count, allowed_count) for term, honouring allowed spans."""
    disallowed = allowed = 0
    start = 0
    while True:
        i = text.find(term, start)
        if i < 0:
            break
        end = i + len(term)
        if any(s <= i and end <= e for s, e in spans):
            allowed += 1
        else:
            disallowed += 1
        start = end
    return disallowed, allowed


def open_items(text: str, spans: list[tuple[int, int]]) -> list[str]:
    """The literal text of each allowed span, de-duplicated, in document order."""
    seen: list[str] = []
    for s, e in sorted(set(spans)):
        snippet = " ".join(text[s:e].split())
        if snippet and snippet not in seen:
            seen.append(snippet)
    return seen


def scan(label: str, text: str, terms: list[str], prefixes: list[str], skip_noise: bool):
    """Print a residual block for one text surface. Returns True if it failed."""
    spans = allowed_spans(text, prefixes)
    failed = False
    print(f"=== Residual check — {label} (should be 0) ===")
    for term in terms:
        if skip_noise and not raw_scannable(term):
            print(f"  [SKIP] {term!r}: n/a — matches JSON structure, not content")
            continue
        bad, ok = count_term(text, term, spans)
        mark = "PASS" if bad == 0 else "FAIL"
        if bad > 0:
            failed = True
        suffix = f"  ({ok} allowed by --allow-prefix)" if ok else ""
        print(f"  [{mark}] {term!r}: {bad}{suffix}")
    return failed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc_id")
    ap.add_argument(
        "--residuals",
        nargs="*",
        default=[],
        metavar="TERM",
        help="Strings that should have count == 0",
    )
    ap.add_argument(
        "--required",
        nargs="*",
        default=[],
        metavar="TERM",
        help="Strings that should have count >= 1",
    )
    ap.add_argument(
        "--allow-prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        help=(
            "Repeatable. Residual matches inside a run starting with PREFIX are not "
            "failures; they are subtracted from the count and listed as open items. "
            "Use '{{TBD' so deliberate {{TBD — …}} placeholders do not fail the "
            "mandated '{{' scan."
        ),
    )
    ap.add_argument(
        "--raw",
        action="store_true",
        help=(
            "Also scan the serialized Docs API JSON, covering hyperlink URLs, "
            "smart-chip title/uri metadata and image alt-text, which the flattened "
            "body text cannot see."
        ),
    )
    args = ap.parse_args()

    d = fetch_doc(args.doc_id)
    text = all_text(d)
    print(f"Doc length: {len(text)} chars\n")

    fail = False

    if args.residuals:
        fail |= scan("body text", text, args.residuals, args.allow_prefix, skip_noise=False)
        if args.raw:
            raw = raw_text(d)
            print(f"\nRaw Docs JSON: {len(raw)} chars")
            fail |= scan(
                "raw Docs JSON (hyperlinks, smart chips, alt-text)",
                raw,
                args.residuals,
                args.allow_prefix,
                skip_noise=True,
            )
        else:
            print(
                "\n  note: body text only. Re-run with --raw to also cover hyperlink "
                "URLs, smart-chip metadata and image alt-text."
            )

    if args.required:
        print("\n=== Required content check (should be >= 1) ===")
        for term in args.required:
            n = text.count(term)
            mark = "PASS" if n > 0 else "FAIL"
            if n == 0:
                fail = True
            print(f"  [{mark}] {term!r}: {n}")

    if args.allow_prefix:
        items = open_items(text, allowed_spans(text, args.allow_prefix))
        print(f"\n=== Open items — allowed placeholders, not failures ({len(items)}) ===")
        if not items:
            print("  (none)")
        for item in items:
            print(f"  - {item}")
        if items:
            print("  Report every one of these back to the engineer with its section.")

    print("\n" + ("OK — all checks passed" if not fail else "FAIL — see above"))
    sys.exit(0 if not fail else 1)


if __name__ == "__main__":
    main()
