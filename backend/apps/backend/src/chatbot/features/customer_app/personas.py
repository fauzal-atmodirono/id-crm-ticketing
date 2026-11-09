"""The demo customer app's persona allow-list.

`personas.json` next to this file is a verbatim copy of the Phone tab's
personas (`.superpowers/sdd/2026-11-09-phone-caller-context/personas.json`,
mirrored in the frontend's `features/phone/personas.ts`; a test keeps the
three in step). The page sends a persona ID, never a number: the server maps
the ID to the persona's fake number here, so the page can never post as an
arbitrary (real) customer. Personas without a number ("unknown", "custom")
are not on the list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_FILE = Path(__file__).with_name("personas.json")
DEMO_TAG = "[DEMO]"


@dataclass(frozen=True)
class DemoPersona:
    id: str
    label: str
    number: str
    seed_name: str = ""
    seed_attributes: dict[str, Any] = field(default_factory=dict)


def _load() -> tuple[DemoPersona, ...]:
    raw = json.loads(_FILE.read_text(encoding="utf-8"))
    out: list[DemoPersona] = []
    for p in raw.get("personas") or []:
        number = str(p.get("number") or "").strip()
        if not number:
            continue
        contact = (p.get("crm_seed") or {}).get("contact") or {}
        out.append(
            DemoPersona(
                id=str(p["id"]),
                label=str(p.get("label") or p["id"]),
                number=number,
                seed_name=str(contact.get("name") or ""),
                seed_attributes=dict(contact.get("custom_attributes") or {}),
            )
        )
    return tuple(out)


PERSONAS: tuple[DemoPersona, ...] = _load()
_BY_ID = {p.id: p for p in PERSONAS}


def allowed_persona(persona_id: str | None) -> DemoPersona | None:
    """The persona for an exact allow-listed ID, else None."""
    if not isinstance(persona_id, str):
        return None
    return _BY_ID.get(persona_id)


def contact_name(persona: DemoPersona) -> str:
    """The Chatwoot contact name to create: always starts with "[DEMO] "."""
    name = persona.seed_name.strip()
    if not name:
        name = persona.label.split("(")[0].strip() or persona.id
    if name.startswith(DEMO_TAG):
        name = name[len(DEMO_TAG) :].strip()
    return f"{DEMO_TAG} {name}"
