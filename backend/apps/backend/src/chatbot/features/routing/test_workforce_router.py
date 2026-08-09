"""P6 task 9 -- the workforce dashboard router.

No Firestore, no real Chatwoot: `build_workforce_router` depends only on
small structural Protocols (`_AgentDirectory`, `_PresenceLog`,
`_StatusCatalogue`), so every collaborator here is a purpose-built
in-memory fake -- the same style `test_presence_thresholds.py` and
`test_custom_status.py` use for theirs. RBAC plumbing (the one test that
needs a real permission denial) mirrors `test_customer360_router.py`'s
`_build_authz_repo`/`_authorized` helpers exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine as build_authz_engine
from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
from chatbot.features.authz.db import init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.features.routing.custom_status import CustomStatus
from chatbot.features.routing.presence import AgentRecord
from chatbot.features.routing.presence_store import PresenceEvent
from chatbot.features.routing.workforce_router import build_workforce_router
from chatbot.platform.config import get_settings

HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}

# RBAC-disabled default (matching `require_permission`'s `_shared_secret_check`
# fallback) -- these tests exercise the dashboard's data shape, not RBAC
# itself, so every one of them authenticates via the plain shared-secret path.
API_KEY = "workforce-test-secret"
API_KEY_HEADERS = {"x-api-key": API_KEY}

AGENT = AgentRecord(id=1, name="Ahmad", availability_status="busy", email="ahmad@example.com")
OTHER_AGENT = AgentRecord(id=2, name="Bea", availability_status="online", email="bea@example.com")


# --- fakes -------------------------------------------------------------


class _FakeAgents:
    def __init__(
        self, agents: list[AgentRecord], open_counts: dict[int, int] | None = None
    ) -> None:
        self._agents = agents
        self._open_counts = open_counts or {}

    async def fetch_agents(self) -> list[AgentRecord]:
        return list(self._agents)

    async def fetch_agent_open_counts(self) -> dict[int, int]:
        return dict(self._open_counts)


class _FakePresenceLog:
    """Per-agent scripted answers -- `agent_id -> ...` -- so a single fake
    instance can serve a whole roster in one test."""

    def __init__(
        self,
        *,
        latest: dict[int, PresenceEvent | None] | None = None,
        elapsed: dict[int, timedelta | None] | None = None,
        since_events: dict[int, list[PresenceEvent]] | None = None,
        time_in_status: dict[int, dict[str, timedelta]] | None = None,
    ) -> None:
        self._latest = latest or {}
        self._elapsed = elapsed or {}
        self._since_events = since_events or {}
        self._time_in_status = time_in_status or {}

    async def latest(self, agent_id: int) -> PresenceEvent | None:
        return self._latest.get(agent_id)

    async def since(self, agent_id: int, at: datetime) -> list[PresenceEvent]:
        return list(self._since_events.get(agent_id, []))

    async def elapsed_in_current_status(self, agent_id: int, now: datetime) -> timedelta | None:
        return self._elapsed.get(agent_id)

    async def time_in_status_since(
        self, agent_id: int, since: datetime, now: datetime
    ) -> dict[str, timedelta]:
        return dict(self._time_in_status.get(agent_id, {}))


def _status(key: str, *, counts_as_unavailable: bool, native: str = "busy") -> CustomStatus:
    return CustomStatus(
        key=key,
        label=key.title(),
        color="#123456",
        routable=False,
        native=native,
        counts_as_unavailable=counts_as_unavailable,
    )


class _FakeStatusCatalogue:
    def __init__(self, statuses: dict[str, CustomStatus] | None = None) -> None:
        self._statuses = statuses or {}

    async def get(self, key: str) -> CustomStatus | None:
        return self._statuses.get(key)


CATALOGUE = _FakeStatusCatalogue(
    {
        "available": _status("available", counts_as_unavailable=False, native="online"),
        "lunch": _status("lunch", counts_as_unavailable=True),
        "busy": _status("busy", counts_as_unavailable=False),
    }
)


def _no_inbox() -> dict[str, Any] | None:
    """Default inbox-hours fetcher: no inbox configured -- always-open
    fallback, so tests that don't care about business hours aren't tripped
    up by them."""
    return None


def _app_with_router(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _build_router(
    *,
    agents: list[AgentRecord],
    open_counts: dict[int, int] | None = None,
    presence_store: _FakePresenceLog | None = None,
    status_store: _FakeStatusCatalogue | None = None,
    inbox_hours_fetcher=_no_inbox,
    now: datetime | None = None,
    settings_overrides: dict[str, Any] | None = None,
):
    settings = get_settings().model_copy(
        update={"faq_admin_api_key": API_KEY, **(settings_overrides or {})}
    )
    clock = now or datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    return build_workforce_router(
        settings,
        None,
        None,
        presence_fetcher=_FakeAgents(agents, open_counts),
        presence_store=presence_store or _FakePresenceLog(),
        status_store=status_store or CATALOGUE,
        inbox_hours_fetcher=inbox_hours_fetcher,
        now_fn=lambda: clock,
    )


# --- tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_response_lists_every_agent_with_a_current_status_and_elapsed_time():
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    presence_store = _FakePresenceLog(
        latest={
            AGENT.id: PresenceEvent(
                agent_id=AGENT.id,
                status="lunch",
                at=now - timedelta(minutes=15),
                source="agent",
                previous="available",
            ),
            OTHER_AGENT.id: PresenceEvent(
                agent_id=OTHER_AGENT.id,
                status="available",
                at=now - timedelta(minutes=5),
                source="agent",
                previous="lunch",
            ),
        },
        elapsed={AGENT.id: timedelta(minutes=15), OTHER_AGENT.id: timedelta(minutes=5)},
    )
    router = _build_router(agents=[AGENT, OTHER_AGENT], presence_store=presence_store, now=now)
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    body = res.json()
    rows = {row["agent_id"]: row for row in body["agents"]}

    assert rows[AGENT.id]["current_status"]["key"] == "lunch"
    assert rows[AGENT.id]["current_status"]["label"] == "Lunch"
    assert rows[AGENT.id]["current_status"]["elapsed_minutes"] == 15.0

    assert rows[OTHER_AGENT.id]["current_status"]["key"] == "available"
    assert rows[OTHER_AGENT.id]["current_status"]["elapsed_minutes"] == 5.0


@pytest.mark.asyncio
async def test_todays_time_per_status_is_returned_per_agent():
    presence_store = _FakePresenceLog(
        time_in_status={
            AGENT.id: {"available": timedelta(minutes=100), "lunch": timedelta(minutes=20)}
        }
    )
    router = _build_router(agents=[AGENT], presence_store=presence_store)
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    row = res.json()["agents"][0]
    assert row["time_in_status_today_minutes"] == {"available": 100.0, "lunch": 20.0}


@pytest.mark.asyncio
async def test_the_availability_percentage_is_computed_over_the_working_day_not_24h():
    """An agent who was 'available' for the entire elapsed working day (and
    only the working day -- the presence log has nothing outside it) must
    show close to 100%, never the ~33-40% a naive 24-hour-denominator
    computation would produce for an 8-hour working day this far into a
    24h+ span. This is the test that decides whether the whole page means
    anything -- see the router's module docstring, point 1.
    """
    base = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)  # local midnight, UTC inbox tz
    now = base + timedelta(hours=20)  # well past a 09:00-17:00 shift
    inbox = {
        "working_hours_enabled": True,
        "timezone": "UTC",
        "working_hours": [
            {
                "day_of_week": base.isoweekday() % 7,
                "open_hour": 9,
                "open_minutes": 0,
                "close_hour": 17,
                "close_minutes": 0,
                "open_all_day": False,
                "closed_all_day": False,
            }
        ],
    }
    presence_store = _FakePresenceLog(
        since_events={
            AGENT.id: [
                PresenceEvent(
                    agent_id=AGENT.id, status="available", at=base, source="agent", previous=None
                )
            ]
        }
    )
    router = _build_router(
        agents=[AGENT],
        presence_store=presence_store,
        inbox_hours_fetcher=lambda: inbox,
        now=now,
    )
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    row = res.json()["agents"][0]

    # The working day (09:00-17:00 = 480 minutes) was spent entirely
    # "available" -> 100%, nowhere near the naive 24h-denominator figure
    # (480 / (20*60) = 40%, or 480/1440 = 33% for a full calendar day).
    assert row["availability_percent_of_working_day"] == 100.0


@pytest.mark.asyncio
async def test_an_agent_with_events_yesterday_but_none_today_gets_a_real_availability_percent():
    """Finding I3: an agent whose last transition predates today (so
    `since(day_start)` comes back empty) must NOT render
    `availability_percent_of_working_day: null` next to a nonzero
    `time_in_status_today_minutes` for the same status -- that contradicted
    itself. The carried-forward status (from `latest()`) must be credited
    for the whole elapsed working day, exactly like `time_in_status_since`
    (task 1's store) already credits it. This is a DIFFERENT agent shape
    than `test_an_agent_with_no_events_today_renders_without_error`, which
    has no events at all, ever -- that one must still render `null`.
    """
    base = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)  # local midnight, UTC inbox tz
    now = base + timedelta(hours=20)
    inbox = {
        "working_hours_enabled": True,
        "timezone": "UTC",
        "working_hours": [
            {
                "day_of_week": base.isoweekday() % 7,
                "open_hour": 9,
                "open_minutes": 0,
                "close_hour": 17,
                "close_minutes": 0,
                "open_all_day": False,
                "closed_all_day": False,
            }
        ],
    }
    carried_event = PresenceEvent(
        agent_id=AGENT.id,
        status="available",
        at=base - timedelta(hours=16),  # yesterday 08:00 -- well before today
        source="agent",
        previous="lunch",
    )
    presence_store = _FakePresenceLog(
        latest={AGENT.id: carried_event},
        since_events={AGENT.id: []},  # nothing today -- the exact I3 shape
        time_in_status={AGENT.id: {"available": timedelta(minutes=1200)}},
    )
    router = _build_router(
        agents=[AGENT],
        presence_store=presence_store,
        inbox_hours_fetcher=lambda: inbox,
        now=now,
    )
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    row = res.json()["agents"][0]

    # The carried "available" status covers the entire elapsed working day
    # -> 100%, not null. Consistent with the nonzero
    # time_in_status_today_minutes on the same row -- the two no longer
    # contradict each other.
    assert row["time_in_status_today_minutes"] == {"available": 1200.0}
    assert row["availability_percent_of_working_day"] == 100.0


@pytest.mark.asyncio
async def test_open_case_counts_are_included():
    router = _build_router(agents=[AGENT], open_counts={AGENT.id: 4})
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["agents"][0]["open_case_count"] == 4
    # The tally was clearly established (someone has a nonzero count) --
    # no caveat needed.
    assert body["open_case_count_caveat"] is None


@pytest.mark.asyncio
async def test_an_agent_absent_from_a_non_empty_tally_gets_a_real_zero():
    """Once the tally is known to have worked (AGENT has a nonzero count),
    an agent simply missing from it has a genuine 0 open cases -- this is
    the "fetched, and this agent has none" state, distinct from "could not
    be fetched" (finding I4)."""
    router = _build_router(agents=[AGENT, OTHER_AGENT], open_counts={AGENT.id: 4})
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    rows = {row["agent_id"]: row for row in res.json()["agents"]}
    assert rows[AGENT.id]["open_case_count"] == 4
    assert rows[OTHER_AGENT.id]["open_case_count"] == 0


@pytest.mark.asyncio
async def test_an_empty_open_case_tally_renders_as_unavailable_not_zero():
    """Finding I4: `fetch_agent_open_counts()` returns `{}` both when
    Chatwoot's pager fails AND when the account genuinely has zero open
    conversations -- this router cannot tell those apart, so it must not
    render the ambiguous case as a fabricated 0 for the whole team."""
    router = _build_router(agents=[AGENT, OTHER_AGENT], open_counts={})
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["open_case_count_caveat"] is not None
    for row in body["agents"]:
        assert row["open_case_count"] is None


@pytest.mark.asyncio
async def test_an_agent_with_no_events_today_renders_without_error():
    """The store returns None/[]/{} for an agent it has never seen -- the
    row must show *unknown*, never a fabricated zero (see the router's
    module docstring, point 2)."""
    never_seen = AgentRecord(id=99, name="New Agent", availability_status="offline", email="")
    router = _build_router(agents=[never_seen], presence_store=_FakePresenceLog())
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    row = res.json()["agents"][0]

    assert row["current_status"]["key"] == "offline"  # falls back to the live native status
    assert row["current_status"]["elapsed_minutes"] is None
    assert row["time_in_status_today_minutes"] == {}
    assert row["availability_percent_of_working_day"] is None
    assert row["availability_history"] == []
    assert row["cases_closed_today"] is None


@pytest.mark.asyncio
async def test_the_response_carries_a_last_updated_timestamp():
    """Every other dashboard in this system is a 6-hour batch report; this
    one reads the presence store live. The response must say so with its
    own freshness stamp rather than implying a streamed feed."""
    now = datetime(2026, 8, 9, 9, 30, tzinfo=UTC)
    router = _build_router(agents=[AGENT], now=now)
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=API_KEY_HEADERS)
    assert res.status_code == 200
    body = res.json()

    assert body["generated_at"] == now.isoformat()
    assert body["refresh"]["mode"] == "poll"
    assert "streamed" in body["refresh"]["note"] or "poll" in body["refresh"]["note"].lower()
    assert isinstance(body["refresh"]["recommended_interval_seconds"], int)


async def _build_authz_repo(tmp_path, name: str) -> AuthzRepository:
    authz_engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(authz_engine)
    return AuthzRepository(build_authz_session_maker(authz_engine))


@pytest.mark.asyncio
async def test_an_unauthorised_caller_is_rejected(tmp_path, respx_mock):
    """An RBAC-enabled caller without `workforce.view` is denied -- the new
    permission actually gates the endpoint, not just exists in the
    registry."""
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "no_perm")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=9, role_id="agent")  # lacks workforce.view

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    validator = TokenValidator(settings)
    router = build_workforce_router(
        settings,
        authz_repo,
        validator,
        presence_fetcher=_FakeAgents([AGENT]),
        presence_store=_FakePresenceLog(),
        status_store=CATALOGUE,
        inbox_hours_fetcher=_no_inbox,
    )
    client = _app_with_router(router)

    res = client.get("/admin/workforce", headers=HEADERS)
    assert res.status_code == 403
