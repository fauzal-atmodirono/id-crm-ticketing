import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from logisly_agents.config import get_settings
from logisly_agents.uploads import convert_uploads, part_to_text

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _read(rel: str) -> bytes:
    return (get_settings().data_dir / rel).read_bytes()


XLSX_PATH = "uc2/inbox/attachments/Penawaran_CV_Mitra_Armada_Jaya_Q-MAJ-1.xlsx"


def test_xlsx_keeps_footnote():
    text = part_to_text("Penawaran_CV_Mitra_Armada_Jaya_Q-MAJ-1.xlsx", XLSX, _read(XLSX_PATH))
    assert text.startswith("[Lampiran Penawaran_CV_Mitra_Armada_Jaya_Q-MAJ-1.xlsx]")
    assert "3850000" in text
    assert "*belum termasuk tol" in text


def test_docx_with_table():
    text = part_to_text(
        "01_request_driver-app-ai-assistant.docx",
        "application/octet-stream",
        _read("uc3/sources/01_request_driver-app-ai-assistant.docx"),
    )
    assert "minggu kedua Januari 2027" in text
    assert "Kasus selesai tanpa ops | 20-30% | 60%" in text


def test_eml():
    text = part_to_text(
        "03_email_cs_epod-request.eml", "message/rfc822", _read("uc3/sources/03_email_cs_epod-request.eml")
    )
    assert "31 Desember 2026" in text
    assert "Subject: Fwd: Permintaan bukti terima digital" in text


def test_text_kinds_decoded():
    assert "halo" in part_to_text("a.md", "application/octet-stream", b"halo")


@pytest.mark.parametrize("mime", ["application/pdf", "image/png", "audio/mp3", "video/mp4", "text/plain"])
def test_untouched(mime):
    assert part_to_text("x", mime, b"1") is None


def test_corrupt_xlsx():
    text = part_to_text("bad.xlsx", XLSX, b"not a zip")
    assert text.startswith("[Lampiran")
    assert "tidak dapat dibaca" in text


def test_unknown_kind():
    text = part_to_text("a.zip", "application/zip", b"x")
    assert text.startswith("[Lampiran a.zip tidak dapat dibaca")


def test_truncation():
    text = part_to_text("big.csv", "text/csv", b"a" * 70000)
    assert text.endswith("... (dipotong)")
    assert len(text) < 60100


def test_callback_rewrites_request():
    xlsx_part = types.Part.from_bytes(data=_read(XLSX_PATH), mime_type=XLSX)
    req = LlmRequest(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="cek"), xlsx_part],
            )
        ]
    )
    assert convert_uploads(None, req) is None
    p1, p2 = req.contents[0].parts
    assert p1.text == "cek"
    assert p2.text and "3850000" in p2.text
    assert p2.inline_data is None


def _req(*parts):
    return LlmRequest(contents=[types.Content(role="user", parts=list(parts))])


def test_mime_with_charset_param_untouched():
    assert part_to_text("note.txt", "Text/Plain; charset=utf-8", b"halo") is None


def test_mime_with_charset_param_markdown_converted():
    assert "halo" in part_to_text("a.md", "text/markdown; charset=UTF-8", b"halo")


def test_octet_stream_pdf_by_name_rewrites_mime():
    blob = types.Blob(data=b"%PDF-1.4", mime_type="application/octet-stream", display_name="tender.PDF")
    req = _req(types.Part(inline_data=blob))
    convert_uploads(None, req)
    part = req.contents[0].parts[0]
    assert part.inline_data is not None
    assert part.inline_data.mime_type == "application/pdf"


@pytest.mark.parametrize(
    "name,mime", [("foto.jpg", "image/jpeg"), ("foto.jpeg", "image/jpeg"), ("a.png", "image/png")]
)
def test_empty_mime_image_by_name(name, mime):
    blob = types.Blob(data=b"1", mime_type="", display_name=name)
    req = _req(types.Part(inline_data=blob))
    convert_uploads(None, req)
    assert req.contents[0].parts[0].inline_data.mime_type == mime


def test_octet_stream_xlsx_by_name_converted():
    blob = types.Blob(
        data=_read(XLSX_PATH),
        mime_type="application/octet-stream",
        display_name="Penawaran_CV_Mitra_Armada_Jaya_Q-MAJ-1.xlsx",
    )
    req = _req(types.Part(inline_data=blob))
    convert_uploads(None, req)
    part = req.contents[0].parts[0]
    assert part.inline_data is None
    assert "3850000" in part.text


def test_file_data_docx_becomes_note():
    fd = types.FileData(file_uri="gs://b/brief.docx", mime_type=DOCX, display_name="brief.docx")
    req = _req(types.Part(file_data=fd))
    convert_uploads(None, req)
    part = req.contents[0].parts[0]
    assert part.file_data is None
    note = "[Lampiran brief.docx tidak dapat dibaca langsung; unggah sebagai PDF atau tempel isinya]"
    assert part.text == note


def test_file_data_name_from_uri():
    fd = types.FileData(file_uri="gs://b/dir/brief.xlsx", mime_type="application/octet-stream")
    req = _req(types.Part(file_data=fd))
    convert_uploads(None, req)
    assert "brief.xlsx" in req.contents[0].parts[0].text


@pytest.mark.parametrize("mime", ["application/pdf", "image/png", "text/plain; charset=utf-8"])
def test_file_data_native_untouched(mime):
    fd = types.FileData(file_uri="gs://b/x", mime_type=mime, display_name="x")
    req = _req(types.Part(file_data=fd))
    convert_uploads(None, req)
    assert req.contents[0].parts[0].file_data is fd


def test_file_data_octet_stream_pdf_by_name_kept():
    fd = types.FileData(file_uri="gs://b/t.pdf", mime_type="application/octet-stream", display_name="t.pdf")
    req = _req(types.Part(file_data=fd))
    convert_uploads(None, req)
    part = req.contents[0].parts[0]
    assert part.file_data is not None
    assert part.file_data.mime_type == "application/pdf"


def test_error_note_without_message_uses_type_name(monkeypatch):
    import logisly_agents.uploads as up

    def boom(data):
        raise ValueError()

    monkeypatch.setattr(up, "_xlsx", boom)
    assert part_to_text("a.xlsx", XLSX, b"x") == "[Lampiran a.xlsx tidak dapat dibaca: ValueError]"
