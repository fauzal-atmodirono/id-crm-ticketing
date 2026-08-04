"""Period arithmetic. Every 'up 24%' figure in the weekly deck comes from here."""

from __future__ import annotations

from datetime import date

import pytest

from chatbot.features.metrics.period import (
    PeriodRange,
    bucket_key,
    delta_pct,
    parse_period,
    previous_period,
)


def test_absent_arguments_mean_no_period_filter():
    assert parse_period(None, None, None) is None


def test_parses_an_explicit_week():
    p = parse_period("2026-07-17", "2026-07-23", "week")
    assert p == PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")


def test_rejects_an_inverted_range():
    with pytest.raises(ValueError):
        parse_period("2026-07-23", "2026-07-17", "week")


def test_rejects_an_unknown_granularity():
    with pytest.raises(ValueError):
        parse_period("2026-07-17", "2026-07-23", "fortnight")


def test_previous_period_is_the_immediately_preceding_window_of_equal_length():
    p = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
    assert previous_period(p) == PeriodRange(date(2026, 7, 10), date(2026, 7, 16), "week")


def test_previous_period_of_a_full_month_is_the_prior_month():
    p = PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "month")
    assert previous_period(p) == PeriodRange(date(2026, 5, 1), date(2026, 5, 31), "month")


def test_delta_matches_the_weekly_deck():
    # 297 inquiries vs 240 the prior week is the deck's "up 24%".
    assert round(delta_pct(297, 240)) == 24


def test_delta_from_zero_is_undefined_not_infinite():
    assert delta_pct(10, 0) is None


def test_delta_of_zero_to_zero_is_zero_not_undefined():
    assert delta_pct(0, 0) == 0.0


def test_week_buckets_do_not_split_across_a_month_boundary():
    # 2026-07-30 and 2026-08-01 fall in the same ISO week.
    assert bucket_key(date(2026, 7, 30), "week") == bucket_key(date(2026, 8, 1), "week")


def test_month_buckets_are_year_month():
    assert bucket_key(date(2026, 6, 15), "month") == "2026-06"


def test_previous_period_of_a_31_day_month_is_the_prior_30_day_month():
    p = PeriodRange(date(2026, 7, 1), date(2026, 7, 31), "month")
    assert previous_period(p) == PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "month")


def test_previous_period_of_january_is_december_of_the_prior_year():
    p = PeriodRange(date(2027, 1, 1), date(2027, 1, 31), "month")
    assert previous_period(p) == PeriodRange(date(2026, 12, 1), date(2026, 12, 31), "month")


def test_previous_period_of_a_single_day_is_the_day_before():
    p = PeriodRange(date(2026, 7, 20), date(2026, 7, 20), "day")
    assert previous_period(p) == PeriodRange(date(2026, 7, 19), date(2026, 7, 19), "day")


def test_previous_period_ignores_full_month_shape_when_granularity_is_not_month():
    # A "day"-granularity range that happens to span a whole month (e.g. from
    # a custom date-range picker) must NOT take the calendar-month branch —
    # it should compare against the immediately preceding 30 days, not May.
    p = PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "day")
    assert previous_period(p) == PeriodRange(date(2026, 5, 2), date(2026, 5, 31), "day")


def test_previous_period_of_february_in_a_leap_year():
    p = PeriodRange(date(2028, 3, 1), date(2028, 3, 31), "month")
    assert previous_period(p) == PeriodRange(date(2028, 2, 1), date(2028, 2, 29), "month")


def test_previous_period_of_february_in_a_non_leap_year():
    p = PeriodRange(date(2027, 3, 1), date(2027, 3, 31), "month")
    assert previous_period(p) == PeriodRange(date(2027, 2, 1), date(2027, 2, 28), "month")


def test_week_53_spans_the_year_boundary_in_one_bucket():
    dec_end = [date(2026, 12, 28), date(2026, 12, 29), date(2026, 12, 30), date(2026, 12, 31)]
    jan_start = [date(2027, 1, 1), date(2027, 1, 2), date(2027, 1, 3)]
    keys = {bucket_key(d, "week") for d in dec_end + jan_start}
    assert keys == {"2026-W53"}
    assert bucket_key(date(2027, 1, 4), "week") == "2027-W01"


def test_parse_period_rejects_a_partial_argument_set():
    # This ValueError is load-bearing for the /reports endpoint's uniform
    # 400 handling: a query string with `from` but no `to`/`granularity`
    # must reject the same way an inverted range or bad granularity does,
    # not raise a bare TypeError from date.fromisoformat(None).
    with pytest.raises(ValueError):
        parse_period("2026-07-17", None, None)
    with pytest.raises(ValueError):
        parse_period(None, "2026-07-23", None)
    with pytest.raises(ValueError):
        parse_period(None, None, "week")
    with pytest.raises(ValueError):
        parse_period("2026-07-17", "2026-07-23", None)
