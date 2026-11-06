import json
from datetime import date

import pytest

from logisly_agents.uc2 import store
from logisly_agents.uc2.evaluate import baseline_periods, compare
from logisly_agents.uc2.pricing import PricingRepo
from logisly_agents.uc2.rules import load_rules
from logisly_agents.uc2.schemas import Quotation, QuoteLine

from .helpers import AS_OF, DATA, PERIODS, build, tender


@pytest.fixture(scope="module")
def repo():
    return PricingRepo(DATA / "uc2" / "pricing" / "pricing.db")


@pytest.fixture(scope="module")
def rules():
    return load_rules(DATA / "uc2" / "business_rules.yaml")


def _expected():
    return json.loads((DATA / "uc2" / "expected" / "recommendation.json").read_text())


def _run(repo, rules, state):
    return compare(tender(), store.list_quotations(state), repo, rules, as_of=AS_OF)


def _check(res, ids, exp):
    for key in ("T1", "T2", "T3", "T4"):
        got, want = res["lanes"][key], exp["lanes"][key]
        for k in (
            "recommended",
            "buy_per_trip",
            "proposed_sell_per_trip_at_15pct_margin",
            "current_contract_sell",
        ):
            expect = ids[want[k]] if k == "recommended" and want[k] else want[k]
            assert got[k] == expect, (key, k)
        by_id = {q["quote_id"]: q for q in got["quotes"]}
        assert len(by_id) == len(want["quotes"]), key
        for wq in want["quotes"]:
            gq = by_id[ids[wq["quote_id"]]]
            for k in ("normalised_per_trip", "blocking_issues", "vs_baseline_pct"):
                assert gq[k] == wq[k], (key, wq["quote_id"], k)
            assert sorted(gq["flags"]) == sorted(wq["flags"]), (key, wq["quote_id"])


def test_matches_answer_key(repo, rules):
    state, ids = build(repo)
    res = _run(repo, rules, state)
    _check(res, ids, _expected())
    assert res["superseded"] == {ids["Q-TCL-1"]: ids["Q-TCL-2"]}
    assert res["unmatched_lines"] == []
    assert set(res["lanes"]["T1"]) == set(_expected()["lanes"]["T1"])
    assert set(res["lanes"]["T1"]["quotes"][0]) >= set(_expected()["lanes"]["T1"]["quotes"][1]) - {"score"}


def test_unmatched_line_is_reported(repo, rules):
    extra = QuoteLine(origin="Bogor", destination="Surabaya", truck_type="Fuso Wingbox", price_idr=9_000_000)
    state, ids = build(repo, extra)
    res = _run(repo, rules, state)
    assert len(res["unmatched_lines"]) == 1
    u = res["unmatched_lines"][0]
    assert u["quote_id"] == ids["Q-LJE-1"] and (u["origin"], u["destination"]) == ("Bogor", "Surabaya")
    _check(res, ids, _expected())


def test_rules_loaded(rules):
    assert rules.scoring.price == 0.5
    assert rules.margin.target_pct == 15


def test_baseline_periods():
    assert baseline_periods(AS_OF) == PERIODS
    assert baseline_periods(date(2026, 2, 1)) == ("2025-11", "2025-12", "2026-01")


def test_unknown_transporter_flagged(repo, rules):
    state, _ = build(repo)
    t = tender()
    lane = t.lanes[0]
    q = Quotation(
        transporter="PT Tidak Dikenal",
        document_name="x",
        document_date="2026-11-05",
        payment_term_days=30,
        valid_until="2026-12-31",
        lines=[
            QuoteLine(
                origin=lane.origin,
                destination=lane.destination,
                truck_type=lane.truck_type,
                price_idr=1_000_000,
            )
        ],
    )
    store.add_quotation(state, q)
    res = compare(t, store.list_quotations(state), repo, rules, as_of=AS_OF)
    row = next(r for r in res["lanes"]["T1"]["quotes"] if r["transporter"] == "PT Tidak Dikenal")
    assert "transporter_unknown" in row["blocking_issues"] and row["on_time_pct"] is None
    assert res["lanes"]["T1"]["recommended"] != row["quote_id"]


@pytest.mark.parametrize("unit", ["kg", "other"])
def test_unit_not_normalisable_blocks(repo, rules, unit):
    state, ids = build(repo)
    t = tender()
    lane = t.lanes[0]
    q = Quotation(
        transporter="PT Garuda Angkut Sejahtera",
        document_name="per_kg",
        document_date="2026-11-08",
        payment_term_days=30,
        valid_until="2026-12-31",
        lines=[
            QuoteLine(
                origin=lane.origin,
                destination=lane.destination,
                truck_type=lane.truck_type,
                price_idr=350,
                unit=unit,
            )
        ],
    )
    added = store.add_quotation(state, q)
    res = compare(t, store.list_quotations(state), repo, rules, as_of=AS_OF)
    row = next(r for r in res["lanes"]["T1"]["quotes"] if r["quote_id"] == added["quote_id"])
    assert "unit_not_normalisable" in row["blocking_issues"]
    assert res["lanes"]["T1"]["recommended"] != row["quote_id"]
    ask = next(f["ask"] for f in res["lanes"]["T1"]["follow_up"] if f["quote_id"] == row["quote_id"])
    assert "ajukan ulang harga per trip" in ask
