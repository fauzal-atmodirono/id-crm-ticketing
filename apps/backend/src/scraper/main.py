from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

from scraper.config import ScraperSettings
from scraper.html_utils import clean_html_for_storage
from scraper.indexer import import_documents, slug_for, upload_html, upload_jsonl, write_jsonl
from scraper.pipeline import build_doc
from scraper.sitemap import fetch_sitemap_urls
from scraper.transport import Transport

_log = structlog.get_logger(__name__)

JSONL_PATH = Path(__file__).parent.parent.parent / "scraped_data" / "proton-kb.jsonl"


def cmd_scrape(settings: ScraperSettings) -> None:
    urls = fetch_sitemap_urls(settings)
    _log.info("scraping", url_count=len(urls))
    transport = Transport(settings)
    html_dir = JSONL_PATH.parent
    html_dir.mkdir(parents=True, exist_ok=True)

    def pdf_fetcher(pdf_url: str) -> bytes | None:
        return transport.get_bytes(pdf_url)

    docs = []
    try:
        for i, url in enumerate(urls, 1):
            html = transport.fetch(url)
            if not html:
                continue
            slug = slug_for(url)
            # Write cleaned HTML alongside the JSONL so content.uri resolves.
            html_path = html_dir / f"{slug}.html"
            html_path.write_text(clean_html_for_storage(html, url), encoding="utf-8")
            doc = build_doc(url, html, settings, pdf_fetcher)
            docs.append(doc)
            _log.info("scraped", n=f"{i}/{len(urls)}", url=url)
    finally:
        transport.close()

    write_jsonl(docs, JSONL_PATH)


def cmd_index(settings: ScraperSettings) -> None:
    upload_html(settings, JSONL_PATH.parent)
    gcs_uri = upload_jsonl(settings, JSONL_PATH)
    import_documents(settings, gcs_uri)


def main() -> int:
    parser = argparse.ArgumentParser(description="Proton KB scraper")
    parser.add_argument("command", choices=["scrape", "index"])
    args = parser.parse_args()
    settings = ScraperSettings()
    if args.command == "scrape":
        cmd_scrape(settings)
    elif args.command == "index":
        cmd_index(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
