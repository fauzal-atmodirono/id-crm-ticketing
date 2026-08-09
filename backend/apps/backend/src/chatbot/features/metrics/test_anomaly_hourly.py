"""P9 task 4 -- hourly anomaly detection, and the two properties that ARE it.

`test_the_baseline_is_not_the_trailing_hours_of_today`,
`test_a_normal_lunchtime_dip_is_not_flagged` and
`test_a_normal_morning_ramp_is_not_flagged` are a set, and they exist because a
trailing-hours baseline is the obvious thing to build first and it flags the
shape of a normal day. A detector that fires on lunchtime gets muted, and a
muted detector never fires on the intra-day explosion it was built for.

`test_an_hour_below_the_minimum_baseline_is_suppressed` and
`test_a_suppressed_hour_is_labelled_insufficient_volume_not_normal` are also a
pair. Without the floor the detector alerts every night at 03:00, where two
messages instead of the usual one is a 100% deviation. With the floor but
without the label, the dashboard shows those hours as fine -- which is a claim,
where the truth is that nothing was concluded.

No BigQuery here (controller decision D2), so the view's SQL is asserted
structurally, exactly as P4/P5/P8 did.
"""

# ruff: noqa: S608  # the f-strings below build EXPECTED DDL fragments to assert
# against, not queries to run; same rationale as bigquery_schema.py's own noqa.

from __future__ import annotations

import inspect
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics import sync
from chatbot.features.metrics.anomaly import (
    HOURLY_STATUS_FLAGGED,
    HOURLY_STATUS_INSUFFICIENT_VOLUME,
    HOURLY_STATUS_NO_BASELINE,
    HOURLY_STATUS_NORMAL,
    evaluate_hourly_anomalies,
    flag_anomalies,
    flag_hourly_anomalies,
)
from chatbot.features.metrics.anomaly_router import build_metrics_anomaly_router
from chatbot.features.metrics.bigquery_schema import HOURLY_BASELINE_DAYS, view_ddls
from chatbot.features.metrics.query_port import (
    AnomalyRow,
    HourlyAnomalyRow,
    MockMetricsQuery,
)
from chatbot.platform.config import Settings

PROJECT, DATASET = "proj", "ds"

# The hourly defaults from config.py. Named here so a test that means "the
# configured floor" is not indistinguishable from one that means "5".
K = 3.5
FLOOR = 5


def _hourly_ddls(**kwargs: object) -> dict[str, str]:
    return view_ddls(PROJECT, DATASET, anomaly_hourly_enabled=True, **kwargs)  # type: ignore[arg-type]


def _row(
    channel: str, hour: int, current: int, mean: float | None, stddev: float | None, days: int = 7
) -> HourlyAnomalyRow:
    return HourlyAnomalyRow(
        channel,
        hour_of_day=hour,
        current_volume=current,
        baseline_mean=mean,
        baseline_stddev=stddev,
        baseline_days=days,
    )


def _status(rows: list[HourlyAnomalyRow], k: float = K, floor: int = FLOOR) -> dict[int, str]:
    """hour_of_day -> status, for a single-channel evaluation."""
    return {h.hour_of_day: h.status for h in evaluate_hourly_anomalies(rows, k, floor)}


# ---------------------------------------------------------------------------
# 1-2: where the baseline comes from
# ---------------------------------------------------------------------------


