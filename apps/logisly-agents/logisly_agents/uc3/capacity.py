"""Sprint capacity check for the sprints before code freeze."""

from __future__ import annotations

from datetime import date, timedelta

TEAM_REPOS = {
    "mobile": {"driver-app"},
    "backend": {"platform-api"},
    "frontend": {"shipper-portal", "ops-console"},
}


def _d(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _workdays(start: date, end: date) -> int:
    n = (end - start).days + 1
    return sum(1 for i in range(max(n, 0)) if (start + timedelta(days=i)).weekday() < 5)


def _num(x: float):
    x = round(x, 1)
    return int(x) if x == int(x) else x


def _team_of(person: dict) -> str | None:
    repos = set(person.get("repos") or [])
    for team, team_repos in TEAM_REPOS.items():
        if repos & team_repos:
            return team
    return None


def _leave_fraction(person: dict, sprint: dict) -> float:
    s_start, s_end = _d(sprint["start"]), _d(sprint["end"])
    total = _workdays(s_start, s_end)
    lost = 0
    for lv in person.get("leave") or []:
        lo, hi = max(_d(lv["from"]), s_start), min(_d(lv["to"]), s_end)
        if lo <= hi:
            lost += _workdays(lo, hi)
    return lost / total if total else 0.0


def capacity_check(items: list[dict], roster: dict, calendar: dict) -> dict:
    freeze = _d(calendar["code_freeze"]["start"])
    sprints = [s for s in calendar["sprints"] if _d(s["end"]) < freeze]
    window = [s["name"] for s in sprints]
    people = roster["people"]
    by_login = {p["github"]: p for p in people}
    committed_map = roster.get("committed") or {}

    cap: dict[str, float] = {t: 0.0 for t in TEAM_REPOS}
    com: dict[str, float] = {t: 0.0 for t in TEAM_REPOS}
    for p in people:
        team = _team_of(p)
        if team is None or not p.get("capacity_sp"):
            continue
        for s in sprints:
            cap[team] += p["capacity_sp"] * (1 - _leave_fraction(p, s))
            com[team] += (committed_map.get(s["name"]) or {}).get(p["name"], 0)

    proposed = {t: 0 for t in TEAM_REPOS}
    after_window, no_team, unknown = [], [], []
    for it in items:
        person = by_login.get(it["assignee"])
        if person is None:
            if it["assignee"] not in unknown:
                unknown.append(it["assignee"])
            continue
        team = _team_of(person)
        if team is None:
            no_team.append(dict(it))
        elif it["sprint"] not in window:
            after_window.append(dict(it))
        else:
            proposed[team] += it["sp"]

    teams = {}
    for t in TEAM_REPOS:
        free = cap[t] - com[t]
        teams[t] = {
            "capacity": _num(cap[t]),
            "committed": _num(com[t]),
            "free": _num(free),
            "proposed": proposed[t],
            "utilisation_pct": round(proposed[t] / free * 100) if free > 0 else 0,
        }
        if free <= 0 and proposed[t] > 0:
            # 0% would hide an over-commitment: flag it explicitly instead.
            teams[t]["utilisation_pct"] = None
            teams[t]["overloaded"] = True
    return {
        "window": window,
        "teams": teams,
        "after_window": after_window,
        "no_team": no_team,
        "unknown_assignees": unknown,
    }
