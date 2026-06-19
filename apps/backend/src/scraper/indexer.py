from __future__ import annotations

import json
import re
from pathlib import Path

import structlog
from google.cloud import discoveryengine_v1beta as discoveryengine
from google.cloud import storage  # type: ignore[attr-defined]

from scraper.config import ScraperSettings
from scraper.models import ScrapedDoc

_log = structlog.get_logger(__name__)


def slug_for(url: str) -> str:
    bare = url.replace("https://", "").replace("http://", "").rstrip("/")
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", bare) or "index"


def doc_id_for(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", slug)[:63] or "doc"


def write_jsonl(docs: list[ScrapedDoc], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(d.to_document(), ensure_ascii=False) for d in docs]
    path.write_text("\n".join(lines), encoding="utf-8")
    _log.info("wrote_jsonl", count=len(docs), path=str(path))


def upload_jsonl(settings: ScraperSettings, path: Path) -> str:
    client = storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.bucket_name)
    blob_name = f"{settings.gcs_prefix}/proton-kb.jsonl"
    bucket.blob(blob_name).upload_from_filename(str(path), content_type="application/json")
    gcs_uri = f"gs://{settings.bucket_name}/{blob_name}"
    _log.info("uploaded_jsonl", uri=gcs_uri)
    return gcs_uri


def import_documents(settings: ScraperSettings, gcs_uri: str) -> None:
    client = discoveryengine.DocumentServiceClient()
    parent = client.branch_path(
        settings.project_id, settings.location, settings.data_store_id, "default_branch"
    )
    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        gcs_source=discoveryengine.GcsSource(input_uris=[gcs_uri], data_schema="document"),
        reconciliation_mode=discoveryengine.ImportDocumentsRequest.ReconciliationMode.FULL,
    )
    op = client.import_documents(request=request)
    _log.info("import_started", lro=op.operation.name)
    op.result()  # type: ignore[no-untyped-call]
    _log.info("import_completed")
