from __future__ import annotations

from scraper.config import ScraperSettings
from scraper.sitemap import filter_urls, parse_sitemap

SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.proton.com/models/all-new-x50</loc></url>
  <url><loc>https://www.proton.com/privacy-notice</loc></url>
  <url><loc>https://www.proton.com/about-us</loc></url>
</urlset>"""


def test_parse_sitemap_extracts_locs() -> None:
    urls = parse_sitemap(SITEMAP)
    assert "https://www.proton.com/models/all-new-x50" in urls
    assert len(urls) == 3


def test_filter_urls_applies_allow_and_deny() -> None:
    settings = ScraperSettings()
    urls = parse_sitemap(SITEMAP)
    kept = filter_urls(urls, settings)
    assert "https://www.proton.com/models/all-new-x50" in kept
    # Denied by substring.
    assert "https://www.proton.com/privacy-notice" not in kept
    # Not in allow_prefixes.
    assert "https://www.proton.com/about-us" not in kept
