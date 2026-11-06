"""Generate the binary UC3 (Product Manager agent) sources.

    python demo-data/logisly/gen_uc3.py

Most of uc3/ is hand-written text checked in as is (meeting notes, chat
export, GitHub snapshot, templates, roster). This script only builds the two
sources that need a real file format:

    uc3/sources/01_request_driver-app-ai-assistant.docx   Ops request doc (as if from Drive)
    uc3/sources/03_email_cs_epod-request.eml              Customer Success email

Several sources disagree on purpose; uc3/expected/consistency_report.md lists
what the agent should catch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from docx import Document
from docx.shared import Pt

from common import ROOT

OUT = ROOT / "uc3" / "sources"
WIB = timezone(timedelta(hours=7))


def request_doc() -> None:
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading("Request: AI Assistant di Driver App", level=1)
    doc.add_paragraph("Dari: Hadi Pranoto (Head of Operations)\nKepada: Nadia Putri (Product)\nTanggal: 30 Oktober 2026")

    doc.add_heading("Latar belakang", level=2)
    doc.add_paragraph(
        "Follow-up driver saat ini lewat agent teks di WhatsApp. Hanya sekitar 20-30% kasus selesai tanpa tim ops: "
        "agent sering salah paham, bertanya berulang, dan tidak bisa membaca voice note atau foto. Biaya WhatsApp naik "
        "sekitar 5 kali sejak tarif per pesan. Telepon ke driver masih manual lewat PBX oleh tim ops."
    )
    doc.add_heading("Yang kami minta", level=2)
    for item in (
        "Driver bisa chat dengan AI assistant langsung di driver app (teks, voice note, foto, video pendek).",
        "AI assistant memahami voice note dan foto, lalu membalas dengan bahasa natural, tidak kaku.",
        "Kalau ada anomali tracking, AI assistant yang menyapa driver duluan di app.",
        "Kalau driver tidak membalas dalam 5 menit, AI assistant menelepon driver otomatis lewat app (fase 1).",
        "Kalau masalah tidak selesai, tiket otomatis ke ops console sudah berisi ringkasan, transkrip dan penjelasan foto.",
        "Bahasa: Indonesia informal, termasuk campuran bahasa Jawa dan Sunda yang biasa dipakai driver.",
    ):
        doc.add_paragraph(item, style="List Number")

    doc.add_heading("Target", level=2)
    t = doc.add_table(rows=1, cols=3)
    t.style = "Table Grid"
    for c, v in zip(t.rows[0].cells, ("Metrik", "Sekarang", "Target")):
        c.text = v
    for row in (
        ("Kasus selesai tanpa ops", "20-30%", "60%"),
        ("Tiket follow-up manual per hari", "sekitar 140", "di bawah 60"),
        ("Biaya WhatsApp per bulan", "naik terus", "turun 50% setelah 3 bulan"),
    ):
        cells = t.add_row().cells
        for c, v in zip(cells, row):
            c.text = v

    doc.add_heading("Jadwal", level=2)
    doc.add_paragraph(
        "Pilot dengan 50 driver di koridor Cikarang - Semarang mulai minggu kedua Januari 2027, "
        "lalu rollout ke semua driver setelah 1 bulan pilot."
    )
    doc.add_heading("Catatan", level=2)
    doc.add_paragraph(
        "Masih banyak driver pakai HP Android lama dan sinyal lemah di jalur Pantura. "
        "Mohon dipertimbangkan mode hemat data."
    )
    doc.save(OUT / "01_request_driver-app-ai-assistant.docx")


def cs_email() -> None:
    msg = EmailMessage()
    msg["From"] = "Sari Indah <sari.indah@logisly.example>"
    msg["To"] = "Nadia Putri <nadia.putri@logisly.example>"
    msg["Subject"] = "Fwd: Permintaan bukti terima digital - PT Sumber Pangan Nusantara"
    msg["Date"] = format_datetime(datetime(2026, 11, 4, 10, 32, tzinfo=WIB))
    msg["Message-ID"] = "<03_email_cs_epod@mock.logisly.example>"
    msg.set_content(
        "Hai Nadia,\n\n"
        "Sesuai product sync kemarin, berikut request dari PT Sumber Pangan Nusantara. Mereka shipper top 10 kita "
        "(sekitar 900 trip per bulan). Tolong dibantu prioritas ya.\n\n"
        "Ringkasan permintaan mereka:\n"
        "1. Foto surat jalan yang sudah ditandatangani penerima muncul di shipper portal per pengiriman.\n"
        "2. Ada nama penerima dan jam terima.\n"
        "3. Bisa download semua bukti terima satu bulan sekaligus untuk audit.\n"
        "4. Mereka butuh ini sebelum 31 Desember 2026 untuk audit tahunan.\n\n"
        "Saya sempat buat issue di GitHub driver-app (Foto bukti terima di app driver), tapi belum ada yang pegang.\n\n"
        "Thanks,\nSari\n\n"
        "---------- Forwarded message ----------\n"
        "From: Lukas Hartono <lukas.hartono@sumberpangan.example>\n"
        "Date: Mon, 2 Nov 2026 16:05\n"
        "Subject: Permintaan bukti terima digital\n\n"
        "Bu Sari,\n\nTim audit kami minta semua bukti terima tahun 2026 tersedia digital. Saat ini bukti terima dari driver "
        "masih dikirim via WhatsApp ke admin kami dan sering tercecer. Apakah Logisly bisa menyediakan bukti terima "
        "(foto dan tanda tangan penerima) di portal, dan bisa diunduh per bulan? Kami butuh sebelum akhir Desember.\n\n"
        "Salam,\nLukas Hartono\nLogistics Manager, PT Sumber Pangan Nusantara\n"
    )
    (OUT / "03_email_cs_epod-request.eml").write_bytes(bytes(msg))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    request_doc()
    cs_email()
    print("wrote", *(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
