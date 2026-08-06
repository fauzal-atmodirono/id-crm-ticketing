#!/usr/bin/env python3
"""Apply a batch of replaceAllText (or other) requests to a Google Doc via the gws CLI.

Usage:
  python apply_replacements.py <DOC_ID> <requests.json>

The requests JSON file should look like:
  {
    "requests": [
      {"replaceAllText": {"containsText": {"text": "OLD", "matchCase": true},
                          "replaceText": "NEW"}},
      ...
    ]
  }

Reports occurrencesChanged per request and flags any zero-match results so you
can iterate without trusting a single "all good" summary.
"""

import argparse
import json
import subprocess
import sys


def run_batch_update(doc_id: str, requests_path: str) -> dict:
    with open(requests_path) as f:
        body = f.read()
    proc = subprocess.run(
        [
            "gws",
            "docs",
            "documents",
            "batchUpdate",
            "--params",
            json.dumps({"documentId": doc_id}),
            "--json",
            body,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"gws CLI failed (exit {proc.returncode}):\n{proc.stderr}\n{proc.stdout}")
    # Strip the "Using keyring backend" status line if present.
    out = proc.stdout
    if not out.lstrip().startswith("{"):
        out = out[out.index("{"):]
    return json.loads(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc_id")
    ap.add_argument("requests_json")
    ap.add_argument("--quiet", action="store_true", help="Only print failures")
    args = ap.parse_args()

    result = run_batch_update(args.doc_id, args.requests_json)
    replies = result.get("replies", [])

    with open(args.requests_json) as f:
        requests = json.load(f).get("requests", [])

    zeros = []
    multis = []
    total_changed = 0

    for i, (req, reply) in enumerate(zip(requests, replies)):
        rkey = next(iter(req.keys()), "?")
        if rkey == "replaceAllText":
            n = reply.get("replaceAllText", {}).get("occurrencesChanged", 0)
            total_changed += n
            search = (
                req["replaceAllText"].get("containsText", {}).get("text", "")[:60]
            )
            if n == 0:
                zeros.append((i, search))
            elif n > 1:
                multis.append((i, n, search))
            if not args.quiet and n != 0:
                print(f"  [{i}] replaceAllText x{n}: {search!r}")
        else:
            if not args.quiet:
                print(f"  [{i}] {rkey}: ok")

    print(f"\nTotal requests: {len(requests)}")
    print(f"Total occurrencesChanged: {total_changed}")
    if zeros:
        print(f"\nZERO-MATCH ({len(zeros)}):")
        for i, s in zeros:
            print(f"  [{i}] {s!r}")
    if multis:
        print(f"\nMULTI-MATCH (>1) — sanity check needed ({len(multis)}):")
        for i, n, s in multis:
            print(f"  [{i}] x{n}: {s!r}")


if __name__ == "__main__":
    main()
