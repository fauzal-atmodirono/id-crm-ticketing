"""Deterministic loading of the UC3 context files (templates, conventions, team, calendar)."""

from __future__ import annotations

from pathlib import Path

import yaml


def read_context(uc3_dir: Path) -> dict:
    uc3_dir = Path(uc3_dir)
    templates = {p.name: p.read_text(encoding="utf-8") for p in sorted((uc3_dir / "templates").glob("*.md"))}

    def load(rel: str) -> dict:
        return yaml.safe_load((uc3_dir / rel).read_text(encoding="utf-8"))

    return {
        "templates": templates,
        "conventions": load("jira/conventions.yaml"),
        "roster": load("team/roster.yaml"),
        "calendar": load("team/calendar.yaml"),
    }
