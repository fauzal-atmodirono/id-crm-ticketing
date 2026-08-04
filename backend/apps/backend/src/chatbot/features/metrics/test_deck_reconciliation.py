"""Package E Task 5 — reconcile the arithmetic chain against the client's own
decks, offline.

The plan's real acceptance gate is a *live* comparison: pick 17-23 July 2026
in the running Weekly Report page against a real tenant's BigQuery data, and
June 2026 in the Monthly Report page, and check the screen against the two
supplied PowerPoint decks (see
``docs/superpowers/specs/2026-08-04-pkg-e-reporting-deck-parity-design.md``
§"Live reconciliation runbook" for the human steps). This checkout has no
BigQuery credentials and `MetricsQueryPort`'s mock fallback ignores period
filtering entirely, so that comparison cannot happen here, and this file
does not pretend otherwise.

What *can* be proven offline is the arithmetic chain: feed rows shaped
exactly like `v_volume_by_type_division_daily`'s output into the real
`BigQueryMetricsQuery` adapter (the actual dict -> dataclass row mapping,
the actual day-grain bucketing SQL construction) wired into the real
`build_metrics_insights_router` (the actual current/previous fan-out, the
actual `delta_pct` computation, the actual `BlockScope` plumbing) via a real
`TestClient` HTTP round trip -- then check that the deck's own published
numbers fall out the far end. If they don't, the maths is wrong. If they
do, a live mismatch against real tenant data is a data or definitional
question, not a bug in this chain.

Two honest limits, stated up front rather than glossed over:

1. The fake BigQuery client below (`_PeriodKeyedFakeClient`) returns
   pre-aggregated canned rows keyed by (view, period-start) -- it does not
   execute the SQL's `WHERE day BETWEEN @start AND @end` / `GROUP BY`
   itself. Every other adapter test in this package works the same way
   (see `test_query_adapter.py`'s module-level fake client and its own
   comment on this). So this file proves "given these post-aggregation
   rows, the adapter maps them correctly and the router's delta/scope
   arithmetic on them is correct" -- not "BigQuery's own SUM/GROUP BY
   produces these rows from raw conversations." That second claim can only
   be checked live, against `ensure_views()`'s real views.
2. The channel-mix share and the division-split total are computed in
   *this test*, not by any backend code -- the Weekly Report page (fork
   patch 0044) computes `sharePct` client-side in Vue
   (`ProtonWeeklyReport.vue`'s `channelMix` computed), and there is no
   backend rollup-by-division at all (see the division-split test below
   for why). The helpers here deliberately mirror the page's operation
   order (sum volumes per group, then divide by the total) so a result
   here is what the page would render, but they are a controlled
   reimplementation of presentation arithmetic, not an execution of the
   Vue code itself -- Python can't run it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.insights_router import build_metrics_insights_router
from chatbot.features.metrics.period import PeriodRange, previous_period
from chatbot.features.metrics.query_adapter import BigQueryMetricsQuery
from chatbot.platform.config import Settings

# --- Fake BigQuery client, period-aware -------------------------------------


class _FakeJob:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def result(self) -> list[dict[str, Any]]:
        return self._rows


class _PeriodKeyedFakeClient:
    """Like `test_query_adapter.py`'s `_FakeClient`, but keyed by (view,
    period start) as well as view name.

    The router fetches the current and previous windows as two *separate*
    queries against the *same* view (`v_volume_by_type_division_daily`) --
    see `insights_router.py`'s `asyncio.gather(port.fetch_volume_by_type_division(period),
    port.fetch_volume_by_type_division(previous_period(period)))`. A
    view-name-only fake client (fine for `test_query_adapter.py`, which only
    ever exercises one leg at a time) would hand back the same canned rows
    for both legs, making a week-over-week delta untestable. This client
    reads the `start` named parameter out of the real `QueryJobConfig` the
    adapter builds and picks the matching leg's rows.
    """

    def __init__(
        self,
        rows_by_view_and_start: dict[tuple[str, str], list[dict[str, Any]]],
    ) -> None:
        self._rows_by_view_and_start = rows_by_view_and_start
        self.queries: list[str] = []

    def query(self, sql: str, job_config: Any = None) -> _FakeJob:
        self.queries.append(sql)
        start = None
        if job_config is not None:
            for p in job_config.query_parameters:
                if p.name == "start":
                    start = p.value.isoformat()
        for (view, view_start), rows in self._rows_by_view_and_start.items():
            if view in sql and view_start == start:
                return _FakeJob(rows)
        return _FakeJob([])


def _router_client(port: BigQueryMetricsQuery, settings: Settings) -> TestClient:
    app = FastAPI()
    app.include_router(build_metrics_insights_router(port, settings))
    return TestClient(app)


def _get_volume_by_type(client: TestClient, period: PeriodRange) -> dict[str, Any]:
    r = client.get(
        "/metrics/volume-by-type",
        params={
            "from": period.start.isoformat(),
            "to": period.end.isoformat(),
            "granularity": period.granularity,
        },
        headers={"x-api-key": "secret"},
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


# --- Presentation-arithmetic helpers (mirror ProtonWeeklyReport.vue) -------


def _channel_shares(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """Mirrors `channelMix` in `ProtonWeeklyReport.vue` (0044-weekly-report.patch):
    sum volume per channel first, *then* divide by the summed total -- not a
    per-row average. See the module docstring's limit #2."""
    by_channel: dict[str, int] = {}
    for row in rows:
        by_channel[row["channel"]] = by_channel.get(row["channel"], 0) + row["volume"]
    total = sum(by_channel.values())
    return {
        channel: (volume / total if total > 0 else None) for channel, volume in by_channel.items()
    }


def _division_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        totals[row["division"]] = totals.get(row["division"], 0) + row["volume"]
    return totals


# --- Weekly deck, 17-23 July 2026 -------------------------------------------
#
# The deck's own window is Friday 17 July - Thursday 23 July -- NOT a
# Monday-Sunday span. `ProtonWeeklyReport.vue`'s week picker always snaps a
# picked date to that week's Monday (`mondayOf()`) and sets the end to
# Monday+6, so the picker can never select this exact 7-day window at all —
# the closest achievable weeks are 13-19 July and 20-26 July, both of which
# necessarily include/exclude different days than the deck. This is a real
# gap in the live-reconciliation runbook (see the spec), not something this
# offline test can paper over: `PeriodRange` and the backend impose no such
# alignment restriction (this file, like `test_period.py` and
# `test_insights_router.py`, uses the deck's literal 2026-07-17..2026-07-23
# window directly against the API), but a human clicking the picker cannot
# reproduce it.

_CURRENT_WEEK = PeriodRange(date(2026, 7, 17), date(2026, 7, 23), "week")
_PREVIOUS_WEEK = previous_period(_CURRENT_WEEK)  # 2026-07-10 .. 2026-07-16


def test_current_week_matches_the_period_module_fixture() -> None:
    """Sanity anchor: `previous_period` must land on the same 10-16 July
    window `test_period.py`'s own delta fixture uses (297 vs 240 -> +24%),
    so the rows below are keyed against the window the router will
    actually request, not a guess."""
    assert PeriodRange(date(2026, 7, 10), date(2026, 7, 16), "week") == _PREVIOUS_WEEK


def _weekly_client() -> TestClient:
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds", metrics_api_key="secret")
    client = _PeriodKeyedFakeClient(
        {
            # Current week: WhatsApp/Phone/Email/Social split chosen so each
            # channel's share, rounded to the nearest whole percent, matches
            # the weekly deck's 73/16/9/2 -- and the four volumes sum to
            # exactly 297, the deck's "total inquiries" headline.
            #   217/297 = 73.06% -> 73
            #    48/297 = 16.16% -> 16
            #    27/297 =  9.09% ->  9
            #     5/297 =  1.68% ->  2
            ("v_volume_by_type_division_daily", "2026-07-17"): [
                {
                    "month": "2026-W29",
                    "channel": "WhatsApp",
                    "case_type": "Inquiry",
                    "division": "Sales",
                    "volume": 217,
                },
                {
                    "month": "2026-W29",
                    "channel": "Phone",
                    "case_type": "Inquiry",
                    "division": "Aftersales",
                    "volume": 48,
                },
                {
                    "month": "2026-W29",
                    "channel": "Email",
                    "case_type": "Inquiry",
                    "division": "Apps",
                    "volume": 27,
                },
                {
                    "month": "2026-W29",
                    "channel": "Social",
                    "case_type": "Inquiry",
                    "division": "Others",
                    "volume": 5,
                },
            ],
            # Previous week: a single total of 240, matching test_period.py's
            # own 297-vs-240 -> +24% fixture.
            ("v_volume_by_type_division_daily", "2026-07-10"): [
                {
                    "month": "2026-W28",
                    "channel": "WhatsApp",
                    "case_type": "Inquiry",
                    "division": "Sales",
                    "volume": 240,
                }
            ],
        }
    )
    adapter = BigQueryMetricsQuery(settings, client=client)
    return _router_client(adapter, settings)


def test_weekly_total_inquiries_matches_the_deck() -> None:
    """297 inquiries for 17-23 July, through the real adapter + router."""
    body = _get_volume_by_type(_weekly_client(), _CURRENT_WEEK)
    total = sum(row["volume"] for row in body["current"]["volume"])
    assert total == 297
    assert body["scopes"]["volume"]["current"]["status"] == "ok"


def test_weekly_wow_delta_matches_the_deck() -> None:
    """+24% week-over-week, computed by the router's real `delta_pct` --
    not reimplemented in this test."""
    body = _get_volume_by_type(_weekly_client(), _CURRENT_WEEK)
    previous_total = sum(row["volume"] for row in body["previous"]["volume"])
    assert previous_total == 240
    assert round(body["deltas"]["volume"]) == 24
    assert body["scopes"]["volume"]["previous"]["status"] == "ok"


def test_weekly_channel_mix_matches_the_deck() -> None:
    """WhatsApp 73% / Phone 16% / Email 9% / Social 2%, from the router's
    real current-leg rows, summed-then-divided in the same order the page
    computes `sharePct` (module docstring, limit #2)."""
    body = _get_volume_by_type(_weekly_client(), _CURRENT_WEEK)
    shares = _channel_shares(body["current"]["volume"])
    assert round(shares["WhatsApp"] * 100) == 73
    assert round(shares["Phone"] * 100) == 16
    assert round(shares["Email"] * 100) == 9
    assert round(shares["Social"] * 100) == 2

    # NOTE (finding, not asserted): "Social" is not a channel value the
    # current sync ever produces. `mapping.py::channel_from_external_id`
    # only maps whatsapp/email/phone/sim/zendesk/chatwoot prefixes to
    # WhatsApp/Email/Phone/Web -- anything else (including a real
    # Instagram/Facebook social inbox) falls through to "Other". If
    # Proton's real data has no "Social" channel value, the live page will
    # show this slice folded into "Other" (or absent), which is a
    # definitional gap to record during the live check, not a bug in this
    # arithmetic.


# --- Weekly deck, division split --------------------------------------------
#
# Sales 49 / Aftersales 47 / Apps 39 / Charging 26 / Product 9 / Marketing 9
# / Others 85 sums to 264 -- NOT 297. This is a genuine, unresolved
# discrepancy inside the deck's own two slides (the headline total vs. the
# division breakdown), not something this test forces into agreement. It is
# recorded as-is below, using its own fixture rather than the total/
# channel-mix one above, precisely so the 264-vs-297 gap stays visible
# instead of being silently absorbed into a single "everything reconciles"
# fixture. See the spec's discrepancy log for the two working hypotheses (a
# case_type filter on the division slide; a rounding artefact) -- neither
# can be confirmed without the live tenant data.
#
# There is also no backend rollup-by-division at all: the Weekly Report page
# renders `volumeByTypeDivision`, the top 10 (channel, case_type, division)
# rows by volume, unaggregated -- not a per-division total. With more than
# 10 distinct combinations (as any real week's data will have), the page
# cannot even display enough rows for a human to hand-sum this split from
# the screen. That is a genuine UI gap, recorded in the spec, not fixed
# here (out of scope for this task: the fork's Vue page is not part of this
# backend test suite's remit).


def test_weekly_division_split_matches_the_deck() -> None:
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds", metrics_api_key="secret")
    client = _PeriodKeyedFakeClient(
        {
            ("v_volume_by_type_division_daily", "2026-07-17"): [
                {
                    "month": "2026-W29",
                    "channel": "WhatsApp",
                    "case_type": "Inquiry",
                    "division": division,
                    "volume": volume,
                }
                for division, volume in (
                    ("Sales", 49),
                    ("Aftersales", 47),
                    ("Apps", 39),
                    ("Charging", 26),
                    ("Product", 9),
                    ("Marketing", 9),
                    ("Others", 85),
                )
            ],
        }
    )
    adapter = BigQueryMetricsQuery(settings, client=client)
    body = _get_volume_by_type(_router_client(adapter, settings), _CURRENT_WEEK)

    totals = _division_totals(body["current"]["volume"])
    assert totals == {
        "Sales": 49,
        "Aftersales": 47,
        "Apps": 39,
        "Charging": 26,
        "Product": 9,
        "Marketing": 9,
        "Others": 85,
    }
    assert sum(totals.values()) == 264  # NOT 297 -- see the comment above


# --- Monthly deck, June 2026 -------------------------------------------------

_JUNE = PeriodRange(date(2026, 6, 1), date(2026, 6, 30), "month")


def test_previous_period_of_june_is_full_calendar_may() -> None:
    assert previous_period(_JUNE) == PeriodRange(date(2026, 5, 1), date(2026, 5, 31), "month")


def test_monthly_case_type_totals_match_the_deck() -> None:
    """Inquiry 1024 / Complaint 770 / Feedback 17, summing to the deck's
    total cases 1811, at month granularity through the same real adapter +
    router path as the weekly tests above (the day-grain view and the
    router's fan-out are granularity-agnostic -- see
    `query_adapter.py`'s module docstring)."""
    settings = Settings(bigquery_project_id="proj", bigquery_dataset="ds", metrics_api_key="secret")
    client = _PeriodKeyedFakeClient(
        {
            ("v_volume_by_type_division_daily", "2026-06-01"): [
                {
                    "month": "2026-06",
                    "channel": "WhatsApp",
                    "case_type": case_type,
                    "division": "Sales",
                    "volume": volume,
                }
                for case_type, volume in (
                    ("Inquiry", 1024),
                    ("Complaint", 770),
                    ("Feedback", 17),
                )
            ],
        }
    )
    adapter = BigQueryMetricsQuery(settings, client=client)
    body = _get_volume_by_type(_router_client(adapter, settings), _JUNE)

    by_case_type: dict[str, int] = {}
    for row in body["current"]["volume"]:
        by_case_type[row["case_type"]] = by_case_type.get(row["case_type"], 0) + row["volume"]

    assert by_case_type == {"Inquiry": 1024, "Complaint": 770, "Feedback": 17}
    assert sum(by_case_type.values()) == 1811
    assert body["scopes"]["volume"]["current"]["status"] == "ok"


# --- What could NOT be pinned ------------------------------------------------
#
# "Escalated to dealers 353" and "escalated to HQ 245" (both June monthly
# deck) have no test here, deliberately:
#
# - Dealers: `v_dealer_escalation` / `fetch_dealer_escalation` has no date
#   column at all (`MetricsQueryPort`'s docstring; `insights_router.py`'s
#   `_reject_period` 400s any period params sent to it -- see
#   `test_insights_router.py::test_dealer_escalation_rejects_period_params`).
#   `SUM(cases_escalated)` over that view is real code and could be pinned
#   to 353, but only as an *all-time* total -- there is no way to scope it
#   to "June 2026" without misrepresenting what the number means. Forcing a
#   test to assert 353 against an all-time query would be exactly the kind
#   of test that "tells a human the numbers reconcile when they do not."
# - HQ: there is no "escalated to HQ" concept anywhere in the schema at
#   all. `CONVERSATIONS_SCHEMA` and every view in `bigquery_schema.py` only
#   ever track `dealer_escalated_at` (escalation *to a dealer*); nothing
#   records an HQ escalation timestamp, flag, or dimension. This isn't a
#   period-filtering gap like the one above -- the data literally doesn't
#   exist yet. See the spec for this as a new, not-yet-scoped gap.
