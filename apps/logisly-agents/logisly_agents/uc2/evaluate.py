"""Deterministic UC2 quotation comparison (port of gen_uc2.evaluate)."""

from __future__ import annotations

from datetime import date

from logisly_agents.uc2.pricing import PricingRepo
from logisly_agents.uc2.rules import Rules
from logisly_agents.uc2.schemas import Tender


def baseline_periods(as_of: date, months: int = 3) -> tuple[str, ...]:
    """The full calendar months before ``as_of``'s month, oldest first."""
    idx = as_of.year * 12 + as_of.month - 1
    out = []
    for i in range(months, 0, -1):
        y, m = divmod(idx - i, 12)
        out.append(f"{y:04d}-{m + 1:02d}")
    return tuple(out)


def _idr(n: float) -> str:
    return "Rp " + f"{int(round(n)):,}".replace(",", ".")


def _round_50k(x: float) -> int:
    return int(round(x / 50_000) * 50_000)


def _ask(issues: list[str]) -> str:
    asks = []
    if "expired_validity" in issues:
        asks.append("perpanjang masa berlaku dan konfirmasi ulang harga")
    if "incomplete" in issues:
        asks.append("lengkapi termin pembayaran dan masa berlaku")
    if "truck_type_mismatch" in issues:
        asks.append("ajukan ulang dengan armada sesuai tender")
    if "transporter_unknown" in issues:
        asks.append("verifikasi data transporter sebelum dinilai")
    return "; ".join(asks)


