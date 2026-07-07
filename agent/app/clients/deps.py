"""Singleton accessors for the httpx-based Chatwoot/Zammad API clients.

The sync layer and webhook routers call these instead of constructing
clients themselves, so there's exactly one long-lived `httpx.AsyncClient`
(with its own connection pool) per downstream service for the life of the
process. `app.main`'s lifespan owns closing them at shutdown via
`aclose_clients`, addressing the Task 3 handoff note that client lifecycle
was previously unowned.
"""

from functools import lru_cache

from app.clients.chatwoot import ChatwootClient
from app.clients.zammad import ZammadClient
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
def get_zammad_client() -> ZammadClient:
    settings = get_settings()
    return ZammadClient(
        base_url=settings.zammad_url,
        api_token=settings.zammad_api_token,
    )


async def aclose_clients() -> None:
    """Close whichever clients were actually constructed. Safe to call even
    if a given client was never instantiated (e.g. in tests that don't
    exercise it)."""
    if get_chatwoot_client.cache_info().currsize:
        await get_chatwoot_client().aclose()
    if get_zammad_client.cache_info().currsize:
        await get_zammad_client().aclose()
