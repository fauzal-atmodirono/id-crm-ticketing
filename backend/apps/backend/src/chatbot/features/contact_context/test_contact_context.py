"""Chat-side caller context: POST /agent/contact-context (minimal view only)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.phone.caller_context import CallerContext
from chatbot.features.contact_context.router import (
    build_contact_context_router,
    chat_context_instruction,
    contact_view,
)
from chatbot.platform.config import Settings

KEY = "test-backend-key"


def _ahmad() -> CallerContext:
    return CallerContext.from_candidates(
        "+60000000101",
        "caller_id",
        [
            {
                "contact_id": 7,
                "name": "[DEMO] Ahmad bin Example",
                "vehicles": [{"plate": "DMO1101", "model": "Proton X50"}],
                "last_dealer": "petaling_jaya",
                "open_cases": 1,
            }
        ],
    )


def _client(ctx: CallerContext | None = None, *, enabled: bool = True, key: str = KEY) -> Any:
    seen: list[str] = []

    async def lookup(phone: str) -> CallerContext:
        seen.append(phone)
        return ctx or CallerContext(phone=phone, source="caller_id")

    settings = Settings(chat_caller_context_enabled=enabled, proton_backend_key=key)
    app = FastAPI()
    app.include_router(build_contact_context_router(settings, lookup))
    return TestClient(app), seen


def test_view_is_minimal_and_strips_the_demo_tag() -> None:
    view = contact_view(_ahmad())
    assert view == {
        "match": "single",
        "customers": [
            {
                "first_name": "Ahmad",
                "vehicles": [{"model": "Proton X50", "plate_ending": "1101"}],
                "usual_dealer": "Petaling Jaya",
                "open_cases": 1,
            }
        ],
    }
    text = json.dumps(view)
    for secret in ("60000000101", "DMO1101", "Example", "contact_id"):
        assert secret not in text


def test_instruction_confirms_never_recites() -> None:
    text = chat_context_instruction(contact_view(_ahmad()))
    assert "Ahmad" in text and "Proton X50" in text and "1101" in text
    assert "Petaling Jaya" in text
    assert "confirm" in text.lower()
    assert "never" in text.lower()
    for secret in ("60000000101", "DMO1101", "Example"):
        assert secret not in text


def test_no_match_gives_no_instruction() -> None:
    assert chat_context_instruction({"match": "none", "customers": []}) == ""


def test_several_people_on_one_number_ask_rather_than_guess() -> None:
    ctx = CallerContext.from_candidates(
        "+60000000103",
        "caller_id",
        [{"contact_id": 1, "name": "Lim"}, {"contact_id": 2, "name": "Tan"}],
    )
    text = chat_context_instruction(contact_view(ctx))
    assert "ask" in text.lower()
    assert "Lim" not in text and "Tan" not in text


def test_endpoint_needs_the_backend_key() -> None:
    client, seen = _client(_ahmad())
    assert client.post("/agent/contact-context", json={"phone": "+60000000101"}).status_code == 401
    res = client.post(
        "/agent/contact-context",
        json={"phone": "+60000000101"},
        headers={"x-api-key": "wrong"},
    )
    assert res.status_code == 401
    assert seen == []


def test_blank_key_fails_closed() -> None:
    client, seen = _client(_ahmad(), key="")
    res = client.post(
        "/agent/contact-context", json={"phone": "+60000000101"}, headers={"x-api-key": ""}
    )
    assert res.status_code == 401
    assert seen == []


def test_flag_off_is_404() -> None:
    client, _ = _client(_ahmad(), enabled=False)
    res = client.post(
        "/agent/contact-context", json={"phone": "+60000000101"}, headers={"x-api-key": KEY}
    )
    assert res.status_code == 404


def test_endpoint_returns_view_and_instruction() -> None:
    client, seen = _client(_ahmad())
    res = client.post(
        "/agent/contact-context", json={"phone": "+60 0000 00101"}, headers={"x-api-key": KEY}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["view"]["customers"][0]["first_name"] == "Ahmad"
    assert "Ahmad" in body["instruction"]
    assert seen == ["+60000000101"]


def test_unusable_number_is_no_match_without_lookup() -> None:
    client, seen = _client(_ahmad())
    res = client.post("/agent/contact-context", json={"phone": "abc"}, headers={"x-api-key": KEY})
    assert res.status_code == 200
    assert res.json() == {"view": {"match": "none", "customers": []}, "instruction": ""}
    assert seen == []


def test_get_with_the_number_in_the_url_is_not_served() -> None:
    client, seen = _client(_ahmad())
    res = client.get(
        "/agent/contact-context", params={"phone": "+60000000101"}, headers={"x-api-key": KEY}
    )
    assert res.status_code == 405
    assert seen == []


def test_contact_context_is_rate_limited() -> None:
    client, _ = _client(_ahmad())
    codes = [
        client.post(
            "/agent/contact-context", json={"phone": "+60000000101"}, headers={"x-api-key": KEY}
        ).status_code
        for _ in range(121)
    ]
    assert codes[:120] == [200] * 120
    assert codes[120] == 429


def test_bad_key_attempts_count_against_the_limit_too() -> None:
    client, seen = _client(_ahmad())
    for _ in range(120):
        client.post("/agent/contact-context", json={"phone": "x"}, headers={"x-api-key": "bad"})
    res = client.post(
        "/agent/contact-context", json={"phone": "+60000000101"}, headers={"x-api-key": KEY}
    )
    assert res.status_code == 429
    assert seen == []
