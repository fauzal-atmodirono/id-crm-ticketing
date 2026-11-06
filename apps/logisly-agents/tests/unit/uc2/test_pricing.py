import json

import pytest

from logisly_agents.config import get_settings
from logisly_agents.uc2.pricing import PricingRepo

DATA = get_settings().data_dir


@pytest.fixture(scope="module")
def repo():
    return PricingRepo(DATA / "uc2" / "pricing" / "pricing.db")


@pytest.mark.parametrize(
    ("origin", "dest", "expected"),
    [
        ("Cikarang, Bekasi", "Surabaya", "LN-02"),
        ("Cikarang", "Surabaya", "LN-02"),
        ("Karawang", "Surabaya", "LN-03"),
        ("Jakarta", "Bandar Lampung", "LN-10"),
        ("Cibitung", "Bandung", "LN-04"),
        ("Bogor", "Surabaya", None),
    ],
)
def test_lane_matching(repo, origin, dest, expected):
    assert repo.lane_for_route(origin, dest) == expected


def test_jakarta_alias_only_without_exact(repo):
    assert repo.lane_for_route("Jakarta Utara", "Bandar Lampung") == "LN-10"
    assert repo.lane_for_route("Tanjung Priok", "Bandung") == "LN-05"


def test_baselines_match_answer_key(repo):
    key = json.loads((DATA / "uc2" / "expected" / "recommendation.json").read_text())
    cases = {
        "T1": ("LN-02", "FUSO_WB"),
        "T2": ("LN-01", "CDD_BOX"),
        "T3": ("LN-10", "TRONTON_WB"),
        "T4": ("LN-04", "CDE_BOX"),
    }
    for tn, (lane, truck) in cases.items():
        got = repo.baseline(lane, truck, ("2026-08", "2026-09", "2026-10"))
        assert got == key["lanes"][tn]["baseline_median_90d"], tn
    assert repo.baseline("LN-02", "FUSO_WB", ()) is None


def test_on_time(repo):
    assert repo.on_time("TRP-001") == 93.8
    assert repo.on_time("TRP-002") == 82.3


def test_toll_and_capacity(repo):
    assert repo.toll("LN-01", "CDD_BOX") == 975000
    assert repo.capacity_ton("FUSO_WB") == 8.0


def test_lookups(repo):
    assert repo.truck_code("fuso wingbox") == "FUSO_WB"
    assert repo.truck_code("FUSO_WB") == "FUSO_WB"
    assert repo.truck_code("kapal") is None
    assert repo.transporter_by_name("Mitra Armada Jaya")["transporter_id"] == "TRP-002"
    assert repo.transporter_by_name("pt. trans cahaya logistik")["transporter_id"] == "TRP-001"
    assert repo.transporter_by_name("nobody") is None
    assert repo.customer_by_name("PT Nusantara Agro Lestari")["customer_id"]
    assert repo.customer_by_name("zzz") is None


def test_contract_and_history(repo):
    cust = repo.customer_by_name("PT Nusantara Agro Lestari")
    row = repo.contract_rate(cust["customer_id"], "LN-01", "CDD_BOX")
    assert row and row["valid_to"] == "2026-11-30"
    assert repo.contract_rate(cust["customer_id"], "LN-02", "FUSO_WB") is None
    hist = repo.price_history("LN-02", "FUSO_WB")
    assert hist and {"period", "median", "min", "max", "count"} <= set(hist[0])
    assert hist == sorted(hist, key=lambda h: h["period"])


def test_run_select(repo):
    assert repo.run_select("SELECT count(*) FROM customers")["rows"] == [[600]]
    assert repo.run_select("WITH x AS (SELECT 1) SELECT * FROM x;")["rows"] == [[1]]
    assert len(repo.run_select("SELECT * FROM rate_history", limit=5)["rows"]) == 5
    assert "error" in repo.run_select("-- hi\nDELETE FROM lanes")


@pytest.mark.parametrize(
    "sql", ["DELETE FROM lanes", "select 1; drop table lanes", "PRAGMA journal_mode=WAL", ""]
)
def test_run_select_rejects_writes(repo, sql):
    assert "error" in repo.run_select(sql)
