"""Test bootstrap: sets env vars required by app.config.Settings before any
test module imports the app, and points AGENT_DATABASE_URL at a throwaway
sqlite file (via aiosqlite) instead of postgres.
"""

import atexit
import os
import tempfile

_TEST_DB_FD, _TEST_DB_PATH = tempfile.mkstemp(suffix=".sqlite3", prefix="agent-test-")
os.close(_TEST_DB_FD)


@atexit.register
def _cleanup_test_db() -> None:
    try:
        os.remove(_TEST_DB_PATH)
    except OSError:
        pass


os.environ.setdefault("CHATWOOT_URL", "http://chatwoot-rails:3000")
os.environ.setdefault("CHATWOOT_API_TOKEN", "test-chatwoot-api-token")
os.environ.setdefault("CHATWOOT_PLATFORM_TOKEN", "test-chatwoot-platform-token")
os.environ.setdefault("CHATWOOT_ACCOUNT_ID", "1")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-api-key")
os.environ.setdefault("GEMINI_MODEL", "gemini-2.5-flash")
os.environ.setdefault("CHATWOOT_WEBHOOK_SECRET", "test-chatwoot-webhook-secret")
os.environ.setdefault("CHATWOOT_BOT_SECRET", "test-chatwoot-bot-secret")
os.environ.setdefault("CHATWOOT_BOT_TOKEN", "test-chatwoot-bot-token")
os.environ.setdefault("AGENT_MODE", "suggest")
os.environ.setdefault("AUTO_RESOLVE", "false")
os.environ.setdefault("AGENT_DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB_PATH}")

import pytest

from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.db.models import Base
from app.db.session import async_session_maker, init_db


@pytest.fixture(autouse=True)
def _reset_client_singletons():
    """`get_chatwoot_client`/`get_proton_config_client` are process-wide
    `lru_cache` singletons (see `app/clients/deps.py`). A test that exercises
    the real lifespan (`test_lifecycle_lifespan.py`) calls `aclose_clients`,
    which closes whichever singleton happens to already be cached -- without
    this, any earlier test that populated the cache leaves every later test
    that reuses it hitting a closed `httpx.AsyncClient`
    ("RuntimeError: Cannot send a request, as the client has been closed").
    Clearing both caches after every test keeps them test-local regardless
    of file/collection order.
    """
    yield
    get_chatwoot_client.cache_clear()
    get_proton_config_client.cache_clear()


@pytest.fixture(autouse=True)
async def _reset_agent_db():
    """Tests write rows to tables like processed_deliveries/ai_actions.
    Create the tables once (idempotent) and wipe rows after each test so
    tests sharing the session-scoped sqlite file stay isolated from each
    other's unique-constrained rows.
    """
    await init_db()
    yield
    async with async_session_maker() as session:
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()
