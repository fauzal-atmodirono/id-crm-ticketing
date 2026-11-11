import json
from types import SimpleNamespace

from logisly_agents.config import get_settings
from logisly_agents.uc3 import tools
from logisly_agents.uc3.context import read_context
from logisly_agents.uc3.jira import MockJira

UC3 = get_settings().data_dir / "uc3"


def _ctx():
    return SimpleNamespace(state={})


def _jira(state):
    c = read_context(UC3)
    return MockJira(state, UC3 / "jira" / "issues_snapshot.json", c["conventions"], c["calendar"])


def _story(summary, **kw):
    return {
        "type": "Story",
        "summary": summary,
        "components": ["Platform"],
        "story_points": 3,
        "acceptance": ["a"],
        **kw,
    }


def _epic(summary="Checklist per milestone", **kw):
    return {"type": "Epic", "summary": summary, "components": ["Platform"], **kw}


def test_write_error_is_dict():
    res = tools.transition_issue(_ctx(), "LOG-9999", "Done")
    assert "error" in res


def test_unknown_key_and_bad_create_leave_state_unchanged():
    ctx = _ctx()
    assert "error" in tools.get_issue(ctx, "LOG-9999")
    before = tools.search_issues(ctx, status="all")["issues"]
    res = tools.create_issue(ctx, "Story", "[BE] As CT, I want x so that y", "d", ["a"], ["Platform"])
    assert "error" in res  # no story points
    assert tools.search_issues(ctx, status="all")["issues"] == before
    assert "error" in tools.search_issues(ctx, status="closed")


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
    assert "components" in tools.get_conventions()["conventions"]
    team = tools.get_team()
    assert "roster" in team and "calendar" in team


def test_context_tools_are_json_safe():
    for out in (tools.get_conventions(), tools.get_team(), tools.get_templates()):
        json.dumps(out)


def test_writes_hit_jira_and_empty_strings_are_none():
    ctx = _ctx()
    res = tools.create_issue(
        ctx, "Story", "[BE] As CT, I want a tool so that it works", "isi", ["a"], ["Platform"], ["pilot"], 3
    )
    assert res.get("key") == "LOG-3421", res
    issue = tools.get_issue(ctx, res["key"])["issue"]
    assert issue["sprint"] is None and issue["assignee"] is None and issue["epic"] is None
    assert "error" not in tools.update_issue(ctx, res["key"], add_labels=["phase-2"], sprint="Sprint 26.23")
    assert "error" not in tools.comment_issue(ctx, res["key"], "halo")
    out = tools.transition_issue(ctx, "LOG-3417", "Won't Do", comment="dup", duplicate_of="LOG-3412")
    assert out["resolution"] == "Duplicate"
    assert _jira(ctx.state).get("LOG-3417")["status"] == "Won't Do"


def test_search_maps_status():
    ctx = _ctx()
    assert all(i["status"] in ("To Do", "In Progress") for i in tools.search_issues(ctx)["issues"])
    assert tools.search_issues(ctx, text="#zzzz-none")["issues"] == []


def test_capacity_tool():
    out = tools.capacity_check([{"assignee": "rizky-r", "sp": 3, "sprint": "Sprint 26.23"}])
    assert out["teams"]["mobile"]["proposed"] == 3
    assert "error" in tools.capacity_check([{"assignee": "x"}])


def test_propose_validates_without_writing():
    ctx = _ctx()
    before = tools.search_issues(ctx, status="all")["issues"]
    res = tools.propose_changes(
        ctx,
        creates=[
            _story("[BE] As CT, I want one so that a", sprint="Sprint 26.23", assignee="fikri-h"),
            _story("[BE] As CT, I want two so that b", components=["Nope"]),
            _story("[BE] As CT, I want one so that a"),  # same summary as P1 inside the proposal
        ],
        updates=[{"key": "LOG-3412", "add_labels": ["pilot"], "comment": "terkait"}],
        transitions=[
            {"key": "LOG-3417", "status": "Won't Do", "duplicate_of": "LOG-3412"},
            {"key": "LOG-9999", "status": "Done"},
        ],
    )
    items = {i["id"]: i for i in res["items"]}
    assert list(items) == ["P1", "P2", "P3", "P4", "P5", "P6"]
    assert items["P1"]["status"] == "ok" and items["P1"]["predicted_key"] == "LOG-3421"
    assert items["P2"]["status"] == "error" and "component" in items["P2"]["error"]
    assert items["P3"]["status"] == "error" and "LOG-3421" in items["P3"]["error"]
    assert items["P4"]["status"] == "ok" and items["P5"]["status"] == "ok"
    assert items["P6"]["status"] == "error"
    assert res["ok"] == 3 and res["errors"] == 3
    assert tools.search_issues(ctx, status="all")["issues"] == before
    assert "uc3:jira" not in ctx.state or ctx.state["uc3:jira"]["next"] == 3421
    assert [i["id"] for i in ctx.state["uc3:proposal"]["items"]] == ["P1", "P2", "P3", "P4", "P5", "P6"]


