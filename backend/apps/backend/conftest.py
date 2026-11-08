"""Pytest-wide fixtures.

Tests must be hermetic: they assert against Settings *defaults* and construct
controlled Settings in-test. The application config loads a local ``.env``
(``config.py`` ``model_config``), so without isolation a developer's real
``.env`` (e.g. ``CHATWOOT_INBOX_ID=3``, a webhook secret) leaks into every
``Settings()`` and breaks default/behaviour assertions. This autouse fixture
disables ``.env`` loading for the whole test session so results don't depend on
whatever happens to be in the working tree's ``.env``.

The second fixture keeps tests off real Google Cloud. A developer machine with
ADC configured would otherwise let a test that forgot to inject a fake build a
real ``bigquery.Client`` and query a real warehouse (it happened: Task 28's
review found an ai-cost test reading a real project). Every real construction
raises a clear error AND is recorded, and the test fails at teardown if one was
attempted -- recording matters because much production code deliberately
swallows a client-construction failure (fail-open adapters), which would
otherwise hide the attempt. A test that wants to exercise that failure path
monkeypatches ``bigquery.Client`` itself, which replaces this guard.
"""

from __future__ import annotations

import pytest

from chatbot.platform.config import Settings


class RealGcpClientInTests(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _isolate_settings_from_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _no_real_bigquery(monkeypatch: pytest.MonkeyPatch):
    from google.cloud import bigquery  # noqa: PLC0415

    attempts: list[str] = []

    def _refuse(*args, **kwargs):  # noqa: ARG001
        attempts.append("google.cloud.bigquery.Client")
        raise RealGcpClientInTests(
            "real BigQuery client in tests: inject a fake client/port instead"
        )

    monkeypatch.setattr(bigquery, "Client", _refuse)
    # NOT also guarding google.auth.default: measured 2026-11-09, 55 app-boot
    # tests resolve ADC while constructing Firestore/other Google clients at
    # startup (construction only, no query). Making those hermetic is its own
    # change; BigQuery -- where tests could actually read warehouse data --
    # is guarded today with zero offenders.
    yield
    if attempts:
        pytest.fail(
            "test tried to build a real BigQuery client "
            f"({len(attempts)}x); inject a fake client or port instead",
            pytrace=False,
        )
