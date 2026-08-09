"""Tests for POST /assist/translate — the agent-facing translate action.

Uses a stub Gemini client and a fake note-posting port (mirroring the real
ChatwootAdapter.add_private_note payload shape, see chat/adapters/chatwoot.py)
so no real Gemini or Chatwoot call is ever made. RBAC wiring for the one
gated test reuses the real sqlite-backed AuthzRepository + TokenValidator,
matching pic_admin_router/customer360_router's own test pattern exactly.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.translate_router import build_translate_router
from chatbot.features.authz.db import build_engine as build_authz_engine
from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
from chatbot.features.authz.db import init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.platform.config import Settings, get_settings

HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeTicketingPort:
    """Records calls with the SAME dict shape ChatwootAdapter.add_private_note
    actually sends (chat/adapters/chatwoot.py:737-743: content/message_type/
    private), so tests assert on the payload's literal `private` key rather
    than merely on having called a method whose name implies privacy — see
    task-3-brief's test six.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        self.calls.append(
            {
                "ticket_id": ticket_id,
                "content": text,
                "message_type": "outgoing",
                "private": True,
            }
        )


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "proton_backend_key": "testkey",
        "assist_gemini_model": "gemini-2.5-flash",
        "translation_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)


def _make_genai(payload: dict[str, str]) -> MagicMock:
    genai = MagicMock()
    response = MagicMock()
    response.text = json.dumps(payload)
    genai.aio.models.generate_content = AsyncMock(return_value=response)
    return genai


def _client(
    settings: Settings,
    genai_client: Any,
    ticketing_port: _FakeTicketingPort | None = None,
    authz_repo: Any = None,
    validator: Any = None,
) -> tuple[TestClient, _FakeTicketingPort]:
    notes = ticketing_port if ticketing_port is not None else _FakeTicketingPort()
    app = FastAPI()
    app.include_router(
        build_translate_router(
            settings=settings,
            genai_client=genai_client,
            ticketing_port=notes,
            authz_repo=authz_repo,
            validator=validator,
        )
    )
    return TestClient(app, raise_server_exceptions=False), notes


async def _build_authz_repo(tmp_path, name: str) -> AuthzRepository:
    authz_engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(authz_engine)
    return AuthzRepository(build_authz_session_maker(authz_engine))


_AUTH = {"x-api-key": "testkey"}


def _payload(text: str, target_language: str = "en", conversation_id: str = "1") -> dict[str, str]:
    return {"conversation_id": conversation_id, "text": text, "target_language": target_language}


# ---------------------------------------------------------------------------
# 1-3: translation into English from each supported source language
# ---------------------------------------------------------------------------


def test_a_malay_message_translates_to_english() -> None:
    genai = _make_genai({"translation": "Hello, how are you?", "detected_source_language": "ms"})
    client, notes = _client(_settings(), genai)

    res = client.post("/assist/translate", json=_payload("Hai, apa khabar?"), headers=_AUTH)

    assert res.status_code == 200
    body = res.json()
    assert body["translation"] == "Hello, how are you?"
    assert body["detected_source_language"] == "ms"
    assert len(notes.calls) == 1


def test_a_tamil_message_translates_to_english() -> None:
    genai = _make_genai(
        {"translation": "I need help with my car.", "detected_source_language": "ta"}
    )
    client, notes = _client(_settings(), genai)

    res = client.post(
        "/assist/translate",
        json=_payload("எனக்கு எனது காருக்கு உதவி தேவை."),
        headers=_AUTH,
    )

    assert res.status_code == 200
    body = res.json()
    assert body["translation"] == "I need help with my car."
    assert body["detected_source_language"] == "ta"
    assert len(notes.calls) == 1


def test_a_chinese_message_translates_to_english() -> None:
    genai = _make_genai({"translation": "My car won't start.", "detected_source_language": "zh"})
    client, notes = _client(_settings(), genai)

    res = client.post("/assist/translate", json=_payload("我的车打不着火。"), headers=_AUTH)

    assert res.status_code == 200
    body = res.json()
    assert body["translation"] == "My car won't start."
    assert body["detected_source_language"] == "zh"
    assert len(notes.calls) == 1


# ---------------------------------------------------------------------------
# 4: detected source language is returned
# ---------------------------------------------------------------------------


