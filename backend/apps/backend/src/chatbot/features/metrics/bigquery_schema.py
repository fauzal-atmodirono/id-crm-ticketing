"""BigQuery table schema + view DDL for the conversations metrics table.

**Time zone: every date bucket in this file is a UTC calendar day.**
`created_at` is a TIMESTAMP (absolute time, no zone), and bare
`DATE(created_at)` / `DATE_TRUNC(DATE(created_at), ...)` /
`EXTRACT(... FROM created_at)` all default to UTC in BigQuery -- none of
them takes the server's or the reader's zone into account. See the
day-grain views below for what that means for the period-scoped pages.
"""

# ruff: noqa: S608  # DDL generation: project/dataset/table are internal config, not user input

from __future__ import annotations

import json

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
    bigquery.SchemaField("dealer", "STRING"),  # Phase-3: dealer dimension for CRR grouping
    bigquery.SchemaField("dealer_escalated_at", "TIMESTAMP"),  # Task 10: dealer escalation timestamp
    bigquery.SchemaField("case_type", "STRING"),
    bigquery.SchemaField("vehicle_model", "STRING"),
    bigquery.SchemaField("first_response_working_minutes", "INT64"),
    bigquery.SchemaField("resolution_working_minutes", "INT64"),
]


def _sla_bucket_case_sql(sla_targets_json: str) -> str:
    """Build a SQL CASE expression bucketing resolution_working_minutes per
    case_type, from RESOLUTION_SLA_TARGETS_JSON. Malformed JSON -> a CASE
    that matches nothing (ELSE NULL), so the view still creates cleanly and
    just returns zero rows until the config is fixed."""
    try:
        targets = json.loads(sla_targets_json or "{}")
    except (ValueError, TypeError):
        targets = {}
    if not isinstance(targets, dict):
        targets = {}

    branches: list[str] = []
    for case_type, spec in targets.items():
        if not isinstance(spec, dict):
            continue
        edges = spec.get("buckets_wh")
        labels = spec.get("labels")
        if not isinstance(edges, list) or not isinstance(labels, list) or len(labels) != len(edges) + 1:
            continue
        if not all(isinstance(label, str) for label in labels):
            continue
        case_type_branches: list[str] = []
        prev_minutes = 0
        try:
            for edge_wh, label in zip(edges, labels[:-1], strict=True):
                edge_minutes = int(edge_wh) * 60
                case_type_branches.append(
                    f"WHEN LOWER(case_type) = '{case_type.lower()}' "
                    f"AND resolution_working_minutes >= {prev_minutes} "
                    f"AND resolution_working_minutes < {edge_minutes} THEN '{label}'"
                )
                prev_minutes = edge_minutes
        except (ValueError, TypeError):
            # A non-numeric buckets_wh entry (e.g. an operator typo like "8hr")
            # -> skip this case_type entirely rather than emit a partial,
            # syntactically-broken CASE branch or crash view_ddls()/ensure_views().
            continue
        case_type_branches.append(
            f"WHEN LOWER(case_type) = '{case_type.lower()}' "
            f"AND resolution_working_minutes >= {prev_minutes} THEN '{labels[-1]}'"
        )
        branches.extend(case_type_branches)
    if not branches:
        return "NULL"
    return "CASE " + " ".join(branches) + " ELSE NULL END"


