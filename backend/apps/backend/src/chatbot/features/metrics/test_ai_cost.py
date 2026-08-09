"""P8 task 4 -- the cost view, the endpoint, and the honesty it has to keep.

The load-bearing tests here are the negative ones. It is easy to write a cost
report that returns a number; the number would be wrong, low, and confident.
`test_the_report_emits_no_unqualified_total` asserts the ABSENCE of a total
key, and `test_an_unmetered_surface_is_never_reported_as_zero_cost` asserts
that the surfaces we cannot see are present-and-null rather than absent or
zero. Both fail if somebody later "tidies up" the payload into a headline
figure.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.metrics.ai_cost import (
    AI_COST_INCOMPLETE_CAVEAT,
    TokenUsageAggregateRow,
    build_ai_cost_report,
)
from chatbot.features.metrics.bigquery_schema import (
    AI_COST_STATUS_METERED,
    AI_COST_STATUS_UNMETERED,
    AI_COST_STATUS_UNPRICEABLE,
    AI_COST_SURFACE_COVERAGE,
    ai_cost_view_ddls,
)
from chatbot.features.metrics.insights_router import build_metrics_insights_router
from chatbot.features.metrics.period import PeriodRange
from chatbot.features.metrics.query_port import MockMetricsQuery

PROJECT, DATASET = "proj", "ds"
DAY = date(2026, 8, 3)
MODEL = "gemini-2.5-flash"


class _FakePrices:
    """A price table with a rate for `MODEL` only, so an unknown model
    resolves to None exactly as `PriceTable.price_for` does."""

    def __init__(self, rates: dict[str, str] | None = None, model: str = MODEL) -> None:
        self._rates = (
            rates
            if rates is not None
            else {
                "prompt_tokens": "0.0000001",
                "output_tokens": "0.0000004",
                "cached_tokens": "0.00000005",
            }
        )
        self._model = model

    async def price_for(self, model: str, token_class: str, at: Any) -> Decimal | None:
        assert isinstance(at, date), "price_for must be asked about the usage date"
        if model != self._model:
            return None
        raw = self._rates.get(token_class)
        return None if raw is None else Decimal(raw)


def _row(**kwargs: Any) -> TokenUsageAggregateRow:
    base: dict[str, Any] = {
        "day": DAY,
        "service": "backend",
        "surface": "assist.suggest",
        "model": MODEL,
        "calls": 2,
        "prompt_tokens": 1000,
        "output_tokens": 500,
        "cached_tokens": 100,
        "calls_with_prompt_tokens": 2,
        "calls_with_output_tokens": 2,
        "calls_with_cached_tokens": 2,
        "calls_without_usage_metadata": 0,
    }
    base.update(kwargs)
    return TokenUsageAggregateRow(**base)


def _surface(report: dict[str, Any], surface: str) -> dict[str, Any]:
    matches = [s for s in report["surfaces"] if s["surface"] == surface]
    assert matches, f"{surface} missing from the report"
    return matches[0]


# ---------------------------------------------------------------------------
# The six tests named in the task brief
# ---------------------------------------------------------------------------


async def test_cost_is_the_sum_of_the_three_token_classes_at_their_own_rates() -> None:
    report = await build_ai_cost_report([_row()], _FakePrices())
    entry = _surface(report, "assist.suggest")
    expected = (
        Decimal("0.0000001") * 1000 + Decimal("0.0000004") * 500 + Decimal("0.00000005") * 100
    )
    assert Decimal(entry["cost_usd"]) == expected
    assert Decimal(report["priced_subtotal_usd"]) == expected
    # each class priced at ITS OWN rate, not one blended rate
    classes = entry["token_classes"]
    assert Decimal(classes["prompt_tokens"]["cost_usd"]) == Decimal("0.0000001") * 1000
    assert Decimal(classes["output_tokens"]["cost_usd"]) == Decimal("0.0000004") * 500
    assert Decimal(classes["cached_tokens"]["cost_usd"]) == Decimal("0.00000005") * 100


async def test_unpriced_models_are_reported_separately_and_not_as_zero() -> None:
    report = await build_ai_cost_report([_row(model="gemini-experimental-unpriced")], _FakePrices())
    entry = _surface(report, "assist.suggest")
    assert entry["cost_usd"] is None, "an unpriced model must not cost 0"
    assert report["priced_subtotal_usd"] is None
    assert "gemini-experimental-unpriced" in report["completeness"]["unpriced_models"]
    # the tokens are still visible -- unpriced, not invisible
    assert entry["token_classes"]["prompt_tokens"]["tokens"] == 1000
    assert entry["token_classes"]["prompt_tokens"]["priced"] is False


async def test_uncaptured_usage_is_reported_as_unknown_and_not_as_zero() -> None:
    """The embedding shape: a real call, all three counts NULL."""
    report = await build_ai_cost_report(
        [
            _row(
                surface="embed",
                prompt_tokens=None,
                output_tokens=None,
                cached_tokens=None,
                calls_with_prompt_tokens=0,
                calls_with_output_tokens=0,
                calls_with_cached_tokens=0,
                calls_without_usage_metadata=2,
            )
        ],
        _FakePrices(),
    )
    entry = _surface(report, "embed")
    assert entry["cost_usd"] is None
    for spec in entry["token_classes"].values():
        assert spec["tokens"] is None, "an uncaptured count must stay None, never 0"
        assert spec["cost_usd"] is None
    assert entry["calls"] == 2, "the calls happened even though the tokens are unknown"
    assert entry["calls_without_usage_metadata"] == 2
    assert entry["cost_status"] == AI_COST_STATUS_UNPRICEABLE
    assert report["priced_subtotal_usd"] is None


async def test_the_endpoint_accepts_a_period_and_the_standard_filters() -> None:
    client = _client()
    res = client.get(
        "/metrics/ai-cost",
        params={"from": "2026-08-01", "to": "2026-08-07", "granularity": "week"},
        headers={"x-api-key": "secret"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["period"] == {
        "from": "2026-08-01",
        "to": "2026-08-07",
        "granularity": "week",
    }
    # The five standard filters are declared on the handler -- an undeclared
    # param is silently dropped by FastAPI, which is how "ignored" ships
    # looking like "honoured". Declared and supplied, they 400 by name,
    # because the cost views carry no case dimension to filter on.
    for name in ("agent_id", "team", "department", "channel", "dealer"):
        rejected = client.get(
            "/metrics/ai-cost", params={name: "x"}, headers={"x-api-key": "secret"}
        )
        assert rejected.status_code == 400, name
        assert name in rejected.json()["detail"]
    # An inverted range is still a 400, not a 500 or an unfiltered answer.
    bad = client.get(
        "/metrics/ai-cost",
        params={"from": "2026-08-07", "to": "2026-08-01", "granularity": "week"},
        headers={"x-api-key": "secret"},
    )
    assert bad.status_code == 400


async def test_cost_per_conversation_is_derivable_from_the_response() -> None:
    """§4.28.2's actual commercial question, answerable from ONE response."""
    report = await build_ai_cost_report([_row()], _FakePrices(), conversations=40)
    assert report["conversations"] == 40
    subtotal = Decimal(report["priced_subtotal_usd"])
    assert Decimal(report["cost_per_conversation_usd"]) == subtotal / 40
    # and the divisor's meaning is stated, because the numerator is partial
    assert "lower bound" in report["cost_per_conversation_basis"]


