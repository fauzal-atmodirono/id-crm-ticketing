"""Session-state store for the UC2 tender, quotations and decisions.

ADK ``State`` only persists on key assignment, so every write reads the value,
copies it, modifies the copy and re-assigns the whole key.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from logisly_agents.uc2.schemas import Quotation, Tender

TENDER_KEY = "uc2:tender"
QUOTES_KEY = "uc2:quotations"
DECISIONS_KEY = "uc2:decisions"

State = MutableMapping[str, Any]


def _tkey(name: str) -> str:
    return " ".join(name.lower().split())


def set_tender(state: State, tender: Tender) -> dict:
    dump = tender.model_dump(mode="json")
    state[TENDER_KEY] = dump
    return dump


def get_tender(state: State) -> Tender | None:
    raw = state.get(TENDER_KEY)
    return Tender.model_validate(raw) if raw else None


def _next_id(records: list[dict]) -> str:
    nums = [int(r["quote_id"][1:]) for r in records]
    return f"Q{max(nums, default=0) + 1}"


def add_quotation(state: State, q: Quotation) -> dict:
    records = [dict(r) for r in state.get(QUOTES_KEY, [])]
    dump = q.model_dump(mode="json")
    key = _tkey(q.transporter)

    for i, r in enumerate(records):
        if (
            _tkey(r["transporter"]) == key
            and r["document_date"] == dump["document_date"]
            and r["lines"] == dump["lines"]
        ):
            records[i] = {**dump, "quote_id": r["quote_id"], "superseded_by": r.get("superseded_by")}
            state[QUOTES_KEY] = records
            return {"quote_id": r["quote_id"], "replaced": True, "supersedes": None}

    quote_id = _next_id(records)
    prior_live = [
        r["quote_id"]
        for r in records
        if _tkey(r["transporter"]) == key and not r.get("superseded_by")
    ]
    records.append({**dump, "quote_id": quote_id, "superseded_by": None})

    # One live quote per transporter: newest document_date, then a revision
    # beats a non-revision, then the later-added record wins.
    group = [(i, r) for i, r in enumerate(records) if _tkey(r["transporter"]) == key]
    _, winner = max(group, key=lambda ir: (ir[1]["document_date"], ir[1]["is_revision"], ir[0]))
    for _, r in group:
        r["superseded_by"] = None if r is winner else winner["quote_id"]

    state[QUOTES_KEY] = records
    won = winner["quote_id"] == quote_id
    return {
        "quote_id": quote_id,
        "replaced": False,
        "supersedes": prior_live[-1] if won and prior_live else None,
    }


def list_quotations(state: State, include_superseded: bool = True) -> list[dict]:
    records = [dict(r) for r in state.get(QUOTES_KEY, [])]
    if include_superseded:
        return records
    return [r for r in records if not r.get("superseded_by")]


def add_decision(state: State, lane: str, quote_id: str, decision: str, note: str) -> dict:
    entry = {"lane": lane, "quote_id": quote_id, "decision": decision, "note": note}
    state[DECISIONS_KEY] = [*state.get(DECISIONS_KEY, []), entry]
    return entry
