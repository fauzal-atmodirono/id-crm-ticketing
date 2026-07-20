"""BigQuery schema + view DDL for FAQ feedback."""

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


def faq_view_ddls(project: str, dataset: str, table: str = "faq_feedback") -> dict[str, str]:
    fq = f"`{project}.{dataset}.{table}`"
    return {
        "v_faq_quality": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_faq_quality` AS "
            f"SELECT article_id, COUNT(*) AS feedback_count, AVG(score) AS avg_score, "
            f"SAFE_DIVIDE(COUNTIF(helpful), COUNT(*)) AS helpful_rate "
            f"FROM {fq} GROUP BY article_id"
        ),
    }
