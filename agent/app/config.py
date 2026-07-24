"""Application settings, sourced from environment variables.

Field names below map (case-insensitively) to the env vars documented in the
"Agent service" section of `deploy/.env.example` — names must match verbatim.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Internal service URLs — used for all API calls (resolve via the
    # platform docker network)
    chatwoot_url: str
    zammad_url: str

    # Public URLs — used only for human-facing links embedded in notes
    # (Zammad ticket body linking back to the Chatwoot conversation, Chatwoot
    # note linking to the Zammad ticket). Optional: fall back to the internal
    # URLs above so nothing breaks if unset (links just won't be clickable
    # from outside the docker network).
    chatwoot_public_url: str | None = None
    zammad_public_url: str | None = None

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
    # Feature flag for the Zammad -> Gemini draft-reply flow (services.responder)
    zammad_ai_drafts: bool = True

    # Gemini / AI behavior
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"
    agent_mode: str = "suggest"
    auto_resolve: bool = False

    # Conversation lifecycle & auto-close (feature is a no-op when disabled).
    # Drives the Proton process-flow SOP: idle warn/close, resolution
    # confirmation, rating surveys, AI disclaimer. See
    # docs/superpowers/specs/2026-07-23-conversation-lifecycle-autoclose-design.md
    lifecycle_enabled: bool = False
    lifecycle_scan_interval_seconds: int = 60
    lifecycle_idle_warn_minutes: int = 10
    lifecycle_idle_close_grace_minutes: int = 5
    lifecycle_idle_close_out_of_hours_grace_minutes: int = 0
    lifecycle_confirm_grace_minutes: int = 10
    lifecycle_survey_enabled: bool = True
    lifecycle_disclaimer_enabled: bool = True
    lifecycle_auto_categorize: bool = False

    # SOP completion (B: categorization taxonomy; C1: email auto-ack).
    # Comma-separated category slugs the bot may assign on resolution (must
    # match the tenant's deployed taxonomy). Empty → auto-categorize no-ops.
    lifecycle_category_labels: str = ""
    # C1: email once-per-thread auto-acknowledgement.
    email_autoack_enabled: bool = False
    email_autoack_template: str = (
        "Dear Customer,\n"
        "Thank you for your email. This message serves to acknowledge receipt "
        "of your enquiry.\n"
        "We will respond within one (1) business day during our operating hours.\n"
        "For urgent matters, please contact our Call Centre at 1300 888 877.\n"
        "Operating Hours:\n"
        "Monday–Friday: 8:30 AM – 5:30 PM\n"
        "Saturday, Sunday & Public Holidays: 9:00 AM – 5:00 PM\n"
        "Thank you for your patience and understanding.\n\n"
        "Warm regards,\n"
        "Proton e.MAS Centre"
    )

    # Agent service's own database
    agent_database_url: str

    # Proton conversational-AI backend (optional; feature disabled when blank)
    proton_backend_url: str | None = None
    proton_backend_key: str | None = None

    @property
    def chatwoot_display_url(self) -> str:
        """Chatwoot base URL for human-facing links: public if configured,
        otherwise the internal URL (still valid inside the docker network,
        just not clickable from outside it)."""
        return self.chatwoot_public_url or self.chatwoot_url

    @property
    def zammad_display_url(self) -> str:
        """Zammad base URL for human-facing links: public if configured,
        otherwise the internal URL (still valid inside the docker network,
        just not clickable from outside it)."""
        return self.zammad_public_url or self.zammad_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
