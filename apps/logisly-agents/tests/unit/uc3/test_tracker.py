import copy
from datetime import date

import pytest

from logisly_agents.config import get_settings
from logisly_agents.uc3.context import read_context
from logisly_agents.uc3.tracker import MockTracker, TrackerError

UC3 = get_settings().data_dir / "uc3"
SNAP = UC3 / "github/issues_snapshot.json"
KEY = "uc3:tracker"
LABELS = ["type:feature", "area:driver-app", "priority:P2", "sp:3"]


def _tracker(state=None):
    ctx = read_context(UC3)
    state = {} if state is None else state
    return MockTracker(state, SNAP, ctx["conventions"], ctx["calendar"], as_of=date(2026, 11, 9)), state


def test_seeded():
    t, _ = _tracker()
    assert len(t.search(repo="driver-app", state_filter="all")) == 7
    assert len(t.get("driver-app#142")["comments"]) == 3
    assert "comments" not in t.search(repo="driver-app")[0]


def test_create_validates():
    t, state = _tracker()
    before = copy.deepcopy(state[KEY])
    bad = ["type:feature", "area:driver-app"]
    with pytest.raises(TrackerError):
        t.create("driver-app", "[driver-app] Layar chat", "b", bad, None, [])
    assert state[KEY] == before
    out = t.create(
        "driver-app", "[driver-app] Layar chat dengan AI assistant", "b", LABELS, "Sprint 26.22", ["rizky-r"]
    )
    assert out == {"ref": "driver-app#154"}
    assert t.get("driver-app#154")["state"] == "open"


@pytest.mark.parametrize(
    "kw",
    [
        {"repo": "nope"},
        {"title": "tanpa prefix"},
        {"labels": LABELS + ["type:bug"]},
        {"labels": LABELS + ["bogus:x"]},
        {"milestone": "Sprint 99"},
    ],
)
def test_create_rejects(kw):
    t, state = _tracker()
    before = copy.deepcopy(state[KEY])
    args = dict(
        repo="driver-app", title="[driver-app] x y", body="", labels=LABELS, milestone=None, assignees=[]
    )
    args.update(kw)
    with pytest.raises(TrackerError):
        t.create(**args)
    assert state[KEY] == before


def test_close_duplicate():
    t, _ = _tracker()
    t.close("driver-app#148", "duplicate", comment="dup")
    issue = t.get("driver-app#148")
    assert issue["state"] == "closed"
    assert "status:duplicate" in issue["labels"]
    assert issue["state_reason"] == "not_planned"
    assert issue["comments"][-1]["body"] == "dup"
    t.close("driver-app#142", "completed")
    assert t.get("driver-app#142")["state_reason"] == "completed"
    assert all(i["number"] != 148 for i in t.search(repo="driver-app"))


def test_unknown_ref():
    t, state = _tracker()
    before = copy.deepcopy(state[KEY])
    for fn in (
        lambda: t.get("driver-app#999"),
        lambda: t.comment("bad", "x"),
        lambda: t.close("driver-app#999", "completed"),
    ):
        with pytest.raises(TrackerError):
            fn()
    assert state[KEY] == before


def test_fresh_state_reseeds():
    t, _ = _tracker()
    t.create("driver-app", "[driver-app] Layar chat", "b", LABELS, None, [])
    t2, _ = _tracker()
    assert len(t2.search(repo="driver-app", state_filter="all")) == 7


def test_existing_state_not_reseeded():
    t, state = _tracker()
    t.create("driver-app", "[driver-app] Layar chat", "b", LABELS, None, [])
    t2, _ = _tracker(state)
    assert len(t2.search(repo="driver-app", state_filter="all")) == 8


