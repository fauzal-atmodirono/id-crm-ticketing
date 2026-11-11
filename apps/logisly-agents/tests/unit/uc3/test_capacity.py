import json

from logisly_agents.config import get_settings
from logisly_agents.uc3.capacity import capacity_check
from logisly_agents.uc3.context import read_context


def _ctx():
    return read_context(get_settings().data_dir / "uc3")


def _items():
    data = json.loads((get_settings().data_dir / "uc3/expected/backlog.json").read_text())
    return [
        {"assignee": c["assignee"], "sp": c["story_points"], "sprint": c["sprint"]}
        for c in data["creates"]
        if c["type"] == "Story"
    ] + [{"assignee": "rizky-r", "sp": 3, "sprint": "Sprint 26.23"}]  # LOG-3412 update


def _run(items):
    ctx = _ctx()
    return capacity_check(items, ctx["roster"], ctx["calendar"])


def test_capacity_matches_report():
    r = _run(_items())
    assert r["window"] == ["Sprint 26.22", "Sprint 26.23", "Sprint 26.24"]
    t = r["teams"]
    assert t["mobile"] == {
        "capacity": 69,
        "committed": 0,
        "free": 69,
        "proposed": 3,
        "utilisation_pct": 4,
        "overloaded": False,
    }
    assert t["backend"] == {
        "capacity": 69,
        "committed": 8,
        "free": 61,
        "proposed": 30,
        "utilisation_pct": 49,
        "overloaded": False,
    }
    assert t["frontend"] == {
        "capacity": 30,
        "committed": 8,
        "free": 22,
        "proposed": 7,
        "utilisation_pct": 32,
        "overloaded": False,
    }
    assert [i["sp"] for i in r["after_window"]] == [5]  # S11 Shopee SOP, Sprint 27.01
    json.dumps(r)


def test_phase2_items_after_window():
    r = _run([{"assignee": "bayu-p", "sp": 8, "sprint": "Sprint 27.02"}])
    assert r["after_window"] == [{"assignee": "bayu-p", "sp": 8, "sprint": "Sprint 27.02"}]
    assert r["teams"]["mobile"]["proposed"] == 0


def test_assignee_buckets():
    r = _run(
        [
            {"assignee": "tegar-w", "sp": 2, "sprint": "Sprint 26.23"},
            {"assignee": "ghost", "sp": 1, "sprint": "Sprint 26.23"},
        ]
    )
    assert r["no_team"] == [{"assignee": "tegar-w", "sp": 2, "sprint": "Sprint 26.23"}]
    assert r["unknown_assignees"] == ["ghost"]


def test_leave_inside_window_is_prorated():
    ctx = _ctx()
    roster = json.loads(json.dumps(ctx["roster"], default=str))
    for p in roster["people"]:
        if p["id"] == "rizky-r":
            p["leave"] = [{"from": "2026-11-23", "to": "2026-11-27"}]  # 5 of 10 days in 26.23
    r = capacity_check([], roster, ctx["calendar"])
    assert r["teams"]["mobile"]["capacity"] == 69 - 6.5


def test_overloaded_team_reports_none_not_zero():
    ctx = _ctx()
    roster = json.loads(json.dumps(ctx["roster"], default=str))
    names = [p["name"] for p in roster["people"]]
    roster["committed"] = {s["name"]: {n: 999 for n in names} for s in ctx["calendar"]["sprints"]}
    r = capacity_check([{"assignee": "rizky-r", "sp": 3, "sprint": "Sprint 26.23"}], roster, ctx["calendar"])
    m = r["teams"]["mobile"]
    assert m["free"] <= 0 and m["proposed"] == 3
    assert m["utilisation_pct"] is None and m["overloaded"] is True
    assert "overloaded" not in r["teams"]["backend"] or r["teams"]["backend"]["proposed"] == 0


def test_unknown_sprint_is_separate_from_after_window():
    r = _run(
        [
            {"assignee": "bayu-p", "sp": 5, "sprint": "Sprint 99.99"},
            {"assignee": "bayu-p", "sp": 8, "sprint": "Sprint 27.02"},
        ]
    )
    assert r["unknown_sprints"] == [{"assignee": "bayu-p", "sp": 5, "sprint": "Sprint 99.99"}]
    assert r["after_window"] == [{"assignee": "bayu-p", "sp": 8, "sprint": "Sprint 27.02"}]
    assert r["teams"]["mobile"]["proposed"] == 0


def test_overloaded_when_proposed_exceeds_free():
    ctx = _ctx()
    free = capacity_check([], ctx["roster"], ctx["calendar"])["teams"]["mobile"]["free"]
    r = _run([{"assignee": "bayu-p", "sp": free + 1, "sprint": "Sprint 26.23"}])
    m = r["teams"]["mobile"]
    assert m["overloaded"] is True
    assert m["utilisation_pct"] == round((free + 1) / free * 100)
