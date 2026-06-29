"""BigQuery table schema + view DDL for the conversations metrics table."""

# ruff: noqa: S608  # DDL generation: project/dataset/table are internal config, not user input

from __future__ import annotations

from google.cloud import bigquery

CONVERSATIONS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("conversation_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("channel", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("created_at", "TIMESTAMP"),
    bigquery.SchemaField("updated_at", "TIMESTAMP"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("resolved_by", "STRING"),
    bigquery.SchemaField("csat_score", "INT64"),
    bigquery.SchemaField("nps_score", "INT64"),
    bigquery.SchemaField("synced_at", "TIMESTAMP"),
]


def view_ddls(project: str, dataset: str, table: str = "conversations") -> dict[str, str]:
    """The CREATE OR REPLACE VIEW statements for the Looker tiles."""
    fq = f"`{project}.{dataset}.{table}`"
    return {
        "v_volume_by_month_channel": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_month_channel` AS "
            f"SELECT FORMAT_DATE('%Y-%m', DATE(created_at)) AS month, channel, "
            f"COUNT(*) AS volume FROM {fq} GROUP BY month, channel"
        ),
        "v_resolution_split": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_resolution_split` AS "
            f"SELECT channel, "
            f"COUNTIF(resolved_by='bot') AS closed_by_bot, "
            f"COUNTIF(resolved_by='agent') AS transfer_to_agent, "
            f"COUNT(*) AS total, "
            f"SAFE_DIVIDE(COUNTIF(resolved_by='bot'), COUNT(*)) AS closed_by_bot_pct, "
            f"SAFE_DIVIDE(COUNTIF(resolved_by='agent'), COUNT(*)) AS transfer_to_agent_pct "
            f"FROM {fq} GROUP BY channel"
        ),
        "v_csat": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_csat` AS "
            f"SELECT channel, "
            f"COUNTIF(csat_score IS NOT NULL) AS respondents, "
            f"AVG(csat_score) AS avg_score, "
            f"SAFE_DIVIDE(COUNTIF(csat_score >= 4), COUNTIF(csat_score IS NOT NULL)) "
            f"AS satisfied_rate "
            f"FROM {fq} GROUP BY channel"
        ),
        "v_nps": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_nps` AS "
            f"SELECT channel, "
            f"COUNTIF(nps_score IS NOT NULL) AS respondents, "
            f"COUNTIF(nps_score >= 9) AS promoters, "
            f"COUNTIF(nps_score BETWEEN 7 AND 8) AS passives, "
            f"COUNTIF(nps_score IS NOT NULL AND nps_score <= 6) AS detractors, "
            f"SAFE_DIVIDE("
            f"COUNTIF(nps_score >= 9) - COUNTIF(nps_score IS NOT NULL AND nps_score <= 6), "
            f"COUNTIF(nps_score IS NOT NULL)) * 100 AS nps "
            f"FROM {fq} GROUP BY channel"
        ),
    }
