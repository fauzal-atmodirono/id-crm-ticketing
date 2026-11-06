import copy

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
    return MockTracker(state, SNAP, ctx["conventions"], ctx["calendar"]), state


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
