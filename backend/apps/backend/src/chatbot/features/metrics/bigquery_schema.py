"""BigQuery table schema + view DDL for the conversations metrics table.

**Time zone: every date bucket in this file is a UTC calendar day.**
`created_at` is a TIMESTAMP (absolute time, no zone), and bare
`{d_created}` / `DATE_TRUNC({d_created}, ...)` /
`EXTRACT(... FROM created_at)` all default to UTC in BigQuery -- none of
them takes the server's or the reader's zone into account. See the
day-grain views below for what that means for the period-scoped pages.
"""

# ruff: noqa: S608  # DDL generation: project/dataset/table are internal config, not user input

from __future__ import annotations

import json
from dataclasses import dataclass

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
    bigquery.SchemaField(
        "dealer_escalated_at", "TIMESTAMP"
    ),  # Task 10: dealer escalation timestamp
    bigquery.SchemaField("case_type", "STRING"),
    bigquery.SchemaField("vehicle_model", "STRING"),
    bigquery.SchemaField("first_response_working_minutes", "INT64"),
    bigquery.SchemaField("resolution_working_minutes", "INT64"),
    # P1: NULLABLE on purpose. Every row synced before P1 has no intake stamp,
    # and NULL is the only honest value for "we never measured this" -- the
    # after-hours views bucket it as `unknown` rather than counting it against
    # either side.
    bigquery.SchemaField("received_in_business_hours", "BOOLEAN", mode="NULLABLE"),
    bigquery.SchemaField("received_at_local", "TIMESTAMP", mode="NULLABLE"),
    # P3: the case columns the client's report decks print. Every one NULLABLE
    # -- the sync reloads historical rows on every run, and a REQUIRED column
    # would fail the whole load job on the first pre-P3 conversation.
    bigquery.SchemaField("case_detail", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("case_state", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("escalated_to", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("vehicle_plate", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("vehicle_chassis", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("purchased_from_dealer", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("delay_reason", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("wip_issue", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("wip_action_taken", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("wip_next_action", "STRING", mode="NULLABLE"),
    # P4 task 8: REPEATED, not a joined string -- a comma-joined value cannot
    # be UNNESTed, and splitting it in SQL would break on any label containing
    # a comma. Every dimension derived from labels (dept, dealer, category) is
    # already its own column; this is the raw list for the tag breakdown.
    bigquery.SchemaField("labels", "STRING", mode="REPEATED"),
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
        if (
            not isinstance(edges, list)
            or not isinstance(labels, list)
            or len(labels) != len(edges) + 1
        ):
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


# Zones a tenant may report in. An allowlist, not a regex: this value is
# interpolated into DDL, and config is not a trust boundary worth betting a
# warehouse on. Add entries as tenants need them.
# ---------------------------------------------------------------------------
# P8 task 8 -- the four AI performance reports, and the column they must NOT
# be built on
# ---------------------------------------------------------------------------
#
# **`resolved_by` cannot distinguish AI from human, and building on it would
# overstate AI resolution by the entire resolved population.** It is derived in
# `mapping.py` from Chatwoot's `status` alone --
# `"agent" if status in {"open", "pending", "snoozed"} else "bot"` -- so
# `resolved_by='bot'` means "this case is not open, pending or snoozed", i.e.
# "resolved", and says nothing at all about who resolved it. (`v_resolution_
# split`'s existing `closed_by_bot` / `transfer_to_agent` column names are
# therefore misleading, but they are live and are deliberately left alone; the
# numbers under them are "resolved" and "not yet resolved".)
#
# So human involvement is inferred from the three signals the warehouse
# actually carries per case: a human assignee (`agent_id`, Chatwoot's
# `meta.assignee.id`), the `escalate` label the AI handoff writes, and
# `escalated_to` for a dealer escalation. One SQL fragment
# (`_HUMAN_TOUCH_SQL`), used by all four reports, so they cannot drift into
# four slightly different definitions of the same word.
#
# **What this still cannot see, named rather than papered over:** whether a
# human sent a MESSAGE without ever being assigned. `first_response_at` is
# Chatwoot's `first_reply_created_at`, which an agent-bot reply also sets, so
# it cannot separate a bot reply from a human one. Assignment is the closest
# available proxy and the definition string says so.

# Whether a human was ever involved in this case. Never NULL: `IS NOT NULL`,
# `IN UNNEST` over a REPEATED column, and `COALESCE` each return a real
# boolean, so `NOT (...)` is safe and no case falls out of both buckets.
_HUMAN_TOUCH_SQL = (
    "(agent_id IS NOT NULL "
    "OR 'escalate' IN UNNEST(labels) "
    "OR COALESCE(escalated_to, 'none') != 'none')"
)
_RESOLVED_SQL = "status = 'resolved'"
_AI_RESOLVED_SQL = f"({_RESOLVED_SQL} AND NOT {_HUMAN_TOUCH_SQL})"
_AGENT_RESOLVED_SQL = f"({_RESOLVED_SQL} AND {_HUMAN_TOUCH_SQL})"

# Returned as a column ON every one of these reports, not as documentation.
# Two reasonable readings of "deflection" differ by roughly a factor of two,
# and the one a client quotes must not be a guess about which we used.
AI_DEFLECTION_DEFINITION = (
    "Deflected means the case reached resolved status with NO human "
    "involvement at all -- no human assignee, no escalate label, no dealer "
    "escalation. A conversation the bot answered before a human took over is "
    "NOT deflected. Human involvement is inferred from assignment, not from "
    "message authorship."
)

AI_RESOLUTION_BASIS = (
    "AI-resolved means resolved with no human assignee, no escalate label and "
    "no dealer escalation. Deliberately NOT derived from resolved_by, which "
    "is computed from Chatwoot status alone and means resolved-vs-open rather "
    "than AI-vs-human. A human who replied without ever being assigned counts "
    "here as AI-resolved; Chatwoot first_reply_created_at is also set by "
    "agent-bot replies, so message authorship is not available to separate "
    "them."
)

AI_HANDOFF_REASON_BASIS = (
    "Reason is the AI classified subcategory on the escalated case. The "
    "model own free-text handoff reason is recorded on the ai_actions table in "
    "the agent service Postgres and is not exported to BigQuery, so it cannot "
    "be grouped here. An unclassified escalation buckets as not_classified, "
    "never folded into another reason."
)


def kb_coverage_basis(kb_score_floor: float) -> str:
    """What `v_kb_coverage`'s numbers actually measure, as a report column.

    Takes the floor so the string names the value that was in force. A coverage
    figure compared across a floor change without noticing is a trend that is
    entirely an artefact of configuration.
    """
    return (
        f"Coverage is the share of enquiries NOT classified "
        f"Subcategory='Unresolved Query'. The KB match score floor "
        f"(KB_SCORE_FLOOR={kb_score_floor}) is applied upstream inside the "
        f"live-FAQ search and the per-enquiry score is not persisted, so the "
        f"below-floor case is measured by the classification the bot writes "
        f"before handing off. Unresolved Query counts AGAINST coverage; "
        f"excluding it would make coverage rise as the KB got worse."
    )


SUPPORTED_REPORTING_TIMEZONES = frozenset(
    {"UTC", "Asia/Kuala_Lumpur", "Asia/Jakarta", "Asia/Singapore", "Asia/Bangkok"}
)


def _validate_timezone(reporting_timezone: str) -> str:
    """Return the zone, or raise. A typo must fail HERE -- at view creation,
    where somebody is watching -- rather than becoming a view that errors at
    query time on a dashboard in front of the client."""
    zone = (reporting_timezone or "UTC").strip()
    if zone not in SUPPORTED_REPORTING_TIMEZONES:
        raise ValueError(
            f"Unsupported REPORTING_TIMEZONE {zone!r}. Supported: "
            f"{', '.join(sorted(SUPPORTED_REPORTING_TIMEZONES))}."
        )
    return zone


def view_ddls(
    project: str,
    dataset: str,
    table: str = "conversations",
    sla_targets_json: str = "{}",
    reporting_timezone: str = "UTC",
    first_response_target_minutes: int = 120,
    csat_by_agent_enabled: bool = False,
    csat_ranking_min_samples: int = 10,
    kb_score_floor: float = 0.55,
) -> dict[str, str]:
    """The CREATE OR REPLACE VIEW statements for the Looker tiles.

    ``reporting_timezone`` defaults to UTC, and that default is the IDENTITY
    TRANSFORM: the emitted DDL is byte-identical to what shipped before this
    parameter existed -- not merely equivalent. `DATE(x, 'UTC')` means the same
    thing to BigQuery but is a different string, and "the string is unchanged"
    is what proves no live tenant's numbers moved. See
    test_bigquery_schema_timezone.py.

    ``csat_by_agent_enabled`` defaults False and omits `v_csat_by_agent`
    entirely, so the defaulted call returns the exact same key set it returned
    before P8 -- flags-off means "not created", not "created and empty",
    because `ensure_views` runs this over a live warehouse.
    """
    fq = f"`{project}.{dataset}.{table}`"
    bucket_case = _sla_bucket_case_sql(sla_targets_json)

    zone = _validate_timezone(reporting_timezone)
    _tz = "" if zone == "UTC" else f", '{zone}'"
    d_created = f"DATE(created_at{_tz})"
    d_resolved = f"DATE(resolved_at{_tz})"
    d_escalated = f"DATE(dealer_escalated_at{_tz})"
    # EXTRACT takes the zone in a different position from DATE.
    at_zone = "" if zone == "UTC" else f" AT TIME ZONE '{zone}'"
    ddls: dict[str, str] = {
        "v_volume_by_month_channel": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_month_channel` AS "
            f"SELECT FORMAT_DATE('%Y-%m', {d_created}) AS month, channel, "
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
        # P1: the two after-hours tiles. Three buckets, never two -- a row with
        # no intake stamp is `unknown`, because counting unmeasured history as
        # after-hours would invent an out-of-hours problem the tenant may not
        # have.
        "v_first_response_by_hours_split": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_first_response_by_hours_split` AS "
            f"SELECT FORMAT_DATE('%Y-%m', {d_created}) AS month, channel, "
            f"CASE WHEN received_in_business_hours IS NULL THEN 'unknown' "
            f"WHEN received_in_business_hours THEN 'in_hours' ELSE 'after_hours' END "
            f"AS arrival_window, "
            f"COUNT(*) AS cases, "
            f"AVG(first_response_working_minutes) AS avg_first_response_working_min, "
            f"APPROX_QUANTILES(first_response_working_minutes, 100)[OFFSET(90)] "
            f"AS p90_first_response_working_min "
            f"FROM {fq} GROUP BY month, channel, arrival_window"
        ),
        "v_volume_after_hours": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_after_hours` AS "
            f"SELECT FORMAT_DATE('%Y-%m', {d_created}) AS month, "
            f"{d_created} AS day, channel, "
            f"CASE WHEN received_in_business_hours IS NULL THEN 'unknown' "
            f"WHEN received_in_business_hours THEN 'in_hours' ELSE 'after_hours' END "
            f"AS arrival_window, "
            f"COUNT(*) AS volume "
            f"FROM {fq} GROUP BY month, day, channel, arrival_window"
        ),
        "v_volume_by_division": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_division` AS "
            f"SELECT FORMAT_DATE('%Y-%m', {d_created}) AS month, "
            f"COALESCE(division, 'Unknown') AS division, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY month, division"
        ),
        "v_dept_pic_performance": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_dept_pic_performance` AS "
            f"SELECT {d_created} AS day, "
            f"COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(pic, 'Unassigned') AS pic, COUNT(*) AS cases, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_resolution_min, "
            f"SAFE_DIVIDE(COUNTIF(resolved_at IS NOT NULL), COUNT(*)) AS resolution_rate "
            f"FROM {fq} GROUP BY day, department, pic ORDER BY cases DESC"
        ),
        "v_sla_achievement": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_sla_achievement` AS "
            f"SELECT {d_created} AS day, channel, COALESCE(division, 'Unknown') AS division, "
            f"COUNTIF(sla_deadline IS NOT NULL) AS with_sla, "
            f"COUNTIF(resolved_at IS NOT NULL AND resolved_at <= sla_deadline) AS met, "
            f"SAFE_DIVIDE(COUNTIF(resolved_at IS NOT NULL AND resolved_at <= sla_deadline), "
            f"COUNTIF(sla_deadline IS NOT NULL)) AS sla_achievement_rate "
            f"FROM {fq} GROUP BY day, channel, division"
        ),
        "v_reopen_rate": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_reopen_rate` AS "
            f"SELECT {d_created} AS day, COALESCE(dealer, 'Unknown') AS dealer, "
            f"COALESCE(department, 'Unknown') AS department, "
            f"COALESCE(pic, 'Unassigned') AS pic, COUNT(*) AS cases, "
            f"COUNTIF(reopen_count > 0) AS reopened, "
            f"SAFE_DIVIDE(COUNTIF(reopen_count > 0), COUNT(*)) AS reopen_rate "
            f"FROM {fq} GROUP BY day, dealer, department, pic"
        ),
        "v_resolution_time": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_resolution_time` AS "
            f"SELECT {d_resolved} AS day, channel, COALESCE(division, 'Unknown') AS division, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE), 100)[OFFSET(50)] AS p50_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE), 100)[OFFSET(90)] AS p90_min "
            f"FROM {fq} WHERE resolved_at IS NOT NULL GROUP BY day, channel, division"
        ),
        "v_nps_by_agent": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_nps_by_agent` AS "
            f"SELECT {d_created} AS day, COALESCE(agent_id, 'Unassigned') AS agent_id, channel, "
            f"COUNTIF(nps_score IS NOT NULL) AS respondents, "
            f"SAFE_DIVIDE("
            f"COUNTIF(nps_score >= 9) - COUNTIF(nps_score IS NOT NULL AND nps_score <= 6), "
            f"COUNTIF(nps_score IS NOT NULL)) * 100 AS nps "
            f"FROM {fq} WHERE channel IN ('Phone', 'WhatsApp') GROUP BY day, agent_id, channel"
        ),
        # ── The three day-grain views (`v_volume_daily`,
        # `v_state_trend_daily`, `v_volume_by_type_division_daily`) are the
        # sources every period-scoped read filters through
        # (`query_adapter._day_grain_block_for_period`, `WHERE day BETWEEN
        # @start AND @end`). Their `day` column, and therefore every week
        # and month bucket derived from it, is a **UTC calendar day**:
        # `{d_created}` on a TIMESTAMP defaults to UTC in BigQuery.
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
        # P4 BUILT THAT FIX: `view_ddls(..., reporting_timezone=...)`, fed by
        # the `REPORTING_TIMEZONE` setting and defaulted to UTC, where the
        # default is the *identity transform* (byte-identical DDL, asserted in
        # test_bigquery_schema_timezone.py). The paragraphs above still
        # describe exactly what a tenant sees while it remains UTC, which is
        # every tenant until someone changes it deliberately -- so this is
        # updated rather than deleted. Before switching a live tenant, run
        # scripts/compare-reporting-timezone.py and keep its output: the switch
        # re-buckets every historical figure on every existing dashboard
        # here. Logged in the Package E spec's §11.4 discrepancy log so the
        # live reconciliation attributes any edge-of-window mismatch to this
        # rather than to a definitional difference. (Package E final fix,
        # finding I4.)
        "v_volume_daily": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_daily` AS "
            f"SELECT {d_created} AS day, channel, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY day, channel"
        ),
        "v_volume_weekly": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_weekly` AS "
            f"SELECT DATE_TRUNC({d_created}, WEEK) AS week, channel, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY week, channel"
        ),
        "v_channel_anomaly": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_channel_anomaly` AS "
            f"WITH daily AS (SELECT channel, {d_created} AS d, COUNT(*) AS v "
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
            f"SELECT EXTRACT(DAYOFWEEK FROM created_at{at_zone}) AS day_of_week, "
            f"EXTRACT(HOUR FROM created_at{at_zone}) AS hour_of_day, "
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
            f"SELECT {d_created} AS day, COALESCE(agent_id, 'Unassigned') AS agent_id, "
            f"COALESCE(pic, 'Unassigned') AS pic, "
            f"COUNT(*) AS cases, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, created_at, MINUTE)) AS avg_resolution_min, "
            f"COUNTIF(status = 'resolved') AS resolved_cases "
            f"FROM {fq} GROUP BY day, agent_id, pic ORDER BY cases DESC"
        ),
        "v_first_response_by_channel": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_first_response_by_channel` AS "
            f"SELECT {d_created} AS day, channel, "
            f"AVG(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE)) AS avg_first_response_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE), 100)[OFFSET(50)] AS p50_first_response_min, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(first_response_at, created_at, MINUTE), 100)[OFFSET(90)] AS p90_first_response_min, "
            f"COUNT(*) AS with_first_response "
            f"FROM {fq} WHERE first_response_at IS NOT NULL GROUP BY day, channel"
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
            f"SELECT FORMAT_DATE('%Y-%m', {d_created}) AS month, "
            # Task 2 (Package E): month_start widens the view with a real DATE
            # so range queries can filter with a named parameter instead of
            # string-matching `month`. Same (month, status, division) grain as
            # before -- one row per group, unchanged -- so this is additive:
            # existing `SELECT *` readers keep getting identical rows plus one
            # new column.
            f"DATE_TRUNC({d_created}, MONTH) AS month_start, "
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
            f"SELECT {d_created} AS day, status, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COUNT(*) AS cases "
            f"FROM {fq} WHERE created_at IS NOT NULL "
            f"GROUP BY day, status, division"
        ),
        "v_resolution_sla_buckets": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_resolution_sla_buckets` AS "
            # P4: keyed on resolved_at. Grouping by creation date would put a
            # January case resolved in March into January's attainment figure.
            f"SELECT {d_resolved} AS day, "
            f"COALESCE(case_type, 'Unknown') AS case_type, "
            f"{bucket_case} AS bucket_label, "
            f"COUNT(*) AS cases "
            f"FROM {fq} WHERE resolution_working_minutes IS NOT NULL "
            f"GROUP BY day, case_type, bucket_label"
        ),
        "v_dealer_escalation": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_dealer_escalation` AS "
            # P4: keyed on dealer_escalated_at, NOT created_at. A dealer's
            # turnaround clock starts when the escalation reaches them, so a
            # case created in May and escalated in June belongs to June's
            # rows -- which is why this view's monthly total does not sum to
            # that month's case count.
            f"SELECT {d_escalated} AS day, "
            f"COALESCE(dealer, 'Unknown') AS dealer, "
            f"COUNT(*) AS cases_escalated, "
            f"AVG(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR)) / 24.0 AS avg_turnaround_days, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR), 100)[OFFSET(50)] "
            f"/ 24.0 AS p50_turnaround_days, "
            f"APPROX_QUANTILES(TIMESTAMP_DIFF(resolved_at, dealer_escalated_at, HOUR), 100)[OFFSET(90)] "
            f"/ 24.0 AS p90_turnaround_days "
            f"FROM {fq} WHERE dealer_escalated_at IS NOT NULL GROUP BY day, dealer"
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
            f"COALESCE(pic, 'Unassigned') AS pic, status, "
            f"COALESCE(case_state, 'unknown') AS case_state, created_at, "
            f"{d_created} AS day, "
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
            f"SELECT FORMAT_DATE('%Y-%m', {d_created}) AS month, "
            # Task 2 (Package E): same additive month_start widening as
            # v_state_trend -- see that view's comment.
            f"DATE_TRUNC({d_created}, MONTH) AS month_start, "
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
            f"SELECT {d_created} AS day, channel, "
            f"COALESCE(case_type, 'Unknown') AS case_type, "
            f"COALESCE(division, 'Unknown') AS division, COUNT(*) AS volume "
            f"FROM {fq} GROUP BY day, channel, case_type, division"
        ),
        "v_category_by_vehicle_model": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_category_by_vehicle_model` AS "
            f"SELECT COALESCE(category, 'Unknown') AS category, "
            f"COALESCE(subcategory, 'Unknown') AS subcategory, "
            f"COALESCE(vehicle_model, 'Unknown') AS vehicle_model, "
            f"COALESCE(case_type, 'Unknown') AS case_type, "
            f"COALESCE(case_detail, 'Unspecified') AS case_detail, COUNT(*) AS cases "
            f"FROM {fq} "
            f"GROUP BY category, subcategory, vehicle_model, case_type, case_detail "
            f"ORDER BY cases DESC"
        ),
        # P3: the client's concern pivot -- Level 1 (subcategory) nested under
        # its division, drilling into Level 2 (case_detail), with a grand total.
        #
        # `case_detail` is COALESCEd to 'Unspecified' rather than filtered out.
        # Dropping null-detail rows would make this pivot's total disagree with
        # the headline case count -- which is exactly the C2 297-vs-264
        # discrepancy the gap analysis raised as question Q8. One instance of
        # that problem is enough.
        # P4 task 7: per-dealer first response against a working-minutes
        # target. `first_response_working_minutes` has been stored since
        # Package E and read by nothing; P1 was its first reader, this is its
        # second.
        #
        # Cases with no first response are EXCLUDED, not counted as failures.
        # An open case has not missed the target -- it has not answered it, and
        # counting it against the rate would make attainment fall as volume
        # rises, which reads as a service regression that did not happen.
        # `measured_cases` ships alongside the percentage because 100% over 3
        # cases and 100% over 3,000 are different statements.
        "v_first_response_by_dealer": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_first_response_by_dealer` AS "
            f"SELECT {d_created} AS day, COALESCE(dealer, 'Unknown') AS dealer, "
            f"COUNT(*) AS measured_cases, "
            f"AVG(first_response_working_minutes) AS avg_first_response_working_min, "
            f"APPROX_QUANTILES(first_response_working_minutes, 100)[OFFSET(90)] "
            f"AS p90_first_response_working_min, "
            f"SAFE_DIVIDE("
            f"COUNTIF(first_response_working_minutes <= {int(first_response_target_minutes)}), "
            f"COUNT(*)) AS attainment_rate "
            f"FROM {fq} WHERE first_response_working_minutes IS NOT NULL "
            f"GROUP BY day, dealer"
        ),
        # P4 task 8 (§4.80): volume per label.
        #
        # UNNEST means a case with three labels appears in three buckets, so
        # this view's total is deliberately LARGER than the case count, and a
        # case with no labels does not appear at all. Both are correct for a
        # tag breakdown and both make the column un-summable -- which is why
        # /metrics/by-tag carries a note saying so.
        "v_volume_by_tag": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_volume_by_tag` AS "
            f"SELECT {d_created} AS day, tag, channel, COUNT(*) AS cases "
            f"FROM {fq}, UNNEST(labels) AS tag "
            f"GROUP BY day, tag, channel"
        ),
        "v_concern_pivot": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_concern_pivot` AS "
            f"SELECT FORMAT_DATE('%Y-%m', {d_created}) AS month, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COALESCE(subcategory, 'Unspecified') AS concern_level_1, "
            f"COALESCE(case_detail, 'Unspecified') AS concern_level_2, "
            f"COUNT(*) AS cases "
            f"FROM {fq} "
            f"GROUP BY ROLLUP(month, division, concern_level_1, concern_level_2)"
        ),
        # P3: the four series the client's status slide asks for, from the
        # real case_state -- not inferred from Chatwoot's `status`, which
        # cannot distinguish WIP from temp-closed. `v_state_trend` above still
        # reads `status` and is deliberately untouched.
        #
        # A NULL case_state reports as 'unknown', never folded into 'closed' or
        # 'open': every row synced before P3 has none, and quietly assigning
        # them a state would invent a trend.
        "v_case_state_trend": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_case_state_trend` AS "
            f"SELECT FORMAT_DATE('%Y-%m', {d_created}) AS month, "
            f"{d_created} AS day, "
            f"CASE WHEN case_state IS NULL OR TRIM(case_state) = '' THEN 'unknown' "
            f"WHEN UPPER(case_state) = 'WIP' THEN 'wip' "
            f"WHEN UPPER(case_state) = 'TEMP_CLOSED' THEN 'temp_closed' "
            f"WHEN UPPER(case_state) IN ('SOLVED', 'CLOSED') THEN 'closed' "
            f"ELSE LOWER(case_state) END AS case_state, "
            f"COALESCE(escalated_to, 'none') AS escalated_to, "
            f"COUNT(*) AS cases "
            f"FROM {fq} GROUP BY month, day, case_state, escalated_to"
        ),
        # ── P8 task 8: the four AI performance reports (§4.56 ①-④ plus the
        # satisfaction split). See `_HUMAN_TOUCH_SQL` above for why none of
        # them reads `resolved_by`.
        #
        # Every rate ships its denominator, and the denominator is `cases`
        # (every case in the bucket), not `resolved` -- an AI resolution rate
        # computed over resolved cases only rises as the backlog grows, which
        # reads as an improvement that did not happen.
        "v_ai_resolution": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_ai_resolution` AS "
            f"SELECT {d_created} AS day, channel, "
            f"COUNT(*) AS cases, "
            f"COUNTIF{_AI_RESOLVED_SQL} AS ai_resolved, "
            f"COUNTIF{_AGENT_RESOLVED_SQL} AS agent_resolved, "
            f"COUNTIF(NOT ({_RESOLVED_SQL})) AS unresolved, "
            f"SAFE_DIVIDE(COUNTIF{_AI_RESOLVED_SQL}, COUNT(*)) AS ai_resolution_rate, "
            f"SAFE_DIVIDE(COUNTIF{_AGENT_RESOLVED_SQL}, COUNT(*)) AS agent_resolution_rate, "
            f"{_sql_string(AI_RESOLUTION_BASIS)} AS resolution_basis "
            f"FROM {fq} GROUP BY day, channel, resolution_basis"
        ),
        # Long form, one row per bucket, deliberately three buckets and not
        # two: `ai_resolved + agent_resolved` alone would not sum to the case
        # count, and a reader who checks (they do) would find the report
        # disagreeing with the headline volume -- the same C2 discrepancy
        # `v_concern_pivot` was written to avoid.
        "v_ai_vs_human": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_ai_vs_human` AS "
            f"SELECT {d_created} AS day, channel, "
            f"CASE WHEN NOT ({_RESOLVED_SQL}) THEN 'unresolved' "
            f"WHEN {_HUMAN_TOUCH_SQL} THEN 'agent_resolved' "
            f"ELSE 'ai_resolved' END AS resolution_path, "
            f"COUNT(*) AS cases, "
            f"SUM(COUNT(*)) OVER (PARTITION BY {d_created}, channel) AS cases_in_bucket, "
            f"{_sql_string(AI_RESOLUTION_BASIS)} AS resolution_basis "
            f"FROM {fq} GROUP BY day, channel, resolution_path, resolution_basis"
        ),
        # A `not_classified` bucket of its own, never folded into another
        # reason: an escalation nobody classified is a gap in classification,
        # not a category of problem.
        "v_ai_escalation_reasons": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_ai_escalation_reasons` AS "
            f"SELECT {d_created} AS day, "
            f"COALESCE(NULLIF(TRIM(subcategory), ''), 'not_classified') AS handoff_reason, "
            f"COUNT(*) AS cases, "
            f"SUM(COUNT(*)) OVER (PARTITION BY {d_created}) AS escalated_cases, "
            f"SAFE_DIVIDE(COUNT(*), SUM(COUNT(*)) OVER (PARTITION BY {d_created})) "
            f"AS share_of_escalations, "
            f"{_sql_string(AI_HANDOFF_REASON_BASIS)} AS handoff_reason_basis "
            f"FROM {fq} WHERE {_HUMAN_TOUCH_SQL} "
            f"GROUP BY day, handoff_reason, handoff_reason_basis "
            f"ORDER BY cases DESC"
        ),
        "v_ai_deflection": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_ai_deflection` AS "
            f"SELECT {d_created} AS day, channel, "
            f"COUNT(*) AS cases, "
            f"COUNTIF{_AI_RESOLVED_SQL} AS deflected, "
            f"COUNTIF({_HUMAN_TOUCH_SQL}) AS human_involved, "
            f"SAFE_DIVIDE(COUNTIF{_AI_RESOLVED_SQL}, COUNT(*)) AS deflection_rate, "
            f"{_sql_string(AI_DEFLECTION_DEFINITION)} AS deflection_definition "
            f"FROM {fq} GROUP BY day, channel, deflection_definition"
        ),
        # The satisfaction split. `respondents` is the denominator of both
        # rates and is not the same as `cases` -- a path with better CSAT and
        # a quarter of the response rate is not a better path, and the reader
        # needs both numbers to see that.
        "v_csat_by_resolution": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_csat_by_resolution` AS "
            f"SELECT {d_created} AS day, channel, "
            f"CASE WHEN NOT ({_RESOLVED_SQL}) THEN 'unresolved' "
            f"WHEN {_HUMAN_TOUCH_SQL} THEN 'agent_resolved' "
            f"ELSE 'ai_resolved' END AS resolution_path, "
            f"COUNT(*) AS cases, "
            f"COUNTIF(csat_score IS NOT NULL) AS respondents, "
            f"AVG(csat_score) AS avg_score, "
            f"SAFE_DIVIDE(COUNTIF(csat_score >= 4), COUNTIF(csat_score IS NOT NULL)) "
            f"AS satisfied_rate, "
            f"{_sql_string(AI_RESOLUTION_BASIS)} AS resolution_basis "
            f"FROM {fq} GROUP BY day, channel, resolution_path, resolution_basis"
        ),
        # ── P8 task 9: KB coverage.
        #
        # The score floor (`KB_SCORE_FLOOR`, default 0.55) is applied UPSTREAM,
        # inside the live-FAQ search, and the per-enquiry match score is not
        # persisted anywhere the warehouse can see. What IS observable is the
        # consequence: an enquiry the KB could not answer above the floor is
        # the one the bot classified `Subcategory='Unresolved Query'` before
        # handing off (see `features/chat/prompts.py`). So coverage is measured
        # from that classification, and `coverage_basis` says which floor was
        # in force so a coverage figure cannot be compared across a floor
        # change without noticing.
        #
        # An `Unresolved Query` counts AGAINST coverage rather than being
        # excluded, which is the whole point -- excluding the failures would
        # make coverage rise as the KB got worse.
        "v_kb_coverage": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_kb_coverage` AS "
            f"SELECT {d_created} AS day, channel, "
            f"COALESCE(division, 'Unknown') AS division, "
            f"COUNT(*) AS enquiries, "
            f"COUNTIF(LOWER(TRIM(COALESCE(subcategory, ''))) = 'unresolved query') "
            f"AS unresolved_queries, "
            f"COUNTIF(LOWER(TRIM(COALESCE(subcategory, ''))) != 'unresolved query') "
            f"AS matched_enquiries, "
            f"SAFE_DIVIDE("
            f"COUNTIF(LOWER(TRIM(COALESCE(subcategory, ''))) != 'unresolved query'), "
            f"COUNT(*)) AS coverage_rate, "
            f"{_sql_string(kb_coverage_basis(kb_score_floor))} AS coverage_basis "
            f"FROM {fq} GROUP BY day, channel, division, coverage_basis"
        ),
    }

    # ── P8 task 6: CSAT per agent.
    #
    # A SIBLING of `v_csat`, never a widening of it. `v_csat` is grouped by
    # channel and read by live dashboards; adding `agent_id` to it would
    # multiply its rows and silently change every tile built on it. So `v_csat`
    # above is byte-identical (`test_v_csat_is_completely_unchanged`) and this
    # is a second view.
    #
    # **The ranking floor suppresses the RANKING, not the row.** An agent with
    # one 5-star response next to one with 200 responses averaging 4.7 is not
    # a comparison, and publishing it as a league table is how a measurement
    # becomes a grievance. But hiding the low-sample agent entirely makes the
    # list look complete when it is not -- the reader cannot tell "this agent
    # has too few ratings to rank" from "this agent has no cases". So every
    # agent gets a row with their real `respondents` count, and
    # `rank_in_channel` is NULL below the floor while `is_rankable` says why.
    # `ranking_min_samples` travels ON the row so a consumer renders the
    # actual configured floor rather than a hardcoded guess.
    #
    # **Every rate ships its denominator.** `avg_score` and `satisfied_rate`
    # are meaningless without `respondents`, and `respondents` is meaningless
    # without `cases` -- 100% satisfied over 2 responses from 300 cases is a
    # response-rate story, not a satisfaction story.
    #
    # **An agent with no ratings has a NULL score, never a 0.** `AVG` over an
    # all-NULL column is NULL and it is deliberately not COALESCEd: 0 is the
    # worst possible rating and "nobody answered" is not a rating at all. The
    # partition trick in `rank_in_channel` (`PARTITION BY ... is_rankable`)
    # exists so the suppressed rows do not consume rank positions -- ranking
    # over every row and then blanking some would leave the visible ranks
    # reading 1, 3, 7.
    if csat_by_agent_enabled:
        floor = int(csat_ranking_min_samples)
        ddls["v_csat_by_agent"] = (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_csat_by_agent` AS "
            f"WITH per_agent AS (SELECT {d_created} AS day, "
            f"COALESCE(agent_id, 'Unassigned') AS agent_id, channel, "
            f"COUNT(*) AS cases, "
            f"COUNTIF(csat_score IS NOT NULL) AS respondents, "
            f"AVG(csat_score) AS avg_score, "
            f"COUNTIF(csat_score >= 4) AS satisfied, "
            f"SAFE_DIVIDE(COUNTIF(csat_score >= 4), COUNTIF(csat_score IS NOT NULL)) "
            f"AS satisfied_rate "
            f"FROM {fq} GROUP BY day, agent_id, channel) "
            f"SELECT day, agent_id, channel, cases, respondents, avg_score, "
            f"satisfied, satisfied_rate, "
            f"{floor} AS ranking_min_samples, "
            f"respondents >= {floor} AS is_rankable, "
            f"CASE WHEN respondents >= {floor} THEN RANK() OVER ("
            f"PARTITION BY day, channel, respondents >= {floor} "
            f"ORDER BY avg_score DESC) END AS rank_in_channel "
            f"FROM per_agent"
        )

    return ddls


