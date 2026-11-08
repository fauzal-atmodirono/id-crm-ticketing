"""Tests for GET /admin/ai-activity (FIX-43, RFP 2026_028 3.2.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine as build_authz_engine
from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
from chatbot.features.authz.db import init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import PERMISSION_REGISTRY, seed_defaults
from chatbot.features.chat.ai_activity_router import build_ai_activity_router
from chatbot.features.tenant_config.custom_features import CUSTOM_FEATURE_REGISTRY
from chatbot.platform.config import get_settings

HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}


def _conv(cid, *, status="pending", labels=None, last=None, assignee=None, name="Ali"):
    return {
        "id": cid,
        "status": status,
        "inbox_id": 3,
        "labels": labels or [],
        "timestamp": 1_700_000_000,
        "meta": {"sender": {"name": name}, "assignee": assignee},
        "last_non_activity_message": last,
    }


def _msg(text, *, message_type, private=False, at=1_700_000_100):
    return {"content": text, "message_type": message_type, "private": private, "created_at": at}


def _fake_chatwoot(pending=None, unassigned_open=None, *, pending_error=False, open_error=False):
    chatwoot = AsyncMock()

    async def by_status(status, **_kw):
        if status == "pending":
            if pending_error:
                raise RuntimeError("chatwoot down")
            return pending or []
        if open_error:
            raise RuntimeError("chatwoot down")
        return unassigned_open or []

    chatwoot.list_conversations_by_status.side_effect = by_status
    chatwoot.is_ai_paused.return_value = False
    return chatwoot


async def _authorized(tmp_path, name, user_id, role="administrator"):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_authz_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=user_id, role_id=role)
    return settings, repo, TokenValidator(settings)


def _client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _mock_profile(respx_mock, settings, uid):
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": uid})
    )


@pytest.mark.asyncio
async def test_lists_live_ai_conversations_with_mode_and_last_action(tmp_path, respx_mock):
    settings, repo, validator = await _authorized(tmp_path, "ok", 10)
    _mock_profile(respx_mock, settings, 10)
    chatwoot = _fake_chatwoot(
        pending=[
            _conv(1, last=_msg("Hi, my car won't start", message_type=0)),
            _conv(2, last=_msg("Suggested: please share your plate", message_type=1, private=True)),
            _conv(3, last=_msg("Your booking is confirmed", message_type=1)),
        ],
        unassigned_open=[
            _conv(4, status="open", labels=["escalate"], last=_msg("Need a human", message_type=0)),
            _conv(5, status="open", labels=[], last=_msg("not ai", message_type=0)),
        ],
    )
    client = _client(build_ai_activity_router(chatwoot, repo, validator, settings))

    res = client.get("/admin/ai-activity", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    by_id = {c["conversation_id"]: c for c in body["conversations"]}
    assert set(by_id) == {1, 2, 3, 4}  # conversation 5 is not AI-handled
    assert by_id[1]["mode"] == "ai_active"
    assert by_id[1]["last_action"]["type"] == "awaiting_ai"
    assert by_id[2]["last_action"]["type"] == "ai_suggestion"
    assert by_id[3]["last_action"]["type"] == "ai_reply"
    assert by_id[3]["last_action"]["preview"] == "Your booking is confirmed"
    assert by_id[4]["mode"] == "escalated"
    assert by_id[1]["customer"] == "Ali"
    assert body["summary"]["ai_active"] == 3
    assert body["summary"]["escalated"] == 1
    assert body["sources"] == {"pending": "ok", "escalated": "ok"}


@pytest.mark.asyncio
async def test_paused_session_is_marked_human_takeover(tmp_path, respx_mock):
    settings, repo, validator = await _authorized(tmp_path, "paused", 10)
    _mock_profile(respx_mock, settings, 10)
    chatwoot = _fake_chatwoot(pending=[_conv(7, last=_msg("x", message_type=0))])
    chatwoot.is_ai_paused.return_value = True
    client = _client(build_ai_activity_router(chatwoot, repo, validator, settings))

    body = client.get("/admin/ai-activity", headers=HEADERS).json()
    assert body["conversations"][0]["mode"] == "ai_paused"


@pytest.mark.asyncio
async def test_requires_permission(tmp_path, respx_mock):
    settings, repo, validator = await _authorized(tmp_path, "noperm", 9, role="agent")
    _mock_profile(respx_mock, settings, 9)
    client = _client(build_ai_activity_router(_fake_chatwoot(), repo, validator, settings))

    assert client.get("/admin/ai-activity", headers=HEADERS).status_code == 403
    assert client.get("/admin/ai-activity").status_code == 401


@pytest.mark.asyncio
async def test_one_failing_source_does_not_blank_the_other(tmp_path, respx_mock):
    settings, repo, validator = await _authorized(tmp_path, "failopen", 10)
    _mock_profile(respx_mock, settings, 10)
    chatwoot = _fake_chatwoot(pending=[_conv(1, last=_msg("hi", message_type=0))], open_error=True)
    client = _client(build_ai_activity_router(chatwoot, repo, validator, settings))

    res = client.get("/admin/ai-activity", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert [c["conversation_id"] for c in body["conversations"]] == [1]
    assert body["sources"] == {"pending": "ok", "escalated": "unavailable"}


@pytest.mark.asyncio
async def test_all_sources_down_is_200_with_unavailable_not_500(tmp_path, respx_mock):
    settings, repo, validator = await _authorized(tmp_path, "alldown", 10)
    _mock_profile(respx_mock, settings, 10)
    chatwoot = _fake_chatwoot(pending_error=True, open_error=True)
    client = _client(build_ai_activity_router(chatwoot, repo, validator, settings))

    res = client.get("/admin/ai-activity", headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["conversations"] == []
    assert res.json()["sources"] == {"pending": "unavailable", "escalated": "unavailable"}


@pytest.mark.asyncio
async def test_pause_lookup_failure_fails_open(tmp_path, respx_mock):
    settings, repo, validator = await _authorized(tmp_path, "pausefail", 10)
    _mock_profile(respx_mock, settings, 10)
    chatwoot = _fake_chatwoot(pending=[_conv(1, last=_msg("hi", message_type=0))])
    chatwoot.is_ai_paused.side_effect = RuntimeError("firestore down")
    client = _client(build_ai_activity_router(chatwoot, repo, validator, settings))

    body = client.get("/admin/ai-activity", headers=HEADERS).json()
    assert body["conversations"][0]["mode"] == "ai_active"


@pytest.mark.asyncio
async def test_result_is_cached_briefly(tmp_path, respx_mock):
    settings, repo, validator = await _authorized(tmp_path, "cache", 10)
    _mock_profile(respx_mock, settings, 10)
    chatwoot = _fake_chatwoot(pending=[_conv(1, last=_msg("hi", message_type=0))])
    now = [1000.0]
    client = _client(
        build_ai_activity_router(chatwoot, repo, validator, settings, clock=lambda: now[0])
    )

    client.get("/admin/ai-activity", headers=HEADERS)
    client.get("/admin/ai-activity", headers=HEADERS)
    assert chatwoot.list_conversations_by_status.await_count == 2  # one refresh = 2 sources
    now[0] += 20
    client.get("/admin/ai-activity", headers=HEADERS)
    assert chatwoot.list_conversations_by_status.await_count == 4


def test_permission_and_feature_are_registered() -> None:
    assert "ai_activity.view" in PERMISSION_REGISTRY
    assert CUSTOM_FEATURE_REGISTRY["ai_activity"].permission == "ai_activity.view"
