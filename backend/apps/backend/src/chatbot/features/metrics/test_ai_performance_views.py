"""P8 task 8 -- the four AI performance reports.

**Test two is the definition**, written as a test because it is the number a
client will quote and two reasonable readings of "deflection" differ by roughly
a factor of two. Test three puts that definition ON the report so nobody has
to guess which reading produced the figure.

The load-bearing negative test here is
`test_no_ai_report_is_built_on_resolved_by`. `resolved_by` is derived in
`mapping.py` from Chatwoot's `status` alone, so `resolved_by='bot'` means
"resolved", not "the AI resolved it" -- an AI-resolution report built on it
would count every resolved case as AI-resolved, which is the exact shape of
overstatement this package exists to prevent.

Scope: this covers §4.56 ①-④ plus the satisfaction split only. AI Root Cause
Analysis and KB Improvement recommendations are NOT built, here or anywhere,
and nothing in these views claims them.

No BigQuery here (controller decision D2), so the SQL is asserted
structurally, as P4 and P5 did.
"""

from __future__ import annotations

from chatbot.features.metrics.bigquery_schema import (
    AI_DEFLECTION_DEFINITION,
    AI_HANDOFF_REASON_BASIS,
    AI_RESOLUTION_BASIS,
    CONVERSATIONS_SCHEMA,
    view_ddls,
)

PROJECT, DATASET = "proj", "ds"

AI_VIEWS = (
    "v_ai_resolution",
    "v_ai_vs_human",
    "v_ai_escalation_reasons",
    "v_ai_deflection",
    "v_csat_by_resolution",
)


def _ddls() -> dict[str, str]:
    return view_ddls(PROJECT, DATASET)


# ---------------------------------------------------------------------------
# The seven tests named in the task brief
# ---------------------------------------------------------------------------


def test_a_case_resolved_with_no_agent_message_counts_as_ai_resolved() -> None:
    sql = _ddls()["v_ai_resolution"]
    assert "`proj.ds.v_ai_resolution`" in sql
    assert "`proj.ds.conversations`" in sql
    # AI-resolved == resolved AND no human involvement, by the one shared
    # definition every report here uses.
    assert (
        "COUNTIF(status = 'resolved' AND NOT (agent_id IS NOT NULL "
        "OR 'escalate' IN UNNEST(labels) "
        "OR COALESCE(escalated_to, 'none') != 'none')) AS ai_resolved" in sql
    )
    assert "AS ai_resolution_rate" in sql


def test_a_case_where_the_bot_replied_then_an_agent_took_over_is_not_deflected() -> None:
    """THE definition. Any human involvement disqualifies, so a conversation
    the bot answered before a human took over is not deflected."""
    sql = _ddls()["v_ai_deflection"]
    # all three human-involvement signals disqualify
    assert "agent_id IS NOT NULL" in sql
    assert "'escalate' IN UNNEST(labels)" in sql
    assert "COALESCE(escalated_to, 'none') != 'none'" in sql
    assert "AND NOT (agent_id IS NOT NULL" in sql, "human involvement must EXCLUDE"
    # and the counter-bucket is published beside it, so the two are checkable
    assert "AS human_involved" in sql
    assert "AS deflected" in sql


def test_the_deflection_definition_string_is_returned_with_the_report() -> None:
    """On the report, not in a comment -- a caption is droppable."""
    sql = _ddls()["v_ai_deflection"]
    assert "AS deflection_definition" in sql
    assert AI_DEFLECTION_DEFINITION.replace("'", "''") in sql
    # the definition says the thing that distinguishes the two readings
    assert "NOT deflected" in AI_DEFLECTION_DEFINITION
    assert "before a human took over" in AI_DEFLECTION_DEFINITION


def test_ai_vs_human_volumes_sum_to_the_total_case_count() -> None:
    """Three buckets, not two. Two would not sum to the case count, and the
    report would visibly disagree with the headline volume."""
    sql = _ddls()["v_ai_vs_human"]
    for bucket in ("'unresolved'", "'agent_resolved'", "'ai_resolved'"):
        assert bucket in sql, bucket
    # a CASE with an ELSE has no unmatched rows, so every case lands in
    # exactly one bucket
    assert "ELSE 'ai_resolved' END AS resolution_path" in sql
    assert "WHERE" not in sql, "a filtered view cannot sum to the case count"
    # and the bucket total travels on the row so the sum is checkable without
    # a second query
    assert "SUM(COUNT(*)) OVER (PARTITION BY DATE(created_at), channel) AS cases_in_bucket" in sql


def test_escalation_reasons_are_grouped_by_the_handoff_reason() -> None:
    sql = _ddls()["v_ai_escalation_reasons"]
    assert "AS handoff_reason" in sql
    assert "GROUP BY day, handoff_reason, handoff_reason_basis" in sql
    # only escalated cases are in scope
    assert "WHERE (agent_id IS NOT NULL" in sql
    # an unclassified escalation is its OWN bucket, never folded into another
    # reason
    assert "COALESCE(NULLIF(TRIM(subcategory), ''), 'not_classified')" in sql
    # and the payload says what "reason" actually means here, because the
    # model's own free-text reason is not in the warehouse
    assert "AS handoff_reason_basis" in sql
    assert "ai_actions" in AI_HANDOFF_REASON_BASIS


