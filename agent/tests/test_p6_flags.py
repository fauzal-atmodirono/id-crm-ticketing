"""P6 task 11 -- the `agent` service's half of the two-service boot check.

Only one of P6's thirteen settings reaches this service: `follow_up_date_enabled`
(`FOLLOW_UP_DATE_ENABLED`), read by `app/services/sync.py`'s `follow_up_at`
custom-attribute handling. The other twelve are backend-only and deliberately
absent from `app/config.py`.

`test_both_services_start_with_none_of_the_new_vars_set` has the same name as
its counterpart in
`backend/apps/backend/src/chatbot/features/routing/test_p6_flags.py`. The two
services are separate Python packages with separate virtualenvs, and neither can
import the other, so "both services start" is honestly a pair of tests -- one
per suite -- rather than a single test that would have to claim coverage it
cannot have.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.main import create_app

_P6_AGENT_VARS = ("FOLLOW_UP_DATE_ENABLED",)


def test_the_follow_up_flag_defaults_to_false(monkeypatch: pytest.MonkeyPatch):
    """Clearing the env first is load-bearing, not defensive: pydantic-settings
    reads `os.environ` whatever `_env_file` says, and
    `deploy/scripts/check-suites-both-flag-states.sh` runs this suite a second
    time with `FOLLOW_UP_DATE_ENABLED=true` exported. Without the delenv this
    test would assert the opposite of its own name on the run that exists to
    find defects.
    """
    for var in _P6_AGENT_VARS:
        monkeypatch.delenv(var, raising=False)
    assert Settings().follow_up_date_enabled is False


async def test_both_services_start_with_none_of_the_new_vars_set(
    monkeypatch: pytest.MonkeyPatch,
):
    for var in _P6_AGENT_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    try:
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
        assert response.status_code == 200
        assert get_settings().follow_up_date_enabled is False
    finally:
        get_settings.cache_clear()
