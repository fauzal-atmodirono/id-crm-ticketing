#!/usr/bin/env python3
"""Refresh e2e/data/tracker.json from the tracker sheet (read-only).

The specs generate one Playwright test per tracker row from this snapshot, so
re-run this after rows are added or renamed in the sheet. Uses the `gws` CLI
(already authenticated on this machine); it only READS columns A:H.

    python3 scripts/sync-tracker.py
"""
import json
import pathlib
import subprocess
import sys

SPREADSHEET_ID = "1mqZphjnBxU4BYUCwc0Nu-Lf0yYbqGEV590Oo_76uqDU"
TABS = [
    "Conversations", "Voice & Phone", "Knowledge", "AI capabilities",
    "SLA Policies", "Audit Log", "Roles & Permissions", "Escalation Routing",
    "Workforce & My status", "Customer 360", "Cases", "RSA Incident Log",
    "Alert preferences", "Case taxonomy", "Custom features", "Contacts",
    "Reports", "Campaigns", "Help Center", "Settings", "Platform & security",
    "Access by role",
]
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "tracker.json"


def read_tab(tab: str) -> list[list[str]]:
    params = json.dumps({"spreadsheetId": SPREADSHEET_ID, "range": f"'{tab}'!A1:H400"})
    proc = subprocess.run(
        ["gws", "sheets", "spreadsheets", "values", "get", "--params", params],
        capture_output=True, text=True, check=True,
    )
    out = proc.stdout
    # gws prints a "Using keyring" line before the JSON.
    return json.loads(out[out.index("{"):]).get("values", [])


def main() -> int:
    tabs = []
    for tab in TABS:
        rows = []
        for i, r in enumerate(read_tab(tab)[1:], start=2):
            r = r + [""] * (8 - len(r))
            if not r[0].strip():
                continue
            rows.append({
                "id": r[0].strip(), "row": i, "subModule": r[1], "test": r[2].strip(),
                "steps": r[3], "dod": r[4], "role": r[5], "priority": r[6], "knownIssue": r[7],
            })
        tabs.append({"tab": tab, "rows": rows})
        print(f"{tab}: {len(rows)} rows", file=sys.stderr)
    OUT.write_text(json.dumps({"spreadsheetId": SPREADSHEET_ID, "tabs": tabs}, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