def test_the_csat_split_distinguishes_ai_resolved_from_agent_resolved_cases() -> None:
    sql = _ddls()["v_csat_by_resolution"]
    assert "AS resolution_path" in sql
    assert "'ai_resolved'" in sql and "'agent_resolved'" in sql
    assert "AVG(csat_score) AS avg_score" in sql
    assert "COUNTIF(csat_score IS NOT NULL) AS respondents" in sql
    # v_csat itself is untouched by this split
    assert "GROUP BY channel" in _ddls()["v_csat"]


def test_every_rate_returns_its_denominator() -> None:
    ddls = _ddls()
    # v_ai_resolution / v_ai_deflection: rates over `cases`, and `cases` is
    # every case in the bucket -- not resolved cases, which would make the
    # rate rise as the backlog grows.
    for view in ("v_ai_resolution", "v_ai_deflection"):
        sql = ddls[view]
        assert "COUNT(*) AS cases" in sql, view
        assert "SAFE_DIVIDE" in sql, view
        assert ", COUNT(*))" in sql, f"{view}: rate not divided by the case count"
    # v_ai_escalation_reasons: share over the day's escalated total
    reasons = ddls["v_ai_escalation_reasons"]
    assert "AS escalated_cases" in reasons
    assert "AS share_of_escalations" in reasons
    # v_csat_by_resolution: respondents is the CSAT denominator, and it is not
    # the same number as cases
    csat = ddls["v_csat_by_resolution"]
    assert "AS respondents" in csat
    assert "COUNT(*) AS cases" in csat
    assert "SAFE_DIVIDE(COUNTIF(csat_score >= 4), COUNTIF(csat_score IS NOT NULL))" in csat
    # v_ai_vs_human: volumes, with the bucket total for the share
    assert "AS cases_in_bucket" in ddls["v_ai_vs_human"]


# ---------------------------------------------------------------------------
# The honesty properties
# ---------------------------------------------------------------------------


def test_no_ai_report_is_built_on_resolved_by() -> None:
    """`resolved_by` is `status`-derived: 'bot' means resolved, not AI.

    A report grouped on it would count every resolved case as AI-resolved --
    an overstatement of the whole resolved population, presented as the
    headline AI figure.
    """
    ddls = _ddls()
    # The basis strings mention `resolved_by` on purpose -- they exist to warn
    # the reader off it -- so strip the quoted literals before checking that
    # no *expression* touches the column.
    literals = (
        AI_RESOLUTION_BASIS.replace("'", "''"),
        AI_DEFLECTION_DEFINITION.replace("'", "''"),
        AI_HANDOFF_REASON_BASIS.replace("'", "''"),
    )
    for view in AI_VIEWS:
        sql = ddls[view]
        for literal in literals:
            sql = sql.replace(literal, "")
        assert "resolved_by" not in sql, f"{view} reads resolved_by"


def test_the_four_reports_share_one_definition_of_human_involvement() -> None:
    """Four slightly different definitions of the same word is how a deck
    ends up with two AI-resolution figures that do not match."""
    fragment = (
        "(agent_id IS NOT NULL OR 'escalate' IN UNNEST(labels) "
        "OR COALESCE(escalated_to, 'none') != 'none')"
    )
    ddls = _ddls()
    for view in AI_VIEWS:
        assert fragment in ddls[view], view


def test_the_resolution_basis_is_returned_on_every_report_that_uses_it() -> None:
    ddls = _ddls()
    escaped = AI_RESOLUTION_BASIS.replace("'", "''")
    for view in ("v_ai_resolution", "v_ai_vs_human", "v_csat_by_resolution"):
        assert "AS resolution_basis" in ddls[view], view
        assert escaped in ddls[view], view
    # and it names the trap explicitly, so a reader of the report knows why
    # the number differs from v_resolution_split's closed_by_bot
    assert "NOT derived from resolved_by" in AI_RESOLUTION_BASIS


def test_no_report_claims_root_cause_analysis_or_kb_recommendations() -> None:
    """§4.56 ⑦ and ⑧ are not built. The vendor response has already once
    claimed capabilities that were not built; do not add to that list."""
    ddls = _ddls()
    for view, sql in ddls.items():
        lowered = sql.lower()
        assert "root_cause" not in lowered, view
        assert "root cause" not in lowered, view
        assert "recommend" not in lowered, view


def test_the_ai_reports_honour_the_reporting_timezone() -> None:
    zoned = view_ddls(PROJECT, DATASET, reporting_timezone="Asia/Kuala_Lumpur")
    for view in AI_VIEWS:
        assert "DATE(created_at, 'Asia/Kuala_Lumpur')" in zoned[view], view
        assert "DATE(created_at)" not in zoned[view], view


def test_every_column_the_ai_reports_reference_exists_in_the_schema() -> None:
    names = {f.name for f in CONVERSATIONS_SCHEMA}
    ddls = _ddls()
    for view in AI_VIEWS:
        for column in (
            "agent_id",
            "labels",
            "escalated_to",
            "status",
            "channel",
            "subcategory",
            "csat_score",
            "created_at",
        ):
            if column in ddls[view]:
                assert column in names, f"{view} references unknown column {column}"