def test_the_baseline_is_the_same_hour_across_preceding_days() -> None:
    """The view's `base` CTE groups on `hour_of_day` and joins on it.

    This is the one property the whole detector rests on, and it is expressed
    entirely in SQL -- so it is asserted in SQL. Three things have to hold
    together: the candidate hours are narrowed to the reference hour
    (`same_hour`), the baseline aggregates within `(channel, hour_of_day)`, and
    the current bucket is matched back to its own hour rather than to any hour
    of the same channel.
    """
    sql = _hourly_ddls()["v_channel_anomaly_hourly"]
    ref_hour = (
        "EXTRACT(HOUR FROM TIMESTAMP_TRUNC("
        "TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR), HOUR))"
    )
    assert f"same_hour AS (SELECT * FROM hourly WHERE hour_of_day = {ref_hour})" in sql
    assert "AVG(v) AS baseline_mean" in sql
    assert "STDDEV(v) AS baseline_stddev" in sql
    assert "GROUP BY channel, hour_of_day)" in sql
    assert "LEFT JOIN cur c USING (channel, hour_of_day)" in sql
    # ...over the PRECEDING days: the window ends yesterday, so today's own
    # buckets can never be part of the baseline they are compared against.
    ref_day = "DATE(TIMESTAMP_TRUNC(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR), HOUR))"
    assert (
        f"WHERE d BETWEEN DATE_SUB({ref_day}, INTERVAL {HOURLY_BASELINE_DAYS} DAY) "
        f"AND DATE_SUB({ref_day}, INTERVAL 1 DAY)"
    ) in sql
    # and the reference bucket is the last COMPLETE hour, not the one in
    # progress -- a partial bucket is under-counted against complete ones and
    # would read as a collapse in volume on every single query.
    assert (
        f"cur AS (SELECT channel, hour_of_day, v AS current_volume FROM same_hour WHERE d = {ref_day})"
        in sql
    )


def test_the_baseline_is_not_the_trailing_hours_of_today() -> None:
    """The failing assertion on the obvious wrong implementation.

    A trailing-hours baseline averages the last N HOURS: it would have to
    aggregate without `hour_of_day` in the GROUP BY (otherwise every group has
    one row and no standard deviation), and it would have to reach into today's
    other buckets -- `INTERVAL n HOUR` on the baseline window rather than
    `INTERVAL n DAY`.
    """
    sql = _hourly_ddls()["v_channel_anomaly_hourly"]
    base = sql.split("base AS (")[1].split("GROUP BY channel, hour_of_day)")[0]
    # The baseline window is measured in DAYS...
    assert "INTERVAL 7 DAY" in base
    # ...and never in hours. `INTERVAL 1 HOUR` appears only in the reference
    # timestamp (the last complete hour), never as the baseline's own window.
    assert "HOUR)" not in base.replace(
        "TIMESTAMP_TRUNC(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR), HOUR)", ""
    )
    # And the aggregation is per hour-of-day, so hour 13 can never contribute to
    # hour 14's baseline.
    assert "GROUP BY channel, hour_of_day" in sql
    assert "GROUP BY channel)" not in sql


# ---------------------------------------------------------------------------
# 3-5: the shape of a normal day is not an anomaly; a real spike is
# ---------------------------------------------------------------------------


def test_a_normal_lunchtime_dip_is_not_flagged() -> None:
    """13:00 is quiet every day. Against its OWN history that is normal.

    Against the trailing hours (11:00 and 12:00, both busy) it is a
    catastrophic drop -- which is what a trailing-hours detector would report,
    every single day, at lunchtime.
    """
    rows = [
        _row("web", 11, current=60, mean=58.0, stddev=4.0),
        _row("web", 12, current=55, mean=54.0, stddev=4.0),
        _row("web", 13, current=12, mean=12.0, stddev=2.0),  # the dip, right on its own mean
        _row("web", 14, current=57, mean=56.0, stddev=4.0),
    ]
    assert _status(rows) == {
        11: HOURLY_STATUS_NORMAL,
        12: HOURLY_STATUS_NORMAL,
        13: HOURLY_STATUS_NORMAL,
        14: HOURLY_STATUS_NORMAL,
    }
    assert flag_hourly_anomalies(rows, K, FLOOR) == []


