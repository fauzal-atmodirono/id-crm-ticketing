import json
from datetime import date

from logisly_agents.uc2 import store
from logisly_agents.uc2.schemas import Quotation, QuoteLine, Tender, TenderLine


def _q(transporter="Trans Cahaya", d=date(2026, 11, 3), rev=False, price=1_000_000):
    return Quotation(
        transporter=transporter,
        document_name="q.pdf",
        document_date=d,
        is_revision=rev,
        lines=[QuoteLine(origin="A", destination="B", truck_type="CDD", price_idr=price)],
    )


def _tender():
    return Tender(
        reference="T1",
        customer="C",
        lanes=[TenderLine(origin="A", destination="B", truck_type="CDD", trips_per_month=5)],
    )


def test_duplicate_replaces():
    s: dict = {}
    r1 = store.add_quotation(s, _q())
    r2 = store.add_quotation(s, _q())
    assert r2["replaced"] is True
    assert r2["quote_id"] == r1["quote_id"] == "Q1"
    assert len(store.list_quotations(s)) == 1


def test_revision_supersedes():
    s: dict = {}
    r1 = store.add_quotation(s, _q())
    r2 = store.add_quotation(s, _q(d=date(2026, 11, 5), rev=True, price=900_000))
    assert r2["supersedes"] == r1["quote_id"]
    assert r2["replaced"] is False
    live = store.list_quotations(s, include_superseded=False)
    assert [x["quote_id"] for x in live] == [r2["quote_id"]]
    first = [x for x in store.list_quotations(s) if x["quote_id"] == r1["quote_id"]][0]
    assert first["superseded_by"] == r2["quote_id"]


def test_later_date_supersedes_and_case_insensitive():
    s: dict = {}
    store.add_quotation(s, _q(transporter=" trans cahaya "))
    r = store.add_quotation(s, _q(d=date(2026, 11, 6), price=1))
    assert r["supersedes"] == "Q1"


def test_older_doc_added_later_is_superseded():
    s: dict = {}
    store.add_quotation(s, _q(d=date(2026, 11, 5)))
    r = store.add_quotation(s, _q(d=date(2026, 11, 3), price=2))
    assert r["supersedes"] is None
    live = store.list_quotations(s, include_superseded=False)
    assert [x["quote_id"] for x in live] == ["Q1"]
    q2 = [x for x in store.list_quotations(s) if x["quote_id"] == "Q2"][0]
    assert q2["superseded_by"] == "Q1"


def test_earlier_dated_revision_loses():
    s: dict = {}
    store.add_quotation(s, _q(d=date(2026, 11, 5)))
    r = store.add_quotation(s, _q(d=date(2026, 11, 3), rev=True, price=2))
    assert r["supersedes"] is None
    assert [x["quote_id"] for x in store.list_quotations(s, include_superseded=False)] == ["Q1"]


def test_same_date_revision_wins_either_order():
    s: dict = {}
    store.add_quotation(s, _q(rev=True, price=1))
    r = store.add_quotation(s, _q(price=2))
    assert r["supersedes"] is None
    assert [x["quote_id"] for x in store.list_quotations(s, include_superseded=False)] == ["Q1"]
    s2: dict = {}
    store.add_quotation(s2, _q(price=1))
    r = store.add_quotation(s2, _q(rev=True, price=2))
    assert r["supersedes"] == "Q1"


def test_same_date_no_flag_later_added_wins():
    s: dict = {}
    store.add_quotation(s, _q(price=1))
    r = store.add_quotation(s, _q(price=2))
    assert r["supersedes"] == "Q1"
    assert [x["quote_id"] for x in store.list_quotations(s, include_superseded=False)] == ["Q2"]


def test_writes_reassign_whole_keys():
    class Stub(dict):
        def __init__(self):
            super().__init__()
            self.sets: list[str] = []

        def __setitem__(self, k, v):
            self.sets.append(k)
            super().__setitem__(k, v)

    s = Stub()
    store.add_quotation(s, _q())
    store.add_quotation(s, _q(d=date(2026, 11, 5), price=2))
    store.add_decision(s, "A-B", "Q2", "accept", "")
    assert s.sets == ["uc2:quotations", "uc2:quotations", "uc2:decisions"]


def test_tender_roundtrip():
    s: dict = {}
    assert store.get_tender(s) is None
    store.set_tender(s, _tender())
    assert store.get_tender(s) == _tender()


def test_state_is_json_safe():
    s: dict = {}
    store.set_tender(s, _tender())
    store.add_quotation(s, _q())
    store.add_quotation(s, _q(transporter="Other"))
    store.add_decision(s, "A-B", "Q1", "accept", "cheapest")
    json.dumps(dict(s))
    assert len(s["uc2:decisions"]) == 1
