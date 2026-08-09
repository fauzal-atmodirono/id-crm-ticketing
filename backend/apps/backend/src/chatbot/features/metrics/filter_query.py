"""Dimension filters for the metrics endpoints: agent, team, department,
channel, dealer.

Two rules carry this module.

**Values are bound as query parameters, never interpolated.** They arrive from
a query string. `predicates_for` returns the SQL fragment and the parameter
list separately precisely so there is no code path where a caller could paste
one into the other, and the tests assert the binding rather than grepping the
rendered SQL -- a string check would pass on an interpolated query that
happened to escape correctly, which is the bug it was meant to catch.

**A filter a view cannot honour is a 400 naming both.** Silently ignoring it
serves an unfiltered answer under a filtered header, which is the same class of
lie `reject_period` used to guard against for dates. `v_volume_by_tag` has no
`department` column; asking it to filter by department must fail loudly rather
than return every department's cases labelled as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query

# Which dimension each filter maps to, and which views actually carry it.
# Derived from the view definitions in bigquery_schema.py -- a filter is only
# offered where the column genuinely exists, so "supported" never means
# "accepted and ignored".
FILTER_COLUMNS: dict[str, str] = {
    "agent_id": "agent_id",
    "team": "department",  # Chatwoot teams map to the department dimension
    "department": "department",
    "channel": "channel",
    "dealer": "dealer",
}

VIEW_COLUMNS: dict[str, frozenset[str]] = {
    "v_dept_pic_performance": frozenset({"department", "pic"}),
    "v_dealer_escalation": frozenset({"dealer"}),
    "v_resolution_sla_buckets": frozenset({"case_type"}),
    "v_case_aging": frozenset({"case_type", "division", "dealer", "pic", "status"}),
    "v_sla_achievement": frozenset({"channel", "division"}),
    "v_tasks_per_agent": frozenset({"agent_id", "pic"}),
    "v_first_response_by_channel": frozenset({"channel"}),
    "v_resolution_time": frozenset({"channel", "division"}),
    "v_nps_by_agent": frozenset({"agent_id", "channel"}),
    "v_first_response_by_dealer": frozenset({"dealer"}),
    "v_volume_by_tag": frozenset({"tag", "channel"}),
    "v_reopen_rate": frozenset({"dealer", "department", "pic"}),
}


class UnsupportedFilter(HTTPException):
    """A filter the requested view has no column for."""

    def __init__(self, filter_name: str, view: str) -> None:
        super().__init__(
            status_code=400,
            detail=(
                f"Filter '{filter_name}' is not available on {view}: that view "
                f"has no such dimension. Available here: "
                f"{', '.join(sorted(VIEW_COLUMNS.get(view, frozenset()))) or 'none'}."
            ),
        )


@dataclass(frozen=True)
class MetricFilters:
    agent_id: str | None = None
    team: str | None = None
    department: str | None = None
    channel: str | None = None
    dealer: str | None = None

    @property
    def active(self) -> dict[str, str]:
        """Only the filters actually supplied. Blank is not a filter -- an
        empty query param means the user cleared the box, not that they want
        rows whose department is the empty string."""
        return {
            name: value.strip()
            for name, value in (
                ("agent_id", self.agent_id),
                ("team", self.team),
                ("department", self.department),
                ("channel", self.channel),
                ("dealer", self.dealer),
            )
            if value is not None and value.strip()
        }

    def predicates_for(self, view: str) -> tuple[str, dict[str, str]]:
        """`(sql_fragment, params)` for this view, or raise `UnsupportedFilter`.

        The fragment contains only placeholders. The values come back
        separately, so binding them is the caller's only option.
        """
        active = self.active
        if not active:
            return "", {}

        known = VIEW_COLUMNS.get(view)
        clauses: list[str] = []
        params: dict[str, str] = {}
        for name, value in active.items():
            column = FILTER_COLUMNS[name]
            if known is not None and column not in known:
                raise UnsupportedFilter(name, view)
            clauses.append(f"{column} = @filter_{name}")
            params[f"filter_{name}"] = value
        # AND: filters narrow, they never widen. Two filters mean "both".
        return " AND ".join(clauses), params


def metric_filters(
    agent_id: Annotated[str | None, Query()] = None,
    team: Annotated[str | None, Query()] = None,
    department: Annotated[str | None, Query()] = None,
    channel: Annotated[str | None, Query()] = None,
    dealer: Annotated[str | None, Query()] = None,
) -> MetricFilters:
    """FastAPI dependency, mirroring the `PeriodQuery` shape already used here."""
    return MetricFilters(
        agent_id=agent_id, team=team, department=department,
        channel=channel, dealer=dealer,
    )


MetricFiltersQuery = Annotated[MetricFilters, Depends(metric_filters)]
