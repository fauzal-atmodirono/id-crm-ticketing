"""Tests for ProtonConfigClient: caching, mode resolution, debounce settings,
and graceful None returns on failure.

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
        {"inbox_id": 10, "name": "Support", "mode": "suggest", "source": "manual", "assistant_id": "asst-abc"},
        {"inbox_id": 20, "name": "Sales", "mode": "auto", "source": "manual", "assistant_id": "asst-xyz"},
        {"inbox_id": 30, "name": "Spam", "mode": "off", "source": "manual"},
    ]
}

ASSISTANT_ABC_RESPONSE = {
    "id": "asst-abc",
    "config": {
        "welcome_message": "Welcome!",
        "handoff_message": "Connecting you to a human.",
        "resolution_message": "Your issue is resolved.",
    },
}

ASSISTANT_XYZ_RESPONSE = {
    "id": "asst-xyz",
    "config": {
        "welcome_message": "",
        "handoff_message": "",
        "resolution_message": "",
    },
}

SETTINGS_RESPONSE = {
    "settings": {
        "debounce_seconds": {"value": 5.0, "source": "default"},
        "other_setting": {"value": True, "source": "manual"},
    }
}


def _make_client(**kwargs) -> ProtonConfigClient:
    """Build a ProtonConfigClient whose httpx.AsyncClient is backed by respx."""
    inner = httpx.AsyncClient(base_url=PROTON_BASE, headers={"x-api-key": "testkey"})
    return ProtonConfigClient(base_url=PROTON_BASE, api_key="testkey", client=inner, **kwargs)


# ---------------------------------------------------------------------------
# effective_inbox_mode
# ---------------------------------------------------------------------------


@respx.mock
async def test_effective_inbox_mode_returns_mode_for_known_inbox():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    client = _make_client()
    assert await client.effective_inbox_mode(10) == "suggest"
    assert await client.effective_inbox_mode(20) == "auto"
    assert await client.effective_inbox_mode(30) == "off"
    await client.aclose()


@respx.mock
async def test_effective_inbox_mode_returns_none_for_unknown_inbox():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    client = _make_client()
    result = await client.effective_inbox_mode(999)
    assert result is None
    await client.aclose()


@respx.mock
async def test_effective_inbox_mode_returns_none_on_non_2xx():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(503, json={"error": "unavailable"})
    )
    client = _make_client()
    result = await client.effective_inbox_mode(10)
    assert result is None
    await client.aclose()


@respx.mock
async def test_effective_inbox_mode_returns_none_on_connection_error():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(side_effect=httpx.ConnectError("boom"))
    client = _make_client()
    result = await client.effective_inbox_mode(10)
    assert result is None
    await client.aclose()


@respx.mock
async def test_effective_inbox_mode_returns_none_on_bad_shape():
    # Top-level key is wrong
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json={"not_inboxes": []})
    )
    client = _make_client()
    result = await client.effective_inbox_mode(10)
    assert result is None
    await client.aclose()


# ---------------------------------------------------------------------------
# effective_debounce_seconds
# ---------------------------------------------------------------------------


@respx.mock
async def test_effective_debounce_seconds_parses_value():
    respx.get(f"{PROTON_BASE}/kb/settings").mock(
        return_value=httpx.Response(200, json=SETTINGS_RESPONSE)
    )
    client = _make_client()
    result = await client.effective_debounce_seconds()
    assert result == 5.0
    await client.aclose()


@respx.mock
async def test_effective_debounce_seconds_returns_none_on_non_2xx():
    respx.get(f"{PROTON_BASE}/kb/settings").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    client = _make_client()
    result = await client.effective_debounce_seconds()
    assert result is None
    await client.aclose()


@respx.mock
async def test_effective_debounce_seconds_returns_none_on_missing_key():
    respx.get(f"{PROTON_BASE}/kb/settings").mock(
        return_value=httpx.Response(200, json={"settings": {"other": {"value": 1}}})
    )
    client = _make_client()
    result = await client.effective_debounce_seconds()
    assert result is None
    await client.aclose()


@respx.mock
async def test_effective_debounce_seconds_returns_none_on_connection_error():
    respx.get(f"{PROTON_BASE}/kb/settings").mock(side_effect=httpx.ConnectError("boom"))
    client = _make_client()
    result = await client.effective_debounce_seconds()
    assert result is None
    await client.aclose()


# ---------------------------------------------------------------------------
# Caching: second call within TTL must NOT refetch
# ---------------------------------------------------------------------------


@respx.mock
async def test_effective_inbox_mode_caches_within_ttl():
    route = respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    client = _make_client(ttl=60.0)
    # Two calls in quick succession — both within TTL
    r1 = await client.effective_inbox_mode(10)
    r2 = await client.effective_inbox_mode(20)
    assert r1 == "suggest"
    assert r2 == "auto"
    # Backend must have been called exactly once (the second call hit the cache)
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_effective_debounce_seconds_caches_within_ttl():
    route = respx.get(f"{PROTON_BASE}/kb/settings").mock(
        return_value=httpx.Response(200, json=SETTINGS_RESPONSE)
    )
    client = _make_client(ttl=60.0)
    r1 = await client.effective_debounce_seconds()
    r2 = await client.effective_debounce_seconds()
    assert r1 == 5.0
    assert r2 == 5.0
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_cache_expires_after_ttl(monkeypatch):
    """With ttl=0, every call is stale and refetches."""
    route = respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    client = _make_client(ttl=0.0)
    await client.effective_inbox_mode(10)
    await client.effective_inbox_mode(10)
    # TTL=0 means both calls trigger a fetch
    assert route.call_count == 2
    await client.aclose()


# ---------------------------------------------------------------------------
# get_assistant_messages
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_assistant_messages_returns_all_three_fields():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    respx.get(f"{PROTON_BASE}/kb/assistants/asst-abc").mock(
        return_value=httpx.Response(200, json=ASSISTANT_ABC_RESPONSE)
    )
    client = _make_client()
    result = await client.get_assistant_messages(10)
    assert result is not None
    assert result["welcome"] == "Welcome!"
    assert result["handoff"] == "Connecting you to a human."
    assert result["resolution"] == "Your issue is resolved."
    await client.aclose()


@respx.mock
async def test_get_assistant_messages_returns_none_for_unknown_inbox():
    """Inbox 999 has no row in the inboxes response; should return None."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    client = _make_client()
    result = await client.get_assistant_messages(999)
    assert result is None
    await client.aclose()


