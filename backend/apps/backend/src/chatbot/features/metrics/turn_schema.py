"""BigQuery schema + view DDL for the per-turn metrics (Phase 2)."""

# ruff: noqa: S608  # DDL generation: project/dataset/table are internal config, not user input

from __future__ import annotations

from google.cloud import bigquery

TURN_EVENTS_SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("event_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("occurred_at", "TIMESTAMP"),
    bigquery.SchemaField("channel", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("session_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("latency_ms", "INT64"),
    bigquery.SchemaField("is_first_turn", "BOOL"),
    bigquery.SchemaField("is_fallback", "BOOL"),
    bigquery.SchemaField("handed_off", "BOOL"),
]


def turn_view_ddls(project: str, dataset: str, table: str = "turn_events") -> dict[str, str]:
    """The three CREATE OR REPLACE VIEW statements for the Phase-2 Looker tiles."""
    fq = f"`{project}.{dataset}.{table}`"
    return {
        "v_speed_of_response": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_speed_of_response` AS "
            f"SELECT channel, is_first_turn, "
            f"APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)] AS p99_latency_ms, "
            f"AVG(latency_ms) AS avg_latency_ms, "
            f"COUNT(*) AS turns "
            f"FROM {fq} GROUP BY channel, is_first_turn"
        ),
        "v_fallback_rate": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_fallback_rate` AS "
            f"SELECT channel, "
            f"SAFE_DIVIDE(COUNTIF(is_fallback), COUNT(*)) AS fallback_rate, "
            f"COUNT(*) AS turns "
            f"FROM {fq} GROUP BY channel"
        ),
        "v_bounce_rate": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_bounce_rate` AS "
            f"WITH session_turns AS ("
            f"SELECT channel, session_id, COUNT(*) AS turns "
            f"FROM {fq} GROUP BY channel, session_id) "
            f"SELECT channel, "
            f"COUNTIF(turns = 1) AS bounced, "
            f"COUNT(*) AS total_sessions, "
            f"SAFE_DIVIDE(COUNTIF(turns = 1), COUNT(*)) AS bounce_rate "
            f"FROM session_turns GROUP BY channel"
        ),
    }
