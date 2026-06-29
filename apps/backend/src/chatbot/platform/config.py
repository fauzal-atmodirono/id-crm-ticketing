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
    session_store: Literal["memory", "firestore"] = "memory"

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

    # Email channel — when True, AI replies become private draft notes instead of
    # public replies (draft-assist mode). Default False = auto-reply (public).
    email_draft_assist: bool = False

    # Firestore — persistent backing store for the handoff bridge.
    # When `handoff_store=firestore`, the bridge's session ↔ conversation
    # mapping survives backend restarts. Auth via ADC.
    handoff_store: Literal["memory", "firestore"] = "memory"
    firestore_project_id: str = "lv-playground-genai"
    firestore_database_id: str = "proton-db"
    firestore_handoff_collection: str = "handoff_sessions"

    # Bot-metrics dashboard (Zendesk -> BigQuery sync)
    bigquery_project_id: str = "lv-playground-genai"
    bigquery_dataset: str = "demo_proton"
    bigquery_conversations_table: str = "conversations"

    # Bot-metrics Phase 2 (per-turn MetricsPort -> BigQuery streaming)
    metrics_provider: Literal["noop", "bigquery"] = "noop"
    bigquery_turn_events_table: str = "turn_events"

    # Bot-metrics Phase 2 (in-app Zendesk -> BQ sync scheduler / the "trigger")
    metrics_sync_enabled: bool = False
    metrics_sync_interval_hours: int = 6

    # Chatwoot settings
    chatwoot_api_url: str = "http://localhost:3000"
    chatwoot_api_token: str = ""
    chatwoot_account_id: int = 1
    chatwoot_enabled: bool = True

    # Zammad settings
    zammad_api_url: str = "http://localhost:3000"
    zammad_api_token: str = ""
    zammad_enabled: bool = True

    # Twilio (WhatsApp Phase A; Phone Phase C). Empty by default so dev
    # environments without Twilio credentials still boot.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""  # e.g. "whatsapp:+60123456789"
    twilio_phone_number: str = ""  # e.g. "+60123456789"
    twilio_webhook_base_url: str = ""  # public https base for webhooks (ngrok in dev)

    # Voice settings
    voice_default_lang: str = "en-US"
    gemini_tts_model: str = "gemini-2.5-flash-tts"
    gemini_tts_voice: str = "Kore"

    # Phone (real-time Gemini Live) settings. Vertex publisher model id — the
    # AI-Studio name "gemini-live-2.5-flash-preview" is rejected by Vertex (1008).
    gemini_live_model: str = "gemini-live-2.5-flash-native-audio"
    gemini_live_voice: str = "Kore"
    # Optional output language hint (e.g. "ms-MY" for Bahasa Melayu). Empty = let
    # the model auto-detect. Only honored by half-cascade Live models; native-audio
    # models auto-detect language and ignore it.
    gemini_live_language: str = ""
    # Browser-softphone access tokens (Twilio Voice grant)
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = ""
    twilio_twiml_app_sid: str = ""
    # Public wss base for the <Stream> URL; falls back to twilio_webhook_base_url with https->wss
    public_wss_base_url: str = ""
    # Browser-softphone token endpoint hardening (a public SPA calls it, so it is
    # unauthenticated by nature). Short TTL limits a leaked token's lifetime; the
    # per-IP rate limit bounds the billing blast radius if the endpoint is abused.
    phone_token_ttl_seconds: int = 300
    phone_token_rate_limit: int = 10
    phone_token_rate_window_seconds: int = 60

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
