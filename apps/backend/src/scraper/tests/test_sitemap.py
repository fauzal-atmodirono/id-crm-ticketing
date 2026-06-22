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


def test_filter_urls_restricts_when_allow_prefixes_set() -> None:
    settings = ScraperSettings(allow_prefixes=("/models",), deny_substrings=("/privacy",))
    urls = parse_sitemap(SITEMAP)
    kept = filter_urls(urls, settings)
    assert "https://www.proton.com/models/all-new-x50" in kept
    # Denied by substring.
    assert "https://www.proton.com/privacy-notice" not in kept
    # Not in allow_prefixes.
    assert "https://www.proton.com/about-us" not in kept


def test_filter_urls_empty_allow_prefixes_keeps_everything_but_deny() -> None:
    # Default settings now have empty allow_prefixes => crawl the whole site,
    # only dropping deny-listed functional pages.
    settings = ScraperSettings(deny_substrings=("/login",))
    sitemap = (
        b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://www.proton.com/about-us</loc></url>"
        b"<url><loc>https://www.proton.com/happenings/news</loc></url>"
        b"<url><loc>https://www.proton.com/login</loc></url>"
        b"</urlset>"
    )
    kept = filter_urls(parse_sitemap(sitemap), settings)
    assert "https://www.proton.com/about-us" in kept
    assert "https://www.proton.com/happenings/news" in kept
    assert "https://www.proton.com/login" not in kept
