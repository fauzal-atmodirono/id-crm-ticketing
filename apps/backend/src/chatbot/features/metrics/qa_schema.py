"""BigQuery schema + view DDL for the manual QA accuracy/quality labels (Phase 4)."""

# ruff: noqa: S608  # DDL generation: project/dataset/table are internal config, not user input

from __future__ import annotations

from google.cloud import bigquery

QA_LABELS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("conversation_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("accuracy", "INT64"),
    bigquery.SchemaField("quality", "INT64"),
    bigquery.SchemaField("reviewer", "STRING"),
    bigquery.SchemaField("notes", "STRING"),
    bigquery.SchemaField("labeled_at", "TIMESTAMP"),
]


def qa_view_ddls(
    project: str,
    dataset: str,
    table: str = "qa_labels",
    conversations_table: str = "conversations",
) -> dict[str, str]:
    """The v_quality view: per-channel avg accuracy/quality, joined to conversations."""
    qa_fq = f"`{project}.{dataset}.{table}`"
    conv_fq = f"`{project}.{dataset}.{conversations_table}`"
    return {
        "v_quality": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_quality` AS "
            f"SELECT c.channel, "
            f"COUNT(*) AS labels, "
            f"AVG(q.accuracy) AS avg_accuracy, "
            f"AVG(q.quality) AS avg_quality "
            f"FROM {qa_fq} q "
            f"JOIN {conv_fq} c USING (conversation_id) "
            f"GROUP BY c.channel"
        ),
    }
