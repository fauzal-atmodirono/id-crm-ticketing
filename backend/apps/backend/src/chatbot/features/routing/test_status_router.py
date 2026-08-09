"""P6 C1 fix -- the status-selection write path.

Most of this file is ordinary router testing. One test is the reason the file
exists:

`test_three_hours_of_lunch_selected_through_the_api_fires_exactly_two_alerts`
drives the whole chain the product actually runs -- HTTP POST -> RBAC
identity resolution -> `CustomStatusStore.set_status` -> a native Chatwoot
mirror -> a real `PresenceEventStore` append -> thirteen sweeps of the real
`sweep_presence_thresholds` reading that same store -- with nothing scripted
in between. Every collaborator that is faked here is faked at the *edge*
(Firestore, the Chatwoot availability PATCH, the Chatwoot agent list, the
alert transports); no presence event is hand-inserted.

That distinction is the whole point. P6's unit suites were green while
requirements 4.13/4.14 could not fire on any tenant, because every test
injected a `lunch` presence event directly into a fake store -- a value no
production writer could produce, since nothing could select a custom status.
An integration-level test is the only thing that would have caught it, so
this one deliberately refuses to insert an event by hand.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

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
from chatbot.features.routing.custom_status import CustomStatusStore
from chatbot.features.routing.presence import AgentRecord
from chatbot.features.routing.presence_store import PresenceEvent, PresenceEventStore
from chatbot.features.routing.presence_thresholds import (
    ESCALATE_ALERT_KEY,
    WARN_ALERT_KEY,
    WipSummary,
    sweep_presence_thresholds,
)
from chatbot.features.routing.status_router import build_status_router
from chatbot.platform.config import get_settings

AGENT_ID = 42
COLLEAGUE_ID = 7
SUPERVISOR_ID = 99

# The devise_token_auth triplet the fork's `adminRequest` forwards.
HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "agent@proton.example",
}
API_KEY = "status-router-test-secret"
API_KEY_HEADERS = {"x-api-key": API_KEY}


# --- a Firestore fake both stores can share -----------------------------
#
# Combines what `test_custom_status.py` needs (document().get()/set()) with
# what `test_presence_store.py` needs (add(), where().stream(),
# snapshot.reference.update()), because the integration test below runs the
# real CustomStatusStore and the real PresenceEventStore against one fake.


class _FakeDocRef:
    def __init__(self, collection: dict[str, dict[str, Any]], doc_id: str) -> None:
        self._collection, self._doc_id = collection, doc_id

    def get(self) -> _FakeDocSnapshot:
        return _FakeDocSnapshot(self._doc_id, self._collection.get(self._doc_id), self._collection)

    def set(self, data: dict[str, Any]) -> None:
        self._collection[self._doc_id] = dict(data)

    def update(self, fields: dict[str, Any]) -> None:
        self._collection[self._doc_id].update(fields)


class _FakeDocSnapshot:
    def __init__(
        self,
        doc_id: str,
        data: dict[str, Any] | None,
        collection: dict[str, dict[str, Any]],
    ) -> None:
        self.id = doc_id
        self._data = data
        self._collection = collection

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None

    @property
    def reference(self) -> _FakeDocRef:
        return _FakeDocRef(self._collection, self.id)


class _FakeQuery:
    """`.where(...).order_by(...).limit(...).stream()`, honoured for real.

    `order_by`/`limit` sort and truncate rather than being accepted and
    ignored: `PresenceEventStore`'s reads are bounded server-side, and a fake
    that ignored the bound would happily return the whole history a real
    `limit(1)` query would not.
    """

    def __init__(
        self, collection: dict[str, dict[str, Any]], docs: list[tuple[str, dict[str, Any]]]
    ) -> None:
        self._collection, self._docs = collection, docs

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        assert op == "==", "these stores only ever issue equality filters"
        return _FakeQuery(
            self._collection,
            [(k, d) for k, d in self._docs if d.get(field) == value],
        )

    def order_by(self, field: str, direction: str = "ASCENDING") -> _FakeQuery:
        ordered = sorted(self._docs, key=lambda kv: kv[1][field], reverse=direction == "DESCENDING")
        return _FakeQuery(self._collection, ordered)

    def limit(self, count: int) -> _FakeQuery:
        return _FakeQuery(self._collection, self._docs[:count])

    def stream(self) -> list[_FakeDocSnapshot]:
        return [_FakeDocSnapshot(k, d, self._collection) for k, d in self._docs]


class _FakeCollection:
    def __init__(self, collection: dict[str, dict[str, Any]]) -> None:
        self._collection = collection

    def document(self, key: str) -> _FakeDocRef:
        return _FakeDocRef(self._collection, key)

    def add(self, data: dict[str, Any]) -> tuple[None, _FakeDocRef]:
        doc_id = uuid.uuid4().hex
        self._collection[doc_id] = dict(data)
        return None, _FakeDocRef(self._collection, doc_id)

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        return _FakeQuery(self._collection, list(self._collection.items())).where(field, op, value)

    def stream(self) -> list[_FakeDocSnapshot]:
        return [_FakeDocSnapshot(k, d, self._collection) for k, d in self._collection.items()]


class _FakeFirestoreClient:
    def __init__(self, collections: dict[str, dict[str, dict[str, Any]]]) -> None:
        self._collections = collections

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))


class _FakeAvailabilityWriter:
    """The Chatwoot `PATCH /agents/{id}` mirror."""

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.calls: list[tuple[int, str]] = []

    async def set_availability(self, agent_id: int, native: str) -> bool:
        self.calls.append((agent_id, native))
        return self.succeed


class _FakeAgents:
    def __init__(self, agents: list[AgentRecord]) -> None:
        self._agents = agents

    async def fetch_agents(self) -> list[AgentRecord]:
        return list(self._agents)


class _RecordingAlert:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, float, WipSummary | None]] = []

    async def __call__(
        self,
        agent: AgentRecord,
        level: str,
        elapsed_minutes: float,
        wip: WipSummary | None,
    ) -> None:
        self.calls.append((agent.id, level, elapsed_minutes, wip))


# --- harness ------------------------------------------------------------


def _settings(**overrides: Any) -> Any:
    return get_settings().model_copy(
        update={
            "presence_custom_statuses_enabled": True,
            "faq_admin_api_key": API_KEY,
            **overrides,
        }
    )


async def _authz_repo(tmp_path: Any, name: str) -> AuthzRepository:
    engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_authz_session_maker(engine))
    await seed_defaults(repo)
    return repo


def _mock_profile(respx_mock: Any, settings: Any, user_id: int) -> None:
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": user_id})
    )


def _client(router: Any) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _stores(
    settings: Any,
    *,
    writer: _FakeAvailabilityWriter | None = None,
) -> tuple[CustomStatusStore, PresenceEventStore, _FakeAvailabilityWriter]:
    """The REAL stores, talking to the shared Firestore fake."""
    presence = PresenceEventStore(settings)
    availability = writer or _FakeAvailabilityWriter()
    statuses = CustomStatusStore(settings, presence, availability)
    return statuses, presence, availability


def _patched_firestore(collections: dict[str, dict[str, dict[str, Any]]]) -> Any:
    """Point `firestore.Client` at one fake holding both collections, so a
    status written through the catalogue and an event read back by the sweeper
    are the same data -- the property this file's integration test is about.

    One patch covers both stores: `custom_status` and `presence_store` each do
    `from google.cloud import firestore`, which binds the same module object,
    so patching the attribute once patches it for both.
    """
    client = _FakeFirestoreClient(collections)
    return patch(
        "chatbot.features.routing.custom_status.firestore.Client",
        side_effect=lambda **_: client,
    )


# --- tests: setting a status -------------------------------------------


@pytest.mark.asyncio
async def test_an_agent_can_set_their_own_status_to_lunch(tmp_path, respx_mock):
    """The endpoint C1 said did not exist. An agent with the default `agent`
    role -- which now carries `presence.set_own_status` -- picks Lunch, and
    the native Chatwoot status is mirrored to `busy` for them."""
    settings = _settings(rbac_enabled=True)
    repo = await _authz_repo(tmp_path, "own")
    await repo.assign_role(chatwoot_user_id=AGENT_ID, role_id="agent")
    _mock_profile(respx_mock, settings, AGENT_ID)

    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, writer = _stores(settings)
        await statuses.seed()
        router = build_status_router(
            settings, repo, TokenValidator(settings), status_store=statuses
        )
        res = _client(router).post(
            "/routing/presence/status", json={"key": "lunch"}, headers=HEADERS
        )

        assert res.status_code == 200, res.text
        assert res.json() == {
            "agent_id": AGENT_ID,
            "key": "lunch",
            "source": "agent",
            "status": "ok",
        }
        assert writer.calls == [(AGENT_ID, "busy")]
        latest = await presence.latest(AGENT_ID)
        assert latest is not None
        assert latest.status == "lunch"
        assert latest.source == "agent"


@pytest.mark.asyncio
async def test_an_agent_cannot_set_a_colleagues_status(tmp_path, respx_mock):
    """Marking a colleague as Lunch removes them from routing and starts an
    absence-alert clock against their name. That needs `workforce.manage`,
    which the `agent` role does not carry."""
    settings = _settings(rbac_enabled=True)
    repo = await _authz_repo(tmp_path, "colleague")
    await repo.assign_role(chatwoot_user_id=AGENT_ID, role_id="agent")
    _mock_profile(respx_mock, settings, AGENT_ID)

    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, writer = _stores(settings)
        await statuses.seed()
        router = build_status_router(
            settings, repo, TokenValidator(settings), status_store=statuses
        )
        res = _client(router).post(
            "/routing/presence/status",
            json={"key": "lunch", "agent_id": COLLEAGUE_ID},
            headers=HEADERS,
        )

        assert res.status_code == 403
        assert "workforce.manage" in res.json()["detail"]
        assert writer.calls == []  # nothing was mirrored
        assert await presence.latest(COLLEAGUE_ID) is None  # ...and nothing recorded


@pytest.mark.asyncio
async def test_an_operator_with_workforce_manage_can_set_another_agents_status(
    tmp_path, respx_mock
):
    """The supervisor case §4.12 implies (someone forgot to mark themselves
    away). Recorded with `source="admin"`, not `"agent"`, so the history says
    who did it."""
    settings = _settings(rbac_enabled=True)
    repo = await _authz_repo(tmp_path, "operator")
    await repo.assign_role(chatwoot_user_id=SUPERVISOR_ID, role_id="administrator")
    _mock_profile(respx_mock, settings, SUPERVISOR_ID)

    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, _ = _stores(settings)
        await statuses.seed()
        router = build_status_router(
            settings, repo, TokenValidator(settings), status_store=statuses
        )
        res = _client(router).post(
            "/routing/presence/status",
            json={"key": "break", "agent_id": AGENT_ID},
            headers=HEADERS,
        )

        assert res.status_code == 200, res.text
        assert res.json()["source"] == "admin"
        latest = await presence.latest(AGENT_ID)
        assert latest is not None
        assert (latest.status, latest.source) == ("break", "admin")


@pytest.mark.asyncio
async def test_without_rbac_the_request_must_name_the_agent_it_is_setting():
    """With RBAC off the only credential is a shared secret -- there is no
    caller identity to infer "me" from, and inventing one from a header
    anybody can set is exactly the forgeable-actor defect the reassignment
    path had to fix. So the request must say who it means."""
    settings = _settings()
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, _ = _stores(settings)
        await statuses.seed()
        client = _client(build_status_router(settings, status_store=statuses))

        bare = client.post(
            "/routing/presence/status", json={"key": "lunch"}, headers=API_KEY_HEADERS
        )
        assert bare.status_code == 400
        assert "agent_id" in bare.json()["detail"]

        named = client.post(
            "/routing/presence/status",
            json={"key": "lunch", "agent_id": AGENT_ID},
            headers=API_KEY_HEADERS,
        )
        assert named.status_code == 200, named.text
        assert named.json()["source"] == "admin"
        latest = await presence.latest(AGENT_ID)
        assert latest is not None and latest.status == "lunch"


@pytest.mark.asyncio
async def test_an_unknown_status_key_is_rejected_before_anything_is_written():
    settings = _settings()
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, writer = _stores(settings)
        await statuses.seed()
        client = _client(build_status_router(settings, status_store=statuses))

        res = client.post(
            "/routing/presence/status",
            json={"key": "nap", "agent_id": AGENT_ID},
            headers=API_KEY_HEADERS,
        )

        assert res.status_code == 400
        assert writer.calls == []
        assert await presence.latest(AGENT_ID) is None


@pytest.mark.asyncio
async def test_a_failed_chatwoot_mirror_reports_502_and_appends_no_event():
    """`set_status` writes the native status first and appends only on
    success. The endpoint must not turn that into a 200: a client told the
    change succeeded would show Lunch while Chatwoot still showed online."""
    settings = _settings()
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, _ = _stores(settings, writer=_FakeAvailabilityWriter(succeed=False))
        await statuses.seed()
        client = _client(build_status_router(settings, status_store=statuses))

        res = client.post(
            "/routing/presence/status",
            json={"key": "lunch", "agent_id": AGENT_ID},
            headers=API_KEY_HEADERS,
        )

        assert res.status_code == 502
        assert await presence.latest(AGENT_ID) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["acw", "offline"])
async def test_the_system_managed_statuses_cannot_be_selected(key: str):
    """After-Call Work is entered at call end and auto-exits on a timeout;
    Offline belongs to Chatwoot's own control. Letting an agent park in
    either from this endpoint would give them a status that changes under
    them for reasons the UI never explained."""
    settings = _settings()
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, _ = _stores(settings)
        await statuses.seed()
        client = _client(build_status_router(settings, status_store=statuses))

        res = client.post(
            "/routing/presence/status",
            json={"key": key, "agent_id": AGENT_ID},
            headers=API_KEY_HEADERS,
        )

        assert res.status_code == 400
        assert await presence.latest(AGENT_ID) is None


@pytest.mark.asyncio
async def test_an_agent_reads_back_the_status_they_just_set(tmp_path, respx_mock):
    """The picker has to be able to show what is currently selected, or it
    goes blank on a page refresh and invites double-setting."""
    settings = _settings(rbac_enabled=True)
    repo = await _authz_repo(tmp_path, "readback")
    await repo.assign_role(chatwoot_user_id=AGENT_ID, role_id="agent")
    _mock_profile(respx_mock, settings, AGENT_ID)

    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, _ = _stores(settings)
        await statuses.seed()
        client = _client(
            build_status_router(
                settings,
                repo,
                TokenValidator(settings),
                status_store=statuses,
                presence_store=presence,
            )
        )

        assert (
            client.post(
                "/routing/presence/status", json={"key": "prayer"}, headers=HEADERS
            ).status_code
            == 200
        )
        body = client.get("/routing/presence/status", headers=HEADERS).json()

        assert body["agent_id"] == AGENT_ID
        assert body["key"] == "prayer"
        assert body["label"] == "Prayer"
        assert body["since"] is not None
        assert body["elapsed_minutes"] == 0


@pytest.mark.asyncio
async def test_an_agent_with_no_presence_history_reads_as_null_not_available():
    """ "No events" and "Available" are different claims -- the same rule the
    presence store and the workforce dashboard already hold. Showing
    Available here would be a statement about routing eligibility that no
    transition backs."""
    settings = _settings()
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, _ = _stores(settings)
        await statuses.seed()
        client = _client(
            build_status_router(settings, status_store=statuses, presence_store=presence)
        )

        body = client.get(
            "/routing/presence/status", params={"agent_id": AGENT_ID}, headers=API_KEY_HEADERS
        ).json()

        assert body == {
            "agent_id": AGENT_ID,
            "key": None,
            "label": None,
            "color": None,
            "since": None,
            "elapsed_minutes": None,
        }


# --- tests: the catalogue ----------------------------------------------


@pytest.mark.asyncio
async def test_the_catalogue_lists_the_shipped_defaults_before_anything_is_seeded():
    """The I2 half of this fix: a tenant that never ran the seed must still
    see (and be able to select) the catalogue, and must be told the entries
    are defaults rather than stored documents."""
    settings = _settings()
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, _, _ = _stores(settings)  # deliberately NOT seeded
        client = _client(build_status_router(settings, status_store=statuses))

        res = client.get("/routing/presence/statuses", headers=API_KEY_HEADERS)

        assert res.status_code == 200, res.text
        rows = {row["key"]: row for row in res.json()["statuses"]}
        assert {"available", "lunch", "break", "toilet", "prayer", "coaching", "training"} <= set(
            rows
        )
        assert rows["lunch"]["stored"] is False
        assert rows["lunch"]["counts_as_unavailable"] is True
        assert rows["acw"]["system_managed"] is True
        assert rows["available"]["system_managed"] is False


@pytest.mark.asyncio
async def test_an_operator_can_add_a_status_through_the_api_and_it_becomes_selectable():
    """§4.17's "an administrator can add one without a software release",
    end to end through HTTP instead of through a hand-written Firestore
    document."""
    settings = _settings()
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, _ = _stores(settings)
        await statuses.seed()
        client = _client(build_status_router(settings, status_store=statuses))

        created = client.put(
            "/routing/presence/statuses/offsite_visit",
            json={
                "label": "Offsite Visit",
                "color": "#795548",
                "routable": False,
                "native": "busy",
                "counts_as_unavailable": True,
            },
            headers=API_KEY_HEADERS,
        )
        assert created.status_code == 200, created.text

        listed = client.get("/routing/presence/statuses", headers=API_KEY_HEADERS).json()
        row = next(r for r in listed["statuses"] if r["key"] == "offsite_visit")
        assert row["stored"] is True
        assert row["counts_as_unavailable"] is True

        picked = client.post(
            "/routing/presence/status",
            json={"key": "offsite_visit", "agent_id": AGENT_ID},
            headers=API_KEY_HEADERS,
        )
        assert picked.status_code == 200, picked.text
        latest = await presence.latest(AGENT_ID)
        assert latest is not None and latest.status == "offsite_visit"


@pytest.mark.asyncio
async def test_editing_the_catalogue_requires_the_admin_permission(tmp_path, respx_mock):
    settings = _settings(rbac_enabled=True)
    repo = await _authz_repo(tmp_path, "catalogue_rbac")
    await repo.assign_role(chatwoot_user_id=AGENT_ID, role_id="agent")
    _mock_profile(respx_mock, settings, AGENT_ID)

    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, _, _ = _stores(settings)
        await statuses.seed()
        client = _client(
            build_status_router(settings, repo, TokenValidator(settings), status_store=statuses)
        )

        # An agent may list the catalogue (they pick from it)...
        assert client.get("/routing/presence/statuses", headers=HEADERS).status_code == 200
        # ...but may not edit it.
        res = client.put(
            "/routing/presence/statuses/lunch",
            json={"label": "Long lunch", "counts_as_unavailable": False},
            headers=HEADERS,
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_a_native_value_outside_chatwoots_enum_is_rejected_on_write():
    """`native` is PATCHed onto Chatwoot's fixed enum, so a typo has to fail
    at the edit rather than on every later selection."""
    settings = _settings()
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, _, _ = _stores(settings)
        client = _client(build_status_router(settings, status_store=statuses))

        res = client.put(
            "/routing/presence/statuses/nap",
            json={"label": "Nap", "native": "sleeping"},
            headers=API_KEY_HEADERS,
        )

        assert res.status_code == 422


@pytest.mark.asyncio
async def test_with_the_flag_off_every_endpoint_is_inert():
    """`PRESENCE_CUSTOM_STATUSES_ENABLED` off must mean the three native
    statuses only -- enforced in the router itself, not just at the mount
    site, so a direct caller gets the same guarantee."""
    settings = _settings(presence_custom_statuses_enabled=False)
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, writer = _stores(settings)
        client = _client(build_status_router(settings, status_store=statuses))

        listed = client.get("/routing/presence/statuses", headers=API_KEY_HEADERS)
        assert listed.status_code == 200
        assert listed.json()["disabled"] is True
        assert listed.json()["statuses"] == []

        posted = client.post(
            "/routing/presence/status",
            json={"key": "lunch", "agent_id": AGENT_ID},
            headers=API_KEY_HEADERS,
        )
        assert posted.status_code == 200
        assert posted.json()["disabled"] is True

        edited = client.put(
            "/routing/presence/statuses/lunch",
            json={"label": "Lunch"},
            headers=API_KEY_HEADERS,
        )
        assert edited.json()["disabled"] is True

        assert writer.calls == []
        assert await presence.latest(AGENT_ID) is None
        assert collections.get("custom_statuses", {}) == {}


# --- the integration test C1 asked for ---------------------------------


@pytest.mark.asyncio
async def test_three_hours_of_lunch_selected_through_the_api_fires_exactly_two_alerts(
    tmp_path, respx_mock
):
    """§4.13 + §4.14 + the anti-noise rule, proved end to end.

    An agent selects Lunch over HTTP and stays there for three hours. The
    real threshold sweeper then runs thirteen times against the real presence
    store the API wrote to, and must produce exactly one 10-minute warn and
    one 1-hour escalate -- and, crucially, must produce them at all: before
    this fix the sweeper resolved every status the product could actually
    write to `None` and returned early on all 180 sweeps of a real absence.

    Nothing here inserts a presence event. The `at` timestamp the sweeps are
    measured against is the one `set_status` itself stamped.
    """
    settings = _settings(
        rbac_enabled=True,
        presence_threshold_alerts_enabled=True,
        presence_warn_minutes=10,
        presence_escalate_minutes=60,
    )
    repo = await _authz_repo(tmp_path, "three_hours")
    await repo.assign_role(chatwoot_user_id=AGENT_ID, role_id="agent")
    _mock_profile(respx_mock, settings, AGENT_ID)

    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, writer = _stores(settings)
        await statuses.seed()
        router = build_status_router(
            settings, repo, TokenValidator(settings), status_store=statuses
        )

        res = _client(router).post(
            "/routing/presence/status", json={"key": "lunch"}, headers=HEADERS
        )
        assert res.status_code == 200, res.text
        assert writer.calls == [(AGENT_ID, "busy")]  # Chatwoot shows Busy...

        selected = await presence.latest(AGENT_ID)
        assert selected is not None
        assert selected.status == "lunch"  # ...our log says Lunch
        started_at = selected.at

        # The agent's Chatwoot availability now reads `busy`, which is what
        # the poller would report for them -- the sweeper must still resolve
        # the LUNCH event it finds in the log, not the mirrored native value.
        agent = AgentRecord(
            id=AGENT_ID, name="Ahmad", availability_status="busy", email="ahmad@example.com"
        )
        alert = _RecordingAlert()
        for minute in (2, 5, 8, 11, 15, 30, 45, 59, 61, 90, 120, 150, 180):
            await sweep_presence_thresholds(
                settings,
                now=started_at + timedelta(minutes=minute),
                presence_fetcher=_FakeAgents([agent]),
                presence_store=presence,
                status_store=statuses,
                alert=alert,
                open_case_fetcher=lambda: [],
            )

        assert [call[1] for call in alert.calls] == ["warn", "escalate"]

        # The stamps landed on the event the API wrote, which is what makes
        # the "exactly two" above hold for the next 120 sweeps too.
        final = await presence.latest(AGENT_ID)
        assert final is not None
        assert final.alerts_sent == frozenset({WARN_ALERT_KEY, ESCALATE_ALERT_KEY})


@pytest.mark.asyncio
async def test_a_native_status_written_by_the_poller_never_produces_an_absence_alert():
    """The other half of the reconciliation decision, stated as a test so it
    cannot be "fixed" by accident: an agent who goes Busy or Offline through
    Chatwoot's own control is working or off shift, not missing. Both now
    RESOLVE (they used to answer `None`) and both still alert about nothing.
    """
    settings = _settings(
        presence_threshold_alerts_enabled=True,
        presence_warn_minutes=10,
        presence_escalate_minutes=60,
    )
    collections: dict[str, dict[str, dict[str, Any]]] = {}
    with _patched_firestore(collections):
        statuses, presence, _ = _stores(settings)
        await statuses.seed()

        # Exactly what `presence_poller._reconcile` writes: raw native values,
        # `source="poll"`. This is the one place a presence event is written
        # by hand rather than through the API, because the writer being
        # simulated IS the poller, and it writes native values only.
        base = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
        for agent_id, native in ((1, "online"), (2, "busy"), (3, "offline")):
            await presence.append(
                PresenceEvent(
                    agent_id=agent_id, status=native, at=base, source="poll", previous=None
                )
            )

        assert (await statuses.resolve("online")) is not None  # was None before this fix
        alert = _RecordingAlert()
        await sweep_presence_thresholds(
            settings,
            now=base + timedelta(minutes=180),
            presence_fetcher=_FakeAgents(
                [
                    AgentRecord(id=1, name="A", availability_status="online", email="a@x.example"),
                    AgentRecord(id=2, name="B", availability_status="busy", email="b@x.example"),
                    AgentRecord(id=3, name="C", availability_status="offline", email="c@x.example"),
                ]
            ),
            presence_store=presence,
            status_store=statuses,
            alert=alert,
            open_case_fetcher=lambda: [],
        )

        assert alert.calls == []
