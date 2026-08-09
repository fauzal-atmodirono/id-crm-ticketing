"""P4 task 3 — a tenant-configurable reporting timezone.

Every date bucket in these views is a UTC calendar day, because `DATE(ts)` on a
TIMESTAMP defaults to UTC in BigQuery. The Weekly Report picker builds its
window from browser-local dates. For a Malaysian tenant (UTC+8) those disagree
by 8 hours at both edges, so cases shift systematically between adjacent
buckets -- "close but not quite" against a deck compiled in MYT, which is the
worst kind of wrong because it does not look wrong.

**The first test is the safety argument for the entire change.** Switching
these to a fixed `'Asia/Kuala_Lumpur'` would re-bucket every historical figure
on every existing dashboard in one deploy. So the timezone is a parameter
defaulting to UTC, and the default must be the *identity transform* -- the DDL
it produces has to be byte-identical to what shipped before, character for
character. If that test ever fails, a live tenant's numbers moved without
anyone asking for it.
"""

from __future__ import annotations

import pytest

from chatbot.features.metrics.bigquery_schema import view_ddls

PROJECT, DATASET = "proj", "ds"


def test_the_default_produces_ddl_byte_identical_to_an_explicit_utc():
    """Defaulted and explicitly-UTC calls must be indistinguishable."""
    assert view_ddls(PROJECT, DATASET) == view_ddls(
        PROJECT, DATASET, reporting_timezone="UTC"
    )


def test_the_default_ddl_contains_no_timezone_argument_at_all():
    """The identity transform is 'emit exactly what we emitted before', not
    'emit DATE(x, "UTC")' -- the latter is equivalent to BigQuery but is a
    different string, and this test is what proves nothing moved."""
    for sql in view_ddls(PROJECT, DATASET).values():
        assert "DATE(created_at, " not in sql
        assert "'UTC'" not in sql


def test_a_configured_timezone_threads_into_every_date_call():
    ddls = view_ddls(PROJECT, DATASET, reporting_timezone="Asia/Kuala_Lumpur")
    touched = [sql for sql in ddls.values() if "DATE(" in sql]
    assert touched, "no view uses DATE() -- the test is not measuring anything"
    for sql in touched:
        # every DATE(...) over a timestamp column carries the zone
        for column in ("created_at", "resolved_at", "dealer_escalated_at"):
            bare = f"DATE({column})"
            assert bare not in sql, f"un-zoned {bare} left in: {sql[:90]}"


def test_the_zone_reaches_the_bucketing_helpers_too():
    sql = view_ddls(PROJECT, DATASET, reporting_timezone="Asia/Kuala_Lumpur")[
        "v_volume_by_month_channel"
    ]
    assert "Asia/Kuala_Lumpur" in sql


def test_no_view_hardcodes_a_timezone_string():
    """Guards against a future view quietly bypassing the parameter."""
    for name, sql in view_ddls(PROJECT, DATASET, reporting_timezone="UTC").items():
        assert "Asia/" not in sql, f"{name} hardcodes a zone"


def test_an_unknown_timezone_is_rejected_rather_than_silently_emitted():
    """A typo must not reach BigQuery as a view that fails at query time, on a
    dashboard, in front of the client."""
    with pytest.raises(ValueError) as excinfo:
        view_ddls(PROJECT, DATASET, reporting_timezone="Asia/Kuala_Lumpar")
    assert "Kuala_Lumpar" in str(excinfo.value)


def test_a_zone_cannot_smuggle_sql_into_the_ddl():
    """The value comes from config, but config is not a trust boundary worth
    betting a warehouse on."""
    with pytest.raises(ValueError):
        view_ddls(PROJECT, DATASET, reporting_timezone="UTC'); DROP TABLE x--")


@pytest.mark.parametrize("zone", ["UTC", "Asia/Kuala_Lumpur", "Asia/Jakarta"])
def test_every_supported_zone_still_produces_one_ddl_per_view(zone):
    default = view_ddls(PROJECT, DATASET)
    assert set(view_ddls(PROJECT, DATASET, reporting_timezone=zone)) == set(default)
