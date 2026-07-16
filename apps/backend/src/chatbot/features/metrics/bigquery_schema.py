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
    bigquery.SchemaField("division", "STRING"),
    bigquery.SchemaField("category", "STRING"),
    bigquery.SchemaField("subcategory", "STRING"),
    bigquery.SchemaField("department", "STRING"),
    bigquery.SchemaField("agent_id", "STRING"),
    bigquery.SchemaField("pic", "STRING"),
    bigquery.SchemaField("sla_minutes", "INT64"),
    bigquery.SchemaField("sla_deadline", "TIMESTAMP"),
    bigquery.SchemaField("first_response_at", "TIMESTAMP"),
    bigquery.SchemaField("resolved_at", "TIMESTAMP"),
    bigquery.SchemaField("reopen_count", "INT64"),
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
        "v_volume_by_division": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_division` AS "
            f"SELECT FORMAT_DATE('%Y-%m', DATE(created_at)) AS month, "
            f"COALESCE(division, 'Unknown') AS division, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY month, division"
        ),
        "v_dept_pic_performance": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_dept_pic_performance` AS "
            f"SELECT COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(pic, 'Unassigned') AS pic, COUNT(*) AS cases, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_resolution_min, "
            f"SAFE_DIVIDE(COUNTIF(resolved_at IS NOT NULL), COUNT(*)) AS resolution_rate "
            f"FROM {fq} GROUP BY department, pic ORDER BY cases DESC"
        ),
        "v_sla_achievement": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_sla_achievement` AS "
            f"SELECT channel, COALESCE(division, 'Unknown') AS division, "
            f"COUNTIF(sla_deadline IS NOT NULL) AS with_sla, "
            f"COUNTIF(resolved_at IS NOT NULL AND resolved_at <= sla_deadline) AS met, "
            f"SAFE_DIVIDE(COUNTIF(resolved_at IS NOT NULL AND resolved_at <= sla_deadline), "
            f"COUNTIF(sla_deadline IS NOT NULL)) AS sla_achievement_rate "
            f"FROM {fq} GROUP BY channel, division"
        ),
        "v_reopen_rate": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_reopen_rate` AS "
            f"SELECT COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(pic, 'Unassigned') AS pic, COUNT(*) AS cases, "
            f"COUNTIF(reopen_count > 0) AS reopened, "
            f"SAFE_DIVIDE(COUNTIF(reopen_count > 0), COUNT(*)) AS reopen_rate "
            f"FROM {fq} GROUP BY department, pic"
        ),
        "v_resolution_time": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_resolution_time` AS "
            f"SELECT channel, COALESCE(division, 'Unknown') AS division, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE), 100)[OFFSET(50)] AS p50_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE), 100)[OFFSET(90)] AS p90_min "
            f"FROM {fq} WHERE resolved_at IS NOT NULL GROUP BY channel, division"
        ),
        "v_nps_by_agent": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_nps_by_agent` AS "
            f"SELECT COALESCE(agent_id, 'Unassigned') AS agent_id, channel, "
            f"COUNTIF(nps_score IS NOT NULL) AS respondents, "
            f"SAFE_DIVIDE("
            f"COUNTIF(nps_score >= 9) - COUNTIF(nps_score IS NOT NULL AND nps_score <= 6), "
            f"COUNTIF(nps_score IS NOT NULL)) * 100 AS nps "
            f"FROM {fq} WHERE channel IN ('Phone', 'WhatsApp') GROUP BY agent_id, channel"
        ),
        "v_volume_daily": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_daily` AS "
            f"SELECT DATE(created_at) AS day, channel, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY day, channel"
        ),
        "v_volume_weekly": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_weekly` AS "
            f"SELECT DATE_TRUNC(DATE(created_at), WEEK) AS week, channel, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY week, channel"
        ),
    }
