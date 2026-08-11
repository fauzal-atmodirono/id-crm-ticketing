"""P12 Task 1 -- Phone number E.164 normalisation.

Safely normalises Malaysian and international phone numbers into E.164 format.
Unparseable inputs return None rather than a wrong match to avoid displaying
one customer's records to another.
"""

from __future__ import annotations

import re


def normalise_phone_number(raw: str | None, default_region: str = "MY") -> str | None:
    """Normalise raw phone string to E.164 format."""
    if not raw or not isinstance(raw, str):
        return None

    cleaned = re.sub(r"[\s\-\(\)\.]", "", raw.strip())
    if not cleaned:
        return None

    # E.164 already
    if cleaned.startswith("+"):
        if re.match(r"^\+[1-9]\d{6,14}$", cleaned):
            return cleaned
        return None

    # Malaysian domestic format: 0123456789 -> +60123456789
    if default_region == "MY":
        if cleaned.startswith("0") and len(cleaned) >= 9:
            return f"+60{cleaned[1:]}"
        if cleaned.startswith("60") and len(cleaned) >= 10:
            return f"+{cleaned}"
        if len(cleaned) in (9, 10) and not cleaned.startswith("0"):
            return f"+60{cleaned}"

    # Generic check for non-plus numbers with country code
    if re.match(r"^[1-9]\d{6,14}$", cleaned):
        return f"+{cleaned}"

    return None
