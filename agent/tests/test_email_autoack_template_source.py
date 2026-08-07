"""The email auto-ack body comes from the tenant store, with env as fallback.

Note: the brief's sample payload shape (`{"email_autoack_template": {...}}` at
the top level) does not match the real `GET /kb/settings` response, which
wraps every managed key under a `"settings"` key (see
`kb_settings_router.py`'s `get_settings_endpoint` in the backend, and
`ProtonConfigClient.effective_debounce_seconds` for the existing agent-side
precedent). The mocks below use the real `{"settings": {...}}` shape.
"""

import httpx
import respx

from app.clients.proton import ProtonConfigClient

PROTON = "http://proton-backend:8080"


@respx.mock
async def test_returns_store_value():
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(
            200,
            json={
                "settings": {
                    "email_autoack_template": {"value": "Stored body", "source": "override"}
                }
            },
        )
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_email_autoack_template() == "Stored body"
    await client.aclose()


@respx.mock
async def test_returns_none_when_unset_so_caller_uses_env():
    respx.get(f"{PROTON}/kb/settings").mock(
        return_value=httpx.Response(
            200, json={"settings": {"email_autoack_template": {"value": "", "source": "env"}}}
        )
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_email_autoack_template() is None
    await client.aclose()


@respx.mock
async def test_returns_none_when_key_absent():
    respx.get(f"{PROTON}/kb/settings").mock(return_value=httpx.Response(200, json={"settings": {}}))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_email_autoack_template() is None
    await client.aclose()


@respx.mock
async def test_returns_none_on_backend_error():
    respx.get(f"{PROTON}/kb/settings").mock(return_value=httpx.Response(500))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_email_autoack_template() is None
    await client.aclose()
