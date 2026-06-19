from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from bs4 import BeautifulSoup

from scraper.config import ScraperSettings
from scraper.html_utils import main_container

_log = structlog.get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_HTTP_OK = 200


def needs_render(html: str, settings: ScraperSettings) -> bool:
    """True when the static HTML's main content is too thin (JS-rendered page)."""
    soup = BeautifulSoup(html, "html.parser")
    text = main_container(soup).get_text(separator=" ", strip=True)
    return len(text) < settings.min_main_chars


class Transport:
    """httpx-first fetcher with a lazily-created Selenium fallback."""

    def __init__(self, settings: ScraperSettings) -> None:
        self._settings = settings
        self._client = httpx.Client(
            headers={"User-Agent": _UA},
            timeout=settings.request_timeout,
            follow_redirects=True,
        )
        self._driver: Any | None = None

    def _selenium_driver(self) -> Any:
        if self._driver is None:
            from selenium import webdriver  # noqa: PLC0415
            from selenium.webdriver.chrome.options import Options  # noqa: PLC0415
            from selenium.webdriver.chrome.service import Service  # noqa: PLC0415
            from webdriver_manager.chrome import ChromeDriverManager  # noqa: PLC0415

            opts = Options()
            if self._settings.headless:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument(f"user-agent={_UA}")
            self._driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), options=opts
            )
        return self._driver

    def fetch(self, url: str) -> str | None:
        time.sleep(self._settings.delay_seconds)
        try:
            res = self._client.get(url)
            if res.status_code != _HTTP_OK:
                _log.info("fetch_non_200", url=url, status=res.status_code)
                return None
            html = res.text
        except Exception as e:  # broad catch: httpx can raise many error subtypes
            _log.warning("httpx_fetch_failed", url=url, error=str(e))
            return None

        if not needs_render(html, self._settings):
            return html

        # Fallback: render with Selenium.
        try:
            driver = self._selenium_driver()
            driver.get(url)
            time.sleep(2.0)
            return str(driver.page_source)
        except Exception as e:  # broad catch: Selenium can raise many error subtypes
            _log.warning("selenium_fetch_failed", url=url, error=str(e))
            return html  # better than nothing

    def get_bytes(self, url: str) -> bytes | None:
        """Fetch raw bytes (e.g. a PDF) via the httpx client. Returns None on failure."""
        try:
            res = self._client.get(url)
            return res.content if res.status_code == _HTTP_OK else None
        except Exception as e:
            _log.warning("get_bytes_failed", url=url, error=str(e))
            return None

    def close(self) -> None:
        self._client.close()
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception as e:  # broad catch: driver may already be dead
                _log.warning("selenium_quit_failed", error=str(e))