def test_a_normal_morning_ramp_is_not_flagged() -> None:
    """08:00 -> 09:00 -> 10:00 triples every morning.

    Each hour sits on its own same-hour baseline, so nothing is flagged. A
    trailing-hours baseline sees 09:00 as 2x the last hour and 10:00 as 1.5x,
    and reports the working day starting as an incident.
    """
    rows = [
        _row("whatsapp", 8, current=20, mean=19.0, stddev=3.0),
        _row("whatsapp", 9, current=42, mean=40.0, stddev=5.0),
        _row("whatsapp", 10, current=61, mean=60.0, stddev=6.0),
    ]
    assert set(_status(rows).values()) == {HOURLY_STATUS_NORMAL}
    assert flag_hourly_anomalies(rows, K, FLOOR) == []


def test_a_genuine_intra_day_spike_is_flagged() -> None:
    """4.79's own example: a channel explodes inside the day.

    260 against a same-hour baseline of 90 (sd 15) is z = 11.3 -- and the point
    of hourly grain is that it is visible now rather than in tomorrow's daily
    roll-up, where 260 extra conversations against a daily baseline of ~2000 is
    inside the noise.
    """
    rows = [
        _row("web", 14, current=130, mean=125.0, stddev=10.0),
        _row("whatsapp", 14, current=260, mean=90.0, stddev=15.0),
    ]
    flagged = flag_hourly_anomalies(rows, K, FLOOR)
    assert [f.channel for f in flagged] == ["whatsapp"]
    assert flagged[0].status == HOURLY_STATUS_FLAGGED
    assert flagged[0].z_score is not None
    assert round(flagged[0].z_score, 2) == 11.33
    assert flagged[0].hour_of_day == 14
    # every bucket is still returned, labelled -- only the alert path filters
    evaluated = evaluate_hourly_anomalies(rows, K, FLOOR)
    assert [(h.channel, h.status) for h in evaluated] == [
        ("web", HOURLY_STATUS_NORMAL),
        ("whatsapp", HOURLY_STATUS_FLAGGED),
    ]


# ---------------------------------------------------------------------------
# 6-7: the mandatory floor, and saying so
# ---------------------------------------------------------------------------


def test_an_hour_below_the_minimum_baseline_is_suppressed() -> None:
    """03:00. Baseline 0.4 cases an hour, three cases arrive, z = 5.2.

    Above the hourly k of 3.5, so without the floor this fires -- every night,
    on three messages. It is the single most likely way for this detector to be
    switched off permanently within a week of being switched on.
    """
    rows = [_row("phone", 3, current=3, mean=0.4, stddev=0.5, days=3)]
    evaluated = evaluate_hourly_anomalies(rows, K, FLOOR)
    assert evaluated[0].z_score is not None
    assert evaluated[0].z_score > K, (
        "the deviation really is large; the floor is what suppresses it"
    )
    assert flag_hourly_anomalies(rows, K, FLOOR) == []
    # The floor is on the BASELINE, and that is sufficient: flagging is
    # upward-only, so anything flaggable already has current_volume above a
    # baseline that is itself above the floor.
    just_over = [_row("phone", 3, current=40, mean=5.0, stddev=1.0)]
    assert [f.channel for f in flag_hourly_anomalies(just_over, K, FLOOR)] == ["phone"]
    assert just_over[0].current_volume > FLOOR


def test_a_suppressed_hour_is_labelled_insufficient_volume_not_normal() -> None:
    """ "We did not look" and "we looked and it was fine" must be different.

    Same principle as P5's `no_data`: a dashboard that renders a suppressed
    03:00 as normal is making a claim about an hour nothing was concluded from.
    """
    rows = [
        _row("phone", 3, current=3, mean=0.4, stddev=0.5, days=3),
        _row("web", 3, current=126, mean=125.0, stddev=10.0),
        _row("email", 3, current=7, mean=None, stddev=None, days=0),
    ]
    evaluated = {h.channel: h for h in evaluate_hourly_anomalies(rows, K, FLOOR)}
    assert evaluated["phone"].status == HOURLY_STATUS_INSUFFICIENT_VOLUME
    assert evaluated["phone"].status != HOURLY_STATUS_NORMAL
    # the real numbers survive, so the page can say WHY it was suppressed
    assert evaluated["phone"].current_volume == 3
    assert evaluated["phone"].baseline_mean == 0.4
    assert evaluated["phone"].min_baseline == FLOOR
    # and an hour that genuinely was examined and was fine says so
    assert evaluated["web"].status == HOURLY_STATUS_NORMAL
    # a third state, also not "normal": there was no baseline to compare with
    assert evaluated["email"].status == HOURLY_STATUS_NO_BASELINE
    assert evaluated["email"].z_score is None, "0.0 would read as 'dead average'"


