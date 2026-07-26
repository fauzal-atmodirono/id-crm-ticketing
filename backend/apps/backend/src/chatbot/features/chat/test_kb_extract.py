import io

import pytest
from reportlab.pdfgen import canvas

from chatbot.features.chat.kb_ingest import UnsupportedFileType, extract_text


def _make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def test_extract_plain_text() -> None:
    assert extract_text("notes.txt", "text/plain", b"hello world") == "hello world"


def test_extract_markdown() -> None:
    assert "Heading" in extract_text("doc.md", None, b"# Heading\nbody")


def test_extract_pdf() -> None:
    out = extract_text("brochure.pdf", "application/pdf", _make_pdf("PriceRM50000"))
    assert "PriceRM50000" in out


def test_unsupported_type_raises() -> None:
    with pytest.raises(UnsupportedFileType):
        extract_text("image.png", "image/png", b"\x89PNG")
