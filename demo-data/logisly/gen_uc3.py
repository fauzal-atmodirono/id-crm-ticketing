"""Generate the binary UC3 (Product Manager agent) sources.

    python demo-data/logisly/gen_uc3.py

Most of uc3/ is hand-written text checked in as is (meeting notes, chat
export, Jira snapshot, templates, roster). This script only builds the two
sources that need a real file format:

    uc3/sources/01_request_milestone-follow-up.docx   Ops request doc (as if from Drive)
    uc3/sources/03_email_cs_unloading-update.eml      Customer Success email

The topic is UC1 (milestone checklists for the driver follow-up agent). Several
sources disagree on purpose; uc3/expected/consistency_report.md lists what the
agent should catch.
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
    doc.add_heading("Request: Follow-up driver per milestone", level=1)
    doc.add_paragraph("Dari: Hadi Pranoto (Head of Operations)\nKepada: Nadia Putri (Product)\nTanggal: 5 November 2026")

    doc.add_heading("Latar belakang", level=2)
    doc.add_paragraph(
        "Follow-up driver sekarang lewat bot WhatsApp yang menjalankan checklist SOP sebagai skrip. Bot menanyakan "
        "hal yang sebenarnya sudah ada di sistem (plat, titik muat, status driver app), mengabaikan foto driver, "
        "dan bertanya berulang sepanjang malam. Hasilnya driver kesal, tim CT dan Allocation harus follow-up manual, "
        "dan biaya WhatsApp terus naik."
    )
    doc.add_heading("Yang kami minta", level=2)
    for item in (
        "Checklist follow-up per milestone order: assigned, menuju muat, di lokasi muat, dalam perjalanan, di lokasi bongkar, selesai bongkar.",
        "Data yang sudah ada di sistem diisi otomatis. Driver hanya ditanya hal yang tidak diketahui sistem.",
        "Foto dari driver (foto unit, surat jalan, POD) dibaca sebagai bukti, tidak diabaikan.",
        "Item yang tidak OK langsung jadi tiket ke tim yang tepat (Allocation, CT, MM, Warehouse).",
        "Jangan kirim pesan ke driver pada jam istirahat malam: pesan ditahan antara 21:00–06:00 WIB.",
        "Operator bisa mengubah milestone manual dari console, dan melihat checklist tiap trip.",
    ):
        doc.add_paragraph(item, style="List Number")

    doc.add_heading("Target", level=2)
    t = doc.add_table(rows=1, cols=3)
    t.style = "Table Grid"
    for c, v in zip(t.rows[0].cells, ("Metrik", "Sekarang", "Target")):
        c.text = v
    for row in (
        ("Pertanyaan ke driver per trip", "18", "6"),
        ("Kasus terlambat terdeteksi sebelum muat", "40%", "80%"),
    ):
        cells = t.add_row().cells
        for c, v in zip(cells, row):
            c.text = v

    doc.add_heading("Jadwal", level=2)
    doc.add_paragraph(
        "Pilot live 4 Desember 2026 untuk shipper FMCG, lalu diperluas ke shipper lain "
        "setelah dua minggu pilot."
    )
    doc.add_heading("Catatan", level=2)
    doc.add_paragraph(
        "SOP Shopee punya item tambahan (ID driver Shopee, status onsite di aplikasi Shopee). "
        "Mohon dipertimbangkan, tapi FMCG dulu."
    )
    doc.save(OUT / "01_request_milestone-follow-up.docx")


def cs_email() -> None:
    msg = EmailMessage()
    msg["From"] = "Sari Indah <sari.indah@logisly.example>"
    msg["To"] = "Nadia Putri <nadia.putri@logisly.example>"
    msg["Subject"] = "Fwd: Update bongkar dan bukti terima terlambat - PT Sinar Pangan Nusantara"
    msg["Date"] = format_datetime(datetime(2026, 11, 9, 10, 32, tzinfo=WIB))
    msg["Message-ID"] = "<03_email_cs_unloading@mock.logisly.example>"
    msg.set_content(
        "Hai Nadia,\n\n"
        "Berikut komplain dari PT Sinar Pangan Nusantara (shipper FMCG, sekitar 600 trip per bulan). "
        "Tolong dibantu prioritas ya.\n\n"
        "Ringkasan:\n"
        "1. Update selesai bongkar dan bukti terima (POD) sering terlambat, kadang baru masuk besok harinya.\n"
        "2. Tim gudang mereka tidak tahu kapan truk sudah selesai bongkar.\n"
        "3. Mereka minta update bongkar dan POD rutin diterima paling lambat 31 Desember 2026, "
        "karena ada audit akhir tahun.\n\n"
        "Saya lihat sudah ada issue LOG-3412 dan LOG-3417 soal upload foto POD di driver app, tapi belum ada yang pegang.\n\n"
        "Thanks,\nSari\n\n"
        "---------- Forwarded message ----------\n"
        "From: Lukas Hartono <lukas.hartono@sinarpangan.example>\n"
        "Date: Fri, 6 Nov 2026 16:05\n"
        "Subject: Update bongkar dan bukti terima terlambat\n\n"
        "Bu Sari,\n\nBeberapa minggu ini kami sering baru tahu truk sudah bongkar setelah menelepon driver sendiri. "
        "Bukti terima juga datang terlambat. Mohon ada update bongkar dan POD yang lebih tepat waktu, "
        "paling lambat 31 Desember 2026 untuk keperluan audit kami.\n\n"
        "Salam,\nLukas Hartono\nLogistics Manager, PT Sinar Pangan Nusantara\n"
    )
    (OUT / "03_email_cs_unloading-update.eml").write_bytes(bytes(msg))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    request_doc()
    cs_email()
    print("wrote", *(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