def compare(tender: Tender, quotes: list[dict], repo: PricingRepo, rules: Rules, as_of: date) -> dict:
    periods = baseline_periods(as_of, rules.baseline.window_months)
    fl = rules.flags
    live = [q for q in quotes if not q.get("superseded_by")]
    superseded = {q["quote_id"]: q["superseded_by"] for q in quotes if q.get("superseded_by")}

    # Tender lanes keyed T1..Tn; one lane id may serve exactly one tender line.
    spec: dict[str, dict] = {}
    by_lane: dict[str, str] = {}
    for i, tl in enumerate(tender.lanes, 1):
        key = f"T{i}"
        lane_id = repo.lane_for_route(tl.origin, tl.destination)
        truck = repo.truck_code(tl.truck_type)
        spec[key] = {"line": tl, "lane_id": lane_id, "truck": truck, "rows": []}
        if lane_id and lane_id not in by_lane:
            by_lane[lane_id] = key

    unmatched: list[dict] = []
    perf_cache: dict[str, float | None] = {}
    for q in live:
        tr = repo.transporter_by_name(q["transporter"])
        tid = tr["transporter_id"] if tr else None
        if tid not in perf_cache:
            perf_cache[tid] = repo.on_time(tid) if tid else None
        ot = perf_cache[tid]
        doc_date = date.fromisoformat(q["document_date"]) if q.get("document_date") else None
        valid_to = date.fromisoformat(q["valid_until"]) if q.get("valid_until") else None
        pay = q.get("payment_term_days")
        for ln in q["lines"]:
            lane_id = repo.lane_for_route(ln["origin"], ln["destination"])
            key = by_lane.get(lane_id) if lane_id else None
            if key is None:
                unmatched.append(
                    {
                        "quote_id": q["quote_id"],
                        "transporter": q["transporter"],
                        "origin": ln["origin"],
                        "destination": ln["destination"],
                        "truck_type": ln["truck_type"],
                        "price_idr": ln["price_idr"],
                    }
                )
                continue
            sp = spec[key]
            req_truck = sp["truck"]
            line_truck = repo.truck_code(ln["truck_type"]) or ln["truck_type"]
            price: float = ln["price_idr"]
            issues: list[str] = []
            notes: list[str] = []
            if ln["unit"] == "ton":
                cap = repo.capacity_ton(req_truck)
                price = price * cap
                notes.append(f"dinormalisasi dari {_idr(ln['price_idr'])}/ton x {cap:g} ton")
            if ln.get("excludes_toll"):
                toll = repo.toll(sp["lane_id"], req_truck)
                price += toll
                notes.append(f"ditambah estimasi tol {_idr(toll)} (belum termasuk tol)")
            if line_truck != req_truck:
                issues.append("truck_type_mismatch")
            if pay is None or valid_to is None:
                issues.append("incomplete")
            if valid_to:
                if valid_to < as_of:
                    issues.append("expired_validity")
                elif doc_date and (valid_to - doc_date).days < fl.min_validity_days:
                    notes.append(f"masa berlaku < {fl.min_validity_days} hari")
            base = repo.baseline(sp["lane_id"], req_truck, periods)
            delta = (price - base) / base * 100 if base else 0.0
            flags: list[str] = []
            if delta > fl.above_baseline_pct:
                flags.append("above_baseline")
            if delta < -fl.below_baseline_pct:
                flags.append("below_baseline_check_hidden_cost")
            if tid is None:
                issues.append("transporter_unknown")
                notes.append("transporter tidak ada di data master, on-time tidak diketahui")
            elif ot < fl.min_on_time_pct:
                flags.append("on_time_below_threshold")
            if pay is not None and pay < fl.min_payment_term_days:
                flags.append("payment_term_too_short")
            sp["rows"].append(
                {
                    "quote_id": q["quote_id"],
                    "transporter": q["transporter"],
                    "truck_type": line_truck,
                    "quoted": ln["price_idr"],
                    "unit": ln["unit"],
                    "normalised_per_trip": int(price),
                    "vs_baseline_pct": round(delta, 1),
                    "on_time_pct": ot,
                    "payment_term_days": pay,
                    "valid_to": q.get("valid_until"),
                    "blocking_issues": issues,
                    "flags": flags,
                    "notes": notes,
                }
            )

    customer = repo.customer_by_name(tender.customer)
    sc = rules.scoring
    lanes: dict[str, dict] = {}
    for key, sp in spec.items():
        rows = sp["rows"]
        eligible = [r for r in rows if not r["blocking_issues"]]
        best = None
        if eligible:
            lo = min(r["normalised_per_trip"] for r in eligible)
            hi = max(r["normalised_per_trip"] for r in eligible)
            for r in eligible:
                p = 1.0 if hi == lo else (hi - r["normalised_per_trip"]) / (hi - lo)
                o = min(1.0, max(0.0, ((r["on_time_pct"] or 0.0) - 80) / 20))
                c = max(0.0, 1.0 - 0.5 * len([f for f in r["flags"] if f != "above_baseline"]))
                r["score"] = round(sc.price * p + sc.on_time * o + sc.compliance * c, 3)
            best = max(eligible, key=lambda r: r["score"])
        buy = best["normalised_per_trip"] if best else None
        sell = _round_50k(buy / (1 - rules.margin.target_pct / 100)) if buy else None
        cur = None
        if customer and sp["lane_id"] and sp["truck"]:
            cur = repo.contract_rate(customer["customer_id"], sp["lane_id"], sp["truck"])
        tl = sp["line"]
        trips = tl.trips_per_month
        base = repo.baseline(sp["lane_id"], sp["truck"], periods) if sp["lane_id"] and sp["truck"] else None
        lanes[key] = {
            "lane": f"{tl.origin} - {tl.destination}",
            "truck_type": tl.truck_type,
            "trips_per_month": trips,
            "baseline_median_90d": int(base) if base is not None else None,
            "quotes": sorted(rows, key=lambda r: r["normalised_per_trip"]),
            "recommended": best["quote_id"] if best else None,
            "recommended_transporter": best["transporter"] if best else None,
            "buy_per_trip": buy,
            f"proposed_sell_per_trip_at_{rules.margin.target_pct:g}pct_margin": sell,
            "current_contract_sell": cur["sell_rate_idr"] if cur else None,
            "monthly_buy_total": buy * trips if buy else None,
            "follow_up": [
                {
                    "quote_id": r["quote_id"],
                    "transporter": r["transporter"],
                    "ask": _ask(r["blocking_issues"]),
                }
                for r in rows
                if r["blocking_issues"]
            ],
        }
    return {"lanes": lanes, "unmatched_lines": unmatched, "superseded": superseded}
