"""Load personas.json (the server-side persona list) once per process."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

_PERSONAS_PATH = Path(__file__).with_name("personas.json")


@cache
def _load(path: Path) -> tuple[dict[str, Any], ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(data["personas"])


def load_personas(path: Path | None = None) -> list[dict[str, Any]]:
    return [dict(p) for p in _load(path or _PERSONAS_PATH)]


def seeded_numbers(personas: list[dict[str, Any]] | None = None) -> tuple[str, ...]:
    """The numbers of the personas the seeder creates a contact for -- the
    only numbers a contact it owns can have."""
    return tuple(
        p["number"] for p in (personas or load_personas()) if p.get("crm_seed") and p.get("number")
    )


def seeded_plates(personas: list[dict[str, Any]] | None = None) -> tuple[str, ...]:
    """Plates of the seeded RSA incidents (fallback match when a tenant's
    rsa_incidents has no customer_phone column)."""
    return tuple(
        str(i.get("vehicle_no") or "")
        for p in (personas or load_personas())
        for i in ((p.get("crm_seed") or {}).get("rsa_incidents") or [])
        if i.get("vehicle_no")
    )


def persona_language(number: str | None, personas: list[dict[str, Any]] | None = None) -> str:
    """The test-console persona's language ("en"/"ms"/"zh") for a web
    call-as number, "" when no persona has that number. Never raises: a
    missing/broken personas.json just means no preferred language."""
    if not number:
        return ""
    try:
        rows = personas if personas is not None else load_personas()
    except Exception:
        return ""
    for p in rows:
        if p.get("number") == number:
            return str(p.get("language") or "")
    return ""
