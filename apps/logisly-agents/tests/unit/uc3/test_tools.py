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


FEAT = ["type:feature", "area:driver-app", "priority:P2"]


def _create(title, **kw):
    return {"repo": "driver-app", "title": title, "body": "isi", "labels": FEAT, **kw}


def test_propose_validates_without_writing():
    ctx = _ctx()
    before = tools.search_issues(ctx, state="all")["issues"]
    tracker_before = dict(ctx.state)
    res = tools.propose_changes(
        ctx,
        creates=[
            _create("[driver-app] Layar chat", milestone="Sprint 26.23", assignees=["rizky-r"]),
            _create("tanpa prefix"),
            _create("[driver-app] Layar chat"),  # duplicate of P1 inside the proposal
        ],
        updates=[{"ref": "driver-app#142", "add_labels": ["sp:3"], "comment": "terkait"}],
        closes=[
            {"ref": "driver-app#148", "reason": "duplicate", "comment": "dup"},
            {"ref": "driver-app#999", "reason": "duplicate"},
        ],
    )
    items = {i["id"]: i for i in res["items"]}
    assert list(items) == ["P1", "P2", "P3", "P4", "P5", "P6"]
    assert items["P1"]["status"] == "ok" and items["P1"]["predicted_ref"] == "driver-app#154"
    assert items["P2"]["status"] == "error" and "judul" in items["P2"]["error"]
    assert items["P3"]["status"] == "error" and "driver-app#154" in items["P3"]["error"]
    assert items["P4"]["status"] == "ok" and items["P5"]["status"] == "ok"
    assert items["P6"]["status"] == "error"
    assert res["ok"] == 3 and res["errors"] == 3
    # No tracker writes happened.
    assert tools.search_issues(ctx, state="all")["issues"] == before
    assert ctx.state.get("uc3:tracker") == tracker_before.get("uc3:tracker")
    assert [i["id"] for i in ctx.state["uc3:proposal"]["items"]] == ["P1", "P2", "P3", "P4", "P5", "P6"]


def test_propose_rejects_unknown_and_missing_keys():
    ctx = _ctx()
    res = tools.propose_changes(ctx, creates=[{"repo": "driver-app", "titel": "x"}], updates=[], closes=[])
    assert res["items"][0]["status"] == "error"


def test_apply_all_then_reapply_is_noop():
    ctx = _ctx()
    tools.propose_changes(
        ctx,
        creates=[_create("[driver-app] Layar chat"), _create("[driver-app] Voice note")],
        updates=[{"ref": "driver-app#142", "add_labels": ["sp:3"], "comment": "terkait"}],
        closes=[{"ref": "driver-app#148", "reason": "duplicate", "comment": "dup"}],
    )
    res = tools.apply_proposal(ctx)
    assert res["applied"] == 4 and res["failed"] == 0, res
    refs = [r.get("ref") for r in res["results"]]
    assert refs[:2] == ["driver-app#154", "driver-app#155"]
    t = _tracker(ctx.state)
    assert t.get("driver-app#148")["state"] == "closed"
    assert "sp:3" in t.get("driver-app#142")["labels"]
    assert t.get("driver-app#142")["comments"][-1]["body"] == "terkait"
    snapshot = ctx.state["uc3:tracker"]
    again = tools.apply_proposal(ctx)
    assert again["applied"] == 0 and again["already_applied"] == 4
    assert ctx.state["uc3:tracker"] == snapshot


def test_apply_subset_and_unknown_id():
    ctx = _ctx()
    tools.propose_changes(
        ctx, creates=[_create("[driver-app] A satu"), _create("[driver-app] B dua")], updates=[], closes=[]
    )
    res = tools.apply_proposal(ctx, ids=["P2", "P9"])
    assert res["applied"] == 1
    assert [r["id"] for r in res["results"]] == ["P2", "P9"]
    assert res["results"][1]["status"] == "error"
    titles = [i["title"] for i in ctx.state["uc3:tracker"]["issues"] if i["author"] == "ai-agent"]
    assert titles == ["[driver-app] B dua"]
    res = tools.apply_proposal(ctx)
    assert res["applied"] == 1 and res["already_applied"] == 1


def test_apply_without_proposal_is_error():
    assert "error" in tools.apply_proposal(_ctx())


def test_apply_reports_item_failure_and_continues():
    ctx = _ctx()
    tools.propose_changes(
        ctx, creates=[_create("tanpa prefix"), _create("[driver-app] Valid")], updates=[], closes=[]
    )
    res = tools.apply_proposal(ctx)
    assert res["applied"] == 1 and res["failed"] == 1
    assert res["results"][0]["status"] == "error"


def test_propose_warns_on_combined_capabilities():
    ctx = _ctx()
    res = tools.propose_changes(
        ctx,
        creates=[
            _create("[driver-app] Kirim chat, voice note, dan foto ke AI assistant"),
            _create("[driver-app] Layar chat AI assistant"),
            _create("[driver-app] Epic: AI assistant, chat dan foto"),
        ],
        updates=[],
        closes=[],
    )
    items = res["items"]
    assert items[0]["status"] == "ok" and "peringatan" in items[0]
    assert "peringatan" not in items[1]
    assert "peringatan" not in items[2]  # epics group capabilities by design
    assert res["warnings"] == 1
