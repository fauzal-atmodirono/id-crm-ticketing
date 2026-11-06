from types import SimpleNamespace

import pytest

from logisly_agents.config import get_settings
from logisly_agents.uc2 import store, tools
from logisly_agents.uc2.pricing import PricingRepo

from .helpers import build, tender


@pytest.fixture(scope="module")
def repo():
    return PricingRepo(get_settings().data_dir / "uc2" / "pricing" / "pricing.db")


def _ctx():
    return SimpleNamespace(state={})


def test_tools_end_to_end_without_model(repo):
    ctx = _ctx()
    built, ids = build(repo)
    res = tools.record_tender(ctx, tender())
    assert res["status"] == "ok"
    for q in store.list_quotations(built):
        q = {k: v for k, v in q.items() if k not in ("quote_id", "superseded_by")}
        out = tools.record_quotation(ctx, q)
        assert "error" not in out, out
    assert len(ctx.state["uc2:quotations"]) == 7
    res = tools.compare_quotes(ctx)
    assert res["lanes"]["T4"]["recommended_transporter"] == "PT Prima Fleet Nusantara"
    assert res["lanes"]["T4"]["recommended"] == ids["Q-PFN-1"]
    listed = tools.list_quotations(ctx, transporter="trans cahaya")
    assert {q["quote_id"] for q in listed["quotations"]} == {ids["Q-TCL-1"], ids["Q-TCL-2"]}


def test_record_tender_rejects_unresolvable_route():
    ctx = _ctx()
    t = tender().model_copy(deep=True)
    t.lanes[0].truck_type = "Pesawat Jet"
    res = tools.record_tender(ctx, t)
    assert "error" in res and res["unresolved"]
    assert "uc2:tender" not in ctx.state


def test_record_quotation_validation_error_is_dict():
    res = tools.record_quotation(_ctx(), {"transporter": "X"})
    assert "error" in res


def test_compare_without_tender():
    assert "error" in tools.compare_quotes(_ctx())


def test_query_sql_error_is_dict():
    assert "error" in tools.query_pricing_sql("DROP TABLE lanes")
    ok = tools.query_pricing_sql("SELECT 1 AS x")
    assert ok["rows"] == [[1]]


def test_price_history_and_decision():
    res = tools.price_history("Cikarang", "Surabaya", "Fuso Wingbox")
    assert "error" in res or res["history"]
    bad = tools.price_history("Nowhere", "Surabaya", "Fuso Wingbox")
    assert "error" in bad
    ctx = _ctx()
    out = tools.record_decision(ctx, "T1", "Q1", "approve", "ok")
    assert out["status"] == "ok" and ctx.state["uc2:decisions"][0]["lane"] == "T1"
    assert "error" in tools.record_decision(ctx, "T1", "Q1", "maybe")
