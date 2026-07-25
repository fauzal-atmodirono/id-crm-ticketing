"""kb_grounded_replies config flag: defaults off, reads the env var."""
from __future__ import annotations

from app.config import get_settings


def test_kb_grounded_replies_defaults_false():
    # conftest sets required env but not KB_GROUNDED_REPLIES → default False
    assert get_settings().kb_grounded_replies is False