# ---------------------------------------------------------------------------
# 8-9: two thresholds, and the daily one untouched
# ---------------------------------------------------------------------------


def test_the_hourly_detector_uses_its_own_k_and_not_the_daily_one() -> None:
    """z = 3.2: over the daily k of 3.0, under the hourly 3.5.

    Hourly buckets are noisier, so the looser threshold is the difference
    between a detection an operator acts on and a stream of them they mute.
    """
    rows = [_row("web", 14, current=157, mean=125.0, stddev=10.0)]
    assert flag_hourly_anomalies(rows, k=3.5, min_baseline=FLOOR) == []
    assert [f.channel for f in flag_hourly_anomalies(rows, k=3.0, min_baseline=FLOOR)] == ["web"]
    # ...and the configured value really does reach the detector, at a
    # non-default setting, rather than 3.5 being baked in.
    settings = Settings(
        anomaly_hourly_enabled=True,
        anomaly_hourly_zscore_k=2.5,
        anomaly_hourly_min_baseline=FLOOR,
    )
    assert settings.anomaly_hourly_zscore_k == 2.5
    assert [
        f.channel
        for f in flag_hourly_anomalies(
            rows, settings.anomaly_hourly_zscore_k, settings.anomaly_hourly_min_baseline
        )
    ] == ["web"]


def test_the_daily_detector_is_completely_unchanged() -> None:
    """The daily grain is what the deployed page and the report email read.

    Pinned three ways: the function's behaviour at its own defaults, its source
    text (so a "shared helper" refactor that routes it through the hourly path
    fails here), and `v_channel_anomaly`'s DDL as a literal string.
    """
    rows = [
        AnomalyRow("web", current_volume=200, baseline_mean=100.0, baseline_stddev=20.0),
        AnomalyRow("wa", current_volume=140, baseline_mean=100.0, baseline_stddev=20.0),
        AnomalyRow("sms", current_volume=999, baseline_mean=5.0, baseline_stddev=1.0),
    ]
    out = flag_anomalies(rows, k=3.0, min_baseline=20)
    assert [a.channel for a in out] == ["web"]
    assert out[0].z_score == 5.0

    source = inspect.getsource(flag_anomalies)
    assert "hourly" not in source.lower().split('"""')[-1], (
        "flag_anomalies must not route through the hourly path"
    )

    expected = (
        "CREATE OR REPLACE VIEW `proj.ds.v_channel_anomaly` AS "
        "WITH daily AS (SELECT channel, DATE(created_at) AS d, COUNT(*) AS v "
        "FROM `proj.ds.conversations` WHERE created_at IS NOT NULL GROUP BY channel, d), "
        "cur AS (SELECT channel, v AS current_volume FROM daily "
        "WHERE d = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)), "
        "base AS (SELECT channel, AVG(v) AS baseline_mean, STDDEV(v) AS baseline_stddev "
        "FROM daily WHERE d BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 8 DAY) "
        "AND DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) GROUP BY channel) "
        "SELECT b.channel, COALESCE(c.current_volume, 0) AS current_volume, "
        "b.baseline_mean, b.baseline_stddev "
        "FROM base b LEFT JOIN cur c USING (channel)"
    )
    assert view_ddls(PROJECT, DATASET)["v_channel_anomaly"] == expected
    # ...and turning the hourly view on does not touch it
    assert _hourly_ddls()["v_channel_anomaly"] == expected


