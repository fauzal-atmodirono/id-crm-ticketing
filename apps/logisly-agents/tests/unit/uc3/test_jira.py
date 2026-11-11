import copy
import json
from datetime import date

import pytest

from logisly_agents.config import get_settings
from logisly_agents.uc3.context import read_context
from logisly_agents.uc3.jira import MockJira, TrackerError

UC3 = get_settings().data_dir / "uc3"
SNAP = UC3 / "jira/issues_snapshot.json"
KEY = "uc3:jira"


def _jira(state=None):
    ctx = read_context(UC3)
    state = {} if state is None else state
    return MockJira(state, SNAP, ctx["conventions"], ctx["calendar"], as_of=date(2026, 11, 12)), state


def _story(**kw):
    base = {
        "issue_type": "Story",
        "summary": "[BE] As CT, I want a thing so that it works",
        "description": "d",
        "acceptance": ["a"],
        "components": ["Platform"],
        "labels": ["pilot"],
        "story_points": 3,
    }
    return base | kw


def test_seeded_next_key_and_idempotent():
    j, state = _jira()
    assert len(j.search(status="all")) == 14
    assert state[KEY]["next"] == 3421
    assert j.create(**_story()) == {"key": "LOG-3421"}
    again, _ = _jira(state)  # re-seeding the same state must not reset it
    assert state[KEY]["next"] == 3422 and len(again.search(status="all")) == 15


@pytest.mark.parametrize(
    "override",
    [
        {"issue_type": "Spike"},
        {"components": ["Billing"]},
        {"components": []},
        {"sprint": "Sprint 99.99"},
        {"links": [{"type": "causes", "key": "LOG-3405"}]},
        {"links": [{"type": "blocks", "key": "LOG-9999"}]},
        {"story_points": None},
        {"story_points": 13},
        {"epic": "LOG-3405"},  # a Story, not an Epic
        {"epic": "LOG-9999"},
        {"summary": "  "},
    ],
)
def test_create_validation_leaves_state_unchanged(override):
    j, state = _jira()
    before = copy.deepcopy(state[KEY])
    with pytest.raises(TrackerError):
        j.create(**_story(**override))
    assert state[KEY] == before


def test_create_rejects_open_duplicate_summary():
    j, state = _jira()
    j.create(**_story())
    before = copy.deepcopy(state[KEY])
    with pytest.raises(TrackerError):
        j.create(**_story())
    assert state[KEY] == before


def test_link_types_come_from_conventions():
    j, _ = _jira()
    out = j.create(**_story(links=[{"type": "is blocked by", "key": "LOG-3405"}]))
    assert j.get(out["key"])["links"] == [{"type": "is blocked by", "key": "LOG-3405"}]


def test_get_unknown_key():
    j, _ = _jira()
    with pytest.raises(TrackerError):
        j.get("LOG-9999")
    with pytest.raises(TrackerError):
        j.get("nonsense")


def test_search_filters():
    j, _ = _jira()
    open_keys = {i["key"] for i in j.search()}
    assert "LOG-3398" not in open_keys  # Done
    assert {"LOG-3405", "LOG-3412", "LOG-3417"} <= open_keys
    done = {i["key"] for i in j.search(status="done")}
    assert "LOG-3398" in done and not done & open_keys
    assert {i["key"] for i in j.search(component="Driver App", issue_type="Story")} >= {
        "LOG-3412",
        "LOG-3417",
    }
    assert all(i["status"] != "Done" for i in j.search(text="pod"))
    assert set(j.search()[0]) == {
        "key", "type", "summary", "status", "components", "sprint", "assignee", "story_points",
    }  # fmt: skip
    with pytest.raises(TrackerError):
        j.search(status="closed")


def test_duplicate_transition():
    j, state = _jira()
    out = j.transition("LOG-3417", "Won't Do", comment="dup", duplicate_of="LOG-3412")
    assert out == {"key": "LOG-3417", "status": "Won't Do", "resolution": "Duplicate"}
    issue = j.get("LOG-3417")
    assert {"type": "duplicates", "key": "LOG-3412"} in issue["links"]
    assert issue["comments"][-1]["body"] == "dup"
    snapshot = copy.deepcopy(state[KEY])
    with pytest.raises(TrackerError):  # already Won't Do
        j.transition("LOG-3417", "Won't Do", duplicate_of="LOG-3412")
    assert state[KEY] == snapshot


def test_transition_validation_and_default_resolution():
    j, state = _jira()
    before = copy.deepcopy(state[KEY])
    for kw in (
        {"status": "Closed"},
        {"status": "Done", "resolution": "Fixed"},
        {"status": "In Progress", "resolution": "Done"},
        {"status": "Done", "duplicate_of": "LOG-9999"},
        {"status": "Done", "duplicate_of": "LOG-3412"},  # LOG-3412 itself
    ):
        with pytest.raises(TrackerError):
            j.transition("LOG-3412", **kw)
    assert state[KEY] == before
    assert j.transition("LOG-3412", "In Progress")["resolution"] is None
    assert j.transition("LOG-3412", "Done")["resolution"] == "Done"


def test_update_and_comment():
    j, state = _jira()
    before = copy.deepcopy(state[KEY])
    with pytest.raises(TrackerError):
        j.update("LOG-3412", sprint="Sprint 99.99")
    with pytest.raises(TrackerError):
        j.update("LOG-3412", add_links=[{"type": "nope", "key": "LOG-3405"}])
    assert state[KEY] == before
    j.update(
        "LOG-3412",
        add_labels=["pilot"],
        sprint="Sprint 26.23",
        assignee="rizky-r",
        description_append="tambahan",
        acceptance_append=["baru"],
        add_links=[{"type": "relates to", "key": "LOG-3417"}],
    )
    i = j.get("LOG-3412")
    assert "pilot" in i["labels"] and i["sprint"] == "Sprint 26.23" and i["assignee"] == "rizky-r"
    assert i["description"].endswith("tambahan") and i["acceptance"][-1] == "baru"
    assert i["links"][-1] == {"type": "relates to", "key": "LOG-3417"}
    assert j.comment("LOG-3412", "halo")["comments"] == len(i["comments"]) + 1
    json.dumps(state[KEY])
