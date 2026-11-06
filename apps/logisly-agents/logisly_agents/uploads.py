"""Convert office and email uploads to text so Gemini can read them.

Gemini accepts PDF, images, audio, video and plain text as inline data. xlsx, docx and eml
uploads would be rejected, so the before_model_callback swaps them for text parts.
"""

from __future__ import annotations

import io
from email import policy
from email.parser import BytesParser

from google.genai import types

MAX_CHARS = 60_000
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TEXT_EXT = (".md", ".txt", ".csv")


def _ext(name: str | None) -> str:
    return "." + name.rsplit(".", 1)[-1].lower() if name and "." in name else ""


def _xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True)
    out: list[str] = []
    for ws in wb.worksheets:
        out.append(f"## {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c) for c in row]
            if any(c.strip() for c in cells):
                out.append(" | ".join(cells).rstrip(" |"))
    return "\n".join(out)


def _docx(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    out: list[str] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, doc).text
            if text.strip():
                out.append(text)
        elif tag == "tbl":
            for row in Table(child, doc).rows:
                out.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(out)


def _eml(data: bytes) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    lines = [f"{h}: {msg[h]}" for h in ("From", "To", "Subject", "Date") if msg[h]]
    body = msg.get_body(preferencelist=("plain",))
    lines += ["", body.get_content().strip() if body else ""]
    names = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    if names:
        lines += ["", "Lampiran email: " + ", ".join(names)]
    return "\n".join(lines)


def part_to_text(name: str | None, mime: str, data: bytes) -> str | None:
    """Return text for an upload Gemini cannot take natively, or None to leave it unchanged."""
    mime = (mime or "").lower()
    ext = _ext(name)
    if mime == "application/pdf" or mime.startswith(("image/", "audio/", "video/")) or mime == "text/plain":
        return None
    label = name or "tanpa nama"
    try:
        if mime == XLSX_MIME or ext == ".xlsx":
            body = _xlsx(data)
        elif mime == DOCX_MIME or ext == ".docx":
            body = _docx(data)
        elif mime == "message/rfc822" or ext == ".eml":
            body = _eml(data)
        elif mime in ("text/markdown", "text/csv") or ext in _TEXT_EXT:
            body = data.decode("utf-8")
        else:
            kind = mime or ext or "tidak dikenal"
            return f"[Lampiran {label} tidak dapat dibaca: format {kind} tidak didukung]"
    except Exception as exc:  # noqa: BLE001 - any parser failure becomes a readable note
        return f"[Lampiran {label} tidak dapat dibaca: {exc}]"
    text = f"[Lampiran {label}]\n{body}"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n... (dipotong)"
    return text


def convert_uploads(callback_context, llm_request) -> None:
    """ADK before_model_callback: replace unsupported inline_data parts with text parts."""
    for content in llm_request.contents or []:
        for i, part in enumerate(content.parts or []):
            blob = part.inline_data
            if blob is None or blob.data is None:
                continue
            text = part_to_text(blob.display_name, blob.mime_type or "", blob.data)
            if text is not None:
                content.parts[i] = types.Part.from_text(text=text)
    return None
