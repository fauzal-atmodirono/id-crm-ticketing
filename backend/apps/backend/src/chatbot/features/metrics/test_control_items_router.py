"""P5 task 3 — the control-items endpoint.

Fourteen rows, always. A row that cannot be measured appears, says so, and
carries its reason -- it is neither dropped nor rendered as zero. The client
counts the rows against the printed page, so omitting one is its own dishonesty.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.control_items_router import build_control_items_router
from chatbot.features.metrics.targets_store import Target

HEADERS = {"x-api-key": "secret"}


class _Settings:
    metrics_api_key = "secret"


class _Targets:
    def __init__(self, targets: dict[str, Target] | None = None) -> None:
        self._targets = targets or {}

    async def resolve(self, key: str, scope: str = "") -> Target | None:
        return self._targets.get(key)


def _client(targets=None, actuals=None):
    app = FastAPI()

    async def _provider(period, scope):  # noqa: ARG001
        if isinstance(actuals, Exception):
            raise actuals
        return actuals or {}

    app.include_router(
        build_control_items_router(
            _Targets(targets), _Settings(), _provider if actuals is not None else None
        )
    )
    return TestClient(app)


def _rows(res) -> dict[int, dict[str, Any]]:
    return {r["number"]: r for r in res.json()["items"]}


def test_the_endpoint_is_key_gated():
    assert _client().get("/metrics/control-items").status_code == 401


def test_the_response_always_contains_exactly_fourteen_rows():
    res = _client().get("/metrics/control-items", headers=HEADERS)
    assert res.status_code == 200
    assert len(res.json()["items"]) == 14


def test_the_five_unsupported_rows_report_no_data():
    rows = _rows(_client().get("/metrics/control-items", headers=HEADERS))
    for number in (10, 11, 12, 13, 14):
        assert rows[number]["status"] in ("no_data", "no_target")
        assert rows[number]["measurable"] is False


def test_abandon_rate_reports_no_data_and_not_zero_percent():
    """The false claim this package must never make."""
    rows = _rows(_client().get("/metrics/control-items", headers=HEADERS))
    abandon = rows[10]

    assert abandon["actual"] is None
    assert abandon["actual"] != 0
    assert abandon["status"] != "missed"
    assert "not a zero-abandon result" in abandon["blocking_reason"]


def test_each_no_data_row_carries_a_human_readable_blocking_reason():
    rows = _rows(_client().get("/metrics/control-items", headers=HEADERS))
    for number in (10, 11, 12, 13, 14):
        assert rows[number]["blocking_reason"]


def test_a_supported_row_populates_from_the_actuals_provider():
    targets = {"first_response": Target("first_response", "lte", 120, "minutes")}
    rows = _rows(
        _client(targets=targets, actuals={3: 90.0}).get(
            "/metrics/control-items", headers=HEADERS
        )
    )
    assert rows[3]["actual"] == 90.0
    assert rows[3]["status"] == "met"


def test_a_row_whose_target_is_unset_reports_no_target_not_missed():
    rows = _rows(_client(actuals={3: 90.0}).get("/metrics/control-items", headers=HEADERS))
    assert rows[3]["status"] == "no_target"


def test_a_measured_zero_is_not_reported_as_no_data():
    """0 is a measurement. Only None is an absence."""
    targets = {"reopen_rate": Target("reopen_rate", "lte", 5, "percent")}
    rows = _rows(
        _client(targets=targets, actuals={5: 0.0}).get(
            "/metrics/control-items", headers=HEADERS
        )
    )
    assert rows[5]["actual"] == 0.0
    assert rows[5]["status"] == "met"


def test_an_actuals_failure_degrades_every_row_to_no_data_never_to_missed():
    targets = {"first_response": Target("first_response", "lte", 120, "minutes")}
    rows = _rows(
        _client(targets=targets, actuals=RuntimeError("bigquery down")).get(
            "/metrics/control-items", headers=HEADERS
        )
    )
    assert all(r["status"] != "missed" for r in rows.values())


def test_the_note_states_how_many_rows_are_measurable():
    note = _client().get("/metrics/control-items", headers=HEADERS).json()["note"]
    assert "NOT zero and NOT missed" in note


def test_the_endpoint_accepts_a_period():
    res = _client().get(
        "/metrics/control-items",
        params={"from": "2026-07-17", "to": "2026-07-23", "granularity": "week"},
        headers=HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["period"] == {"from": "2026-07-17", "to": "2026-07-23"}
