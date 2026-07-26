"""Knowledge ingestion: text extraction, chunking, and the embed pipeline.

All functions are fail-open where they touch external services: an embedding
failure marks the document ``failed`` rather than raising, matching the
background-task invariant.
"""

from __future__ import annotations

import io

import docx
from pypdf import PdfReader


def chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split ``text`` into word-boundary chunks of at most ``max_chars``,
    carrying ``overlap_chars`` of trailing words into the next chunk."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        add = len(word) + (1 if current else 0)
        if length + add > max_chars and current:
            chunks.append(" ".join(current))
            overlap: list[str] = []
            olen = 0
            for w in reversed(current):
                wl = len(w) + (1 if overlap else 0)
                if olen + wl > overlap_chars:
                    break
                overlap.insert(0, w)
                olen += wl
            current = overlap
            length = olen
            add = len(word) + (1 if current else 0)
        current.append(word)
        length += add
    if current:
        chunks.append(" ".join(current))
    return chunks


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class UnsupportedFileType(Exception):
    """Raised when an uploaded file's type cannot be extracted."""


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)


def extract_text(filename: str | None, mime_type: str | None, data: bytes) -> str:
    name = (filename or "").lower()
    mime = mime_type or ""
    if name.endswith(".pdf") or mime == "application/pdf":
        return _extract_pdf(data)
    if name.endswith(".docx") or mime == _DOCX_MIME:
        return _extract_docx(data)
    if name.endswith((".md", ".markdown", ".txt")) or mime.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    raise UnsupportedFileType(f"Unsupported file type: {filename or mime_type}")
