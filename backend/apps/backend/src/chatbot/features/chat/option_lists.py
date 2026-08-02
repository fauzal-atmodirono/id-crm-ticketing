"""Flat tenant-configurable option lists — CASE_TYPE_OPTIONS_JSON and
VEHICLE_MODELS_JSON share this loader since both are the same shape: a bare
list of display strings (unlike case_taxonomy.py's nested category ->
subcategories structure). Mirrors case_taxonomy.py's fail-open JSON parsing:
malformed/absent config never crashes the app, it just yields an empty list
(classify_ticket_tool then no-ops for that dimension).
"""

from __future__ import annotations

import json

import structlog

_log = structlog.get_logger(__name__)


class OptionList:
    def __init__(self, options: list[str]) -> None:
        self._options = options
        self._lower = {o.lower() for o in options}

    def options(self) -> list[str]:
        return list(self._options)

    def is_valid(self, value: str) -> bool:
        return value.lower() in self._lower

    def is_empty(self) -> bool:
        return not self._options


def build_option_list(raw_json: str) -> OptionList:
    """Parse a `{"options": [...]}` JSON blob into an OptionList.

    Returns an empty OptionList (is_valid always False) when the JSON is
    absent, malformed, not an object, or "options" isn't a list.
    """
    raw = (raw_json or "").strip()
    if not raw:
        return OptionList([])
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("option_list_json_parse_failed", error=str(exc))
        return OptionList([])
    if not isinstance(data, dict):
        _log.warning("option_list_json_not_a_dict", got=type(data).__name__)
        return OptionList([])
    raw_options = data.get("options")
    if not isinstance(raw_options, list):
        _log.warning("option_list_options_key_missing_or_wrong_type")
        return OptionList([])
    return OptionList([str(o) for o in raw_options])
