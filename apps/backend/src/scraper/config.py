from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ScraperSettings(BaseSettings):
    """Scraper configuration. Override any field via SCRAPER_* env vars."""

    project_id: str = "lv-playground-genai"
    location: str = "global"
    data_store_id: str = "proton-kb"
    engine_id: str = "proton-kb-engine"
    bucket_name: str = "proton-kb-scraped-lv-playground-genai"
    gcs_prefix: str = "scraped_data"
    sitemap_url: str = "https://www.proton.com/sitemap.xml"

    # Crawl behaviour
    request_timeout: float = 20.0
    delay_seconds: float = 1.0
    headless: bool = True
    # Render with Selenium only when httpx content looks empty.
    min_main_chars: int = 200

    # Only crawl these path prefixes; drop everything else.
    allow_prefixes: tuple[str, ...] = ("/models", "/all-new", "/after-sales", "/ownership")
    deny_substrings: tuple[str, ...] = ("/privacy", "/thank-you", "/cookie", "/terms")

    model_config = SettingsConfigDict(env_prefix="SCRAPER_", extra="ignore")
