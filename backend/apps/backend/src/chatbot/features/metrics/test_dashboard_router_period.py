"""Period wiring for GET /metrics/dashboard (Package E final fix, finding I1).

Deliberately a separate file from `test_dashboard_router.py`: that one
builds its app via `create_app(Settings())` / `bootstrap_application()`,
which needs a live `GEMINI_API_KEY` and is therefore one of this suite's
two pre-existing collection errors. A test that never runs is not a guard
-- and "never runs" is exactly how this endpoint kept its period params
undeclared while every port-level test made the feature look alive. These
build a bare `FastAPI()` around the router only, same shape as
`test_insights_router.py`, so they actually execute.

P9 task 7 note: `build_metrics_query_router` now takes `Settings`, so it can
stamp the freshness contract. Every client here is built with a stub whose
`dashboard_freshness_enabled` is explicitly False -- **not** a bare
`Settings()`, which reads `os.environ` and would pick up
`DASHBOARD_FRESHNESS_ENABLED=true` from the both-flag-states gate and add
`as_of`/`source`/`freshness` keys to the payloads whose exact key sets this file
pins. The freshness stamp has its own coverage in `test_freshness_contract.py`.
"""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.period import PeriodRange
from chatbot.features.metrics.query_port import (
    BlockScope,
    BounceRow,
    CsatRow,
    DashboardMetrics,
    FallbackRow,
    MetricsQueryPort,
    MockMetricsQuery,
    NpsRow,
    QualityRow,
    ResolutionRow,
    SpeedRow,
    VolumeRow,
)

_UNFILTERED = BlockScope(status="unfiltered", period=None, supported_granularity=None)

_CURRENT_WEEK_START = date(2026, 7, 17)

_DASHBOARD_BLOCKS = {
    "volume",
    "resolution",
    "csat",
    "nps",
    "speed",
    "fallback",
    "bounce",
    "quality",
}


class _PeriodAwarePort(MockMetricsQuery):
    """Different volume totals for the current vs. previous window, so the
    delta arithmetic is exercised rather than merely wired -- the canned
    mock is period-blind and would yield a coincidental 0%."""

    async def fetch_dashboard(self, period: PeriodRange | None = None) -> DashboardMetrics:
        if period is None:
            return await super().fetch_dashboard()
        total = 297 if period.start == _CURRENT_WEEK_START else 240
        metrics = DashboardMetrics(
            volume=[VolumeRow(month="2026-W29", channel="web", volume=total, bucket="2026-W29")],
            resolution=[ResolutionRow("web", 90, 30, 120, 0.75, 0.25)],
            csat=[CsatRow("web", 40, 4.3, 0.85)],
            nps=[NpsRow("web", 35, 20, 10, 5, 42.86)],
            speed=[SpeedRow("web", True, 1800, 950.0, 130)],
            fallback=[FallbackRow("web", 0.08, 540)],
            bounce=[BounceRow("web", 18, 120, 0.15)],
            quality=[QualityRow("web", 20, 88.5, 91.0)],
        )
        scopes = dict.fromkeys(_DASHBOARD_BLOCKS - {"volume"}, _UNFILTERED)
        scopes["volume"] = BlockScope(status="ok", period=period, supported_granularity=None)
        metrics.attach_scopes(scopes)
        return metrics


class _NoFreshnessSettings:
    """The minimum `build_metrics_query_router` reads, with the stamp off.

    A stub rather than `Settings(dashboard_freshness_enabled=False)` because a
    real `Settings` still reads the ambient environment for every other field,
    and because the router only ever asks these two questions of it.
    """

    dashboard_freshness_enabled = False
    metrics_sync_interval_hours = 6


def _client(port: MetricsQueryPort | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_metrics_query_router(port or MockMetricsQuery(), _NoFreshnessSettings())  # type: ignore[arg-type]
    )
    return TestClient(app)


