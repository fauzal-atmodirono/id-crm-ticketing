from types import SimpleNamespace

from logisly_agents.config import get_settings
from logisly_agents.uc3 import tools
from logisly_agents.uc3.context import read_context
from logisly_agents.uc3.tracker import MockTracker


def _ctx():
    return SimpleNamespace(state={})


def _tracker(state):
    uc3 = get_settings().data_dir / "uc3"
    c = read_context(uc3)
    return MockTracker(state, uc3 / "github" / "issues_snapshot.json", c["conventions"], c["calendar"])


def test_write_error_is_dict():
    ctx = _ctx()
    res = tools.close_issue(ctx, "driver-app#999", "duplicate")
    assert "error" in res


def test_unknown_ref_and_bad_create_leave_state_unchanged():
    ctx = _ctx()
    assert "error" in tools.get_issue(ctx, "driver-app#999")
    before = tools.search_issues(ctx, state="all")["issues"]
    res = tools.create_issue(ctx, "driver-app", "[driver-app] Tes", "x", ["type:feature"], "Sprint 26.23", [])
    assert "error" in res
    assert tools.search_issues(ctx, state="all")["issues"] == before


def test_save_document():
    ctx = _ctx()
    out = tools.save_document(ctx, "prd", "# PRD\nPertanyaan terbuka")
    assert out == {"name": "prd", "chars": len("# PRD\nPertanyaan terbuka")}
    assert ctx.state["uc3:docs"]["prd"].startswith("# PRD")
    tools.save_document(ctx, "lain", "x")
    assert set(ctx.state["uc3:docs"]) == {"prd", "lain"}
    assert "error" in tools.save_document(ctx, "", "x")


def test_context_tools():
    assert "prd_template.md" in tools.get_templates()["templates"]
    assert "repos" in tools.get_conventions()["conventions"]
    team = tools.get_team()
    assert "roster" in team and "calendar" in team


def test_writes_hit_tracker_and_milestone_empty_is_none():
    ctx = _ctx()
    res = tools.create_issue(
        ctx, "driver-app", "[driver-app] Tes tool", "isi",
        ["type:feature", "area:driver-app", "priority:P2"], "", [],
    )
    assert res.get("ref", "").startswith("driver-app#"), res
    assert tools.get_issue(ctx, res["ref"])["issue"]["milestone"] is None
    assert "error" not in tools.update_issue(ctx, res["ref"], add_labels=["sp:3"], milestone="Sprint 26.23")
    assert "error" not in tools.comment_issue(ctx, res["ref"], "halo")
    assert tools.close_issue(ctx, "driver-app#148", "duplicate", "dup")["state"] == "closed"
    assert _tracker(ctx.state).get("driver-app#148")["state"] == "closed"


def test_search_maps_state():
    ctx = _ctx()
    assert all(i["state"] == "open" for i in tools.search_issues(ctx)["issues"])
    assert tools.search_issues(ctx, text="#zzzz-none")["issues"] == []


def test_capacity_tool():
    out = tools.capacity_check([{"assignee": "rizky-r", "sp": 3, "sprint": "Sprint 26.23"}])
    assert out["teams"]["mobile"]["proposed"] == 3
    assert "error" in tools.capacity_check([{"assignee": "x"}])


def test_context_tools_are_json_safe():
    import json

    for out in (tools.get_conventions(), tools.get_team(), tools.get_templates()):
        json.dumps(out)
