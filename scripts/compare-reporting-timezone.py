#!/usr/bin/env python3
"""Show exactly which cases move if you change REPORTING_TIMEZONE.

Switching the reporting timezone re-buckets **every historical figure on every
existing dashboard** the next time `ensure_views()` runs. A case created at
23:00 MYT is 15:00 UTC the same day; one created at 07:00 MYT is 23:00 UTC the
*previous* day. So totals do not change, but cases slide between adjacent days,
weeks and months -- which is why the effect is "close but not quite" rather
than obviously broken, and why it must never be a surprise.

Run this BEFORE switching a live tenant and keep the output. It is the evidence
that the movement you see on Monday's dashboard was expected.

**Read-only by construction.** It issues one SELECT and never creates,
replaces or drops anything -- see `assert_read_only()` and its test. An
operator runs this against production while deciding; it must not be able to
change production.

Usage:
    python3 scripts/compare-reporting-timezone.py \\
        --project my-proj --dataset my_ds \\
        --from 2026-07-01 --to 2026-07-31 \\
        --to-timezone Asia/Kuala_Lumpur
"""

from __future__ import annotations

import argparse
import sys

# Mirrors bigquery_schema.SUPPORTED_REPORTING_TIMEZONES. Duplicated rather than
# imported so this script runs from a plain checkout with no backend deps.
SUPPORTED = ("UTC", "Asia/Kuala_Lumpur", "Asia/Jakarta", "Asia/Singapore", "Asia/Bangkok")


class NotReadOnly(AssertionError):
    """Raised if the generated SQL could modify anything."""


_FORBIDDEN = (
    "create ", "replace", "drop ", "delete ", "insert ", "update ",
    "merge ", "truncate", "alter ", "grant ",
)


def assert_read_only(sql: str) -> str:
    """Refuse to run anything that is not a bare SELECT.

    A safety property, not a lint: this runs against a production dataset by an
    operator who is deciding whether to switch, and the blast radius of a
    mistake here is the warehouse.
    """
    lowered = sql.lower()
    if not lowered.lstrip().startswith("select") and not lowered.lstrip().startswith("with"):
        raise NotReadOnly("query does not start with SELECT/WITH")
    for token in _FORBIDDEN:
        if token in lowered:
            raise NotReadOnly(f"query contains forbidden token {token.strip()!r}")
    return sql


def build_sql(
    project: str, dataset: str, table: str, from_date: str, to_date: str,
    from_tz: str, to_tz: str,
) -> str:
    """One row per (old bucket, new bucket) pair with a count of what moved."""
    fq = f"`{project}.{dataset}.{table}`"
    old = "DATE(created_at)" if from_tz == "UTC" else f"DATE(created_at, '{from_tz}')"
    new = "DATE(created_at)" if to_tz == "UTC" else f"DATE(created_at, '{to_tz}')"
    return (
        f"SELECT {old} AS bucket_before, {new} AS bucket_after, "
        f"COUNT(*) AS cases "
        f"FROM {fq} "
        f"WHERE created_at >= TIMESTAMP(@from_date) "
        f"AND created_at < TIMESTAMP_ADD(TIMESTAMP(@to_date), INTERVAL 1 DAY) "
        f"GROUP BY bucket_before, bucket_after "
        f"ORDER BY bucket_before, bucket_after"
    )


def summarise(rows: list[dict]) -> tuple[int, int, float]:
    """(total, moved, pct_moved). Identical zones give moved == 0."""
    total = sum(int(r["cases"]) for r in rows)
    moved = sum(int(r["cases"]) for r in rows if r["bucket_before"] != r["bucket_after"])
    pct = (100.0 * moved / total) if total else 0.0
    return total, moved, pct


def render(rows: list[dict], from_tz: str, to_tz: str) -> str:
    lines = [f"Reporting timezone comparison: {from_tz} -> {to_tz}", ""]
    movers = [r for r in rows if r["bucket_before"] != r["bucket_after"]]
    if not movers:
        lines.append("  No cases change bucket.")
    else:
        lines.append(f"  {'from bucket':<14} {'to bucket':<14} {'cases':>7}")
        for r in movers:
            lines.append(
                f"  {str(r['bucket_before']):<14} {str(r['bucket_after']):<14} "
                f"{int(r['cases']):>7}"
            )
    total, moved, pct = summarise(rows)
    lines += [
        "",
        f"  {moved} of {total} cases ({pct:.1f}%) change bucket.",
        "  Totals are unchanged; cases move between adjacent buckets.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--table", default="conversations")
    parser.add_argument("--from", dest="from_date", required=True)
    parser.add_argument("--to", dest="to_date", required=True)
    parser.add_argument("--from-timezone", default="UTC", choices=SUPPORTED)
    parser.add_argument("--to-timezone", required=True, choices=SUPPORTED)
    args = parser.parse_args()

    sql = assert_read_only(
        build_sql(
            args.project, args.dataset, args.table,
            args.from_date, args.to_date,
            args.from_timezone, args.to_timezone,
        )
    )

    from google.cloud import bigquery  # noqa: PLC0415 - keeps import cost off --help

    client = bigquery.Client(project=args.project)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("from_date", "STRING", args.from_date),
            bigquery.ScalarQueryParameter("to_date", "STRING", args.to_date),
        ],
        # Belt and braces: even if the SQL check were bypassed, a dry-run-safe
        # config with no destination cannot write anywhere.
        use_legacy_sql=False,
    )
    rows = [dict(r) for r in client.query(sql, job_config=job_config).result()]
    print(render(rows, args.from_timezone, args.to_timezone))
    return 0


if __name__ == "__main__":
    sys.exit(main())
