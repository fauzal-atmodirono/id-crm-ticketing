"""File-level checks on the UC3 demo data: planted facts, Jira snapshot, answer keys."""

import json
import re

import yaml
from docx import Document

from logisly_agents.config import get_settings

UC3 = get_settings().data_dir / "uc3"
PRD_HEADINGS = [
    "Background",
    "Impact",
    "Requirements & Timeline",
    "Flow",
    "Timeline",
    "Mock Up",
    "Stories",
    "Pertanyaan terbuka",
]


def _snapshot():
    return json.loads((UC3 / "jira" / "issues_snapshot.json").read_text())


def _conventions():
    return yaml.safe_load((UC3 / "jira" / "conventions.yaml").read_text())


def _by_key():
    return {i["key"]: i for i in _snapshot()["issues"]}


def _assert_headings_in_order(text: str):
    pos = 0
    for h in PRD_HEADINGS:
        m = re.search(rf"^#+\s*(?:\d+\.\s*)?{re.escape(h)}\s*$", text[pos:], re.M | re.I)
        assert m, f"heading {h!r} missing or out of order"
        pos += m.end()


def test_snapshot_shape_and_max_key():
    snap = _snapshot()
    assert snap["project"] == "LOG"
    issues = snap["issues"]
    assert len(issues) == 14
    assert max(int(i["key"].split("-")[1]) for i in issues) == 3420
    required = {
        "key",
        "type",
        "summary",
        "description",
        "acceptance",
        "status",
        "resolution",
        "components",
        "labels",
        "story_points",
        "sprint",
        "assignee",
        "epic",
        "links",
        "comments",
        "created",
        "updated",
    }
    for i in issues:
        assert required <= set(i), i["key"]


def test_planted_issues():
    by = _by_key()
    for k in ("LOG-3417", "LOG-3412"):
        assert by[k]["status"] == "To Do"
        assert "Driver App" in by[k]["components"]
    assert by["LOG-3405"]["status"] == "In Progress"
    assert by["LOG-3398"]["status"] == "Done" and by["LOG-3398"]["type"] == "Story"


def test_issues_follow_conventions():
    conv = _conventions()
    comps = set(conv["components"])
    assert conv["story_points"] == [1, 2, 3, 5, 8]
    for i in _snapshot()["issues"]:
        assert set(i["components"]) <= comps, i["key"]
        assert i["type"] in conv["issue_types"]
        assert i["status"] in conv["statuses"]
        if i["type"] == "Story":
            assert i["story_points"] in conv["story_points"] or i["status"] == "Done", i["key"]


def test_roster_components_valid():
    comps = set(_conventions()["components"])
    roster = yaml.safe_load((UC3 / "team" / "roster.yaml").read_text())
    for p in roster["people"]:
        assert set(p.get("components") or []) <= comps, p["name"]
        assert "repos" not in p


def test_source_dates_planted():
    doc = Document(UC3 / "sources" / "01_request_milestone-follow-up.docx")
    text = "\n".join(p.text for p in doc.paragraphs)
    text += "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    assert "4 Desember 2026" in text and "21:00–06:00" in text
    assert "Pertanyaan ke driver per trip" in text
    notes = (UC3 / "sources" / "02_meeting-notes_2026-11-10_product-sync.md").read_text()
    assert "18 Desember 2026" in notes and "22:00–05:00" in notes and "LOG-3405" in notes
    eml = (UC3 / "sources" / "03_email_cs_unloading-update.eml").read_text()
    assert "31 Desember 2026" in eml and "PT Sinar Pangan Nusantara" in eml
    chat = (UC3 / "sources" / "04_ops-chat-export_ct-followup.txt").read_text()
    assert "Posisi di mana pak?" in chat and "Rp 38 juta/bulan" in chat


def test_old_data_removed():
    assert not (UC3 / "github").exists()
    assert not (UC3 / "expected" / "prd_driver-app-ai-assistant.md").exists()


def test_template_and_expected_prd_headings():
    _assert_headings_in_order((UC3 / "templates" / "prd_template.md").read_text())
    prd = (UC3 / "expected" / "prd_uc1-milestone-checklist.md").read_text()
    _assert_headings_in_order(prd)
    assert "22:00–05:00" in prd


def test_backlog_answer_key():
    bl = json.loads((UC3 / "expected" / "backlog.json").read_text())
    creates = bl["creates"]
    assert sum(c["type"] == "Epic" for c in creates) == 1
    stories = [c for c in creates if c["type"] == "Story"]
    assert len(stories) >= 8
    for s in stories:
        assert s["story_points"] in (1, 2, 3, 5, 8) and len(s["components"]) == 1
    tr = [t for t in bl["transitions"] if t["key"] == "LOG-3417"]
    assert tr and tr[0]["status"] == "Won't Do" and tr[0]["duplicate_of"] == "LOG-3412"
    assert any(u["key"] == "LOG-3412" for u in bl["updates"])
