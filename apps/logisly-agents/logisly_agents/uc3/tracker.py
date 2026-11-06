"""Mock GitHub issue tracker kept in session state under ``uc3:tracker``.

State shape: ``{"issues": [...], "next": {repo: n}}``, JSON-safe. ADK ``State``
persists only on key assignment, so every write copies, modifies and re-assigns.
All validation happens before any write, so a failure leaves state unchanged.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import MutableMapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

from logisly_agents.config import get_settings

KEY = "uc3:tracker"
TITLE_RE = re.compile(r"^\[[a-z-]+\] .+")


class TrackerError(Exception):
    pass


class MockTracker:
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
        self.repos = set(conventions["repos"])
        self.labels: set[str] = set()
        for group in conventions["labels"].values():
            self.labels.update(group)  # list items or dict keys
        self.sprints = {s["name"] for s in calendar["sprints"]}
        if KEY not in state:
            issues = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))["issues"]
            nxt: dict[str, int] = {}
            for i in issues:
                nxt[i["repo"]] = max(nxt.get(i["repo"], 0), i["number"])
            state[KEY] = {"issues": copy.deepcopy(issues), "next": {r: n + 1 for r, n in nxt.items()}}

    # -- helpers
    def _data(self) -> dict:
        return copy.deepcopy(self.state[KEY])

    def _save(self, data: dict) -> None:
        self.state[KEY] = data

    @staticmethod
    def _parse(ref: str) -> tuple[str, int]:
        m = re.fullmatch(r"([a-z-]+)#(\d+)", ref) if isinstance(ref, str) else None
        if not m:
            raise TrackerError(f"ref tidak valid: {ref!r} (format '<repo>#<n>')")
        return m.group(1), int(m.group(2))

    def _find(self, data: dict, ref: str) -> dict:
        repo, n = self._parse(ref)
        for i in data["issues"]:
            if i["repo"] == repo and i["number"] == n:
                return i
        raise TrackerError(f"issue tidak ditemukan: {ref}")

    def _check_labels(self, labels) -> None:
        unknown = [x for x in labels if x not in self.labels]
        if unknown:
            raise TrackerError(f"label tidak dikenal: {unknown}")

    def _check_milestone(self, milestone) -> None:
        if milestone is not None and milestone not in self.sprints:
            raise TrackerError(f"milestone bukan sprint di kalender: {milestone}")

    @staticmethod
    def _summary(i: dict) -> dict:
        out = {k: v for k, v in i.items() if k not in ("comments", "body")}
        return copy.deepcopy(out) | {"ref": f"{i['repo']}#{i['number']}"}

    # -- reads
    def search(
        self, repo: str = "", state_filter: str = "open", text: str = "", label: str = ""
    ) -> list[dict]:
        out = []
        for i in self.state[KEY]["issues"]:
            if repo and i["repo"] != repo:
                continue
            if state_filter != "all" and i["state"] != state_filter:
                continue
            if label and label not in i["labels"]:
                continue
            if text and text.lower() not in (i["title"] + "\n" + i["body"]).lower():
                continue
            out.append(self._summary(i))
        return out

    def get(self, ref: str) -> dict:
        return copy.deepcopy(self._find(self.state[KEY], ref))

    # -- writes
    def create(
        self, repo: str, title: str, body: str, labels: list[str], milestone: str | None, assignees: list[str]
    ) -> dict:
        if repo not in self.repos:
            raise TrackerError(f"repo tidak dikenal: {repo}")
        if not TITLE_RE.match(title or ""):
            raise TrackerError("judul harus berformat '[area] kalimat'")
        for prefix in ("type:", "area:", "priority:"):
            if sum(1 for x in labels if x.startswith(prefix)) != 1:
                raise TrackerError(f"harus tepat satu label {prefix}*")
        self._check_labels(labels)
        self._check_milestone(milestone)
        data = self._data()
        n = data["next"].get(repo, 1)
        data["next"][repo] = n + 1
        data["issues"].append(
            {
                "repo": repo,
                "number": n,
                "state": "open",
                "title": title,
                "labels": list(labels),
                "milestone": milestone,
                "assignees": list(assignees),
                "author": "ai-agent",
                "created_at": self.today,
                "body": body,
                "comments": [],
            }
        )
        self._save(data)
        return {"ref": f"{repo}#{n}"}

    def update(
        self,
        ref: str,
        add_labels=(),
        remove_labels=(),
        milestone: str | None = None,
        assignees: list[str] | None = None,
        body_append: str = "",
    ) -> dict:
        self._check_labels(add_labels)
        self._check_milestone(milestone)
        data = self._data()
        issue = self._find(data, ref)
        labels = [x for x in issue["labels"] if x not in set(remove_labels)]
        labels += [x for x in add_labels if x not in labels]
        issue["labels"] = labels
        if milestone is not None:
            issue["milestone"] = milestone
        if assignees is not None:
            issue["assignees"] = list(assignees)
        if body_append:
            issue["body"] = (issue["body"] + "\n\n" + body_append).strip()
        self._save(data)
        return {"ref": ref}

    def comment(self, ref: str, body: str) -> dict:
        data = self._data()
        issue = self._find(data, ref)
        issue["comments"].append({"author": "ai-agent", "created_at": self.today, "body": body})
        self._save(data)
        return {"ref": ref, "comments": len(issue["comments"])}

    def close(
        self, ref: str, reason: Literal["completed", "not_planned", "duplicate"], comment: str = ""
    ) -> dict:
        if reason not in ("completed", "not_planned", "duplicate"):
            raise TrackerError(f"alasan tidak valid: {reason}")
        data = self._data()
        issue = self._find(data, ref)
        if issue["state"] == "closed":
            raise TrackerError(f"issue sudah ditutup: {ref}")
        if reason == "duplicate" and "status:duplicate" not in issue["labels"]:
            issue["labels"].append("status:duplicate")
        issue["state"] = "closed"
        issue["state_reason"] = "completed" if reason == "completed" else "not_planned"
        issue["closed_at"] = self.today
        if comment:
            issue["comments"].append(
                {"author": "ai-agent", "created_at": issue["closed_at"], "body": comment}
            )
        self._save(data)
        return {"ref": ref, "state": "closed", "state_reason": issue["state_reason"]}
