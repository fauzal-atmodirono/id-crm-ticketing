"""Application settings, sourced from environment variables.

Field names below map (case-insensitively) to the env vars documented in the
"Agent service" section of `deploy/.env.example` — names must match verbatim.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Internal service URLs
    chatwoot_url: str
    zammad_url: str

    # Chatwoot
    chatwoot_api_token: str
    chatwoot_platform_token: str
    chatwoot_account_id: int
    chatwoot_webhook_secret: str
    chatwoot_bot_secret: str
    chatwoot_bot_token: str

    # Zammad
    zammad_api_token: str
    zammad_webhook_secret: str
    zammad_integration_login: str = "integration@local"

    # Gemini / AI behavior
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"
    agent_mode: str = "suggest"
    auto_resolve: bool = False

    # Agent service's own database
    agent_database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