def test_the_detected_source_language_is_returned() -> None:
    genai = _make_genai(
        {"translation": "Thank you for the update.", "detected_source_language": "zh"}
    )
    client, _ = _client(_settings(), genai)

    res = client.post("/assist/translate", json=_payload("谢谢你的更新。"), headers=_AUTH)

    assert res.status_code == 200
    assert res.json()["detected_source_language"] == "zh"


# ---------------------------------------------------------------------------
# 5: already-in-target-language message returned unchanged
# ---------------------------------------------------------------------------


def test_a_message_already_in_the_target_language_is_returned_unchanged() -> None:
    original = "The technician will call you back shortly."
    genai = _make_genai({"translation": original, "detected_source_language": "en"})
    client, _ = _client(_settings(), genai)

    res = client.post(
        "/assist/translate", json=_payload(original, target_language="en"), headers=_AUTH
    )

    assert res.status_code == 200
    body = res.json()
    assert body["translation"] == original
    assert body["detected_source_language"] == "en"


# ---------------------------------------------------------------------------
# 6: customer-safety property — posts as a private note, never outgoing
# ---------------------------------------------------------------------------


def test_the_translation_posts_as_a_private_note_not_an_outgoing_message() -> None:
    genai = _make_genai({"translation": "Hello", "detected_source_language": "ms"})
    client, notes = _client(_settings(), genai)

    res = client.post("/assist/translate", json=_payload("Hai"), headers=_AUTH)

    assert res.status_code == 200
    assert len(notes.calls) == 1
    payload = notes.calls[0]
    # Assert on the actual payload's keys, not on having called a method whose
    # name merely implies privacy (task-3-brief's explicit instruction).
    assert payload["private"] is True
    assert payload["message_type"] == "outgoing"


# ---------------------------------------------------------------------------
# 7-8: the Tamil split — outbound gated, inbound not
# ---------------------------------------------------------------------------


def test_outbound_tamil_replies_are_blocked_while_the_tamil_flag_is_off() -> None:
    genai = _make_genai({"translation": "unused", "detected_source_language": "en"})
    client, notes = _client(_settings(translation_outbound_tamil_enabled=False), genai)

    res = client.post(
        "/assist/translate",
        json=_payload("Thank you for contacting us.", target_language="ta"),
        headers=_AUTH,
    )

    assert res.status_code == 403
    assert notes.calls == []
    # Blocked before any model call is attempted.
    genai.aio.models.generate_content.assert_not_awaited()


def test_inbound_tamil_translation_works_regardless_of_the_outbound_flag() -> None:
    genai = _make_genai(
        {"translation": "I need help with my car.", "detected_source_language": "ta"}
    )
    client, notes = _client(_settings(translation_outbound_tamil_enabled=False), genai)

    res = client.post(
        "/assist/translate",
        json=_payload("எனக்கு எனது காருக்கு உதவி தேவை.", target_language="en"),
        headers=_AUTH,
    )

    assert res.status_code == 200
    assert res.json()["detected_source_language"] == "ta"
    assert len(notes.calls) == 1


# ---------------------------------------------------------------------------
# 9: RBAC gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_endpoint_is_rbac_gated(tmp_path, respx_mock) -> None:
    settings = get_settings().model_copy(update={"rbac_enabled": True, "translation_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "translate_rbac")
    await seed_defaults(authz_repo)
    # user 9 has no role assigned at all -> no permissions, must be denied.
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    validator = TokenValidator(settings)
    genai = _make_genai({"translation": "Hello", "detected_source_language": "ms"})
    client, notes = _client(settings, genai, authz_repo=authz_repo, validator=validator)

    denied = client.post("/assist/translate", json=_payload("Hai"), headers=HEADERS)
    assert denied.status_code == 403
    assert notes.calls == []

    # Granting the default "agent" role (which carries translation.use) is
    # enough — reading a customer's message in translation is an agent's own
    # job, not an admin-only action.
    await authz_repo.assign_role(chatwoot_user_id=9, role_id="agent")
    allowed = client.post("/assist/translate", json=_payload("Hai"), headers=HEADERS)
    assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# 10: model failure is a clear error, never a half-posted note
# ---------------------------------------------------------------------------


def test_a_model_failure_returns_a_clear_error_and_does_not_post_a_note() -> None:
    genai = MagicMock()
    genai.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("gemini unavailable"))
    client, notes = _client(_settings(), genai)

    res = client.post("/assist/translate", json=_payload("Hai"), headers=_AUTH)

    assert res.status_code == 502
    assert "translation failed" in res.json()["detail"].lower()
    assert notes.calls == []