async def test_both_services_appear_in_the_service_dimension() -> None:
    report = await build_ai_cost_report([_row()], _FakePrices())
    services = {entry["service"] for entry in report["surfaces"]}
    assert services == {"backend", "agent"}
    # ...and the agent service appears as unmetered-in-the-warehouse, not as
    # zero spend: its counts live on ai_actions in Postgres.
    agent_rows = [e for e in report["surfaces"] if e["service"] == "agent"]
    assert agent_rows and all(e["cost_usd"] is None for e in agent_rows)
    assert all(e["cost_status"] == AI_COST_STATUS_UNMETERED for e in agent_rows)
    assert any("ai_actions" in e["cost_status_reason"] for e in agent_rows)


# ---------------------------------------------------------------------------
# The honesty properties, asserted so a later tidy-up cannot drop them
# ---------------------------------------------------------------------------


async def test_the_report_emits_no_unqualified_total() -> None:
    report = await build_ai_cost_report([_row()], _FakePrices(), conversations=10)
    for key in report:
        assert key != "total", "an unqualified total omits chat.turn entirely"
        assert not key.startswith("total_"), key
        assert not key.endswith("_total"), key
    assert "priced_subtotal_usd" in report
    assert report["completeness"]["is_complete"] is False
    assert report["completeness"]["caveat"] == AI_COST_INCOMPLETE_CAVEAT


