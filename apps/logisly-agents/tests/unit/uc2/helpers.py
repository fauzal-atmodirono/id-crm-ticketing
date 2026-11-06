"""Shared UC2 fixtures: the tender and quotations built from the generator."""

import sys
from datetime import date

from logisly_agents.config import get_settings
from logisly_agents.uc2 import store
from logisly_agents.uc2.schemas import Quotation, QuoteLine, Tender, TenderLine

DATA = get_settings().data_dir
sys.path.insert(0, str(DATA))
import common  # noqa: E402
import gen_uc2  # noqa: E402

AS_OF = date(2026, 11, 9)
PERIODS = ("2026-08", "2026-09", "2026-10")


def route(lane_id):
    lane = common.LANE[lane_id]
    return lane.origin, lane.destination


def tender():
    lanes = []
    for lane_id, truck, trips in gen_uc2.TENDER.values():
        o, d = route(lane_id)
        lanes.append(
            TenderLine(origin=o, destination=d, truck_type=common.TRUCK[truck].name, trips_per_month=trips)
        )
    return Tender(reference=gen_uc2.RFQ_ID, customer=gen_uc2.CUSTOMER, lanes=lanes)


def build(repo, extra_line=None):
    base = {k: repo.baseline(lane, truck, PERIODS) for k, (lane, truck, _) in gen_uc2.TENDER.items()}
    state: dict = {}
    ids = {}
    for q in gen_uc2.QUOTES:
        lines = []
        for ln in gen_uc2.quote_lines(q, base):
            o, d = route(ln["lane_id"])
            lines.append(
                QuoteLine(
                    origin=o,
                    destination=d,
                    truck_type=common.TRUCK[ln["truck_type"]].name,
                    price_idr=ln["price"],
                    unit=ln["unit"],
                    trips_per_month=ln["trips"],
                    excludes_toll=ln["key"] in q.get("toll_excluded", []),
                )
            )
        if extra_line and q["id"] == "Q-LJE-1":
            lines.append(extra_line)
        quo = Quotation(
            transporter=gen_uc2.TRANSPORTER[q["trp"]].name,
            document_name=q["id"],
            document_date=q["date"],
            is_revision=bool(q.get("revision_of")),
            payment_term_days=q["payment"],
            valid_until=q["valid_to"],
            lines=lines,
        )
        ids[q["id"]] = store.add_quotation(state, quo)["quote_id"]
    return state, ids