# ---------------------------------------------------------------------------
# P8 task 4 -- AI cost, and the honesty problem it inherits
# ---------------------------------------------------------------------------
#
# These views read `token_usage` (see `features/metrics/token_usage.py`), NOT
# `conversations`, which is why they live in their own function rather than in
# `view_ddls`: one DDL builder per base table, the same split `faq_schema.py`
# and `turn_schema.py` already use.
#
# **There is no cost column in any of them, and that is deliberate.** Prices
# are effective-dated and stored in Firestore (`features/metrics/
# price_table.py`), so a BigQuery view cannot join to a rate at all -- tokens
# become money in Python, in `features/metrics/ai_cost.py`, at the point where
# `PriceTable.price_for(model, token_class, at=day)` can be asked about the
# date of the usage rather than about today. A view that emitted a
# `cost_usd` column would have to hardcode a rate to do it.
#
# **What these views exist to make structural: the cost report cannot be
# complete, and must not read as if it were.** Five of the nine surfaces this
# product calls Gemini from are metered; one is visible but unpriceable; three
# produce no row at all. A `SELECT SUM(...) FROM token_usage` would therefore
# print a number that silently omits the busiest surface in the product, and
# would print it as a total. So the inventory below is published *into the
# warehouse* as its own view and LEFT JOINed onto the usage aggregate: every
# declared surface gets a row in `v_ai_cost` whether or not it has ever
# produced usage, and an unmetered one carries NULL tokens and a
# `cost_status_reason` saying why. A zero is a claim about spend; a NULL is a
# statement about instrumentation, and the two must not be interchangeable on
# a dashboard that reads only SQL.

