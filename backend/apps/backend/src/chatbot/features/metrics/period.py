"""Period arithmetic. Every 'up 24%' figure in the weekly deck comes from here.

Pure functions, no I/O: parsing a reporting period from query-string args,
computing the comparison window for a "vs previous period" delta, turning
that delta into a percentage safe to render, and bucketing individual dates
into the week/month keys the deck groups by.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

_VALID_GRANULARITIES = {"day", "week", "month"}


@dataclass(frozen=True)
class PeriodRange:
    start: date
    end: date
    granularity: str


def parse_period(from_: str | None, to: str | None, granularity: str | None) -> PeriodRange | None:
    """Parse explicit period args into a PeriodRange.

    Returns None when `from_`, `to`, and `granularity` are all absent — that
    means "today's behaviour, no period filter" and callers must treat it as
    such rather than as an error. A *partial* set of args (e.g. `from_` given
    but `to` or `granularity` absent) is not a supported "no filter" signal
    and raises ValueError, same as an inverted range or unknown granularity,
    so callers can convert every rejection to one error response uniformly.
    """
    if from_ is None and to is None and granularity is None:
        return None
    if from_ is None or to is None or granularity is None:
        raise ValueError("from_, to, and granularity must all be given, or all omitted")

    start = date.fromisoformat(from_)
    end = date.fromisoformat(to)
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError(f"unknown granularity: {granularity!r}")
    if start > end:
        raise ValueError(f"inverted range: {start} is after {end}")

    return PeriodRange(start, end, granularity)


def _is_full_calendar_month(period: PeriodRange) -> bool:
    last_day = monthrange(period.start.year, period.start.month)[1]
    return (
        period.start.day == 1
        and period.end.day == last_day
        and period.start.month == period.end.month
        and period.start.year == period.end.year
    )


def previous_period(period: PeriodRange) -> PeriodRange:
    """The comparison window a reader expects for "vs previous period".

    A range that is exactly one full calendar month compares against the
    prior full calendar month (28-31 days, whatever that month has) — "vs
    last month" means May, not "the 30 days before June". Any other range
    compares against the immediately preceding window of equal length.
    """
    if _is_full_calendar_month(period):
        prev_end = period.start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        return PeriodRange(prev_start, prev_end, period.granularity)

    length = (period.end - period.start).days + 1
    prev_end = period.start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return PeriodRange(prev_start, prev_end, period.granularity)


def delta_pct(current: float, previous: float) -> float | None:
    """Percentage change from `previous` to `current`.

    None when `previous` is zero and `current` is not (an infinite/undefined
    percentage); 0.0 when both are zero (no change, not undefined).
    """
    if previous == 0:
        return 0.0 if current == 0 else None
    return (current - previous) / previous * 100


def bucket_key(d: date, granularity: str) -> str:
    """Group key for `d` at the given granularity.

    Week buckets use ISO calendar weeks (`date.isocalendar()`), not
    `strftime("%Y-%W")`: the naive form splits a week that spans a month
    boundary into two different buckets.
    """
    if granularity == "day":
        return d.isoformat()
    if granularity == "week":
        iso_year, iso_week, _ = d.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if granularity == "month":
        return f"{d.year:04d}-{d.month:02d}"
    raise ValueError(f"unknown granularity: {granularity!r}")
