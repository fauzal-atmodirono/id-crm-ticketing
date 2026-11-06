import json

from logisly_agents.config import get_settings
from logisly_agents.uc3.capacity import capacity_check
from logisly_agents.uc3.context import read_context

KEYS = {f"A{i}" for i in range(1, 6)} | {f"B{i}" for i in range(1, 6)} | {"P1", "P2", "P3"}


def _ctx():
    return read_context(get_settings().data_dir / "uc3")


def _items():
    data = json.loads((get_settings().data_dir / "uc3/expected/backlog.json").read_text())
    items = []
    for c in data["create"]:
        if c["key"] not in KEYS:
            continue
        sp = next(int(label[3:]) for label in c["labels"] if label.startswith("sp:"))
        items.append({"assignee": c["assignee"], "sp": sp, "sprint": c["milestone"]})
    items.append({"assignee": "bayu-p", "sp": 5, "sprint": "Sprint 26.23"})
    items.append({"assignee": "laras-ayu", "sp": 3, "sprint": "Sprint 26.24"})
    return items


def _run(items):
    ctx = _ctx()
    return capacity_check(items, ctx["roster"], ctx["calendar"])


def test_capacity_matches_report():
    r = _run(_items())
    assert r["window"] == ["Sprint 26.22", "Sprint 26.23", "Sprint 26.24"]
    t = r["teams"]
    assert t["mobile"] == {
        "capacity": 69,
        "committed": 24,
        "free": 45,
        "proposed": 31,
        "utilisation_pct": 69,
    }
    assert t["backend"] == {
        "capacity": 69,
        "committed": 18,
        "free": 51,
        "proposed": 28,
        "utilisation_pct": 55,
    }
    assert t["frontend"] == {
        "capacity": 30,
        "committed": 13,
        "free": 17,
        "proposed": 11,
        "utilisation_pct": 65,
    }
    json.dumps(r)


def test_phase2_items_after_window():
    r = _run([{"assignee": "bayu-p", "sp": 8, "sprint": "Sprint 27.02"}])
    assert r["after_window"] == [{"assignee": "bayu-p", "sp": 8, "sprint": "Sprint 27.02"}]
    assert r["teams"]["mobile"]["proposed"] == 0


def test_assignee_buckets():
    r = _run(
        [
            {"assignee": "tegar-qa", "sp": 2, "sprint": "Sprint 26.23"},
            {"assignee": "ghost", "sp": 1, "sprint": "Sprint 26.23"},
        ]
    )
    assert r["no_team"] == [{"assignee": "tegar-qa", "sp": 2, "sprint": "Sprint 26.23"}]
    assert r["unknown_assignees"] == ["ghost"]


def test_leave_inside_window_is_prorated():
    ctx = _ctx()
    roster = json.loads(json.dumps(ctx["roster"], default=str))
    for p in roster["people"]:
        if p["github"] == "rizky-r":
            p["leave"] = [{"from": "2026-11-23", "to": "2026-11-27"}]  # 5 of 10 days in 26.23
    r = capacity_check([], roster, ctx["calendar"])
    assert r["teams"]["mobile"]["capacity"] == 69 - 6.5