AI_COST_STATUS_METERED = "metered"
AI_COST_STATUS_UNPRICEABLE = "unpriceable"
AI_COST_STATUS_UNMETERED = "unmetered"


@dataclass(frozen=True)
class SurfaceCoverage:
    """One product surface that calls Gemini, and whether its spend is
    knowable.

    `status` is one of the three `AI_COST_STATUS_*` constants above.
    `reason` is shown to the reader of the report -- it is the sentence that
    stops a NULL being read as a zero, so it says what is missing and why,
    not "unavailable".
    """

    service: str
    surface: str
    status: str
    reason: str


# The exact inventory established by P8 tasks 1-3 and the metering routing
# fix. Any change here is a change to what the cost report claims, so it is
# one list, in one place, read by both the warehouse view and the endpoint.
AI_COST_SURFACE_COVERAGE: tuple[SurfaceCoverage, ...] = (
    SurfaceCoverage(
        "backend",
        "assist.suggest",
        AI_COST_STATUS_METERED,
        "Metered at the google-genai client boundary. /assist/summarize and "
        "/assist/ask roll up here -- one router, one client.",
    ),
    SurfaceCoverage(
        "backend",
        "assist.copilot",
        AI_COST_STATUS_METERED,
        "Metered at the google-genai client boundary.",
    ),
    SurfaceCoverage(
        "backend",
        "assist.translate",
        AI_COST_STATUS_METERED,
        "Metered at the google-genai client boundary.",
    ),
    SurfaceCoverage(
        "backend",
        "chat.transcribe",
        AI_COST_STATUS_METERED,
        "Metered, but this is the speech-to-text call only. The /chat/turn "
        "reply on the same path is generated by google-adk and is not "
        "metered -- see the chat.turn row.",
    ),
    SurfaceCoverage(
        "backend",
        "phone.classify",
        AI_COST_STATUS_METERED,
        "Metered at the google-genai client boundary.",
    ),
    SurfaceCoverage(
        "backend",
        "embed",
        AI_COST_STATUS_UNPRICEABLE,
        "Visible but unpriceable. Embeddings bill per CHARACTER and "
        "EmbedContentResponse carries no usage_metadata at all, so all three "
        "token counts are NULL by construction. The price table has a "
        "per-character class, but token_usage has no character-count column, "
        "so embedding cost is not computable end to end. Reported as "
        "unpriced, never as 0.",
    ),
    SurfaceCoverage(
        "backend",
        "chat.turn",
        AI_COST_STATUS_UNMETERED,
        "Absent entirely, and this is the largest gap. google-adk takes a "
        "model STRING and constructs its own Gemini client inside the "
        "installed package, so the busiest surface in the product cannot be "
        "metered at our client boundary. No usage row is ever written, so "
        "its spend is missing from this report rather than being zero.",
    ),
    SurfaceCoverage(
        "backend",
        "phone.live",
        AI_COST_STATUS_UNMETERED,
        "Absent entirely. Live API usage arrives in server messages rather "
        "than on a response object, so no usage row is produced even though "
        "the session is routed through the metered client.",
    ),
    SurfaceCoverage(
        "agent",
        "orchestrator",
        AI_COST_STATUS_UNMETERED,
        "Absent from the warehouse. The agent service records all three "
        "token counts onto its ai_actions table in Postgres, and nothing "
        "exports them to BigQuery, so this service contributes no priced "
        "spend here yet.",
    ),
)

