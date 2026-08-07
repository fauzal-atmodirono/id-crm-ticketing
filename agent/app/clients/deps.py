"""Singleton accessors for the httpx-based Chatwoot API client.

The sync layer and webhook routers call these instead of constructing
clients themselves, so there's exactly one long-lived `httpx.AsyncClient`
(with its own connection pool) per downstream service for the life of the
process. `app.main`'s lifespan owns closing them at shutdown via
`aclose_clients`, addressing the Task 3 handoff note that client lifecycle
was previously unowned.

`get_proton_config_client` follows the same pattern but returns None when the
proton backend is not configured (proton_backend_url / proton_backend_key
absent from Settings), so callers can fail-open without branching on env vars.
"""

from functools import lru_cache

from app.clients.chatwoot import ChatwootClient
from app.clients.proton import ProtonConfigClient
from app.config import get_settings


@lru_cache
def get_chatwoot_client() -> ChatwootClient:
    settings = get_settings()
    return ChatwootClient(
        base_url=settings.chatwoot_url,
        api_access_token=settings.chatwoot_api_token,
        account_id=settings.chatwoot_account_id,
    )


@lru_cache
def get_proton_config_client() -> ProtonConfigClient | None:
    """Return the ProtonConfigClient singleton, or None when unconfigured.

    Returns None if either proton_backend_url or proton_backend_key is falsy,
    so callers can simply do `if (proton := get_proton_config_client()):` and
    the entire feature is a no-op when the env vars are absent.
    """
    settings = get_settings()
    if not settings.proton_backend_url or not settings.proton_backend_key:
        return None
    return ProtonConfigClient(
        base_url=settings.proton_backend_url,
        api_key=settings.proton_backend_key,
    )


async def aclose_clients() -> None:
    """Close whichever clients were actually constructed, then evict them
    from the `lru_cache`. Safe to call even if a given client was never
    instantiated (e.g. in tests that don't exercise it).

    The cache_clear() calls matter beyond tidiness: without them a closed
    client stays cached, and every subsequent get_chatwoot_client()/
    get_proton_config_client() call hands back that closed client instead of
    constructing a fresh one -- a latent bug for any process that re-enters
    the lifespan (or, in tests, for any test that runs after one that
    exercises the real lifespan and reuses either singleton)."""
    if get_chatwoot_client.cache_info().currsize:
        await get_chatwoot_client().aclose()
        get_chatwoot_client.cache_clear()
    if get_proton_config_client.cache_info().currsize:
        proton = get_proton_config_client()
        if proton is not None:
            await proton.aclose()
        get_proton_config_client.cache_clear()
