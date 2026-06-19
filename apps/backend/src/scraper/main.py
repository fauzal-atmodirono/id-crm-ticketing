from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

from scraper.config import ScraperSettings
from scraper.indexer import import_documents, upload_jsonl, write_jsonl
from scraper.pdf import extract_pdf_text
from scraper.pipeline import brochure_doc, build_doc
from scraper.sitemap import fetch_sitemap_urls
from scraper.transport import Transport

_log = structlog.get_logger(__name__)

JSONL_PATH = Path(__file__).parent.parent.parent / "scraped_data" / "proton-kb.jsonl"


def cmd_scrape(settings: ScraperSettings) -> None:
    urls = fetch_sitemap_urls(settings)
    _log.info("scraping", url_count=len(urls))
    transport = Transport(settings)

    def pdf_fetcher(pdf_url: str) -> bytes | None:
        return transport.get_bytes(pdf_url)

    docs = []
    try:
        for i, url in enumerate(urls, 1):
            html = transport.fetch(url)
            if not html:
                continue
            doc = build_doc(url, html, settings, pdf_fetcher)
            docs.append(doc)
            # Emit a separate brochure document when one exists.
            if doc.source_type == "model" and doc.brochure_url:
                data = pdf_fetcher(doc.brochure_url)
                text = extract_pdf_text(data) if data else ""
                if text:
                    docs.append(brochure_doc(doc, settings, text))
            _log.info("scraped", n=f"{i}/{len(urls)}", url=url)
    finally:
        transport.close()

    write_jsonl(docs, JSONL_PATH)


def cmd_index(settings: ScraperSettings) -> None:
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
