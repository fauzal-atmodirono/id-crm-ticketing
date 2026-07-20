from __future__ import annotations

import pytest

from chatbot.features.chat.router import _is_no_reply


@pytest.mark.parametrize(
    "email,expected",
    [
        ("no-reply@acme.com", True),
        ("noreply@acme.com", True),
        ("mailer-daemon@acme.com", True),
        ("postmaster@acme.com", True),
        ("donotreply@acme.com", True),
        ("alice@acme.com", False),
        (None, False),
        ("", False),
    ],
)
def test_is_no_reply(email: str | None, expected: bool) -> None:
    assert _is_no_reply(email) is expected
