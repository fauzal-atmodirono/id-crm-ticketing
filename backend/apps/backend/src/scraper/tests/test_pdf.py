from __future__ import annotations

import io

from pypdf import PdfWriter

from scraper.pdf import extract_pdf_text


def _blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_pdf_text_handles_valid_pdf() -> None:
    # A blank page has no text but must not raise; returns a string.
    result = extract_pdf_text(_blank_pdf_bytes())
    assert isinstance(result, str)


def test_extract_pdf_text_returns_empty_on_garbage() -> None:
    assert extract_pdf_text(b"not a pdf") == ""
