#!/usr/bin/env python3
"""Local residual scan for the PRO-NET RFP 2026_028 deliverables.

Mirrors scripts/verify_residuals.py from the technical-proposal-generator, but
reads .docx instead of the Google Docs API (gws is not installed here).

Scans the full document part XML, not just flattened paragraph text, so
hyperlink targets, table cells and headers are covered - the .docx equivalent
of that script's --raw mode.

Asserts:
  - zero residual "{{" other than deliberate "{{TBD" placeholders, and no
    dangling "}}";
  - zero template-origin leaks (Finnet, Fincx, Tableau, the Indonesian timezone
    defaults) and zero internal pricing leaks (Rp, margin, rate cards);
  - required strings present.

Exits 1 on any failure.
"""

from __future__ import annotations

import re
import sys
import zipfile

DOCS = [
    "PRO-NET RFP 2026_028 - Devoteam Vendor Response (Technical).docx",
    "PRO-NET - Devoteam G Cloud Technical Proposal SOW - CCMS.docx",
]

# Template-origin strings that must never reach this client, plus internal
# pricing markers. 'Rp' is word-bounded so it cannot match inside 'Reporting'.
RESIDUALS = [
    "Finnet", "Fincx", "Tableau",
    "WIB", "WIT (", "Western Indonesia", "Indonesia Time",
    r"\bRp\b", r"\bmargin\b", "Lead Consultant", "Senior Consultant",
    "manday", "Mandays",
]

REQUIRED = {
    "PRO-NET RFP 2026_028 - Devoteam Vendor Response (Technical).docx": [
        "PRO-NET", "RFP 2026_028", "Customer Complaint Management System",
        "Appendix A", "Appendix B", "Power BI", "PDPA",
    ],
    "PRO-NET - Devoteam G Cloud Technical Proposal SOW - CCMS.docx": [
        "PRO-NET", "Devoteam G Cloud", "Customer Complaint Management System",
        "BigQuery", "Vertex AI", "Gemini", "asia-southeast1", "MYT",
    ],
}

ALLOW_PREFIX = "{{TBD"


def doc_xml(path: str) -> str:
    """Concatenate every document part - body, headers, footers."""
    out = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                out.append(z.read(name).decode("utf-8", "replace"))
    return "\n".join(out)


def flat_text(xml: str) -> str:
    """Strip tags so runs split across <w:r> boundaries still match."""
    text = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def main() -> int:
    failures = 0
    for path in DOCS:
        print(f"\n{'=' * 72}\n{path}\n{'=' * 72}")
        try:
            xml = doc_xml(path)
        except FileNotFoundError:
            print("  FAIL  file not found")
            failures += 1
            continue
        text = flat_text(xml)

        # ---- open tokens
        opens = [m.start() for m in re.finditer(re.escape("{{"), text)]
        tbd, stray = [], []
        for i in opens:
            frag = text[i:i + 200]
            (tbd if frag.startswith(ALLOW_PREFIX) else stray).append(
                frag.split("}}")[0] + "}}"
            )
        closes = len(re.findall(re.escape("}}"), text))
        if stray:
            failures += 1
            print(f"  FAIL  {len(stray)} stray token(s) not marked TBD:")
            for s in stray[:20]:
                print(f"          {s[:110]}")
        else:
            print(f"  PASS  no stray tokens ({len(opens)} open / {closes} close balanced"
                  f"{'' if len(opens) == closes else ' - MISMATCH'})")
        if len(opens) != closes:
            failures += 1
            print(f"  FAIL  unbalanced braces: {len(opens)} '{{{{' vs {closes} '}}}}'")

        # ---- residuals
        hits = []
        for term in RESIDUALS:
            pat = term if term.startswith(r"\b") else re.escape(term)
            found = re.findall(pat, text, re.IGNORECASE if "\\b" not in term else 0)
            if found:
                hits.append((term, len(found)))
        if hits:
            failures += 1
            print("  FAIL  residual/pricing leak:")
            for term, n in hits:
                print(f"          {term}  x{n}")
        else:
            print(f"  PASS  no residual or pricing leaks ({len(RESIDUALS)} terms scanned)")

        # ---- required
        missing = [r for r in REQUIRED[path] if r not in text]
        if missing:
            failures += 1
            print(f"  FAIL  required strings missing: {missing}")
        else:
            print(f"  PASS  all {len(REQUIRED[path])} required strings present")

        # ---- open items
        print(f"\n  OPEN ITEMS ({len(tbd)}):")
        for i, t in enumerate(sorted(set(tbd)), 1):
            print(f"    {i:2}. {t[:150]}")

    print(f"\n{'=' * 72}")
    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures} check group(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
