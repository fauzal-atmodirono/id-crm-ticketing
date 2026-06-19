#!/usr/bin/env python
"""Scrape the Proton website, structure each page as a Vertex AI Search Document,
and import the result into the `proton-kb` data store.

Pipeline:
    1. Fetch sitemap.xml  →  list of URLs to scrape.
    2. For each URL: download HTML, extract structured fields
       (title, body_excerpt, headings, language, canonical link), and write:
         a. apps/backend/scraped_data/<slug>.html   (raw cleaned HTML)
         b. apps/backend/scraped_data/proton-kb.jsonl  (one Document per line)
    3. Upload BOTH the HTML files and the JSONL to GCS.
    4. (Re-)create the Discovery Engine data store and engine if missing.
    5. ImportDocuments with `data_schema="document"` so Vertex indexes our
       structured fields directly — no post-ingest backfill required.

Run the whole pipeline:
    .venv/bin/python scripts/scrape_proton.py

Or individual stages:
    .venv/bin/python scripts/scrape_proton.py --scrape-only
    .venv/bin/python scripts/scrape_proton.py --upload-only
    .venv/bin/python scripts/scrape_proton.py --import-only
    .venv/bin/python scripts/scrape_proton.py --purge   # wipe + reingest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from google.cloud import discoveryengine_v1beta as discoveryengine
from google.cloud import storage

# --- Configuration -----------------------------------------------------------

PROJECT_ID = "lv-playground-genai"
LOCATION = "global"
DATA_STORE_ID = "proton-kb"
ENGINE_ID = "proton-kb-engine"
BUCKET_NAME = f"proton-kb-scraped-{PROJECT_ID}"
GCS_HTML_PREFIX = "scraped_data"
JSONL_BLOB_NAME = f"{GCS_HTML_PREFIX}/proton-kb.jsonl"
SCRAPED_DIR = Path(__file__).parent.parent / "scraped_data"
JSONL_PATH = SCRAPED_DIR / "proton-kb.jsonl"
SITEMAP_URL = "https://www.proton.com/sitemap.xml"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


# --- Stage 1: scrape ---------------------------------------------------------


def get_urls_from_sitemap(sitemap_url: str) -> list[str]:
    print(f"Fetching sitemap from {sitemap_url}…")
    req = urllib.request.Request(sitemap_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as response:
        xml_data = response.read()
    root = ET.fromstring(xml_data)
    urls = [elem.text for elem in root.iter() if elem.tag.endswith("loc") and elem.text]
    print(f"  → {len(urls)} URLs in sitemap")
    return urls


def slug_for(url: str) -> str:
    bare = url.replace("https://", "").replace("http://", "").rstrip("/")
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", bare)
    return slug or "index"


def doc_id_for(slug: str) -> str:
    # Discovery Engine document IDs accept [a-zA-Z0-9_-], max 63 chars.
    return re.sub(r"[^a-zA-Z0-9_-]", "_", slug)[:63] or "doc"


def extract_structured_fields(html: str, source_url: str) -> dict[str, object]:
    """Pull title, language, body_excerpt, and section headings out of an HTML
    page so Vertex can index them as structured fields.
    """
    soup = BeautifulSoup(html, "html.parser")

    title = (
        soup.title.string.strip()
        if soup.title and soup.title.string
        else "PROTON Page"
    )

    lang = (soup.html.get("lang") if soup.html else None) or "en"
    # Normalize to BCP-47 prefix the agent understands.
    lang = lang.split("-")[0].lower()
    if lang not in {"en", "ms", "zh"}:
        lang = "en"

    # Strip obvious chrome / noise before extracting body text.
    for el in soup(
        ["script", "style", "noscript", "nav", "header", "footer", "iframe", "svg"]
    ):
        el.decompose()

    body = soup.body if soup.body else soup
    text = body.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    body_excerpt = text[:1500]

    headings: list[str] = []
    for h in body.find_all(["h1", "h2", "h3"]):
        heading_text = h.get_text(strip=True)
        if heading_text and heading_text not in headings:
            headings.append(heading_text)
        if len(headings) >= 20:
            break

    return {
        "title": title,
        "link": source_url,
        "language": lang,
        "body_excerpt": body_excerpt,
        "sections": headings,
    }


def clean_html_for_storage(html: str, source_url: str) -> str:
    """Produce a slim HTML payload to keep in GCS alongside the JSONL —
    useful for raw-content retrieval if Vertex Enterprise tier is later
    enabled (snippets / extractive answers).
    """
    soup = BeautifulSoup(html, "html.parser")
    title = (
        soup.title.string.strip()
        if soup.title and soup.title.string
        else "PROTON Page"
    )
    for el in soup(["script", "style", "noscript", "iframe", "svg"]):
        el.decompose()
    body = soup.body if soup.body else soup
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        '  <meta charset="utf-8">\n'
        f'  <title>{title}</title>\n'
        f'  <link rel="canonical" href="{source_url}">\n'
        '</head>\n<body>\n'
        f"  {body}\n"
        "</body>\n</html>"
    )


def scrape_website() -> None:
    urls = get_urls_from_sitemap(SITEMAP_URL)
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)

    documents: list[dict[str, object]] = []
    with httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True) as client:
        for idx, url in enumerate(urls, start=1):
            slug = slug_for(url)
            html_path = SCRAPED_DIR / f"{slug}.html"
            print(f"  [{idx}/{len(urls)}] {url}")
            try:
                res = client.get(url)
                if res.status_code != 200:
                    print(f"    skipped: HTTP {res.status_code}")
                    continue
                fields = extract_structured_fields(res.text, url)
                # Persist HTML copy for re-use / debugging.
                html_path.write_text(
                    clean_html_for_storage(res.text, url), encoding="utf-8"
                )
                documents.append(
                    {
                        "id": doc_id_for(slug),
                        "struct_data": fields,
                        "content": {
                            "uri": f"gs://{BUCKET_NAME}/{GCS_HTML_PREFIX}/{slug}.html",
                            "mime_type": "text/html",
                        },
                    }
                )
            except Exception as e:
                print(f"    error: {e}")

    JSONL_PATH.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in documents), encoding="utf-8"
    )
    print(f"  → wrote {len(documents)} JSONL records to {JSONL_PATH}")


# --- Stage 2: upload to GCS --------------------------------------------------


def ensure_bucket(client: storage.Client) -> storage.Bucket:
    try:
        return client.get_bucket(BUCKET_NAME)
    except Exception:
        print(f"Creating bucket gs://{BUCKET_NAME}…")
        return client.create_bucket(BUCKET_NAME, location="us-central1")


def upload_to_gcs() -> None:
    print(f"Uploading to gs://{BUCKET_NAME}…")
    client = storage.Client(project=PROJECT_ID)
    bucket = ensure_bucket(client)

    html_files = sorted(SCRAPED_DIR.glob("*.html"))
    for idx, path in enumerate(html_files, start=1):
        blob = bucket.blob(f"{GCS_HTML_PREFIX}/{path.name}")
        blob.upload_from_filename(str(path), content_type="text/html")
        if idx % 20 == 0 or idx == len(html_files):
            print(f"  HTML uploaded {idx}/{len(html_files)}")

    if JSONL_PATH.exists():
        bucket.blob(JSONL_BLOB_NAME).upload_from_filename(
            str(JSONL_PATH), content_type="application/json"
        )
        print(f"  JSONL uploaded → gs://{BUCKET_NAME}/{JSONL_BLOB_NAME}")
    else:
        print(f"  WARNING: {JSONL_PATH} missing — re-run with --scrape-only first")


# --- Stage 3: data store + engine --------------------------------------------


def ensure_data_store_and_engine() -> None:
    ds_client = discoveryengine.DataStoreServiceClient()
    engine_client = discoveryengine.EngineServiceClient()

    parent_collection = ds_client.collection_path(
        project=PROJECT_ID, location=LOCATION, collection="default_collection"
    )

    ds_name = ds_client.data_store_path(
        project=PROJECT_ID, location=LOCATION, data_store=DATA_STORE_ID
    )
    try:
        ds_client.get_data_store(name=ds_name)
        print(f"Data store '{DATA_STORE_ID}' already exists")
    except Exception:
        print(f"Creating data store '{DATA_STORE_ID}'…")
        op = ds_client.create_data_store(
            parent=parent_collection,
            data_store=discoveryengine.DataStore(
                display_name="Proton KB",
                industry_vertical=discoveryengine.IndustryVertical.GENERIC,
                content_config=discoveryengine.DataStore.ContentConfig.CONTENT_REQUIRED,
                solution_types=[discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH],
            ),
            data_store_id=DATA_STORE_ID,
        )
        op.result()
        print(f"  → '{DATA_STORE_ID}' created")

    engine_name = engine_client.engine_path(
        project=PROJECT_ID, location=LOCATION,
        collection="default_collection", engine=ENGINE_ID,
    )
    try:
        engine_client.get_engine(name=engine_name)
        print(f"Engine '{ENGINE_ID}' already exists")
    except Exception:
        print(f"Creating engine '{ENGINE_ID}'…")
        op = engine_client.create_engine(
            parent=parent_collection,
            engine=discoveryengine.Engine(
                display_name="Proton Chat Search",
                solution_type=discoveryengine.SolutionType.SOLUTION_TYPE_SEARCH,
                data_store_ids=[DATA_STORE_ID],
                search_engine_config=discoveryengine.Engine.SearchEngineConfig(
                    search_tier=discoveryengine.SearchTier.SEARCH_TIER_STANDARD
                ),
            ),
            engine_id=ENGINE_ID,
        )
        op.result()
        print(f"  → '{ENGINE_ID}' created")


# --- Stage 4: import JSONL ---------------------------------------------------


def list_existing_doc_ids() -> Iterable[str]:
    client = discoveryengine.DocumentServiceClient()
    parent = client.branch_path(
        PROJECT_ID, LOCATION, DATA_STORE_ID, "default_branch"
    )
    request = discoveryengine.ListDocumentsRequest(parent=parent, page_size=200)
    for d in client.list_documents(request=request):
        yield d.id


def purge_data_store() -> None:
    """Delete every document in the data store. Use with care."""
    client = discoveryengine.DocumentServiceClient()
    parent = client.branch_path(
        PROJECT_ID, LOCATION, DATA_STORE_ID, "default_branch"
    )
    print("Purging existing documents…")
    deleted = 0
    for doc_id in list(list_existing_doc_ids()):
        try:
            client.delete_document(name=f"{parent}/documents/{doc_id}")
            deleted += 1
        except Exception as e:
            print(f"  warning: delete {doc_id}: {e}")
        if deleted % 20 == 0:
            print(f"  deleted {deleted}…")
    print(f"  → {deleted} documents deleted")


def import_from_jsonl() -> None:
    doc_client = discoveryengine.DocumentServiceClient()
    parent = doc_client.branch_path(
        PROJECT_ID, LOCATION, DATA_STORE_ID, "default_branch"
    )

    gcs_uri = f"gs://{BUCKET_NAME}/{JSONL_BLOB_NAME}"
    print(f"Importing structured Documents from {gcs_uri}…")
    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        gcs_source=discoveryengine.GcsSource(
            input_uris=[gcs_uri],
            data_schema="document",
        ),
        reconciliation_mode=(
            discoveryengine.ImportDocumentsRequest.ReconciliationMode.FULL
        ),
    )
    op = doc_client.import_documents(request=request)
    print(f"  LRO: {op.operation.name}")
    print("  Waiting for completion (this can take several minutes)…")
    result = op.result()
    print("  → import completed")
    print(result)


# --- CLI ---------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scrape-only", action="store_true")
    parser.add_argument("--upload-only", action="store_true")
    parser.add_argument("--import-only", action="store_true")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete all documents in the data store before importing",
    )
    args = parser.parse_args()

    do_all = not (args.scrape_only or args.upload_only or args.import_only)

    if do_all or args.scrape_only:
        scrape_website()
    if do_all or args.upload_only:
        upload_to_gcs()
    if do_all:
        ensure_data_store_and_engine()
    if args.purge:
        purge_data_store()
    if do_all or args.import_only:
        import_from_jsonl()

    print("\n=== DONE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
