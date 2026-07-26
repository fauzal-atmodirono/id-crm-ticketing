"""Tests for ProtonConfigClient.get_assistant_persona and the 7 new lifecycle
message keys added to get_assistant_messages.

All HTTP is intercepted by respx — the real proton backend is never hit.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.clients.proton import ProtonConfigClient

PROTON_BASE = "http://proton-backend:8080"

INBOXES_RESPONSE = {
    "inboxes": [
        {"inbox_id": 3, "assistant_id": "asst_1", "mode": "auto"},
    ]
}

FULL_ASSISTANT_RESPONSE = {
    "id": "asst_1",
    "config": {
        "instructions": "Be terse.",
        "guardrails": ["No prices"],
        "language": "English",
        "welcome_message": "Hello!",
        "handoff_message": "Connecting you.",
        "resolution_message": "All resolved.",
        "idle_warning_message": "Still there?",
        "idle_close_message": "Closing.",
        "resolution_prompt_message": "Is your issue resolved?",
        "survey_ai_message": "How was the AI?",
        "survey_agent_message": "How was the agent?",
        "thanks_message": "Cheers!",
        "assign_agent_message": "Assigning an agent.",
    },
}


def _make_client(**kwargs) -> ProtonConfigClient:
    """Build a ProtonConfigClient whose httpx.AsyncClient is backed by respx."""
    inner = httpx.AsyncClient(base_url=PROTON_BASE, headers={"x-api-key": "testkey"})
    return ProtonConfigClient(
        base_url=PROTON_BASE, api_key="testkey", client=inner, **kwargs
    )


# ---------------------------------------------------------------------------
# get_assistant_persona
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_assistant_persona_maps_fields() -> None:
    """Persona fields are correctly extracted from assistant config."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    respx.get(f"{PROTON_BASE}/kb/assistants/asst_1").mock(
        return_value=httpx.Response(200, json=FULL_ASSISTANT_RESPONSE)
    )
    client = _make_client()
    persona = await client.get_assistant_persona(3)
    assert persona == {
        "instructions": "Be terse.",
        "guardrails": ["No prices"],
        "language": "English",
    }
    await client.aclose()


@respx.mock
async def test_get_assistant_messages_includes_new_lifecycle_keys() -> None:
    """get_assistant_messages returns all 7 new keys alongside the original 3."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    respx.get(f"{PROTON_BASE}/kb/assistants/asst_1").mock(
        return_value=httpx.Response(200, json=FULL_ASSISTANT_RESPONSE)
    )
    client = _make_client()
    msgs = await client.get_assistant_messages(3)
    assert msgs is not None
    # original keys
    assert msgs["welcome"] == "Hello!"
    assert msgs["handoff"] == "Connecting you."
    assert msgs["resolution"] == "All resolved."
    # new lifecycle keys
    assert msgs["idle_warning"] == "Still there?"
    assert msgs["idle_close"] == "Closing."
    assert msgs["resolution_prompt"] == "Is your issue resolved?"
    assert msgs["survey_ai"] == "How was the AI?"
    assert msgs["survey_agent"] == "How was the agent?"
    assert msgs["thanks"] == "Cheers!"
    assert msgs["assign_agent"] == "Assigning an agent."
    await client.aclose()


@respx.mock
async def test_get_assistant_persona_and_messages_share_cached_fetch() -> None:
    """Calling both persona and messages for the same inbox uses only one assistant fetch."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    assistant_route = respx.get(f"{PROTON_BASE}/kb/assistants/asst_1").mock(
        return_value=httpx.Response(200, json=FULL_ASSISTANT_RESPONSE)
    )
    client = _make_client(ttl=60.0)
    persona = await client.get_assistant_persona(3)
    msgs = await client.get_assistant_messages(3)
    assert persona is not None
    assert msgs is not None
    assert msgs["thanks"] == "Cheers!"
    # Both calls share the same _fetch_cached result — only one HTTP hit total
    assert assistant_route.call_count == 1
    await client.aclose()


@respx.mock
async def test_persona_fail_open_on_500() -> None:
    """get_assistant_persona returns None when the backend returns 500."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(500)
    )
    client = _make_client()
    assert await client.get_assistant_persona(3) is None
    await client.aclose()


@respx.mock
async def test_persona_fail_open_on_unknown_inbox() -> None:
    """get_assistant_persona returns None when inbox has no assistant_id."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(
            200,
            json={"inboxes": [{"inbox_id": 99, "mode": "auto"}]},
        )
    )
    client = _make_client()
    assert await client.get_assistant_persona(3) is None
    await client.aclose()


@respx.mock
async def test_new_lifecycle_keys_default_to_empty_string_when_absent() -> None:
    """When new message fields are absent from config, they default to empty string."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    respx.get(f"{PROTON_BASE}/kb/assistants/asst_1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "asst_1", "config": {"welcome_message": "Hi"}},
        )
    )
    client = _make_client()
    msgs = await client.get_assistant_messages(3)
    assert msgs is not None
    assert msgs["idle_warning"] == ""
    assert msgs["idle_close"] == ""
    assert msgs["resolution_prompt"] == ""
    assert msgs["survey_ai"] == ""
    assert msgs["survey_agent"] == ""
    assert msgs["thanks"] == ""
    assert msgs["assign_agent"] == ""
    await client.aclose()