# ---------------------------------------------------------------------------
# Reachability: flag -> view builder -> endpoint. A green unit test on
# evaluate_hourly_anomalies() proves nothing about any of these.
# ---------------------------------------------------------------------------


def test_the_view_does_not_exist_unless_the_flag_is_on() -> None:
    off = set(view_ddls(PROJECT, DATASET))
    on = set(_hourly_ddls())
    assert "v_channel_anomaly_hourly" not in off
    assert on - off == {"v_channel_anomaly_hourly"}
    assert off - on == set()


def test_the_flag_and_both_tunables_reach_the_view_builder_from_settings() -> None:
    """A tunable `ensure_views` never forwards does nothing at any value."""
    source = inspect.getsource(sync.ensure_views)
    assert "anomaly_hourly_enabled=settings.anomaly_hourly_enabled" in source
    assert "anomaly_hourly_zscore_k=settings.anomaly_hourly_zscore_k" in source
    assert "anomaly_hourly_min_baseline=settings.anomaly_hourly_min_baseline" in source


def test_the_view_prints_the_configured_floor_and_threshold_not_a_hardcoded_pair() -> None:
    sql = _hourly_ddls(anomaly_hourly_min_baseline=8, anomaly_hourly_zscore_k=2.5)[
        "v_channel_anomaly_hourly"
    ]
    assert "8 AS min_baseline" in sql
    assert "b.baseline_mean >= 8 AS has_sufficient_volume" in sql
    assert "2.5 AS zscore_k" in sql
    assert "5 AS min_baseline" not in sql
    assert "3.5 AS zscore_k" not in sql
    # and the floor never becomes a WHERE: a suppressed hour must still have a row
    assert "WHERE d " in sql  # the baseline window
    assert "WHERE baseline_mean" not in sql
    assert "HAVING" not in sql


def test_the_view_honours_the_reporting_timezone() -> None:
    assert "DATE(created_at) AS d" in _hourly_ddls()["v_channel_anomaly_hourly"]
    zoned = view_ddls(
        PROJECT, DATASET, reporting_timezone="Asia/Kuala_Lumpur", anomaly_hourly_enabled=True
    )["v_channel_anomaly_hourly"]
    assert "DATE(created_at, 'Asia/Kuala_Lumpur') AS d" in zoned
    assert "EXTRACT(HOUR FROM created_at AT TIME ZONE 'Asia/Kuala_Lumpur') AS hour_of_day" in zoned
    # the reference bucket is resolved in the SAME zone -- an hour-of-day taken
    # in Kuala Lumpur and compared against a UTC reference is off by eight
    assert (
        "DATE(TIMESTAMP_TRUNC(TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR), HOUR), 'Asia/Kuala_Lumpur')"
        in zoned
    )
    assert "AT TIME ZONE 'Asia/Kuala_Lumpur')" in zoned


