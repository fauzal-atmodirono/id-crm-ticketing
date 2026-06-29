from typing import Any

import pytest

from chatbot.features.chat.phone.handoff_csat_tools import (
    REQUEST_HANDOFF_TOOL,
    SUBMIT_CSAT_TOOL,
    parse_csat_score,
)


def test_tools_declare_expected_function_names() -> None:
    handoff = [fd.name for fd in (REQUEST_HANDOFF_TOOL.function_declarations or [])]
    csat = [fd.name for fd in (SUBMIT_CSAT_TOOL.function_declarations or [])]
    assert "request_human_handoff" in handoff
    assert "submit_csat" in csat


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"score": 5}, 5),
        ({"score": 1}, 1),
        ({"score": 5.0}, 5),
        ({"score": "4"}, 4),
        ({"score": 0}, None),
        ({"score": 6}, None),
        ({"score": "x"}, None),
        ({}, None),
        ({"score": None}, None),
    ],
)
def test_parse_csat_score(args: dict[str, Any], expected: int | None) -> None:
    assert parse_csat_score(args) == expected
