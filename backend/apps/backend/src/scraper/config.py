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

    # JS rendering. Proton pages lazy-load hero images, brochure/pricelist PDFs,
    # and news via client-side JS, so render-first (Selenium + scroll) captures
    # far more than httpx alone. Set render=False for a fast httpx-only crawl.
    render: bool = True
    render_wait: float = 3.0
    scroll_passes: int = 6
    scroll_wait: float = 1.0

    # Path prefixes to crawl. EMPTY means "crawl the entire sitemap" (only the
    # deny-list below is applied). Set a non-empty tuple to restrict coverage.
    allow_prefixes: tuple[str, ...] = ()
    # Drop functional / non-content pages (auth, search, form thank-you, cookie).
    deny_substrings: tuple[str, ...] = (
        "/login",
        "/forget-password",
        "/search-results",
        "/count-down",
        "/thank-you",
        "/cookie",
    )

    model_config = SettingsConfigDict(env_prefix="SCRAPER_", extra="ignore")