# Billed token classes `TokenUsage` never captures, so no report built on it
# can include them. Named rather than dropped: dropping them understates
# spend for any thinking-enabled model.
AI_COST_EXCLUDED_TOKEN_CLASSES: tuple[str, ...] = (
    "thoughts_token_count",
    "tool_use_prompt_token_count",
)


def _sql_string(value: str) -> str:
    """A single-quoted SQL literal. Doubles embedded quotes -- these strings
    are ours, but a reason sentence acquiring an apostrophe must not be able
    to produce a view that fails at query time on somebody's dashboard."""
    return "'" + value.replace("'", "''") + "'"


def ai_cost_view_ddls(
    project: str,
    dataset: str,
    table: str = "token_usage",
    reporting_timezone: str = "UTC",
) -> dict[str, str]:
    """The three `token_usage` views behind `GET /metrics/ai-cost`.

    - **`v_ai_token_usage`** -- day x service x surface x model: the token
      sums, and beside each sum the number of calls that actually carried
      that count. The counts are not decoration. `SUM()` skips NULLs, so a
      surface whose responses carry no usage metadata sums to NULL and one
      that carried it on 3 of 3000 calls sums to a small number; without
      `calls_with_*` beside it a reader cannot tell a small bill from a small
      sample. `calls_without_usage_metadata` counts the calls that carried
      none of the three, which is exactly the embedding case.
    - **`v_ai_cost_surface_coverage`** -- the `AI_COST_SURFACE_COVERAGE`
      inventory as SQL rows, so the caveat lives in the warehouse and not
      only in an endpoint payload a BI tool never reads.
    - **`v_ai_cost`** -- the coverage inventory LEFT JOINed onto the usage
      aggregate. Every declared surface appears. An unmetered surface has
      `day IS NULL` and NULL tokens, never 0, with its reason on the row.

    None of the three carries money; see the module comment above.
    """
    fq = f"`{project}.{dataset}.{table}`"
    zone = _validate_timezone(reporting_timezone)
    _tz = "" if zone == "UTC" else f", '{zone}'"
    d_occurred = f"DATE(occurred_at{_tz})"

    coverage_structs = ", ".join(
        f"STRUCT({_sql_string(row.service)} AS service, "
        f"{_sql_string(row.surface)} AS surface, "
        f"{_sql_string(row.status)} AS cost_status, "
        f"{_sql_string(row.reason)} AS cost_status_reason)"
        for row in AI_COST_SURFACE_COVERAGE
    )

    return {
        "v_ai_token_usage": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_ai_token_usage` AS "
            f"SELECT {d_occurred} AS day, service, surface, model, "
            f"COUNT(*) AS calls, "
            f"SUM(prompt_tokens) AS prompt_tokens, "
            f"SUM(output_tokens) AS output_tokens, "
            f"SUM(cached_tokens) AS cached_tokens, "
            f"COUNTIF(prompt_tokens IS NOT NULL) AS calls_with_prompt_tokens, "
            f"COUNTIF(output_tokens IS NOT NULL) AS calls_with_output_tokens, "
            f"COUNTIF(cached_tokens IS NOT NULL) AS calls_with_cached_tokens, "
            f"COUNTIF(prompt_tokens IS NULL AND output_tokens IS NULL "
            f"AND cached_tokens IS NULL) AS calls_without_usage_metadata "
            f"FROM {fq} GROUP BY day, service, surface, model"
        ),
        "v_ai_cost_surface_coverage": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_ai_cost_surface_coverage` AS "
            f"SELECT * FROM UNNEST([{coverage_structs}])"
        ),
        "v_ai_cost": (
            f"CREATE OR REPLACE VIEW `{project}.{dataset}.v_ai_cost` AS "
            f"SELECT c.service, c.surface, c.cost_status, c.cost_status_reason, "
            f"u.day, u.model, u.calls, "
            f"u.prompt_tokens, u.output_tokens, u.cached_tokens, "
            f"u.calls_with_prompt_tokens, u.calls_with_output_tokens, "
            f"u.calls_with_cached_tokens, u.calls_without_usage_metadata "
            f"FROM `{project}.{dataset}.v_ai_cost_surface_coverage` c "
            f"LEFT JOIN `{project}.{dataset}.v_ai_token_usage` u "
            f"ON u.service = c.service AND u.surface = c.surface"
        ),
    }
