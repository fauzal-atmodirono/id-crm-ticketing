"""Flat tenant-configurable option lists (case_type/vehicle_model). Mirrors
backend/'s option_lists.py — duplicated, not shared, per this repo's
agent/backend decoupling."""

from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)


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
    raw = (raw_json or "").strip()
    if not raw:
        return OptionList([])
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("option_list_json_parse_failed: %s", exc)
        return OptionList([])
    if not isinstance(data, dict):
        _log.warning("option_list_json_not_a_dict: got %s", type(data).__name__)
        return OptionList([])
    raw_options = data.get("options")
    if not isinstance(raw_options, list):
        _log.warning("option_list_options_key_missing_or_wrong_type")
        return OptionList([])
    return OptionList([str(o) for o in raw_options])
