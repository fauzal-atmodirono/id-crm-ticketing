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
os.environ.setdefault("ZAMMAD_URL", "http://zammad-nginx:8080")
os.environ.setdefault("CHATWOOT_API_TOKEN", "test-chatwoot-api-token")
os.environ.setdefault("CHATWOOT_PLATFORM_TOKEN", "test-chatwoot-platform-token")
os.environ.setdefault("CHATWOOT_ACCOUNT_ID", "1")
os.environ.setdefault("ZAMMAD_API_TOKEN", "test-zammad-api-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-api-key")
os.environ.setdefault("GEMINI_MODEL", "gemini-2.5-flash")
os.environ.setdefault("CHATWOOT_WEBHOOK_SECRET", "test-chatwoot-webhook-secret")
os.environ.setdefault("CHATWOOT_BOT_SECRET", "test-chatwoot-bot-secret")
os.environ.setdefault("CHATWOOT_BOT_TOKEN", "test-chatwoot-bot-token")
os.environ.setdefault("ZAMMAD_WEBHOOK_SECRET", "test-zammad-webhook-secret")
os.environ.setdefault("ZAMMAD_INTEGRATION_LOGIN", "integration@local")
os.environ.setdefault("AGENT_MODE", "suggest")
os.environ.setdefault("AUTO_RESOLVE", "false")
os.environ.setdefault("AGENT_DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB_PATH}")