def test_propose_rejects_unknown_and_missing_fields():
    res = tools.propose_changes(_ctx(), creates=[{"type": "Story", "titel": "x"}], updates=[], transitions=[])
    assert res["items"][0]["status"] == "error"


def test_epic_temp_id_resolves_on_apply():
    ctx = _ctx()
    res = tools.propose_changes(
        ctx,
        creates=[
            _epic(temp_id="E1"),
            _story("[BE] As CT, I want one so that a", epic="E1"),
            _story("[BE] As CT, I want two so that b", epic="E1"),
        ],
        updates=[],
        transitions=[],
    )
    assert res["errors"] == 0, res
    assert [i["predicted_key"] for i in res["items"]] == ["LOG-3421", "LOG-3422", "LOG-3423"]
    out = tools.apply_proposal(ctx)
    assert out["applied"] == 3 and out["failed"] == 0, out
    j = _jira(ctx.state)
    assert j.get("LOG-3422")["epic"] == "LOG-3421" and j.get("LOG-3423")["epic"] == "LOG-3421"
    assert j.get("LOG-3421")["type"] == "Epic"


def test_link_may_name_temp_id():
    ctx = _ctx()
    tools.propose_changes(
        ctx,
        creates=[
            _story("[BE] As CT, I want one so that a", temp_id="S1"),
            _story("[BE] As CT, I want two so that b", links=[{"type": "is blocked by", "key": "S1"}]),
        ],
        updates=[],
        transitions=[],
    )
    assert tools.apply_proposal(ctx)["failed"] == 0
    assert _jira(ctx.state).get("LOG-3422")["links"] == [{"type": "is blocked by", "key": "LOG-3421"}]


def test_unknown_temp_id_is_error_item():
    ctx = _ctx()
    res = tools.propose_changes(
        ctx, creates=[_story("[BE] As CT, I want one so that a", epic="E9")], updates=[], transitions=[]
    )
    assert res["items"][0]["status"] == "error" and "E9" in res["items"][0]["error"]
    out = tools.apply_proposal(ctx)
    assert out["failed"] == 1 and out["applied"] == 0
    assert tools.search_issues(ctx, status="all")["issues"] == _stock()


def _stock():
    return tools.search_issues(_ctx(), status="all")["issues"]


def test_apply_twice_is_noop():
    ctx = _ctx()
    tools.propose_changes(
        ctx,
        creates=[_epic(temp_id="E1"), _story("[BE] As CT, I want one so that a", epic="E1")],
        updates=[{"key": "LOG-3412", "add_labels": ["pilot"], "comment": "terkait"}],
        transitions=[{"key": "LOG-3417", "status": "Won't Do", "duplicate_of": "LOG-3412"}],
    )
    res = tools.apply_proposal(ctx)
    assert res["applied"] == 4 and res["failed"] == 0, res
    j = _jira(ctx.state)
    assert j.get("LOG-3417")["resolution"] == "Duplicate"
    assert j.get("LOG-3412")["comments"][-1]["body"] == "terkait"
    snapshot = json.dumps(ctx.state["uc3:jira"], sort_keys=True)
    again = tools.apply_proposal(ctx)
    assert again["applied"] == 0 and again["already_applied"] == 4 and again["failed"] == 0
    assert json.dumps(ctx.state["uc3:jira"], sort_keys=True) == snapshot
    assert again["results"][0] == {"id": "P1", "status": "already_applied", "key": "LOG-3421"}


