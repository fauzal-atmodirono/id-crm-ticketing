"""BigQuery schema + view DDL for the manual QA accuracy/quality labels (Phase 4).

P8 task 7 added the `channel`, `rubric_*`, and `call_qa_percentage` columns
below plus the `v_call_qa` view. `v_quality`'s own SQL is UNCHANGED (pinned
by test_v_quality_is_unchanged_for_existing_consumers) -- adding a column
this view does not select from cannot affect it.

**Existing tenants need a manual migration.** `BigQueryQaLabels._ensure_schema`
calls `create_table(..., exists_ok=True)`, which does not retroactively add
columns to a table that already exists -- the same limitation already noted
for `ai_actions.output_tokens`/`cached_tokens`. A tenant with call_qa_enabled
on and a pre-existing qa_labels table needs:

    ALTER TABLE `<project>.<dataset>.qa_labels`
      ADD COLUMN IF NOT EXISTS channel STRING,
      ADD COLUMN IF NOT EXISTS rubric_greeting BOOL,
      ADD COLUMN IF NOT EXISTS rubric_identification BOOL,
      ADD COLUMN IF NOT EXISTS rubric_resolution BOOL,
      ADD COLUMN IF NOT EXISTS rubric_closing BOOL,
      ADD COLUMN IF NOT EXISTS rubric_compliance BOOL,
      ADD COLUMN IF NOT EXISTS call_qa_percentage FLOAT64;

Until that runs, `qa_adapter.BigQueryQaLabels.record_label` never even
attempts to write these columns for a tenant that never sets `channel`/a
rubric (see its own comment) -- so a tenant that hasn't migrated AND hasn't
turned `call_qa_enabled` on is completely unaffected either way.
"""

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
    # P8 task 7 -- all nullable, so a row with none of these (today's
    # channel-agnostic label) is unaffected.
    bigquery.SchemaField("channel", "STRING"),
    bigquery.SchemaField("rubric_greeting", "BOOL"),
    bigquery.SchemaField("rubric_identification", "BOOL"),
    bigquery.SchemaField("rubric_resolution", "BOOL"),
    bigquery.SchemaField("rubric_closing", "BOOL"),
    bigquery.SchemaField("rubric_compliance", "BOOL"),
    bigquery.SchemaField("call_qa_percentage", "FLOAT64"),
]


def qa_view_ddls(
    project: str,
    dataset: str,
    table: str = "qa_labels",
    conversations_table: str = "conversations",
) -> dict[str, str]:
    """`v_quality` (per-channel avg accuracy/quality, joined to conversations
    -- UNCHANGED by P8) plus the new `v_call_qa` (per-day call-rubric
    attainment against P5's 85% target, computed by the caller via
    `attainment.evaluate` -- this view only rolls up the percentage itself)."""
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
        "v_call_qa": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_call_qa` AS "
            f"SELECT DATE(labeled_at) AS day, "
            f"COUNT(*) AS calls_reviewed, "
            f"COUNTIF(call_qa_percentage IS NOT NULL) AS calls_scored, "
            f"AVG(call_qa_percentage) AS avg_call_qa_percentage "
            f"FROM {qa_fq} "
            f"WHERE channel = 'Phone' "
            f"GROUP BY day"
        ),
    }
