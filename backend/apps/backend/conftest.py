"""Pytest-wide fixtures.

Tests must be hermetic: they assert against Settings *defaults* and construct
controlled Settings in-test. The application config loads a local ``.env``
(``config.py`` ``model_config``), so without isolation a developer's real
``.env`` (e.g. ``CHATWOOT_INBOX_ID=3``, a webhook secret) leaks into every
``Settings()`` and breaks default/behaviour assertions. This autouse fixture
disables ``.env`` loading for the whole test session so results don't depend on
whatever happens to be in the working tree's ``.env``.
"""

from __future__ import annotations

import pytest

from chatbot.platform.config import Settings


@pytest.fixture(autouse=True)
def _isolate_settings_from_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