def test_transition_already_done_is_error_item():
    ctx = _ctx()
    tools.transition_issue(ctx, "LOG-3417", "Won't Do", duplicate_of="LOG-3412")
    res = tools.propose_changes(
        ctx, creates=[], updates=[], transitions=[{"key": "LOG-3417", "status": "Won't Do"}]
    )
    assert res["items"][0]["status"] == "error" and "sudah" in res["items"][0]["error"]


def test_apply_subset_and_unknown_id():
    ctx = _ctx()
    tools.propose_changes(
        ctx,
        creates=[_story("[BE] As CT, I want a so that a"), _story("[BE] As CT, I want b so that b")],
        updates=[],
        transitions=[],
    )
    res = tools.apply_proposal(ctx, ids=["P2", "P9"])
    assert res["applied"] == 1
    assert [r["id"] for r in res["results"]] == ["P2", "P9"]
    assert res["results"][1]["status"] == "error"
    mine = [i["summary"] for i in ctx.state["uc3:jira"]["issues"] if i["key"] > "LOG-3420"]
    assert mine == ["[BE] As CT, I want b so that b"]
    res = tools.apply_proposal(ctx)
    assert res["applied"] == 1 and res["already_applied"] == 1


def test_subset_apply_keeps_temp_ids_across_calls():
    ctx = _ctx()
    tools.propose_changes(
        ctx,
        creates=[_epic(temp_id="E1"), _story("[BE] As CT, I want a so that a", epic="E1")],
        updates=[],
        transitions=[],
    )
    assert tools.apply_proposal(ctx, ids=["P2"])["failed"] == 1  # epic not applied yet
    assert tools.apply_proposal(ctx, ids=["P1"])["applied"] == 1
    assert tools.apply_proposal(ctx, ids=["P2"])["applied"] == 1
    assert _jira(ctx.state).get("LOG-3422")["epic"] == "LOG-3421"


def test_apply_without_proposal_is_error():
    assert "error" in tools.apply_proposal(_ctx())


def test_apply_reports_item_failure_and_continues():
    ctx = _ctx()
    tools.propose_changes(
        ctx,
        creates=[
            _story("[BE] As CT, I want a so that a", components=["Nope"]),
            _story("[BE] As CT, I want b so that b"),
        ],
        updates=[],
        transitions=[],
    )
    res = tools.apply_proposal(ctx)
    assert res["applied"] == 1 and res["failed"] == 1
    assert res["results"][0]["status"] == "error"


def test_propose_warns_on_combined_capabilities():
    res = tools.propose_changes(
        _ctx(),
        creates=[
            _story("[Driver App] As Driver, I want to send chat, voice note, and photo so that I can report"),
            _story("[Driver App] As Driver, I want to send chat so that I can report"),
            _story("[BE] As Logisly, I want milestones set by gps, app or manual so that follow-up starts"),
            _epic("Epic AI assistant: chat, voice note dan foto"),
        ],
        updates=[],
        transitions=[],
    )
    items = res["items"]
    assert items[0]["status"] == "ok" and "peringatan" in items[0]
    assert all("peringatan" not in i for i in items[1:])
    assert res["warnings"] == 1


def test_propose_requires_valid_story_points_on_stories():
    res = tools.propose_changes(
        _ctx(),
        creates=[
            _story("[BE] As CT, I want a so that a", story_points=None),
            _story("[BE] As CT, I want b so that b", story_points=13),
            _story("[BE] As CT, I want c so that c", story_points=5),
            _epic(),
        ],
        updates=[],
        transitions=[],
    )
    assert [i["status"] for i in res["items"]] == ["error", "error", "ok", "ok"], res["items"]
    assert "story point" in res["items"][0]["error"] and "pecah" in res["items"][1]["error"]


def test_answer_key_is_a_valid_proposal():
    key = json.loads((UC3 / "expected" / "backlog.json").read_text(encoding="utf-8"))
    ctx = _ctx()
    res = tools.propose_changes(ctx, key["creates"], key["updates"], key["transitions"])
    bad = [i for i in res["items"] if i["status"] != "ok"]
    assert not bad, bad
    assert res["warnings"] == 0, [i for i in res["items"] if "peringatan" in i]
    out = tools.apply_proposal(ctx)
    assert out["failed"] == 0, out
    j = _jira(ctx.state)
    assert j.get("LOG-3417")["resolution"] == "Duplicate"
    stories = [i for i in j.search(issue_type="Story", label="uc1-milestone") if i["key"] >= "LOG-3421"]
    assert len(stories) == 11