def _client(*, enabled: bool, k: float = K, floor: int = FLOOR, **extra: object) -> TestClient:
    """A client whose hourly config is stated EXPLICITLY, never defaulted.

    `Settings(...)` still reads `os.environ` -- `_env_file=None` does not stop
    it -- and the flags-ON gate run sets `ANOMALY_HOURLY_ZSCORE_K=2.5` and
    `ANOMALY_HOURLY_MIN_BASELINE=8` deliberately. A test that built
    `Settings(anomaly_hourly_enabled=True)` and then asserted `zscore_k == 3.5`
    would be asserting the ambient environment, and it fails under the gate --
    which is exactly what happened while writing this file. Every value this
    test depends on is passed in.

    `dashboard_freshness_enabled=False` for the same reason: the gate also sets
    `DASHBOARD_FRESHNESS_ENABLED=true`, and P9 task 5 adds `as_of`/`source`/
    `freshness` keys to both endpoints here when it is on. Nothing in this file
    is about freshness, so it states the flag rather than inheriting it -- see
    `test_freshness_contract.py` for the stamped shape.
    """
    app = FastAPI()
    fields: dict[str, object] = {
        "anomaly_hourly_enabled": enabled,
        "anomaly_hourly_zscore_k": k,
        "anomaly_hourly_min_baseline": floor,
        "dashboard_freshness_enabled": False,
    }
    fields.update(extra)
    app.include_router(
        build_metrics_anomaly_router(MockMetricsQuery(), Settings(**fields))  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_the_hourly_endpoint_404s_until_the_flag_is_on() -> None:
    assert _client(enabled=False).get("/metrics/anomalies/hourly").status_code == 404


def test_the_hourly_endpoint_returns_every_hour_with_its_label() -> None:
    r = _client(enabled=True).get("/metrics/anomalies/hourly")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    by_channel = {h["channel"]: h["status"] for h in body["hours"]}
    assert by_channel == {
        "web": HOURLY_STATUS_NORMAL,
        "whatsapp": HOURLY_STATUS_FLAGGED,
        "phone": HOURLY_STATUS_INSUFFICIENT_VOLUME,
    }
    assert [a["channel"] for a in body["anomalies"]] == ["whatsapp"]
    # The configuration in force travels with the answer -- and it is the
    # settings' value, not a constant: at a different floor the same canned rows
    # come back labelled differently and the echo moves with them.
    assert body["zscore_k"] == K
    assert body["min_baseline"] == FLOOR
    tighter = _client(enabled=True, k=2.0, floor=200).get("/metrics/anomalies/hourly").json()
    assert tighter["zscore_k"] == 2.0
    assert tighter["min_baseline"] == 200
    assert {h["status"] for h in tighter["hours"]} == {HOURLY_STATUS_INSUFFICIENT_VOLUME}
    assert tighter["anomalies"] == []


def test_an_unreadable_view_is_unavailable_not_an_empty_all_clear() -> None:
    """`ensure_views` not yet re-run is a likely state, and it must not read as
    "no anomalies in the last hour"."""
    app = FastAPI()
    app.include_router(
        build_metrics_anomaly_router(
            MockMetricsQuery(degraded=True),
            Settings(anomaly_hourly_enabled=True),
        )
    )
    body = TestClient(app).get("/metrics/anomalies/hourly").json()
    assert body["status"] == "unavailable"
    assert body["hours"] == []
    assert body["anomalies"] == []


def test_the_daily_endpoint_is_untouched_by_the_hourly_addition() -> None:
    # The DAILY thresholds are stated too: leaving them to the ambient
    # environment is what made the sibling assertion above fail under the
    # flags-ON gate.
    r = _client(enabled=True, anomaly_zscore_k=3.0, anomaly_min_baseline=20).get(
        "/metrics/anomalies"
    )
    assert r.status_code == 200
    assert set(r.json()) == {"anomalies"}
    assert [a["channel"] for a in r.json()["anomalies"]] == ["whatsapp"]


def test_the_row_shape_matches_the_view_columns() -> None:
    """`_query_block` does `row_type(**dict(r))`, so a column the view emits and
    the dataclass does not have is a TypeError at query time, on a dashboard."""
    sql = _hourly_ddls()["v_channel_anomaly_hourly"]
    projection = sql.rsplit(" FROM base b ", 1)[0].rsplit(") SELECT ", 1)[1]
    emitted = {
        # unaliased columns arrive as `b.baseline_mean`; BigQuery names the
        # output column after the identifier, not the qualifier
        part.rsplit(" AS ", 1)[-1].strip().rsplit(".", 1)[-1]
        for part in projection.split(", ")
    }
    emitted = {name for name in emitted if name.isidentifier()}
    fields = set(HourlyAnomalyRow.__dataclass_fields__)
    assert emitted <= fields, f"view emits columns the row cannot accept: {emitted - fields}"
    assert {"channel", "hour_of_day", "current_volume", "day", "min_baseline"} <= emitted
    # `day` is a real DATE the row can hold
    assert HourlyAnomalyRow("web", 14, 1, 1.0, 1.0, day=date(2026, 8, 9)).day == date(2026, 8, 9)
