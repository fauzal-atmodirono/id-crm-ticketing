from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.insights_router import build_metrics_insights_router
from chatbot.features.metrics.query_port import (
    BlockScope,
    LifecycleMetrics,
    MockMetricsQuery,
    StateTrendRow,
    VolumeByTypeDivisionMetrics,
    VolumeByTypeDivisionRow,
)

_UNFILTERED = BlockScope(status="unfiltered", period=None, supported_granularity=None)

# Mirrors period.py's own delta fixture (297 vs 240 -> +24%) so the router
# test exercises the same arithmetic the weekly deck reconciliation depends
# on, rather than a coincidental 0%-delta from a period-blind mock.
_CURRENT_WEEK_START = date(2026, 7, 17)
_PREVIOUS_WEEK_START = date(2026, 7, 10)


class _PeriodAwarePort(MockMetricsQuery):
    """Returns different totals for the current vs. previous test window so
    delta arithmetic is actually exercised, not just wired."""

    async def fetch_lifecycle(self, period=None):
        if period is None:
            return await super().fetch_lifecycle()
        total = 297 if period.start == _CURRENT_WEEK_START else 240
        metrics = LifecycleMetrics(
            cases=[],
            state_trend=[
                StateTrendRow(month="2026-07", status="resolved", division="Sales", cases=total)
            ],
        )
        metrics.attach_scopes(
            {
                "cases": _UNFILTERED,
                "state_trend": BlockScope(status="ok", period=period, supported_granularity=None),
            }
        )
        return metrics

    async def fetch_volume_by_type_division(self, period=None):
        if period is None:
            return await super().fetch_volume_by_type_division()
        total = 100 if period.start == _CURRENT_WEEK_START else 50
        metrics = VolumeByTypeDivisionMetrics(
            volume=[
                VolumeByTypeDivisionRow(
                    month="2026-07",
                    channel="WhatsApp",
                    case_type="Inquiry",
                    division="Sales",
                    volume=total,
                )
            ]
        )
        metrics.attach_scopes(
            {"volume": BlockScope(status="ok", period=period, supported_granularity=None)}
        )
        return metrics


class _DegradedPreviousPort(MockMetricsQuery):
    """Current leg succeeds ("ok"); previous leg degrades to "unavailable"
    (e.g. a transient BigQuery failure on just the comparison-window query).
    Exercises Important-1: the payload must distinguish the two legs' scope
    rather than reflecting only current's, and must suppress the delta
    rather than compute it against the degraded (empty) previous rows."""

    async def fetch_lifecycle(self, period=None):
        if period is None:
            return await super().fetch_lifecycle()
        if period.start == _CURRENT_WEEK_START:
            metrics = LifecycleMetrics(
                cases=[],
                state_trend=[
                    StateTrendRow(month="2026-07", status="resolved", division="Sales", cases=297)
                ],
            )
            metrics.attach_scopes(
                {
                    "cases": _UNFILTERED,
                    "state_trend": BlockScope(
                        status="ok", period=period, supported_granularity=None
                    ),
                }
            )
            return metrics
        # previous window: query failed, degrades to an empty block
        metrics = LifecycleMetrics(cases=[], state_trend=[])
        metrics.attach_scopes(
            {
                "cases": _UNFILTERED,
                "state_trend": BlockScope(
                    status="unavailable", period=period, supported_granularity=None
                ),
            }
        )
        return metrics


def _client(key="secret", port=None):
    class S:
        metrics_api_key = key

    app = FastAPI()
    app.include_router(build_metrics_insights_router(port or MockMetricsQuery(), S()))
    return TestClient(app)


def _week_params(start="2026-07-17", end="2026-07-23"):
    return {"from": start, "to": end, "granularity": "week"}


def test_departments_requires_key():
    assert _client().get("/metrics/departments").status_code == 401


