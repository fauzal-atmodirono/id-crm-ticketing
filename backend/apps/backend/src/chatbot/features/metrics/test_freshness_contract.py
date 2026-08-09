"""P9 task 5 -- the freshness contract.

`test_as_of_reflects_the_last_sync_not_the_request_time` is the whole point.
`as_of = now()` would make a six-hour-old figure look current, which is exactly
the misrepresentation this task exists to remove -- and it is the implementation
anyone would reach for first, because it always produces a plausible timestamp.

`test_a_stale_sync_is_visible_as_an_old_as_of_rather_than_hidden` is its pair:
the answer to a stale sync is to SHOW the old timestamp, not to hide, refresh or
round it.

The third thing under test here is per-surface truth. A shared helper that
stamped one value across the product would label the live alert stream as batch
or the batch dashboard as live, and either is worse than no stamp at all.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics import scheduler
from chatbot.features.metrics.anomaly_router import build_metrics_anomaly_router
from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.freshness import (
    AS_OF_CONTINUOUS,
    AS_OF_MEASURED,
    AS_OF_UNKNOWN,
    SOURCE_BATCH,
    SOURCE_LIVE_STREAM,
    SOURCE_POLL_60S,
    SURFACE_ALERT_STREAM,
    SURFACE_ANOMALY_HOURLY,
    SURFACE_DASHBOARD,
    SURFACE_MY_TASKS,
    SURFACE_REPORTS,
    batch_freshness,
    last_sync_completed_at,
    live_stream_freshness,
    poll_freshness,
    record_sync_completed,
    reset_sync_clock,
    stamp_freshness,
    surface_freshness,
)
from chatbot.features.metrics.insights_router import build_metrics_insights_router
from chatbot.features.metrics.query_port import MockMetricsQuery
from chatbot.platform.config import Settings

API_KEY = "test-metrics-key"


# Every Settings here states the fields it depends on. `Settings(...)` still
# reads os.environ -- `_env_file=None` does not stop it -- and the flags-ON gate
# sets DASHBOARD_FRESHNESS_ENABLED=true, so a test that built a bare `Settings()`
# and asserted the stamp was absent would be asserting the ambient environment.
def _settings(*, freshness: bool, **extra: object) -> Settings:
    fields: dict[str, object] = {
        "dashboard_freshness_enabled": freshness,
        "metrics_sync_interval_hours": 6,
        "metrics_api_key": API_KEY,
        "anomaly_hourly_enabled": True,
        "anomaly_hourly_zscore_k": 3.5,
        "anomaly_hourly_min_baseline": 5,
        "anomaly_zscore_k": 3.0,
        "anomaly_min_baseline": 20,
        "inbound_alerts_enabled": False,
    }
    fields.update(extra)
    return Settings(**fields)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_clock() -> None:
    """The sync clock is process-local module state; no test may inherit it."""
    reset_sync_clock()


def _insights_client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.include_router(build_metrics_insights_router(MockMetricsQuery(), settings))
    return TestClient(app)


def _anomaly_client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.include_router(build_metrics_anomaly_router(MockMetricsQuery(), settings))
    return TestClient(app)


# The endpoints stamped by this task, and the shape each needs to be called
# with. `/metrics/dashboard` is deliberately absent -- see
# `test_the_one_metrics_response_not_yet_stamped_is_named_not_hidden`.
_INSIGHTS_PATHS = (
    "/metrics/departments",
    "/metrics/callcenter",
    "/metrics/lifecycle",
    "/metrics/dealer-escalation",
    "/metrics/sla-buckets",
    "/metrics/case-aging",
    "/metrics/after-hours",
    "/metrics/by-tag",
    "/metrics/volume-by-type",
)


# ---------------------------------------------------------------------------
# The five tests named in the task brief
# ---------------------------------------------------------------------------


def test_every_metrics_endpoint_response_carries_as_of_and_source() -> None:
    """Both keys, on every stamped endpoint, and on every declared surface.

    Two halves on purpose. The endpoint half is what an HTTP consumer sees; the
    surface half is the inventory `GET /metrics/freshness` publishes, and a
    surface missing from it is a page with nothing to render.
    """
    record_sync_completed(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))
    settings = _settings(freshness=True)

    client = _insights_client(settings)
    for path in _INSIGHTS_PATHS:
        body = client.get(path, headers={"x-api-key": API_KEY}).json()
        assert "as_of" in body, path
        assert "source" in body, path
        assert body["as_of"] == "2026-08-09T12:00:00+00:00", path
        assert body["source"] == SOURCE_BATCH, path

    anomalies = _anomaly_client(settings)
    for path in ("/metrics/anomalies", "/metrics/anomalies/hourly"):
        body = anomalies.get(path).json()
        assert body["as_of"] == "2026-08-09T12:00:00+00:00", path
        assert body["source"] == SOURCE_BATCH, path

    surfaces = anomalies.get("/metrics/freshness").json()["surfaces"]
    assert set(surfaces) == {
        SURFACE_DASHBOARD,
        SURFACE_REPORTS,
        SURFACE_ANOMALY_HOURLY,
        SURFACE_ALERT_STREAM,
        SURFACE_MY_TASKS,
    }
    for name, entry in surfaces.items():
        assert "as_of" in entry, name
        assert "source" in entry, name
        assert entry["basis"], f"{name} has no basis sentence"


def test_a_bigquery_backed_response_reports_batch_6h() -> None:
    """Everything read out of the warehouse, including the "real-time" pages.

    The anomaly page is the interesting one: §3.5 calls it real-time, its push
    notification genuinely is, and its FIGURES are still whatever the last sync
    loaded. It reports `batch_6h`.
    """
    settings = _settings(freshness=True)
    record_sync_completed(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))
    f = batch_freshness(settings)
    assert f.source == SOURCE_BATCH
    assert f.max_staleness_seconds == 6 * 3600
    assert "6h" in f.basis
    # the basis carries the reconciliation point in words, not just a token: a
    # difference against the live CRM is EXPECTED, which is the whole reason this
    # sentence ships in the payload instead of living in a runbook
    assert "expected" in f.basis.lower()

    surfaces = surface_freshness(settings)
    for name in (SURFACE_DASHBOARD, SURFACE_REPORTS, SURFACE_ANOMALY_HOURLY):
        assert surfaces[name].source == SOURCE_BATCH, name
        assert surfaces[name].source != SOURCE_LIVE_STREAM, name

    # ...and over HTTP, on the page the design calls real-time
    body = _anomaly_client(settings).get("/metrics/anomalies/hourly").json()
    assert body["source"] == SOURCE_BATCH

    # the configured interval is honoured, not the 6 in the token name
    twelve = batch_freshness(_settings(freshness=True, metrics_sync_interval_hours=12))
    assert twelve.max_staleness_seconds == 12 * 3600
    assert "12h" in twelve.basis


def test_the_alert_stream_reports_live_stream() -> None:
    """The one surface that is genuinely live -- and only when it is switched on.

    A shared helper that stamped everything `batch_6h` would understate the
    alert stream; one that stamped everything `live_stream` would overstate five
    other surfaces. And with `INBOUND_ALERTS_ENABLED` off, the alerting a tenant
    actually has is the existing 60-second poll, so claiming a live stream would
    be a claim about software that is not running.
    """
    live = live_stream_freshness()
    assert live.source == SOURCE_LIVE_STREAM
    assert live.as_of is None
    assert live.as_of_status == AS_OF_CONTINUOUS
    assert not live.stale

    on = surface_freshness(_settings(freshness=True, inbound_alerts_enabled=True))
    assert on[SURFACE_ALERT_STREAM].source == SOURCE_LIVE_STREAM
    # the batch surfaces are untouched by that flag: per-surface, not global
    assert on[SURFACE_DASHBOARD].source == SOURCE_BATCH

    off = surface_freshness(_settings(freshness=True, inbound_alerts_enabled=False))
    assert off[SURFACE_ALERT_STREAM].source == SOURCE_POLL_60S
    assert off[SURFACE_MY_TASKS].source == SOURCE_POLL_60S
    assert poll_freshness().max_staleness_seconds == 60


def test_as_of_reflects_the_last_sync_not_the_request_time() -> None:
    """THE test. `as_of = now()` is the implementation this forbids."""
    settings = _settings(freshness=True)
    synced = datetime(2026, 8, 9, 6, 0, tzinfo=UTC)
    record_sync_completed(synced)
    assert last_sync_completed_at() == synced

    before = datetime.now(UTC)
    body = (
        _insights_client(settings)
        .get("/metrics/departments", headers={"x-api-key": API_KEY})
        .json()
    )
    after = datetime.now(UTC)

    assert body["as_of"] == synced.isoformat()
    stamped = datetime.fromisoformat(body["as_of"])
    assert stamped == synced
    assert not (before <= stamped <= after), "as_of is the request time, not the sync time"
    assert body["freshness"]["as_of_status"] == AS_OF_MEASURED

    # A failed sync must not move it forward: the data would get older while the
    # stamp got newer.
    scheduler.run_sync_job(
        settings,
        sync=lambda _s: (_ for _ in ()).throw(RuntimeError("BigQuery is down")),
        ensure=lambda _s: None,
    )
    assert last_sync_completed_at() == synced

    # ...and a successful one does.
    scheduler.run_sync_job(settings, sync=lambda _s: {"rows": 1}, ensure=lambda _s: None)
    moved = last_sync_completed_at()
    assert moved is not None and moved > synced


def test_a_stale_sync_is_visible_as_an_old_as_of_rather_than_hidden() -> None:
    """A sync that has not run for 30 hours shows a 30-hour-old timestamp.

    Not blanked (which loses the size of the gap), not refreshed (which is the
    lie), not rounded to "today". `stale` is the extra signal, and it never
    replaces the timestamp.
    """
    settings = _settings(freshness=True)
    old = datetime.now(UTC) - timedelta(hours=30)
    record_sync_completed(old)

    body = (
        _insights_client(settings).get("/metrics/callcenter", headers={"x-api-key": API_KEY}).json()
    )
    assert body["as_of"] == old.isoformat()
    assert body["freshness"]["stale"] is True
    assert body["freshness"]["as_of_status"] == AS_OF_MEASURED
    assert body["freshness"]["max_staleness_seconds"] == 6 * 3600
    # the response is still served -- a stale figure that is LABELLED stale is
    # useful; refusing to answer just moves the reader to an unlabelled source
    assert "sla" in body or "tasks_per_agent" in body

    fresh = datetime.now(UTC) - timedelta(minutes=5)
    record_sync_completed(fresh)
    body = (
        _insights_client(settings).get("/metrics/callcenter", headers={"x-api-key": API_KEY}).json()
    )
    assert body["freshness"]["stale"] is False


# ---------------------------------------------------------------------------
# "Unknown" is a first-class answer, and it is not `now`
# ---------------------------------------------------------------------------


def test_an_unknown_as_of_is_blank_and_never_substituted_with_now() -> None:
    """No sync recorded in this process -> `as_of: null`.

    This is the state after every restart, and on any replica that never runs
    the scheduler. A stamp of `now` here would be a false assurance about data
    of entirely unknown age, so the timestamp is omitted and the basis says why.
    """
    settings = _settings(freshness=True)
    assert last_sync_completed_at() is None

    f = batch_freshness(settings)
    assert f.as_of is None
    assert f.as_of_status == AS_OF_UNKNOWN
    assert "blank" in f.basis and "current time" in f.basis
    # unknown is NOT reported as stale: "we cannot tell" and "it is out of date"
    # are different claims
    assert f.stale is False

    body = (
        _insights_client(settings)
        .get("/metrics/sla-buckets", headers={"x-api-key": API_KEY})
        .json()
    )
    assert body["as_of"] is None
    assert body["source"] == SOURCE_BATCH
    assert body["freshness"]["as_of_status"] == AS_OF_UNKNOWN


def test_the_three_as_of_statuses_are_distinguishable() -> None:
    """measured / unknown / continuous. Collapsing any two loses a real state."""
    record_sync_completed(datetime(2026, 8, 9, 6, 0, tzinfo=UTC))
    assert batch_freshness(_settings(freshness=True)).as_of_status == AS_OF_MEASURED
    reset_sync_clock()
    assert batch_freshness(_settings(freshness=True)).as_of_status == AS_OF_UNKNOWN
    assert live_stream_freshness().as_of_status == AS_OF_CONTINUOUS
    assert poll_freshness().as_of_status == AS_OF_UNKNOWN
    assert len({AS_OF_MEASURED, AS_OF_UNKNOWN, AS_OF_CONTINUOUS}) == 3


# ---------------------------------------------------------------------------
# Flag off is byte-identical, and the flag has real consumers
# ---------------------------------------------------------------------------


def test_the_flag_off_response_is_the_same_object_untouched() -> None:
    payload: dict[str, object] = {"anomalies": []}
    assert stamp_freshness(payload, live_stream_freshness(), enabled=False) is payload
    stamped = stamp_freshness(payload, live_stream_freshness(), enabled=True)
    assert stamped is not payload
    assert set(stamped) - set(payload) == {"as_of", "source", "freshness"}


def test_the_flag_off_endpoints_gain_no_keys_at_all() -> None:
    record_sync_completed(datetime(2026, 8, 9, 12, 0, tzinfo=UTC))
    settings = _settings(freshness=False)
    client = _insights_client(settings)
    for path in _INSIGHTS_PATHS:
        body = client.get(path, headers={"x-api-key": API_KEY}).json()
        assert "as_of" not in body, path
        assert "source" not in body, path
        assert "freshness" not in body, path
    anomalies = _anomaly_client(settings)
    assert set(anomalies.get("/metrics/anomalies").json()) == {"anomalies"}
    assert "as_of" not in anomalies.get("/metrics/anomalies/hourly").json()
    # and the endpoint itself is not there
    assert anomalies.get("/metrics/freshness").status_code == 404


def test_the_freshness_endpoint_is_reachable_over_http() -> None:
    """Mounted through `build_metrics_anomaly_router`, which `main.py` already
    wires -- so this is live without a `main.py` change. A router nobody mounts
    is the failure mode this run has hit repeatedly."""
    r = _anomaly_client(_settings(freshness=True)).get("/metrics/freshness")
    assert r.status_code == 200
    assert set(r.json()) == {"surfaces"}


def test_the_sync_job_is_what_records_the_clock() -> None:
    """A clock nothing writes leaves `as_of` permanently unknown -- the tunable
    that does nothing at any value, in freshness form."""
    source = inspect.getsource(scheduler.run_sync_job)
    assert "record_sync_completed()" in source
    # after the load AND the view refresh, and inside the try -- never in a
    # finally, which would record a failed run
    assert source.index("ensure_views)(settings)") < source.index("record_sync_completed()")
    assert "finally" not in source


def test_the_one_metrics_response_not_yet_stamped_is_named_not_hidden() -> None:
    """`/metrics/dashboard` is the §2.2.3 executive dashboard and it is NOT
    stamped, because `build_metrics_query_router` takes no `Settings` and
    `main.py` was out of scope for this task. Recorded as a named gap rather
    than papered over with an optional `settings=None` parameter that no call
    site would ever pass -- an unreachable stamp is not a stamp.
    """
    params = inspect.signature(build_metrics_query_router).parameters
    assert "settings" not in params, (
        "if dashboard_router now takes Settings, stamp its response and delete this test"
    )