async def test_an_unmetered_surface_is_never_reported_as_zero_cost() -> None:
    report = await build_ai_cost_report([_row()], _FakePrices())
    for surface in ("chat.turn", "phone.live"):
        entry = _surface(report, surface)
        assert entry["cost_usd"] is None
        assert entry["calls"] is None, "no rows exist; 0 calls would be a claim"
        assert entry["cost_status"] == AI_COST_STATUS_UNMETERED
        assert entry["cost_status_reason"]
    named = {row["surface"] for row in report["completeness"]["unmetered_surfaces"]}
    assert {"chat.turn", "phone.live", "orchestrator"} <= named


async def test_the_thinking_token_classes_are_named_as_excluded() -> None:
    report = await build_ai_cost_report([_row()], _FakePrices())
    excluded = report["completeness"]["excluded_token_classes"]
    assert "thoughts_token_count" in excluded
    assert "tool_use_prompt_token_count" in excluded


async def test_every_sum_ships_with_the_number_of_calls_that_carried_it() -> None:
    """A small bill and a small sample are different statements."""
    report = await build_ai_cost_report(
        [_row(calls=3000, prompt_tokens=90, calls_with_prompt_tokens=3)], _FakePrices()
    )
    spec = _surface(report, "assist.suggest")["token_classes"]["prompt_tokens"]
    assert spec["tokens"] == 90
    assert spec["calls_captured"] == 3, "3 of 3000 calls -- the reader must see that"


async def test_a_failed_read_reports_unavailable_rather_than_no_spend() -> None:
    report = await build_ai_cost_report([], _FakePrices(), ok=False)
    assert report["read_status"] == "unavailable"
    assert report["priced_subtotal_usd"] is None


async def test_each_day_is_priced_at_that_days_rate() -> None:
    """A price change mid-window must not re-price the earlier day."""

    class _ChangingPrices:
        async def price_for(self, model: str, token_class: str, at: Any) -> Decimal | None:
            if token_class != "prompt_tokens":
                return None
            return Decimal("0.001") if at < date(2026, 8, 5) else Decimal("0.002")

    rows = [
        _row(day=date(2026, 8, 3), prompt_tokens=100, output_tokens=None, cached_tokens=None),
        _row(day=date(2026, 8, 6), prompt_tokens=100, output_tokens=None, cached_tokens=None),
    ]
    report = await build_ai_cost_report(rows, _ChangingPrices())
    # 100*0.001 + 100*0.002, not 200 at either single rate
    assert Decimal(report["priced_subtotal_usd"]) == Decimal("0.3")


# ---------------------------------------------------------------------------
# The DDL, asserted structurally (no BigQuery here -- controller decision D2)
# ---------------------------------------------------------------------------


def test_the_cost_views_are_dimensioned_by_day_service_surface_and_model() -> None:
    sql = ai_cost_view_ddls(PROJECT, DATASET)["v_ai_token_usage"]
    assert f"`{PROJECT}.{DATASET}.v_ai_token_usage`" in sql
    assert f"`{PROJECT}.{DATASET}.token_usage`" in sql
    assert "GROUP BY day, service, surface, model" in sql


def test_the_usage_view_counts_the_calls_behind_every_sum() -> None:
    sql = ai_cost_view_ddls(PROJECT, DATASET)["v_ai_token_usage"]
    for column in (
        "calls_with_prompt_tokens",
        "calls_with_output_tokens",
        "calls_with_cached_tokens",
        "calls_without_usage_metadata",
    ):
        assert column in sql, column
    assert "COUNTIF(prompt_tokens IS NOT NULL)" in sql


def test_no_cost_view_computes_money() -> None:
    """Prices are effective-dated and live in Firestore, so a view that
    emitted a cost column could only do it with a hardcoded rate."""
    for name, sql in ai_cost_view_ddls(PROJECT, DATASET).items():
        assert "cost_usd" not in sql, name
        assert "usd" not in sql.lower().replace("cost_status", ""), name


