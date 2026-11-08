#!/usr/bin/env python3
"""Detect edited fork patches whose old text later patches still rely on.

Patches are `git apply`-ed in numeric order. If patch N is edited so that a
line it used to ADD disappears or changes, any later patch M that touches the
same file and carries that old line as context (' ') or removal ('-') no
longer applies, and the image build fails. Build edits additions-only where
possible; this script flags the rest.

Usage: check_patch_edit_context.py <base-rev> [<patch-file> ...]
  Compares each patch (default: every patch changed since <base-rev>) at
  <base-rev> against the working tree and reports later patches that need a
  line the edit removed. Exit 1 on any hit.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PATCHES = Path(__file__).resolve().parent.parent / "patches"


def parse(text: str) -> dict[str, list[tuple[str, str]]]:
    """file -> [(kind, content)] for every hunk body line (kind in ' +-')."""
    out: dict[str, list[tuple[str, str]]] = {}
    cur = None
    in_hunk = False
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            cur = line.split(" b/", 1)[1]
            out.setdefault(cur, [])
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
            if cur is not None:
                out[cur].append(("@", ""))  # hunk boundary
        elif in_hunk and cur is not None and line[:1] in (" ", "+", "-"):
            out[cur].append((line[:1], line[1:]))
    return out


def old_text(rev: str, name: str) -> str:
    rel = f"deploy/chatwoot-fork/patches/{name}"
    r = subprocess.run(["git", "show", f"{rev}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def adjacency(
    name: str, new: dict[str, list[tuple[str, str]]], old: dict[str, list[tuple[str, str]]]
) -> int:
    """Flag later hunks whose context straddles a boundary of lines `name` adds.

    A later hunk's old side (context + removals) must be contiguous in the
    file. If it runs from one of this patch's added lines to a neighbour, the
    two must still be adjacent here, i.e. nothing may be inserted between them.
    Short or generic lines (braces, blanks) are ignored to avoid noise.
    """
    hits = 0
    for f, lines in new.items():
        old_adds = {c for k, c in old.get(f, []) if k == "+"}
        runs: list[list[str]] = []
        prev = None
        for k, c in lines:
            if k == "+":
                if prev == "+":
                    runs[-1].append(c)
                else:
                    runs.append([c])
            prev = k
        pos = {}
        for r in runs:
            for i, c in enumerate(r):
                if len(c.strip()) >= 12:
                    pos.setdefault(c, (r, i))
        for later in sorted(PATCHES.glob("*.patch")):
            if later.name <= name:
                continue
            q = parse(later.read_text()).get(f)
            if not q:
                continue
            seqs, cur_seq = [], []
            for k, c in q:
                if k == "@":
                    seqs.append(cur_seq)
                    cur_seq = []
                elif k in (" ", "-"):
                    cur_seq.append(c)
            seqs.append(cur_seq)
            pairs = [p for sq in seqs for p in zip(sq, sq[1:])]
            for a, b in pairs:
                ra, rb = pos.get(a), pos.get(b)
                # only boundaries this edit created are interesting
                if (not ra or a in old_adds) and (not rb or b in old_adds):
                    continue
                if ra and rb and ra[0] is rb[0] and rb[1] == ra[1] + 1:
                    continue
                if ra and ra[1] < len(ra[0]) - 1 and not (rb and rb[0] is ra[0]):
                    hits += 1
                    print(f"SPLIT {name} -> {later.name} [{f}]: after '{a.strip()[:60]}'")
                elif rb and rb[1] > 0 and not (ra and ra[0] is rb[0]):
                    hits += 1
                    print(f"SPLIT {name} -> {later.name} [{f}]: before '{b.strip()[:60]}'")
    return hits


def main(argv: list[str]) -> int:
    base = argv[0]
    names = [Path(a).name for a in argv[1:]]
    if not names:
        diff = subprocess.run(
            ["git", "diff", "--name-only", base, "--", str(PATCHES)],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        names = [Path(d).name for d in diff if d.endswith(".patch")]
    hits = 0
    for name in sorted(names):
        old = parse(old_text(base, name))
        new = parse((PATCHES / name).read_text())
        for f, lines in old.items():
            new_adds = {c for k, c in new.get(f, []) if k in ("+", " ")}
            gone = {c for k, c in lines if k == "+" and c.strip() and c not in new_adds}
            if not gone:
                continue
            for later in sorted(PATCHES.glob("*.patch")):  # removed-line check
                if later.name <= name:
                    continue
                for lf, ll in parse(later.read_text()).items():
                    if lf != f:
                        continue
                    for k, c in ll:
                        if k in (" ", "-") and c in gone:
                            hits += 1
                            print(f"HIT {name} -> {later.name} [{f}]: {c.strip()[:90]}")
        hits += adjacency(name, new, old)
    print(f"{hits} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