def _week_params(start: str = "2026-07-17", end: str = "2026-07-23") -> dict[str, str]:
    return {"from": start, "to": end, "granularity": "week"}


# --- The no-period path must stay byte-identical: this is the constraint
# the whole package holds to, because the deployed SPA reads this payload. ---


def test_no_period_shape_is_unchanged_exact_key_set() -> None:
    r = _client().get("/metrics/dashboard")
    assert r.status_code == 200
    assert set(r.json().keys()) == _DASHBOARD_BLOCKS


def test_no_period_payload_has_no_wrapper_or_scopes_key() -> None:
    body = _client().get("/metrics/dashboard").json()
    for leaked in ("scopes", "current", "previous", "deltas"):
        assert leaked not in body


def test_no_period_still_requires_no_auth_header() -> None:
    assert _client().get("/metrics/dashboard").status_code == 200


# --- The bug: period params used to be silently dropped, returning 200
# with all-time data under a caller-supplied week header. ---


def test_period_params_are_honoured_not_silently_ignored() -> None:
    r = _client(port=_PeriodAwarePort()).get("/metrics/dashboard", params=_week_params())
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"current", "previous", "deltas", "scopes"}
    assert body["current"]["volume"][0]["volume"] == 297
    assert body["previous"]["volume"][0]["volume"] == 240
    # the period actually reached the port, rather than the request
    # falling through to the unfiltered payload
    assert body["scopes"]["volume"]["current"]["status"] == "ok"
    assert body["scopes"]["volume"]["current"]["period"]["start"] == "2026-07-17"
    assert body["scopes"]["volume"]["current"]["period"]["end"] == "2026-07-23"


def test_period_delta_is_computed_in_the_api_layer() -> None:
    body = _client(port=_PeriodAwarePort()).get("/metrics/dashboard", params=_week_params()).json()
    assert round(body["deltas"]["volume"]) == 24  # 297 vs 240


def test_period_marks_the_seven_dateless_blocks_unfiltered() -> None:
    """Only `volume` has a date dimension. The other seven must say so, or
    a client rendering all eight under one "17-23 July" header presents
    all-time CSAT/NPS/bot-resolution figures as if they were that week's."""
    body = _client(port=_PeriodAwarePort()).get("/metrics/dashboard", params=_week_params()).json()
    assert set(body["scopes"].keys()) == _DASHBOARD_BLOCKS
    for block in _DASHBOARD_BLOCKS - {"volume"}:
        assert body["scopes"][block]["current"]["status"] == "unfiltered"
        assert body["scopes"][block]["previous"]["status"] == "unfiltered"


def test_period_against_the_period_blind_mock_reports_unfiltered_and_null_delta() -> None:
    """`MockMetricsQuery` ignores `period` entirely. Wiring the params up
    must not turn its canned all-time rows into a number labelled as that
    week's: every block reports "unfiltered" and the delta is suppressed."""
    body = _client().get("/metrics/dashboard", params=_week_params()).json()
    assert body["scopes"]["volume"]["current"]["status"] == "unfiltered"
    assert body["deltas"]["volume"] is None


# --- Invalid ranges are a 400 naming the problem, never a 500 and never a
# silent fallback to unfiltered data (same contract as insights_router). ---


def test_rejects_inverted_range() -> None:
    r = _client().get(
        "/metrics/dashboard",
        params={"from": "2026-07-23", "to": "2026-07-17", "granularity": "week"},
    )
    assert r.status_code == 400
    assert "inverted" in r.json()["detail"].lower()


def test_rejects_unknown_granularity() -> None:
    r = _client().get(
        "/metrics/dashboard",
        params={"from": "2026-07-17", "to": "2026-07-23", "granularity": "fortnight"},
    )
    assert r.status_code == 400
    assert "granularity" in r.json()["detail"].lower()


def test_rejects_partial_period_args() -> None:
    assert _client().get("/metrics/dashboard", params={"from": "2026-07-17"}).status_code == 400
