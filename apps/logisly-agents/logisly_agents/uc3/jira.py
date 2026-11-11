"""Mock Jira project kept in session state under ``uc3:jira``.

State shape: ``{"issues": [...], "next": int}``, JSON-safe. ADK ``State``
persists only on key assignment, so every write copies, modifies and re-assigns.
All validation happens before any write, so a failure leaves state unchanged.

Link semantics: a link ``{"type": t, "key": k}`` stored on issue X reads "X t k"
(so ``LOG-3417 duplicates LOG-3412`` is stored on LOG-3417).
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import MutableMapping
from datetime import date
from pathlib import Path
from typing import Any

from logisly_agents.config import get_settings

KEY = "uc3:jira"
OPEN_STATUSES = ("To Do", "In Progress")
CLOSED_STATUSES = ("Done", "Won't Do")
_KEY_RE = re.compile(r"^([A-Z][A-Z0-9]*)-(\d+)$")


class TrackerError(Exception):
    pass


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().split())


class MockJira:
    def __init__(
        self,
        state: MutableMapping[str, Any],
        snapshot_path: Path,
        conventions: dict,
        calendar: dict,
        as_of: date | None = None,
    ):
        self.state = state
        self.today = (as_of or get_settings().as_of).isoformat()
        self.project = conventions["project"]["key"]
        self.issue_types = list(conventions["issue_types"])
        self.statuses = list(conventions["statuses"])
        self.resolutions = list(conventions["resolutions"])
        self.link_types = list(conventions["link_types"])
        self.components = set(conventions["components"])
        self.story_points = list(conventions["story_points"])
        self.sprints = {s["name"] for s in calendar["sprints"]}
        if KEY not in state:
            issues = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))["issues"]
            top = max((self._number(i["key"]) for i in issues), default=0)
            state[KEY] = {"issues": copy.deepcopy(issues), "next": top + 1}

    # -- helpers
    def _data(self) -> dict:
        return copy.deepcopy(self.state[KEY])

    def _save(self, data: dict) -> None:
        self.state[KEY] = data

    @staticmethod
    def _number(key: str) -> int:
        m = _KEY_RE.match(key) if isinstance(key, str) else None
        if not m:
            raise TrackerError(f"kunci issue tidak valid: {key!r} (format 'LOG-123')")
        return int(m.group(2))

    def _find(self, data: dict, key: str) -> dict:
        self._number(key)
        for i in data["issues"]:
            if i["key"] == key:
                return i
        raise TrackerError(f"issue tidak ditemukan: {key}")

    def _check_components(self, components) -> None:
        if not components:
            raise TrackerError("minimal satu component wajib diisi")
        unknown = [c for c in components if c not in self.components]
        if unknown:
            raise TrackerError(f"component tidak dikenal: {unknown}")

    def _check_sprint(self, sprint) -> None:
        if sprint is not None and sprint not in self.sprints:
            raise TrackerError(f"sprint tidak ada di kalender: {sprint}")

    def _check_points(self, points) -> None:
        if points is not None and points not in self.story_points:
            raise TrackerError(
                f"story point harus salah satu dari {self.story_points}; bila lebih besar, pecah story"
            )

    def _check_links(self, data: dict, links) -> list[dict]:
        out = []
        for ln in links or []:
            if not isinstance(ln, dict) or "type" not in ln or "key" not in ln:
                raise TrackerError("setiap link harus berisi type dan key")
            if ln["type"] not in self.link_types:
                raise TrackerError(f"tipe link tidak dikenal: {ln['type']!r}; pilihan {self.link_types}")
            self._find(data, ln["key"])
            out.append({"type": ln["type"], "key": ln["key"]})
        return out

    def _check_epic(self, data: dict, epic) -> None:
        if epic is None:
            return
        target = self._find(data, epic)
        if target["type"] != "Epic":
            raise TrackerError(f"{epic} bukan Epic (tipe {target['type']})")

    @staticmethod
    def _summary(i: dict) -> dict:
        keep = ("key", "type", "summary", "status", "components", "sprint", "assignee", "story_points")
        return copy.deepcopy({k: i.get(k) for k in keep})

    # -- reads
    def search(
        self,
        text: str = "",
        status: str = "open",
        component: str = "",
        issue_type: str = "",
        label: str = "",
    ) -> list[dict]:
        if status not in ("open", "done", "all"):
            raise TrackerError("status harus open, done atau all")
        out = []
        for i in self.state[KEY]["issues"]:
            if status == "open" and i["status"] not in OPEN_STATUSES:
                continue
            if status == "done" and i["status"] not in CLOSED_STATUSES:
                continue
            if component and component not in i["components"]:
                continue
            if issue_type and i["type"] != issue_type:
                continue
            if label and label not in i["labels"]:
                continue
            if text and text.lower() not in (i["summary"] + "\n" + i["description"]).lower():
                continue
            out.append(self._summary(i))
        return out

    def get(self, key: str) -> dict:
        return copy.deepcopy(self._find(self.state[KEY], key))

    # -- writes
    def create(
        self,
        issue_type: str,
        summary: str,
        description: str,
        acceptance: list[str],
        components: list[str],
        labels: list[str],
        story_points: int | None = None,
        sprint: str | None = None,
        assignee: str | None = None,
        epic: str | None = None,
        links: list[dict] | None = None,
    ) -> dict:
        if issue_type not in self.issue_types:
            raise TrackerError(f"tipe issue tidak dikenal: {issue_type!r}; pilihan {self.issue_types}")
        if not (summary or "").strip():
            raise TrackerError("summary tidak boleh kosong")
        self._check_components(components)
        self._check_sprint(sprint)
        if issue_type == "Story" and story_points is None:
            raise TrackerError(f"Story wajib punya story point {self.story_points}")
        self._check_points(story_points)
        data = self._data()
        self._check_epic(data, epic)
        checked_links = self._check_links(data, links)
        for i in data["issues"]:
            if i["status"] in OPEN_STATUSES and _norm(i["summary"]) == _norm(summary):
                # Idempotent retries: the same story is never created twice.
                raise TrackerError(f"issue terbuka dengan summary sama sudah ada: {i['key']}")
        n = data["next"]
        data["next"] = n + 1
        key = f"{self.project}-{n}"
        data["issues"].append(
            {
                "key": key,
                "type": issue_type,
                "summary": summary,
                "description": description or "",
                "acceptance": list(acceptance or []),
                "status": "To Do",
                "resolution": None,
                "components": list(components),
                "labels": list(labels or []),
                "story_points": story_points,
                "sprint": sprint,
                "assignee": assignee,
                "epic": epic,
                "links": checked_links,
                "comments": [],
                "created": self.today,
                "updated": self.today,
            }
        )
        self._save(data)
        return {"key": key}

    def update(
        self,
        key: str,
        add_labels=(),
        remove_labels=(),
        sprint: str | None = None,
        assignee: str | None = None,
        story_points: int | None = None,
        description_append: str = "",
        acceptance_append=(),
        add_links=(),
    ) -> dict:
        self._check_sprint(sprint)
        self._check_points(story_points)
        data = self._data()
        issue = self._find(data, key)
        links = self._check_links(data, add_links)
        labels = [x for x in issue["labels"] if x not in set(remove_labels)]
        labels += [x for x in add_labels if x not in labels]
        issue["labels"] = labels
        if sprint is not None:
            issue["sprint"] = sprint
        if assignee is not None:
            issue["assignee"] = assignee
        if story_points is not None:
            issue["story_points"] = story_points
        if description_append:
            issue["description"] = (issue["description"] + "\n\n" + description_append).strip()
        issue["acceptance"] += [a for a in acceptance_append if a not in issue["acceptance"]]
        issue["links"] += [ln for ln in links if ln not in issue["links"]]
        issue["updated"] = self.today
        self._save(data)
        return {"key": key}

    def comment(self, key: str, body: str) -> dict:
        data = self._data()
        issue = self._find(data, key)
        issue["comments"].append({"author": "ai-agent", "created": self.today, "body": body})
        issue["updated"] = self.today
        self._save(data)
        return {"key": key, "comments": len(issue["comments"])}

    def transition(
        self, key: str, status: str, resolution: str = "", comment: str = "", duplicate_of: str = ""
    ) -> dict:
        if status not in self.statuses:
            raise TrackerError(f"status tidak dikenal: {status!r}; pilihan {self.statuses}")
        if resolution and resolution not in self.resolutions:
            raise TrackerError(f"resolution tidak dikenal: {resolution!r}; pilihan {self.resolutions}")
        data = self._data()
        issue = self._find(data, key)
        if issue["status"] == status:
            raise TrackerError(f"{key} sudah berstatus {status}")
        if duplicate_of:
            if duplicate_of == key:
                raise TrackerError("issue tidak bisa menjadi duplikat dirinya sendiri")
            self._find(data, duplicate_of)
            if status not in CLOSED_STATUSES:
                raise TrackerError("duplikat harus dipindah ke Done atau Won't Do")
            resolution = "Duplicate"
            link = {"type": "duplicates", "key": duplicate_of}
            if link not in issue["links"]:
                issue["links"].append(link)
        if status in OPEN_STATUSES:
            if resolution:
                raise TrackerError(f"resolution tidak berlaku untuk status {status}")
            resolution = ""
        else:
            resolution = resolution or status  # Done -> Done, Won't Do -> Won't Do
        issue["status"] = status
        issue["resolution"] = resolution or None
        issue["updated"] = self.today
        if comment:
            issue["comments"].append({"author": "ai-agent", "created": self.today, "body": comment})
        self._save(data)
        return {"key": key, "status": status, "resolution": issue["resolution"]}
