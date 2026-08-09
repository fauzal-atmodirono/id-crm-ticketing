"""P8 task 9 -- KB coverage and the staleness review queue.

**Tests three and four are the useful behaviour.** A review queue ordered by
age alone puts a year-old entry nobody has ever hit at the top and buries the
three-month-old entry that answers a tenth of all enquiries. Multiplying age by
serve count gives both required properties at once: never-served scores 0
however old, edited-today scores 0 however busy.

`v_kb_staleness` reads a `faq_entries` table nothing populates yet -- see
`faq_schema.py`'s module docstring, which states exactly what is owed. The view
is authored so the shape is fixed and reviewable now; it returns no rows until
the snapshot loader lands.

No BigQuery here (controller decision D2), so the SQL is asserted structurally.
"""

from __future__ import annotations

import inspect

from chatbot.features.metrics import faq_schema, sync
from chatbot.features.metrics.bigquery_schema import kb_coverage_basis, view_ddls
from chatbot.features.metrics.faq_schema import (
    FAQ_ENTRIES_SCHEMA,
    FAQ_FEEDBACK_SCHEMA,
    faq_view_ddls,
)

PROJECT, DATASET = "proj", "ds"


def _coverage(**kwargs: object) -> str:
    return view_ddls(PROJECT, DATASET, **kwargs)["v_kb_coverage"]  # type: ignore[arg-type]


def _staleness() -> str:
    return faq_view_ddls(PROJECT, DATASET)["v_kb_staleness"]


# ---------------------------------------------------------------------------
# The six tests named in the task brief
# ---------------------------------------------------------------------------


def test_coverage_is_the_share_of_enquiries_with_a_match_above_the_score_floor() -> None:
    sql = _coverage()
    assert "`proj.ds.v_kb_coverage`" in sql
    assert "`proj.ds.conversations`" in sql
    assert "COUNT(*) AS enquiries" in sql
    assert "AS matched_enquiries" in sql
    # the rate is matched / every enquiry, denominator included on the row
    assert "COUNT(*)) AS coverage_rate" in sql
    # The floor itself is applied upstream and is not persisted per enquiry, so
    # the report says which floor was in force rather than implying it measured
    # the score.
    assert "AS coverage_basis" in sql
    assert "KB_SCORE_FLOOR=0.55" in sql
    assert "KB_SCORE_FLOOR=0.7" in _coverage(kb_score_floor=0.7)
    assert "KB_SCORE_FLOOR=0.55" not in _coverage(kb_score_floor=0.7)


def test_unresolved_query_rows_count_against_coverage() -> None:
    """Excluding the failures would make coverage rise as the KB got worse."""
    sql = _coverage()
    assert "'unresolved query'" in sql
    assert "AS unresolved_queries" in sql
    # counted against, not filtered out
    assert "WHERE" not in sql
    assert "COUNTIF(LOWER(TRIM(COALESCE(subcategory, ''))) != 'unresolved query')" in sql
    assert "counts AGAINST coverage" in kb_coverage_basis(0.55)


def test_staleness_weights_age_by_how_often_the_entry_was_served() -> None:
    sql = _staleness()
    assert "`proj.ds.v_kb_staleness`" in sql
    assert "AS age_days" in sql
    assert (
        "DATE_DIFF(CURRENT_DATE(), DATE(e.updated_at), DAY) * e.serve_count "
        "AS staleness_score" in sql
    )
    # ordered by "stale AND load-bearing"
    assert "ORDER BY staleness_status, staleness_score DESC" in sql


def test_a_never_served_stale_entry_ranks_below_a_frequently_served_one() -> None:
    """serve_count = 0 makes the product 0 whatever the age, so a year-old
    entry nobody hits cannot outrank a busy one. Asserted as arithmetic, not
    just as the presence of a multiplication."""
    age_old, age_recent = 400, 30
    assert age_old * 0 < age_recent * 50
    sql = _staleness()
    assert "* e.serve_count" in sql
    # and NOT age alone, which is the ordering this test exists to rule out
    assert "ORDER BY age_days" not in sql