def test_update_and_search():
    t, state = _tracker()
    t.update(
        "driver-app#142",
        add_labels=["sp:8"],
        remove_labels=["sp:5"],
        milestone="Sprint 26.23",
        assignees=["bayu-p"],
        body_append="tambahan",
    )
    i = t.get("driver-app#142")
    assert "sp:8" in i["labels"] and "sp:5" not in i["labels"]
    assert i["milestone"] == "Sprint 26.23" and i["assignees"] == ["bayu-p"]
    assert i["body"].endswith("tambahan")
    before = copy.deepcopy(state[KEY])
    with pytest.raises(TrackerError):
        t.update("driver-app#142", add_labels=["nope:1"])
    with pytest.raises(TrackerError):
        t.update("driver-app#142", milestone="Sprint 99")
    assert state[KEY] == before
    assert t.search(label="sp:8")
    assert t.search(text="zzzznotfound") == []
    t.comment("driver-app#142", "halo")
    assert t.get("driver-app#142")["comments"][-1]["body"] == "halo"


def test_dates_use_as_of():
    t, _ = _tracker()
    ref = t.create("driver-app", "[driver-app] Layar chat", "b", LABELS, None, [])["ref"]
    assert t.get(ref)["created_at"] == "2026-11-09"
    t.comment(ref, "c")
    assert t.get(ref)["comments"][-1]["created_at"] == "2026-11-09"
    t.close(ref, "completed")
    assert t.get(ref)["closed_at"] == "2026-11-09"


def test_default_as_of_from_settings():
    ctx = read_context(UC3)
    t = MockTracker({}, SNAP, ctx["conventions"], ctx["calendar"])
    ref = t.create("driver-app", "[driver-app] Layar chat", "b", LABELS, None, [])["ref"]
    assert t.get(ref)["created_at"] == get_settings().as_of.isoformat()


@pytest.mark.parametrize(
    "call",
    [
        lambda t: t.update("driver-app#999", add_labels=["sp:1"]),
        lambda t: t.update("driver-app#142", add_labels=["nope:1"]),
        lambda t: t.update("driver-app#142", milestone="Sprint 99"),
        lambda t: t.comment("driver-app#999", "x"),
        lambda t: t.close("driver-app#142", "bogus"),
        lambda t: t.close("driver-app#139", "completed"),
        lambda t: t.get("driver-app#abc"),
        lambda t: t.get("#5"),
        lambda t: t.get(None),
    ],
)
def test_failures_leave_state_unchanged(call):
    t, state = _tracker()
    before = copy.deepcopy(state[KEY])
    with pytest.raises(TrackerError):
        call(t)
    assert state[KEY] == before


def test_write_replaces_not_mutates():
    t, state = _tracker()
    held = state[KEY]
    snapshot = copy.deepcopy(held)
    t.comment("driver-app#142", "x")
    t.update("driver-app#142", add_labels=["sp:8"])
    t.create("driver-app", "[driver-app] Layar chat", "b", LABELS, None, [])
    t.close("driver-app#142", "completed")
    assert held == snapshot
    assert state[KEY] is not held


def test_search_results_do_not_alias_store():
    t, state = _tracker()
    t.search(repo="driver-app")[0]["labels"].append("zzz")
    assert all("zzz" not in i["labels"] for i in state[KEY]["issues"])


def test_create_rejects_open_duplicate_title_case_insensitive():
    t, state = _tracker()
    t.create("driver-app", "[driver-app] Layar chat AI", "b", LABELS, None, [])
    before = copy.deepcopy(state[KEY])
    with pytest.raises(TrackerError, match="driver-app#154"):
        t.create("driver-app", "[driver-app]  LAYAR chat ai ", "b", LABELS, None, [])
    assert state[KEY] == before
    # Same title in another repo, or after the first is closed, is fine.
    other = ["type:feature", "area:platform", "priority:P2"]
    t.create("platform-api", "[driver-app] Layar chat AI", "b", other, None, [])
    t.close("driver-app#154", "not_planned")
    again = t.create("driver-app", "[driver-app] Layar chat AI", "b", LABELS, None, [])
    assert again["ref"] == "driver-app#155"
