"""Tests for the Task 3 handoff note: httpx client lifecycle must be owned
end-to-end (constructed on demand, closed on app shutdown) rather than left
for garbage collection to clean up.
"""

from app.clients.deps import get_chatwoot_client, get_zammad_client
from app.main import create_app, lifespan


async def test_lifespan_closes_clients_constructed_during_the_request_cycle():
    app = create_app()

    async with lifespan(app):
        chatwoot_client = get_chatwoot_client()
        zammad_client = get_zammad_client()
        assert chatwoot_client._client.is_closed is False
        assert zammad_client._client.is_closed is False

    assert chatwoot_client._client.is_closed is True
    assert zammad_client._client.is_closed is True

    # Clear the lru_cache singletons so later tests get fresh (open) clients
    # instead of reusing ones this test just closed.
    get_chatwoot_client.cache_clear()
    get_zammad_client.cache_clear()
