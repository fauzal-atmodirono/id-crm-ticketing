"""P8 task 6 -- per-agent CSAT, and the two tests that ARE the design.

`test_an_agent_below_the_minimum_sample_size_is_excluded_from_rankings` and
`test_that_agent_still_appears_in_the_unranked_listing_with_their_count` are a
pair, and neither is safe alone. Ranking a 1-response 100% against a
200-response 94% is not a comparison; publishing it as one is how a
measurement becomes a grievance. But dropping the low-sample agent makes the
list look complete when it is not, and the reader cannot then tell "too few
ratings to rank" from "no cases at all". So the ranking is suppressed and the
row is not.

No BigQuery here (controller decision D2), so the SQL is asserted
structurally, exactly as P4 and P5 did.
"""

from __future__ import annotations

import inspect

from chatbot.features.metrics import sync
from chatbot.features.metrics.bigquery_schema import view_ddls

PROJECT, DATASET = "proj", "ds"


def _ddls(**kwargs: object) -> dict[str, str]:
    return view_ddls(PROJECT, DATASET, csat_by_agent_enabled=True, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The six tests named in the task brief
# ---------------------------------------------------------------------------


def test_v_csat_is_completely_unchanged() -> None:
    """Existing dashboards read `v_csat`. Per-agent CSAT is a SIBLING view.

    Pinned as a literal, not as "equal to itself with the flag off": the point
    is that the string is the one that shipped, byte for byte, so widening it
    with an `agent_id` dimension (which would multiply its rows and change
    every tile built on it) fails here.
    """
    expected = (
        "CREATE OR REPLACE VIEW `proj.ds.v_csat` AS "
        "SELECT channel, "
        "COUNTIF(csat_score IS NOT NULL) AS respondents, "
        "AVG(csat_score) AS avg_score, "
        "SAFE_DIVIDE(COUNTIF(csat_score >= 4), COUNTIF(csat_score IS NOT NULL)) "
        "AS satisfied_rate "
        "FROM `proj.ds.conversations` GROUP BY channel"
    )
    assert view_ddls(PROJECT, DATASET)["v_csat"] == expected
    # and turning the new view on does not touch it
    assert _ddls()["v_csat"] == expected


def test_v_csat_by_agent_groups_by_agent_id() -> None:
    sql = _ddls()["v_csat_by_agent"]
    assert "`proj.ds.v_csat_by_agent`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "COALESCE(agent_id, 'Unassigned') AS agent_id" in sql
    assert "GROUP BY day, agent_id, channel" in sql


def test_every_row_returns_the_rating_count_alongside_the_average() -> None:
    """A rate with no denominator is a number, not a measurement."""
    sql = _ddls()["v_csat_by_agent"]
    assert "COUNTIF(csat_score IS NOT NULL) AS respondents" in sql
    assert "AVG(csat_score) AS avg_score" in sql
    assert "AS satisfied_rate" in sql
    # respondents is the denominator of BOTH rates, and `cases` is the
    # denominator of the response rate itself.
    assert "COUNT(*) AS cases" in sql
    assert "SAFE_DIVIDE(COUNTIF(csat_score >= 4), COUNTIF(csat_score IS NOT NULL))" in sql
    # every selected column survives the outer SELECT -- a CTE that computed
    # respondents and then dropped it would pass every assertion above
    outer = sql.split("FROM per_agent")[0].rsplit(") SELECT ", 1)[1]
    for column in ("cases", "respondents", "avg_score", "satisfied", "satisfied_rate"):
        assert column in outer, f"{column} computed but not projected"


def test_an_agent_below_the_minimum_sample_size_is_excluded_from_rankings() -> None:
    sql = _ddls(csat_ranking_min_samples=10)["v_csat_by_agent"]
    assert "CASE WHEN respondents >= 10 THEN RANK() OVER (" in sql
    assert "AS rank_in_channel" in sql
    # ...and the floor is the CONFIGURED one, not a hardcoded 10
    other = _ddls(csat_ranking_min_samples=25)["v_csat_by_agent"]
    assert "CASE WHEN respondents >= 25 THEN RANK() OVER (" in other
    assert "25 AS ranking_min_samples" in other
    assert ">= 10 THEN RANK()" not in other


def test_that_agent_still_appears_in_the_unranked_listing_with_their_count() -> None:
    """Suppress the ranking, not the existence."""
    sql = _ddls()["v_csat_by_agent"]
    # No WHERE anywhere: nothing filters a low-sample agent out of the view.
    assert "WHERE" not in sql, "a WHERE clause here would hide the low-sample agents"
    # The row instead carries why it is not ranked, plus its real count.
    assert "respondents >= 10 AS is_rankable" in sql
    assert "10 AS ranking_min_samples" in sql
    # The suppressed rows must not consume rank positions either, or the
    # visible ranking reads 1, 3, 7.
    assert "PARTITION BY day, channel, respondents >= 10" in sql


def test_an_agent_with_no_ratings_appears_with_a_null_score_not_a_zero() -> None:
    """A zero score is a terrible rating. No ratings is not a rating."""
    sql = _ddls()["v_csat_by_agent"]
    assert "AVG(csat_score) AS avg_score" in sql
    assert "COALESCE(AVG(csat_score)" not in sql
    assert "IFNULL(AVG(csat_score)" not in sql
    assert "COALESCE(avg_score" not in sql
    # SAFE_DIVIDE (not `/`) so a zero-respondent row yields NULL rather than
    # an error or a fabricated 0.
    assert "SAFE_DIVIDE(" in sql
    # and the row exists even with nothing to average: the grouping is over
    # every conversation, not only the ones carrying a csat_score.
    assert "FROM `proj.ds.conversations` GROUP BY day, agent_id, channel" in sql


# ---------------------------------------------------------------------------
# Flag behaviour: off is "not created", not "created and empty"
# ---------------------------------------------------------------------------


def test_the_view_does_not_exist_unless_the_flag_is_on() -> None:
    """`ensure_views` runs this against a live warehouse, so flags-off has to
    mean the view is not created at all -- otherwise enabling the flag is not
    the thing that changes the tenant's warehouse."""
    assert "v_csat_by_agent" not in view_ddls(PROJECT, DATASET)
    assert "v_csat_by_agent" in _ddls()


def test_the_flag_off_key_set_is_the_pre_p8_key_set() -> None:
    off = set(view_ddls(PROJECT, DATASET))
    on = set(_ddls())
    assert on - off == {"v_csat_by_agent"}
    assert off - on == set()


def test_the_flag_reaches_the_view_builder_from_settings() -> None:
    """A flag `ensure_views` never forwards is a flag with no effect -- and
    the DDL-builder unit test would pass either way. This asserts the call
    site forwards both."""
    source = inspect.getsource(sync.ensure_views)
    assert "csat_by_agent_enabled=settings.csat_by_agent_enabled" in source
    assert "csat_ranking_min_samples=settings.csat_ranking_min_samples" in source


def test_the_view_honours_the_reporting_timezone() -> None:
    assert "DATE(created_at) AS day" in _ddls()["v_csat_by_agent"]
    zoned = view_ddls(
        PROJECT,
        DATASET,
        reporting_timezone="Asia/Kuala_Lumpur",
        csat_by_agent_enabled=True,
    )
    assert "DATE(created_at, 'Asia/Kuala_Lumpur') AS day" in zoned["v_csat_by_agent"]
    assert "DATE(created_at)" not in zoned["v_csat_by_agent"]
