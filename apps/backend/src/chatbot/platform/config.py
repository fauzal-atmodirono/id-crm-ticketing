from __future__ import annotations

from functools import cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables and .env file."""

    # Provider configuration
    crm_provider: Literal["chatwoot", "zendesk"] = "chatwoot"
    voice_provider: Literal["mock", "gcp"] = "mock"
    knowledge_provider: Literal["mock", "zendesk", "vertex_search"] = "mock"

    # Server settings
    port: int = 8000
    host: str = "127.0.0.1"
    debug: bool = True

    # GCP / Vertex AI settings
    google_genai_use_vertexai: bool = False
    vertex_project_id: str | None = None
    vertex_location: str = "us-central1"
    gemini_model: str = "gemini-2.5-flash"

    # Vertex AI Search settings
    vertex_search_project_id: str = ""
    vertex_search_location: str = "global"
    vertex_search_data_store_id: str = "proton-kb"
    vertex_search_engine_id: str = "proton-kb-engine"

    # Zendesk credentials
    zendesk_subdomain: str = "your-zendesk-subdomain"
    zendesk_email: str = ""
    zendesk_app_id: str = ""
    zendesk_key_id: str = ""
    zendesk_secret_key: str = ""
    zendesk_api_token: str = ""

    # Sunshine Conversations webhook integration secret (used to HMAC-verify
    # inbound /webhooks/sunshine requests). Configured in the Sunshine
    # Conversations dashboard alongside the webhook URL.
    sunshine_webhook_secret: str = ""

    # Secret used to verify webhook calls from Zendesk Support (for standard ticket comment syncing)
    zendesk_support_webhook_secret: str = ""


    # Firestore — persistent backing store for the handoff bridge.
    # When `handoff_store=firestore`, the bridge's session ↔ conversation
    # mapping survives backend restarts. Auth via ADC.
    handoff_store: Literal["memory", "firestore"] = "memory"
    firestore_project_id: str = "lv-playground-genai"
    firestore_database_id: str = "proton-db"
    firestore_handoff_collection: str = "handoff_sessions"

    # Chatwoot settings
    chatwoot_api_url: str = "http://localhost:3000"
    chatwoot_api_token: str = ""
    chatwoot_account_id: int = 1
    chatwoot_enabled: bool = True

    # Zammad settings
    zammad_api_url: str = "http://localhost:3000"
    zammad_api_token: str = ""
    zammad_enabled: bool = True

    # Voice settings
    voice_default_lang: str = "en-US"
    gemini_tts_model: str = "gemini-2.5-flash-tts"
    gemini_tts_voice: str = "Kore"

    # Frontend CORS — origins of the Vue dev/prod app (comma-separated in env).
    # Defaults cover Vite's first few fallback ports (5173-5180) so a stale dev
    # server on 5173 doesn't break a fresh one bound to 5174+.
    frontend_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5177",
        "http://localhost:5178",
        "http://localhost:5179",
        "http://localhost:5180",
    ]

    # Settings configurations
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@cache
def get_settings() -> Settings:
    """Helper function to load and cache application settings."""
    return Settings()
