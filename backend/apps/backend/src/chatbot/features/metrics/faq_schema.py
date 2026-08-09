"""BigQuery schema + view DDL for FAQ feedback, and (P8 task 9) FAQ health.

**`v_kb_staleness` needs a table nothing writes yet, and that is stated here
rather than discovered later.** Staleness is "how old is this entry, weighted
by how load-bearing it is", which needs an edit timestamp and a serve count per
FAQ entry. Neither exists in the warehouse today: `faq_feedback` records one row
per piece of user FEEDBACK, not per serve, and carries no notion of when the
entry was last edited. So `FAQ_ENTRIES_SCHEMA` and `v_kb_staleness` are the
target shape, and two things are owed before either produces a number:

1. the `faq_entries` table has to exist (`FAQ_ENTRIES_SCHEMA`), and
2. something has to snapshot the operator-authored FAQ store into it --
   `updated_at` from the entry, `serve_count` from the live-FAQ search.

Until then `v_kb_staleness` returns no rows. Note also that `faq_view_ddls` has
**no runtime caller at all** today (`ensure_views` only runs
`bigquery_schema.view_ddls`), so even `v_faq_quality` is not created by any
deploy path -- deliberately not "fixed" here by wiring this function into
`ensure_views`, because a `CREATE VIEW` over a `faq_entries` table that does
not exist yet FAILS, and it would fail partway through the loop that creates
every other view.

**Why a NULL serve count must not become a 0.** `age_days * serve_count` is the
staleness weight, and `COALESCE(serve_count, 0)` would score an
entry-we-never-instrumented identically to an entry-nobody-ever-hits: zero,
bottom of the review queue, invisible. So NULL propagates and
`staleness_status` labels the row `unmeasured`, which is a statement about
instrumentation rather than a claim that the entry is unused.
"""

# ruff: noqa: S608

from __future__ import annotations

from google.cloud import bigquery

FAQ_FEEDBACK_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("article_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING"),
    bigquery.SchemaField("helpful", "BOOL"),
    bigquery.SchemaField("score", "INT64"),
    bigquery.SchemaField("at", "TIMESTAMP"),
]

# P8 task 9. Every measured field is NULLABLE, including `serve_count`: a
# snapshot taken before serve counting is instrumented must record "unknown",
# not "zero served".
FAQ_ENTRIES_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("article_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("title", "STRING", mode="NULLABLE"),
    # When the operator last edited the entry. NULL means the source store did
    # not report one -- an entry with no edit timestamp has unknown age, not
    # zero age.
    bigquery.SchemaField("updated_at", "TIMESTAMP", mode="NULLABLE"),
    # How often the live-FAQ search served this entry. NULL means not
    # instrumented; 0 means genuinely never served. Do not collapse them.
    bigquery.SchemaField("serve_count", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("last_served_at", "TIMESTAMP", mode="NULLABLE"),
    # When this snapshot row was taken. `v_kb_staleness`'s `day` column, so a
    # period-scoped read selects a snapshot rather than an edit date.
    bigquery.SchemaField("snapshot_at", "TIMESTAMP", mode="NULLABLE"),
]


def faq_view_ddls(
    project: str,
    dataset: str,
    table: str = "faq_feedback",
    entries_table: str = "faq_entries",
) -> dict[str, str]:
    fq = f"`{project}.{dataset}.{table}`"
    entries_fq = f"`{project}.{dataset}.{entries_table}`"
    return {
        "v_faq_quality": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_faq_quality` AS "
            f"SELECT article_id, COUNT(*) AS feedback_count, AVG(score) AS avg_score, "
            f"SAFE_DIVIDE(COUNTIF(helpful), COUNT(*)) AS helpful_rate "
            f"FROM {fq} GROUP BY article_id"
        ),
        # P8 task 9: the review queue, ordered by "stale AND load-bearing".
        #
        # `staleness_score = age_days * serve_count` is the whole behaviour the
        # brief asks for, and the multiplication gives both required properties
        # for free: a never-served entry scores 0 however old it is (a
        # year-old entry nobody hits is not the problem), and an entry edited
        # today scores 0 however often it is served. Age alone would put the
        # first at the top of the queue and the second nowhere near it.
        #
        # `ORDER BY staleness_status, staleness_score DESC` puts every scored
        # row above every unmeasured one ('scored' < 'unmeasured'), so a row
        # whose serve count was never instrumented is visibly grouped as
        # unmeasured rather than silently sinking among the zero-staleness
        # entries.
        #
        # `v_faq_quality` is LEFT JOINed rather than INNER: an entry nobody has
        # given feedback on is exactly the kind of thing a review queue should
        # surface, so it must not drop out for lack of a quality row.
        "v_kb_staleness": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_kb_staleness` AS "
            f"SELECT e.article_id, e.title, "
            f"DATE(e.snapshot_at) AS day, "
            f"e.updated_at, e.serve_count, e.last_served_at, "
            f"DATE_DIFF(CURRENT_DATE(), DATE(e.updated_at), DAY) AS age_days, "
            f"DATE_DIFF(CURRENT_DATE(), DATE(e.updated_at), DAY) * e.serve_count "
            f"AS staleness_score, "
            f"CASE WHEN e.updated_at IS NULL OR e.serve_count IS NULL "
            f"THEN 'unmeasured' ELSE 'scored' END AS staleness_status, "
            f"q.feedback_count, q.avg_score AS feedback_avg_score, q.helpful_rate "
            f"FROM {entries_fq} e "
            f"LEFT JOIN `{project}.{dataset}.v_faq_quality` q "
            f"ON q.article_id = e.article_id "
            f"ORDER BY staleness_status, staleness_score DESC"
        ),
    }