@respx.mock
async def test_get_assistant_messages_returns_none_for_inbox_without_assistant_id():
    """Inbox 30 has no assistant_id in the fixture; should return None."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    client = _make_client()
    result = await client.get_assistant_messages(30)
    assert result is None
    await client.aclose()


@respx.mock
async def test_get_assistant_messages_returns_none_on_non_2xx_assistant_fetch():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    respx.get(f"{PROTON_BASE}/kb/assistants/asst-abc").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    client = _make_client()
    result = await client.get_assistant_messages(10)
    assert result is None
    await client.aclose()


@respx.mock
async def test_get_assistant_messages_returns_none_on_connection_error():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        side_effect=httpx.ConnectError("boom")
    )
    client = _make_client()
    result = await client.get_assistant_messages(10)
    assert result is None
    await client.aclose()


@respx.mock
async def test_get_assistant_messages_caches_assistant_fetch():
    """Two get_assistant_messages calls for the same inbox should only hit
    /kb/assistants/{id} once (result is cached by the _fetch_cached path)."""
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    assistant_route = respx.get(f"{PROTON_BASE}/kb/assistants/asst-abc").mock(
        return_value=httpx.Response(200, json=ASSISTANT_ABC_RESPONSE)
    )
    client = _make_client(ttl=60.0)
    r1 = await client.get_assistant_messages(10)
    r2 = await client.get_assistant_messages(10)
    assert r1 is not None
    assert r2 is not None
    assert r1["handoff"] == r2["handoff"]
    # The assistant endpoint should be called exactly once (cache hit on second call)
    assert assistant_route.call_count == 1
    await client.aclose()


@respx.mock
async def test_get_assistant_messages_empty_strings_when_fields_absent():
    """When the assistant config omits message fields, they default to empty string."""
    empty_config = {"id": "asst-abc", "config": {}}
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=INBOXES_RESPONSE)
    )
    respx.get(f"{PROTON_BASE}/kb/assistants/asst-abc").mock(
        return_value=httpx.Response(200, json=empty_config)
    )
    client = _make_client()
    result = await client.get_assistant_messages(10)
    assert result is not None
    assert result["welcome"] == ""
    assert result["handoff"] == ""
    assert result["resolution"] == ""
    await client.aclose()
