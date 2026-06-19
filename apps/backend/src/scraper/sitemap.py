from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from scraper.config import ScraperSettings


def parse_sitemap(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)  # noqa: S314 - sitemap XML from trusted HTTPS source
    urls: list[str] = []
    for e in root.iter():
        if e.tag.endswith("loc") and e.text:
            urls.append(e.text)
    return urls


def filter_urls(urls: list[str], settings: ScraperSettings) -> list[str]:
    kept: list[str] = []
    for url in urls:
        if any(s in url for s in settings.deny_substrings):
            continue
        path = urlparse(url).path
        if not any(path.startswith(p) for p in settings.allow_prefixes):
            continue
        if url not in kept:
            kept.append(url)
    return sorted(kept)


def fetch_sitemap_urls(settings: ScraperSettings) -> list[str]:
    req = urllib.request.Request(  # noqa: S310 - fixed https sitemap URL
        settings.sitemap_url, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        xml = resp.read()
    return filter_urls(parse_sitemap(xml), settings)
