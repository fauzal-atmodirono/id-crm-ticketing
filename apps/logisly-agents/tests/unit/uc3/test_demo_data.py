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
    assert not (UC3 / ("git" + "hub")).exists()
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


def _backlog():
    return json.loads((UC3 / "expected" / "backlog.json").read_text())


def _calendar():
    return yaml.safe_load((UC3 / "team" / "calendar.yaml").read_text())


def _roster():
    return yaml.safe_load((UC3 / "team" / "roster.yaml").read_text())


def test_backlog_references_resolve():
    conv, bl, snap = _conventions(), _backlog(), _by_key()
    sprints = {s["name"] for s in _calendar()["sprints"]}
    ids = {p["id"] for p in _roster()["people"]}
    temp_ids = {c["temp_id"] for c in bl["creates"]}
    known = temp_ids | set(snap)
    items = bl["creates"] + bl["updates"]
    for c in items:
        if c.get("sprint"):
            assert c["sprint"] in sprints, c
        if c.get("assignee"):
            assert c["assignee"] in ids, c
        if c.get("epic"):
            assert c["epic"] in known, c
        for lk in (c.get("links") or []) + (c.get("add_links") or []):
            assert lk["type"] in conv["link_types"], lk
            assert lk["key"] in known, lk
        if c.get("key"):
            assert c["key"] in snap
        assert set(c.get("components") or []) <= set(conv["components"])
    for t in bl["transitions"]:
        assert t["key"] in snap and t["duplicate_of"] in snap
    s10 = next(c for c in bl["creates"] if c["temp_id"] == "S10")
    assert s10["links"] == [{"type": "is blocked by", "key": "LOG-3405"}]
    for i in _snapshot()["issues"]:
        if i["sprint"]:
            assert i["sprint"] in sprints
        if i["assignee"]:
            assert i["assignee"] in ids
        for lk in i["links"]:
            assert lk["type"] in conv["link_types"] and lk["key"] in snap


def test_planted_dates():
    by = _by_key()
    assert by["LOG-3405"]["sprint"] == "Sprint 26.22"
    assert by["LOG-3412"]["created"] == "2026-10-20"
    assert by["LOG-3417"]["created"] == "2026-11-05"
    for i in by.values():
        assert i["updated"] >= max([i["created"]] + [c["created"] for c in i["comments"]]), i["key"]


def test_docx_kpi_rows():
    doc = Document(UC3 / "sources" / "01_request_milestone-follow-up.docx")
    rows = [[c.text for c in r.cells] for t in doc.tables for r in t.rows]
    assert ["Pertanyaan ke driver per trip", "18", "6"] in rows
    assert ["Kasus terlambat terdeteksi sebelum muat", "40%", "80%"] in rows


def test_chat_not_before_request_date():
    doc = Document(UC3 / "sources" / "01_request_milestone-follow-up.docx")
    assert any("Tanggal: 5 November 2026" in p.text for p in doc.paragraphs)
    chat = (UC3 / "sources" / "04_ops-chat-export_ct-followup.txt").read_text()
    # the first chat line that mentions the request is on or after the docx date
    first = next(line for line in chat.splitlines() if "request milestone" in line)
    assert first.startswith("[05/11/26") or first.startswith("[06/11/26")


def test_roster_committed_matches_snapshot():
    names = {p["id"]: p["name"] for p in _roster()["people"]}
    derived: dict[str, dict[str, int]] = {}
    for i in _snapshot()["issues"]:
        if i["sprint"] and i["assignee"] and i["story_points"]:
            per = derived.setdefault(i["sprint"], {})
            per[names[i["assignee"]]] = per.get(names[i["assignee"]], 0) + i["story_points"]
    assert _roster()["committed"] == derived


def test_capacity_figures_match_backlog():
    conv, bl = _conventions(), _backlog()
    sprints = {s["name"] for s in _calendar()["sprints"]}
    window = {"Sprint 26.22", "Sprint 26.23", "Sprint 26.24"}
    assert window <= sprints
    new = {"mobile": 0, "backend": 0, "frontend": 0}
    for c in bl["creates"]:
        if c["type"] == "Story" and c["sprint"] in window:
            new[conv["components"][c["components"][0]]["team"]] += c["story_points"]
    for u in bl["updates"]:  # existing stories enriched and planned inside the window
        i = _by_key()[u["key"]]
        if u.get("sprint") in window and i["story_points"]:
            new[conv["components"][i["components"][0]]["team"]] += i["story_points"]
    assert new == {"mobile": 3, "backend": 30, "frontend": 7}
    report = (UC3 / "expected" / "consistency_report.md").read_text()
    assert "LOG-3412 = 3 SP" in report
    assert "5+5+5+5+3+2+2+3 = 30 SP" in report
    assert "5+2 = 7 SP" in report
    assert "(0+3)/69" in report and "(8+30)/69" in report and "(8+7)/30" in report
