"""P3 task 5 — the case-record panel's read/write endpoints.

The panel renders from CASE_FIELDS, so `GET` returns the whole spec with
current values rather than the fork having its own copy of the field list.

`PATCH` is partial: an absent key means "leave it alone", because the panel
sends what the agent changed. Clearing is an explicit empty string, which the
validator turns into None.
"""

from __future__ import annotations

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
from chatbot.features.chat.case_fields_router import build_case_fields_router
from chatbot.platform.config import get_settings

HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}


class _Chatwoot:
    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        self.attrs = attrs or {}
        self.writes: list[tuple[str, dict]] = []

    async def read(self, conv_id: str):
        return {"id": conv_id, "custom_attributes": self.attrs}

    async def merge(self, conv_id: str, updates: dict):
        self.writes.append((conv_id, updates))


class _DealerStore:
    async def get(self, dealer: str):
        if dealer != "komang_motor":
            return None

        class _R:
            pass

        return _R()


async def _repo(tmp_path, name: str) -> AuthzRepository:
    engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}.db")
    await init_authz_db(engine)
    return AuthzRepository(build_authz_session_maker(engine))


async def _client(tmp_path, respx_mock, name, *, user_id=10, role="administrator",
                  enabled=True, chatwoot=None):
    settings = get_settings().model_copy(
        update={"rbac_enabled": True, "case_fields_enabled": enabled}
    )
    repo = await _repo(tmp_path, name)
    await seed_defaults(repo)
    if role:
        await repo.assign_role(chatwoot_user_id=user_id, role_id=role)
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": user_id})
    )
    cw = chatwoot or _Chatwoot()
    app = FastAPI()
    app.include_router(
        build_case_fields_router(
            cw.read, cw.merge, repo, TokenValidator(settings), settings,
            dealer_store=_DealerStore(),
        )
    )
    return TestClient(app), cw


@pytest.mark.asyncio
async def test_get_returns_every_field_with_its_current_value(tmp_path, respx_mock):
    cw = _Chatwoot({"vehicle_plate": "WXY1234", "wip_issue": "Waiting on parts"})
    client, _ = await _client(tmp_path, respx_mock, "get_all", chatwoot=cw)

    res = client.get("/cases/42/fields", headers=HEADERS)

    assert res.status_code == 200
    by_name = {f["name"]: f for f in res.json()["fields"]}
    assert by_name["vehicle_plate"]["value"] == "WXY1234"
    assert by_name["wip_issue"]["value"] == "Waiting on parts"
    assert by_name["delay_reason"]["value"] is None
    # the panel needs the choices to render a dropdown
    assert by_name["escalated_to"]["choices"] == ["dealer", "none"]


@pytest.mark.asyncio
async def test_patch_writes_only_the_fields_supplied(tmp_path, respx_mock):
    client, cw = await _client(tmp_path, respx_mock, "patch_partial")

    res = client.patch(
        "/cases/42/fields", json={"fields": {"wip_issue": "Parts on order"}},
        headers=HEADERS,
    )

    assert res.status_code == 200
    assert cw.writes == [("42", {"wip_issue": "Parts on order"})]


@pytest.mark.asyncio
async def test_patch_normalises_a_plate_number_before_storing(tmp_path, respx_mock):
    client, cw = await _client(tmp_path, respx_mock, "patch_plate")

    client.patch(
        "/cases/42/fields", json={"fields": {"vehicle_plate": "wxy 1234"}},
        headers=HEADERS,
    )

    assert cw.writes[0][1] == {"vehicle_plate": "WXY1234"}


@pytest.mark.asyncio
async def test_patch_rejects_an_unknown_field_name(tmp_path, respx_mock):
    client, cw = await _client(tmp_path, respx_mock, "patch_unknown")

    res = client.patch(
        "/cases/42/fields", json={"fields": {"not_a_field": "x"}}, headers=HEADERS
    )

    assert res.status_code == 400
    assert not cw.writes, "nothing should be written when any field is invalid"


@pytest.mark.asyncio
async def test_patch_rejects_an_invalid_dealer_slug_with_a_usable_message(
    tmp_path, respx_mock
):
    client, _ = await _client(tmp_path, respx_mock, "patch_dealer")

    res = client.patch(
        "/cases/42/fields",
        json={"fields": {"purchased_from_dealer": "no_such_dealer"}},
        headers=HEADERS,
    )

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "no_such_dealer" in detail
    assert "Escalation Routing" in detail


@pytest.mark.asyncio
async def test_patch_rejects_hq_and_says_why(tmp_path, respx_mock):
    client, _ = await _client(tmp_path, respx_mock, "patch_hq")

    res = client.patch(
        "/cases/42/fields", json={"fields": {"escalated_to": "hq"}}, headers=HEADERS
    )

    assert res.status_code == 400
    assert "Q5" in res.json()["detail"]


@pytest.mark.asyncio
async def test_an_unauthorised_caller_is_rejected(tmp_path, respx_mock):
    client, _ = await _client(tmp_path, respx_mock, "unauth", role=None)

    assert client.get("/cases/42/fields", headers=HEADERS).status_code == 403


@pytest.mark.asyncio
async def test_a_caller_with_no_credentials_is_rejected(tmp_path, respx_mock):
    client, _ = await _client(tmp_path, respx_mock, "nocreds")

    assert client.get("/cases/42/fields").status_code == 401


@pytest.mark.asyncio
async def test_an_agent_can_edit_the_panel(tmp_path, respx_mock):
    """Filling these in IS the agent's job; an admin-only panel is useless."""
    client, cw = await _client(tmp_path, respx_mock, "agent_edit", user_id=12, role="agent")

    res = client.patch(
        "/cases/42/fields", json={"fields": {"wip_issue": "Chasing the dealer"}},
        headers=HEADERS,
    )

    assert res.status_code == 200
    assert cw.writes


@pytest.mark.asyncio
async def test_the_flag_off_returns_404_so_the_panel_does_not_render(
    tmp_path, respx_mock
):
    """404 rather than 403: a tenant that never enabled this should see no
    panel, not a permissions error on every conversation."""
    client, _ = await _client(tmp_path, respx_mock, "flag_off", enabled=False)

    assert client.get("/cases/42/fields", headers=HEADERS).status_code == 404


@pytest.mark.asyncio
async def test_a_chatwoot_read_failure_still_renders_the_empty_panel(
    tmp_path, respx_mock
):
    class _Broken(_Chatwoot):
        async def read(self, conv_id):
            raise RuntimeError("chatwoot down")

    client, _ = await _client(tmp_path, respx_mock, "read_fail", chatwoot=_Broken())

    res = client.get("/cases/42/fields", headers=HEADERS)

    assert res.status_code == 200
    assert all(f["value"] is None for f in res.json()["fields"])