def view_ddls(
    project: str, dataset: str, table: str = "conversations", sla_targets_json: str = "{}"
) -> dict[str, str]:
    """The CREATE OR REPLACE VIEW statements for the Looker tiles."""
    fq = f"`{project}.{dataset}.{table}`"
    bucket_case = _sla_bucket_case_sql(sla_targets_json)
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
            f"SELECT COALESCE(dealer, 'Unknown') AS dealer, "
            f"COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(pic, 'Unassigned') AS pic, COUNT(*) AS cases, "
            f"COUNTIF(reopen_count > 0) AS reopened, "
            f"SAFE_DIVIDE(COUNTIF(reopen_count > 0), COUNT(*)) AS reopen_rate "
            f"FROM {fq} GROUP BY dealer, department, pic"
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
        # ── The three day-grain views (`v_volume_daily`,
        # `v_state_trend_daily`, `v_volume_by_type_division_daily`) are the
        # sources every period-scoped read filters through
        # (`query_adapter._day_grain_block_for_period`, `WHERE day BETWEEN
        # @start AND @end`). Their `day` column, and therefore every week
        # and month bucket derived from it, is a **UTC calendar day**:
        # `DATE(created_at)` on a TIMESTAMP defaults to UTC in BigQuery.
        #
        # The Weekly Report picker builds its window from **browser-local**
        # dates and sends them as bare `from`/`to` date strings. For a
        # Malaysian tenant (UTC+8) the two disagree by 8 hours at both
        # edges: a case created 07:00 MYT on Fri 17 Jul is 23:00 UTC on
        # Thu 16 Jul and lands in the *previous* week's bucket, while one
        # created 23:00 MYT on Thu 23 Jul lands in the next. The visible
        # effect is a small, systematic shift of cases between adjacent
        # buckets -- not a total that is wrong by a fixed amount, which is
        # why it will not show up as an obvious error at the acceptance
        # gate; it will show up as "close but not quite" against a deck
        # that is presumably compiled in MYT.
        #
        # This is documented, not silently "fixed": changing these to
        # `DATE(created_at, 'Asia/Kuala_Lumpur')` would re-bucket every
        # historical figure on every existing dashboard in one deploy,
        # including the month-grain views patch 0020's live charts read.
        # The right eventual fix is a tenant-configurable reporting time
        # zone threaded through `view_ddls()` (a `Settings` field, defaulted
        # to UTC so today's numbers are unchanged) -- recommended, not built
        # here. Logged in the Package E spec's §11.4 discrepancy log so the
        # live reconciliation attributes any edge-of-window mismatch to this
        # rather than to a definitional difference. (Package E final fix,
        # finding I4.)
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
        "v_channel_anomaly": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_channel_anomaly` AS "
            f"WITH daily AS (SELECT channel, DATE(created_at) AS d, COUNT(*) AS v "
            f"FROM {fq} WHERE created_at IS NOT NULL GROUP BY channel, d), "
            f"cur AS (SELECT channel, v AS current_volume FROM daily "
            f"WHERE d = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)), "
            f"base AS (SELECT channel, AVG(v) AS baseline_mean, STDDEV(v) AS baseline_stddev "
            f"FROM daily WHERE d BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 8 DAY) "
            f"AND DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) GROUP BY channel) "
            f"SELECT b.channel, COALESCE(c.current_volume, 0) AS current_volume, "
            f"b.baseline_mean, b.baseline_stddev "
            f"FROM base b LEFT JOIN cur c USING (channel)"
        ),
        # Phase-3 additions
        "v_peak_hours": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_peak_hours` AS "
            f"SELECT EXTRACT(DAYOFWEEK FROM created_at) AS day_of_week, "
            f"EXTRACT(HOUR FROM created_at) AS hour_of_day, "
            f"channel, COUNT(*) AS volume "
            f"FROM {fq} WHERE created_at IS NOT NULL "
            f"GROUP BY day_of_week, hour_of_day, channel "
            f"ORDER BY day_of_week, hour_of_day"
        ),
        "v_complaint_type_ranking": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_complaint_type_ranking` AS "
            f"SELECT COALESCE(category, 'Unknown') AS category, "
            f"COALESCE(subcategory, 'Unknown') AS subcategory, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COUNT(*) AS cases, "
            f"SAFE_DIVIDE(COUNT(*), SUM(COUNT(*)) OVER ()) AS share_pct "
            f"FROM {fq} "
            f"GROUP BY category, subcategory, division "
            f"ORDER BY cases DESC"
        ),
        "v_tasks_per_agent": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_tasks_per_agent` AS "
            f"SELECT COALESCE(agent_id, 'Unassigned') AS agent_id, "
            f"COALESCE(pic, 'Unassigned') AS pic, "
            f"COUNT(*) AS cases, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_resolution_min, "
            f"COUNTIF(status = 'resolved') AS resolved_cases "
            f"FROM {fq} GROUP BY agent_id, pic ORDER BY cases DESC"
        ),
        "v_first_response_by_channel": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_first_response_by_channel` AS "
            f"SELECT channel, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE), 100)[OFFSET(50)] AS p50_first_response_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE), 100)[OFFSET(90)] AS p90_first_response_min, "
            f"COUNT(*) AS with_first_response "
            f"FROM {fq} WHERE first_response_at IS NOT NULL GROUP BY channel"
        ),
        "v_case_lifecycle": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_case_lifecycle` AS "
            f"SELECT conversation_id, channel, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(dealer, 'Unknown') AS dealer, "
            f"status, created_at, first_response_at, resolved_at, "
            f"TIMESTAMP_DIFF(first_response_at, created_at, MINUTE) AS first_response_minutes, "
            f"TIMESTAMP_DIFF(resolved_at, created_at, MINUTE) AS resolution_minutes, "
            f"reopen_count "
            f"FROM {fq} WHERE created_at IS NOT NULL"
        ),
        "v_state_trend": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_state_trend` AS "
            f"SELECT FORMAT_DATE('%Y-%m', DATE(created_at)) AS month, "
            # Task 2 (Package E): month_start widens the view with a real DATE
            # so range queries can filter with a named parameter instead of
            # string-matching `month`. Same (month, status, division) grain as
            # before -- one row per group, unchanged -- so this is additive:
            # existing `SELECT *` readers keep getting identical rows plus one
            # new column.
            f"DATE_TRUNC(DATE(created_at), MONTH) AS month_start, "
            f"status, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COUNT(*) AS cases "
            f"FROM {fq} WHERE created_at IS NOT NULL "
            f"GROUP BY month, month_start, status, division "
            f"ORDER BY month, status"
        ),
        # Task 2 (Package E) reopened: `v_state_trend` is grouped at month
        # grain, and `0020-reports-native-merge.patch`'s state-trend chart
        # reads it as lookup[f"{r.month}__{r.status}"] = r.cases -- an
        # *overwriting* assignment that assumes exactly one row per
        # (month, status, division). Widening THAT view to day grain (the
        # only way a week window's own total can be recovered -- a week
        # can't be reconstructed from a value already collapsed to a
        # month) would return ~30x the rows and silently corrupt that
        # chart, exactly like `v_volume_by_month_channel` would have (see
        # `v_volume_daily`'s comment above, and query_adapter.py's module
        # docstring). So this is a second, deliberately separate day-grain
        # view, not a widening of `v_state_trend` -- same precedent as
        # `v_volume_daily` alongside `v_volume_by_month_channel`. Do not
        # "tidy up" this duplication into one view; the two grains exist
        # because the plan's general prefer-widening-over-parallel-views
        # rule (Task 2 Step 3) is infeasible for exactly this shape.
        # `day` here is a UTC calendar day -- see the note above
        # `v_volume_daily` for the tenant-timezone consequence.
        "v_state_trend_daily": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_state_trend_daily` AS "
            f"SELECT DATE(created_at) AS day, status, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COUNT(*) AS cases "
            f"FROM {fq} WHERE created_at IS NOT NULL "
            f"GROUP BY day, status, division"
        ),
        "v_resolution_sla_buckets": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_resolution_sla_buckets` AS "
            f"SELECT COALESCE(case_type, 'Unknown') AS case_type, "
            f"{bucket_case} AS bucket_label, "
            f"COUNT(*) AS cases "
            f"FROM {fq} WHERE resolution_working_minutes IS NOT NULL "
            f"GROUP BY case_type, bucket_label"
        ),
        "v_dealer_escalation": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_dealer_escalation` AS "
            f"SELECT COALESCE(dealer, 'Unknown') AS dealer, "
            f"COUNT(*) AS cases_escalated, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR)) / 24.0 AS avg_turnaround_days, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR), 100)[OFFSET(50)] "
            f"/ 24.0 AS p50_turnaround_days, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR), 100)[OFFSET(90)] "
            f"/ 24.0 AS p90_turnaround_days "
            f"FROM {fq} WHERE dealer_escalated_at IS NOT NULL GROUP BY dealer"
        ),
        "v_dealer_escalation_slowest_cases": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_dealer_escalation_slowest_cases` AS "
            f"SELECT conversation_id, COALESCE(dealer, 'Unknown') AS dealer, "
            f"TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR) / 24.0 AS turnaround_days "
            f"FROM {fq} WHERE dealer_escalated_at IS NOT NULL AND resolved_at IS NOT NULL "
            f"ORDER BY turnaround_days DESC LIMIT 50"
        ),
        "v_case_aging": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_case_aging` AS "
            f"SELECT conversation_id, COALESCE(case_type, 'Unknown') AS case_type, "
            f"COALESCE(division, 'Unknown') AS division, COALESCE(dealer, 'Unknown') AS dealer, "
            f"COALESCE(pic, 'Unassigned') AS pic, status, created_at, "
            f"TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, HOUR) / 24.0 AS age_days, "
            f"CASE "
            f"WHEN TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, DAY) <= 3 THEN '1-3 days' "
            f"WHEN TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, DAY) <= 6 THEN '4-6 days' "
            f"ELSE '7+ days' END AS bucket_label "
            f"FROM {fq} WHERE status IN ('open', 'pending') AND created_at IS NOT NULL "
            f"ORDER BY age_days DESC"
        ),
        "v_volume_by_type_division": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_type_division` AS "
            f"SELECT FORMAT_DATE('%Y-%m', DATE(created_at)) AS month, "
            # Task 2 (Package E): same additive month_start widening as
            # v_state_trend -- see that view's comment.
            f"DATE_TRUNC(DATE(created_at), MONTH) AS month_start, "
            f"channel, "
            f"COALESCE(case_type, 'Unknown') AS case_type, "
            f"COALESCE(division, 'Unknown') AS division, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY month, month_start, channel, case_type, division"
        ),
        # Task 2 (Package E) reopened: same two-grains-not-one reasoning as
        # v_state_trend_daily above -- v_volume_by_type_division is
        # month-grain and has no known fork consumer yet (checked in the
        # original Task 2 pass), but widening it to day grain would still
        # be a live landmine for whoever adds one later, using the exact
        # same SELECT * assumption every other reader in this codebase
        # uses. A separate day-grain sibling avoids ever having to find
        # out the hard way.
        # `day` here is a UTC calendar day -- see the note above
        # `v_volume_daily` for the tenant-timezone consequence.
        "v_volume_by_type_division_daily": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_type_division_daily` AS "
            f"SELECT DATE(created_at) AS day, channel, "
            f"COALESCE(case_type, 'Unknown') AS case_type, "
            f"COALESCE(division, 'Unknown') AS division, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY day, channel, case_type, division"
        ),
        "v_category_by_vehicle_model": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_category_by_vehicle_model` AS "
            f"SELECT COALESCE(category, 'Unknown') AS category, "
            f"COALESCE(subcategory, 'Unknown') AS subcategory, "
            f"COALESCE(vehicle_model, 'Unknown') AS vehicle_model, "
            f"COALESCE(case_type, 'Unknown') AS case_type, COUNT(*) AS cases "
            f"FROM {fq} GROUP BY category, subcategory, vehicle_model, case_type "
            f"ORDER BY cases DESC"
        ),
    }