def test_the_surface_inventory_is_published_into_the_warehouse() -> None:
    ddls = ai_cost_view_ddls(PROJECT, DATASET)
    sql = ddls["v_ai_cost_surface_coverage"]
    for cover in AI_COST_SURFACE_COVERAGE:
        assert cover.surface in sql, cover.surface
    assert AI_COST_STATUS_UNMETERED in sql
    assert AI_COST_STATUS_UNPRICEABLE in sql
    assert AI_COST_STATUS_METERED in sql


def test_v_ai_cost_left_joins_so_an_unmetered_surface_still_has_a_row() -> None:
    sql = ai_cost_view_ddls(PROJECT, DATASET)["v_ai_cost"]
    assert "LEFT JOIN" in sql
    assert "v_ai_cost_surface_coverage` c" in sql
    assert "ON u.service = c.service AND u.surface = c.surface" in sql


def test_the_cost_views_honour_the_reporting_timezone_and_default_to_utc() -> None:
    default = ai_cost_view_ddls(PROJECT, DATASET)
    assert default == ai_cost_view_ddls(PROJECT, DATASET, reporting_timezone="UTC")
    assert "DATE(occurred_at) AS day" in default["v_ai_token_usage"]
    zoned = ai_cost_view_ddls(PROJECT, DATASET, reporting_timezone="Asia/Kuala_Lumpur")
    assert "DATE(occurred_at, 'Asia/Kuala_Lumpur') AS day" in zoned["v_ai_token_usage"]
    with pytest.raises(ValueError):
        ai_cost_view_ddls(PROJECT, DATASET, reporting_timezone="Mars/Olympus_Mons")


def test_the_surface_inventory_matches_the_metered_reality() -> None:
    """Pins the exact inventory the metering work established, so a surface
    quietly appearing or disappearing fails here rather than in a client's
    cost figure."""
    by_status: dict[str, set[str]] = {}
    for cover in AI_COST_SURFACE_COVERAGE:
        by_status.setdefault(cover.status, set()).add(cover.surface)
    assert by_status[AI_COST_STATUS_METERED] == {
        "assist.suggest",
        "assist.copilot",
        "assist.translate",
        "chat.transcribe",
        "phone.classify",
    }
    assert by_status[AI_COST_STATUS_UNPRICEABLE] == {"embed"}
    assert by_status[AI_COST_STATUS_UNMETERED] == {"chat.turn", "phone.live", "orchestrator"}


# ---------------------------------------------------------------------------
# Reachability: the route must exist on a real router, and be gated
# ---------------------------------------------------------------------------


class _FakeUsagePort:
    def __init__(self, rows: list[TokenUsageAggregateRow] | None = None) -> None:
        self.rows = rows if rows is not None else [_row()]

    async def fetch_token_usage(
        self, period: PeriodRange | None
    ) -> tuple[list[TokenUsageAggregateRow], bool]:
        self.period = period
        return self.rows, True

    async def fetch_conversation_count(self, period: PeriodRange | None) -> int | None:
        return 40


def _client(enabled: bool = True, key: str = "secret") -> TestClient:
    class S:
        metrics_api_key = key
        ai_cost_reporting_enabled = enabled

    app = FastAPI()
    app.include_router(
        build_metrics_insights_router(
            MockMetricsQuery(),
            S(),
            ai_cost_port=_FakeUsagePort(),
            price_table=_FakePrices(),
        )
    )
    return TestClient(app)


def test_the_endpoint_is_mounted_on_the_insights_router() -> None:
    class S:
        metrics_api_key = "secret"
        ai_cost_reporting_enabled = True

    router = build_metrics_insights_router(MockMetricsQuery(), S())
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/metrics/ai-cost" in paths


def test_the_endpoint_requires_the_metrics_key() -> None:
    assert _client().get("/metrics/ai-cost").status_code == 401


def test_the_endpoint_is_off_unless_the_flag_is_on() -> None:
    res = _client(enabled=False).get("/metrics/ai-cost", headers={"x-api-key": "secret"})
    assert res.status_code == 404


def test_the_endpoint_returns_the_partial_shape_not_a_total() -> None:
    body = _client().get("/metrics/ai-cost", headers={"x-api-key": "secret"}).json()
    assert "total" not in body
    assert body["priced_subtotal_usd"] is not None
    assert body["conversations"] == 40
    assert body["completeness"]["is_complete"] is False
    assert body["currency"] == "USD"