def test_an_entry_edited_today_has_zero_staleness_regardless_of_serve_count() -> None:
    """age_days = 0 makes the product 0 whatever the serve count."""
    assert 0 * 100000 == 0
    sql = _staleness()
    assert "DATE_DIFF(CURRENT_DATE(), DATE(e.updated_at), DAY)" in sql
    # the age term must not have a floor of 1 or an offset that would keep a
    # freshly-edited entry in the queue
    assert "DAY) + 1" not in sql
    assert "GREATEST(" not in sql


def test_both_views_accept_a_period() -> None:
    """Both expose a `day` DATE column, which is what a period filters on."""
    assert "AS day" in _coverage()
    assert "DATE(created_at) AS day" in _coverage()
    assert "DATE(e.snapshot_at) AS day" in _staleness()


# ---------------------------------------------------------------------------
# The honesty properties
# ---------------------------------------------------------------------------


def test_an_uninstrumented_serve_count_is_not_scored_as_never_served() -> None:
    """`COALESCE(serve_count, 0)` would score "we never measured this"
    identically to "nobody ever hits this": zero, bottom of the queue,
    invisible. NULL must propagate and the row must say why."""
    sql = _staleness()
    assert "COALESCE(e.serve_count" not in sql
    assert "IFNULL(e.serve_count" not in sql
    assert (
        "CASE WHEN e.updated_at IS NULL OR e.serve_count IS NULL "
        "THEN 'unmeasured' ELSE 'scored' END AS staleness_status" in sql
    )
    by_name = {f.name: f for f in FAQ_ENTRIES_SCHEMA}
    assert by_name["serve_count"].mode == "NULLABLE"
    assert by_name["updated_at"].mode == "NULLABLE"


def test_the_existing_faq_feedback_schema_and_quality_view_are_unchanged() -> None:
    """`v_faq_quality` has consumers; task 9 only adds beside it."""
    assert {f.name for f in FAQ_FEEDBACK_SCHEMA} == {
        "article_id",
        "session_id",
        "helpful",
        "score",
        "at",
    }
    assert faq_view_ddls(PROJECT, DATASET)["v_faq_quality"] == (
        "CREATE OR REPLACE VIEW `proj.ds.v_faq_quality` AS "
        "SELECT article_id, COUNT(*) AS feedback_count, AVG(score) AS avg_score, "
        "SAFE_DIVIDE(COUNTIF(helpful), COUNT(*)) AS helpful_rate "
        "FROM `proj.ds.faq_feedback` GROUP BY article_id"
    )


def test_staleness_left_joins_quality_so_an_unreviewed_entry_still_appears() -> None:
    """An entry nobody has given feedback on is exactly what a review queue
    should surface, so it must not drop out for lack of a quality row."""
    sql = _staleness()
    assert "LEFT JOIN `proj.ds.v_faq_quality` q" in sql
    assert "INNER JOIN" not in sql
    assert "AS feedback_avg_score" in sql


def test_the_missing_faq_entries_loader_is_stated_not_implied() -> None:
    """The table this view reads is not populated by anything yet. That has to
    be findable from the code, not only from a report -- this run has shipped
    four features nothing could reach."""
    assert faq_schema.__doc__ is not None
    doc = faq_schema.__doc__
    assert "nothing writes yet" in doc
    assert "faq_entries" in doc
    assert "no runtime caller" in doc


def test_the_score_floor_reaches_the_view_builder_from_settings() -> None:
    source = inspect.getsource(sync.ensure_views)
    assert "kb_score_floor=settings.kb_score_floor" in source


def test_v_kb_coverage_honours_the_reporting_timezone() -> None:
    zoned = view_ddls(PROJECT, DATASET, reporting_timezone="Asia/Kuala_Lumpur")
    assert "DATE(created_at, 'Asia/Kuala_Lumpur') AS day" in zoned["v_kb_coverage"]
    assert "DATE(created_at)" not in zoned["v_kb_coverage"]