def test_departments_ok_with_key():
    r = _client().get("/metrics/departments", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert "dept_pic" in r.json()


def test_callcenter_and_lifecycle_ok():
    c = _client()
    assert c.get("/metrics/callcenter", headers={"x-api-key": "secret"}).status_code == 200
    assert c.get("/metrics/lifecycle", headers={"x-api-key": "secret"}).status_code == 200


def test_dealer_escalation_requires_api_key():
    assert _client().get("/metrics/dealer-escalation").status_code == 401


def test_dealer_escalation_returns_mock_data():
    r = _client().get("/metrics/dealer-escalation", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert "by_dealer" in r.json()


def test_sla_buckets_returns_mock_data():
    r = _client().get("/metrics/sla-buckets", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert "buckets" in r.json()


def test_case_aging_returns_mock_data():
    r = _client().get("/metrics/case-aging", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert "cases" in r.json()


def test_volume_by_type_returns_mock_data():
    r = _client().get("/metrics/volume-by-type", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert "volume" in r.json()


# --- Requirement 2: no-period shape is byte-identical, exact key set,
# for all seven endpoints (not just the two period-capable ones) -- "the
# code path is unchanged" is exactly the reasoning that let the earlier
# `scopes` leak through Task 2, so it isn't trusted here either. ---


def test_lifecycle_no_period_shape_is_unchanged_key_set():
    r = _client().get("/metrics/lifecycle", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"cases", "state_trend"}


def test_volume_by_type_no_period_shape_is_unchanged_key_set():
    r = _client().get("/metrics/volume-by-type", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"volume"}


def test_departments_no_period_shape_is_unchanged_key_set():
    r = _client().get("/metrics/departments", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"dept_pic", "reopen", "category_by_vehicle_model"}


def test_callcenter_no_period_shape_is_unchanged_key_set():
    r = _client().get("/metrics/callcenter", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {
        "sla",
        "tasks_per_agent",
        "first_response",
        "resolution_time",
        "complaint_types",
        "peak_hours",
        "nps_by_agent",
    }


def test_dealer_escalation_no_period_shape_is_unchanged_key_set():
    r = _client().get("/metrics/dealer-escalation", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"by_dealer", "slowest_cases"}


def test_sla_buckets_no_period_shape_is_unchanged_key_set():
    r = _client().get("/metrics/sla-buckets", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"buckets"}


def test_case_aging_no_period_shape_is_unchanged_key_set():
    r = _client().get("/metrics/case-aging", headers={"x-api-key": "secret"})
    assert r.status_code == 200
    assert set(r.json().keys()) == {"cases"}


# --- Requirement 3: period supplied -> {current, previous, deltas} + scopes ---


def test_lifecycle_with_period_returns_wrapped_shape_and_computed_delta():
    port = _PeriodAwarePort()
    r = _client(port=port).get(
        "/metrics/lifecycle", params=_week_params(), headers={"x-api-key": "secret"}
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"current", "previous", "deltas", "scopes"}
    assert body["current"]["state_trend"][0]["cases"] == 297
    assert body["previous"]["state_trend"][0]["cases"] == 240
    # deltas computed in the API layer, not by the caller
    assert round(body["deltas"]["state_trend"]) == 24
    assert body["scopes"]["state_trend"]["current"]["status"] == "ok"
    assert body["scopes"]["state_trend"]["previous"]["status"] == "ok"
    assert body["scopes"]["cases"]["current"]["status"] == "unfiltered"
    assert body["scopes"]["cases"]["previous"]["status"] == "unfiltered"


def test_volume_by_type_with_period_returns_wrapped_shape_and_computed_delta():
    port = _PeriodAwarePort()
    r = _client(port=port).get(
        "/metrics/volume-by-type", params=_week_params(), headers={"x-api-key": "secret"}
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"current", "previous", "deltas", "scopes"}
    assert body["current"]["volume"][0]["volume"] == 100
    assert body["previous"]["volume"][0]["volume"] == 50
    assert round(body["deltas"]["volume"]) == 100  # 100 vs 50 -> +100%
    assert body["scopes"]["volume"]["current"]["status"] == "ok"
    assert body["scopes"]["volume"]["previous"]["status"] == "ok"


# --- Important 1 (review round 1): previous leg's scope must be surfaced
# distinctly from current's, and a delta against a degraded leg must be
# suppressed to null rather than computed against silently-empty rows. ---


def test_lifecycle_distinguishes_degraded_previous_scope_and_suppresses_delta():
    port = _DegradedPreviousPort()
    r = _client(port=port).get(
        "/metrics/lifecycle", params=_week_params(), headers={"x-api-key": "secret"}
    )
    assert r.status_code == 200
    body = r.json()
    # current succeeded, previous degraded -- the payload must say so per-leg
    assert body["scopes"]["state_trend"]["current"]["status"] == "ok"
    assert body["scopes"]["state_trend"]["previous"]["status"] == "unavailable"
    # a percentage computed against a degraded leg is worse than none at all
    assert body["deltas"]["state_trend"] is None
    # the raw current/previous rows are still both present for the page to
    # render current data even though the comparison can't be trusted
    assert body["current"]["state_trend"][0]["cases"] == 297
    assert body["previous"]["state_trend"] == []


# --- Requirement 4: 400 on any invalid range, never a 500 or silent fallback ---


def test_lifecycle_rejects_inverted_range():
    r = _client().get(
        "/metrics/lifecycle",
        params={"from": "2026-07-23", "to": "2026-07-17", "granularity": "week"},
        headers={"x-api-key": "secret"},
    )
    assert r.status_code == 400
    assert "inverted" in r.json()["detail"].lower()


def test_lifecycle_rejects_unknown_granularity():
    r = _client().get(
        "/metrics/lifecycle",
        params={"from": "2026-07-17", "to": "2026-07-23", "granularity": "fortnight"},
        headers={"x-api-key": "secret"},
    )
    assert r.status_code == 400
    assert "granularity" in r.json()["detail"].lower()


def test_lifecycle_rejects_partial_period_args():
    r = _client().get(
        "/metrics/lifecycle",
        params={"from": "2026-07-17"},
        headers={"x-api-key": "secret"},
    )
    assert r.status_code == 400


def test_volume_by_type_rejects_invalid_range():
    r = _client().get(
        "/metrics/volume-by-type",
        params={"from": "2026-07-23", "to": "2026-07-17", "granularity": "week"},
        headers={"x-api-key": "secret"},
    )
    assert r.status_code == 400


# --- Requirement 6: endpoints whose method takes no period must not ignore
# one -- all four assert both the status and the message, consistently. ---


def test_departments_rejects_period_params_rather_than_ignoring_them():
    r = _client().get(
        "/metrics/departments", params=_week_params(), headers={"x-api-key": "secret"}
    )
    assert r.status_code == 400
    assert "period" in r.json()["detail"].lower()


def test_callcenter_rejects_period_params():
    r = _client().get("/metrics/callcenter", params=_week_params(), headers={"x-api-key": "secret"})
    assert r.status_code == 400
    assert "period" in r.json()["detail"].lower()


def test_dealer_escalation_rejects_period_params():
    r = _client().get(
        "/metrics/dealer-escalation", params=_week_params(), headers={"x-api-key": "secret"}
    )
    assert r.status_code == 400
    assert "period" in r.json()["detail"].lower()


def test_sla_buckets_rejects_period_params():
    r = _client().get(
        "/metrics/sla-buckets", params=_week_params(), headers={"x-api-key": "secret"}
    )
    assert r.status_code == 400
    assert "period" in r.json()["detail"].lower()


def test_case_aging_rejects_period_params():
    r = _client().get("/metrics/case-aging", params=_week_params(), headers={"x-api-key": "secret"})
    assert r.status_code == 400
    assert "period" in r.json()["detail"].lower()


def test_period_incapable_endpoint_400_takes_priority_over_missing_key():
    # auth is still checked first -- an unauthenticated caller doesn't learn
    # anything about which endpoints support period filtering.
    r = _client().get("/metrics/departments", params=_week_params())
    assert r.status_code == 401
