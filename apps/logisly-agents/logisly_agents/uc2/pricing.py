"""Read-only access to the pricing database (SQLite demo; SQL Server in production)."""

from __future__ import annotations

import re
import sqlite3
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_ALIASES = ("jakarta utara", "tanjung priok")
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def _head(text: str) -> str:
    """Drop a trailing ", <regency>" before normalising."""
    return _norm(text.split(",")[0])


def _company(name: str) -> str:
    n = _norm(name)
    return re.sub(r"^(pt|cv)\s+", "", n)


def company_key(name: str) -> str:
    """Normalised company name: punctuation and a leading PT/CV dropped, lower case."""
    return _company(name)


def _matches(lane_part: str, wanted: str) -> bool:
    return bool(lane_part) and bool(wanted) and (
        lane_part == wanted or wanted in lane_part or lane_part in wanted
    )


class PricingRepo:
    def __init__(self, db_path: Path) -> None:
        self._con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lanes = [dict(r) for r in self._con.execute("SELECT * FROM lanes")]
        self._trucks = [dict(r) for r in self._con.execute("SELECT * FROM truck_types")]

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        return [dict(r) for r in self._con.execute(sql, params)]

    def _one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    # --- lookups ---------------------------------------------------------

    def _lane_hits(self, origin: str, destination: str) -> list[str]:
        o, d = _head(origin), _head(destination)
        return [
            lane["lane_id"]
            for lane in self._lanes
            if _matches(_norm(lane["origin"]), o) and _matches(_norm(lane["destination"]), d)
        ]

    def lane_for_route(self, origin: str, destination: str) -> str | None:
        hits = self._lane_hits(origin, destination)
        if not hits:
            def alias(t: str) -> str:
                n = _head(t)
                return "jakarta" if n in _ALIASES else n

            hits = self._lane_hits(alias(origin), alias(destination))
        return hits[0] if len(hits) == 1 else None

    def truck_code(self, name: str) -> str | None:
        def squash(text: str) -> str:
            return re.sub(r"[\s_]+", "", text.lower())

        key = squash(name)
        for t in self._trucks:
            if key in (squash(t["name"]), squash(t["truck_type"])):
                return t["truck_type"]
        return None

    def transporter_by_name(self, name: str) -> dict | None:
        key = _company(name)
        for t in self._rows("SELECT * FROM transporters"):
            if _company(t["name"]) == key:
                return t
        return None

    def customer_by_name(self, name: str) -> dict | None:
        key = _company(name)
        for c in self._rows("SELECT * FROM customers"):
            if _company(c["name"]) == key:
                return c
        return None

    # --- numbers ---------------------------------------------------------

    def baseline(self, lane_id: str, truck: str, periods: Sequence[str]) -> float | None:
        if not periods:
            return None
        marks = ",".join("?" * len(periods))
        rates = [
            r["rate_idr"]
            for r in self._rows(
                f"SELECT rate_idr FROM rate_history WHERE lane_id=? AND truck_type=? AND period IN ({marks})",
                [lane_id, truck, *periods],
            )
        ]
        return float(statistics.median(rates)) if rates else None

    def on_time(self, transporter_id: str) -> float:
        row = self._one(
            "SELECT avg(on_time_pct) AS v FROM transporter_performance WHERE transporter_id=?",
            [transporter_id],
        )
        return round(row["v"], 1) if row and row["v"] is not None else 0.0

    def toll(self, lane_id: str, truck: str) -> float:
        row = self._one(
            "SELECT l.base_toll_idr * t.toll_factor AS v FROM lanes l, truck_types t "
            "WHERE l.lane_id=? AND t.truck_type=?",
            [lane_id, truck],
        )
        return float(row["v"]) if row and row["v"] is not None else 0.0

    def capacity_ton(self, truck: str) -> float:
        row = self._one("SELECT capacity_ton FROM truck_types WHERE truck_type=?", [truck])
        return float(row["capacity_ton"]) if row else 0.0

    def contract_rate(self, customer_id: str, lane_id: str, truck: str) -> dict | None:
        return self._one(
            "SELECT * FROM customer_contract_rates WHERE customer_id=? AND lane_id=? AND truck_type=? "
            "ORDER BY valid_to DESC LIMIT 1",
            [customer_id, lane_id, truck],
        )

    def price_history(self, lane_id: str, truck: str) -> list[dict]:
        by_period: dict[str, list[int]] = {}
        for r in self._rows(
            "SELECT period, rate_idr FROM rate_history WHERE lane_id=? AND truck_type=? ORDER BY period",
            [lane_id, truck],
        ):
            by_period.setdefault(r["period"], []).append(r["rate_idr"])
        return [
            {
                "period": p,
                "median": float(statistics.median(v)),
                "min": min(v),
                "max": max(v),
                "count": len(v),
            }
            for p, v in sorted(by_period.items())
        ]

    # --- free SQL --------------------------------------------------------

    def run_select(self, sql: str, limit: int = 200) -> dict:
        stmt = _COMMENT_RE.sub(" ", sql).strip()
        if stmt.endswith(";"):
            stmt = stmt[:-1].strip()
        if not stmt:
            return {"error": "Query kosong."}
        if ";" in stmt:
            return {"error": "Hanya satu pernyataan SELECT yang diizinkan."}
        if stmt.split(None, 1)[0].lower() not in ("select", "with"):
            return {"error": "Hanya pernyataan SELECT atau WITH yang diizinkan."}
        try:
            cur = self._con.execute(stmt)
            rows = cur.fetchmany(limit)
            return {"columns": [c[0] for c in cur.description], "rows": [list(r) for r in rows]}
        except sqlite3.Error as exc:
            return {"error": str(exc)}
