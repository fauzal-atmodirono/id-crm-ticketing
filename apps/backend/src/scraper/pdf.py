from __future__ import annotations

import io

import structlog
from pypdf import PdfReader

from scraper.html_utils import clean_text

_log = structlog.get_logger(__name__)


def extract_pdf_text(data: bytes) -> str:
    """Extract plain text from PDF bytes. Returns '' on any failure."""
    try:
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = [page.extract_text() or "" for page in reader.pages]
        return clean_text(" ".join(parts))
    except Exception as e:  # BLE001 - scraper must never crash on a bad PDF
        _log.warning("pdf_extract_failed", error=str(e))
        return ""
