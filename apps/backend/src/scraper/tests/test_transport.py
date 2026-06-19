from __future__ import annotations

from scraper.config import ScraperSettings
from scraper.transport import needs_render

SETTINGS = ScraperSettings()


def test_needs_render_true_for_empty_main() -> None:
    html = "<html><body><main></main></body></html>"
    assert needs_render(html, SETTINGS) is True


def test_needs_render_false_for_rich_main() -> None:
    html = "<html><body><main>" + ("content " * 100) + "</main></body></html>"
    assert needs_render(html, SETTINGS) is False
