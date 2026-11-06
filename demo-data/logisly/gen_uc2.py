"""Generate the UC2 (pricing & quotation filtering) mock data.

    python demo-data/logisly/gen_uc2.py

Writes demo-data/logisly/uc2/:

    pricing/pricing.db               SQLite stand-in for Logisly's SQL Server pricing DB
    pricing/schema_sqlserver.sql     the same schema in T-SQL, for the real adapter
    pricing/*.csv                    every table as CSV
    business_rules.yaml              thresholds the agent applies
    inbox/*.eml                      purchasing mailbox: tender, quotations, noise
    inbox/attachments/               the same attachments, unpacked
    sent/RFQ-LGS-2611-007.pdf        Logisly's RFQ to transporters
    expected/recommendation.json     ground truth the agent is scored against
    expected/recommendation.md       the same, as the summary a buyer should get

Scenario: shipper PT Nusantara Agro Lestari invites Logisly to tender four
lanes for Dec 2026 - Feb 2027. Logisly purchasing sends RFQ-LGS-2611-007 to
its transporters and receives six quotations (one revised) plus two emails
that are not quotations. Each quotation carries one planted problem so the
demo can show the agent catching it.
"""

from __future__ import annotations

import csv
import json
import random
import sqlite3
from datetime import date
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path

import openpyxl
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from openpyxl.styles import Alignment, Font, PatternFill
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common import (
    LANE,
    LANES,
    PERIODS,
    ROOT,
    SEED,
    TRANSPORTER,
    TRANSPORTERS,
    TRUCK,
    TRUCK_TYPES,
    customers,
    docx_to_pdf,
    idr,
    market_rate,
    median,
    round_rate,
)

OUT = ROOT / "uc2"
WIB = timezone(timedelta(hours=7))
PURCHASING = "purchasing@logisly.example"

TENDER_ID = "TND-NAL-2026-11"
RFQ_ID = "RFQ-LGS-2611-007"
CUSTOMER = "PT Nusantara Agro Lestari"
# tender lane key -> (lane, truck, trips per month)
TENDER = {
    "T1": ("LN-02", "FUSO_WB", 40),
    "T2": ("LN-01", "CDD_BOX", 60),
    "T3": ("LN-10", "TRONTON_WB", 20),
    "T4": ("LN-04", "CDE_BOX", 80),
}
BASELINE_PERIODS = ("2026-08", "2026-09", "2026-10")  # the 90-day baseline

RULES_YAML = """\
# Business rules the UC2 agent applies. Illustrative values for the demo:
# Logisly's real thresholds are an open item (MOM 29 Oct, data list UC2).
currency: IDR
price_unit: per_trip           # quotes in another unit are normalised to per trip
baseline:
  source: rate_history         # agreed buy rates, pricing DB
  window_months: 3             # Aug-Oct 2026 for this tender
  statistic: median            # across transporters, same lane and truck type
flags:
  above_baseline_pct: 10       # quote more than 10% above baseline -> flag "mahal"
  below_baseline_pct: 15       # more than 15% below -> flag "cek biaya tersembunyi"
  min_on_time_pct: 90          # last 4 quarters, transporter_performance
  max_claim_pct: 1.5
  min_payment_term_days: 30    # Logisly pays transporters no sooner than this
  min_validity_days: 21        # validity must run at least 21 days past the quote date
required_fields:               # a quotation missing any of these is incomplete
  - transporter
  - lane
  - truck_type
  - price
  - price_basis                # what is included: toll, ferry, insurance
  - payment_term
  - validity
tender_requirements:           # from the shipper's tender document
  truck_type_must_match: true
  cargo_insurance_min_idr: 500000000
  gps_tracking: required
scoring:                       # among quotes with no blocking issue
  price: 0.5
  on_time: 0.3
  compliance: 0.2
blocking_issues:               # these exclude a quote until resolved
  - expired_validity
  - truck_type_mismatch
  - incomplete
margin:
  basis: sell_price            # gross margin = (sell - buy) / sell
  min_pct: 10
  target_pct: 15
decision: human                # the agent recommends; purchasing approves
"""


# --- pricing DB -------------------------------------------------------------

def build_pricing(rng: random.Random) -> dict[str, list[dict]]:
    tender_pairs = {(lane, truck) for lane, truck, _ in TENDER.values()}
    rate_rows: list[dict] = []
    rid = 1
    for lane in LANES:
        served = [t for t in TRANSPORTERS if lane.region in t.regions]
        for t in served:
            for code in lane.trucks:
                forced = (lane.id, code) in tender_pairs
                if not forced and rng.random() > 0.75:
                    continue
                for period in PERIODS:
                    if not (forced and period in BASELINE_PERIODS) and rng.random() > 0.85:
                        continue
                    rate = market_rate(lane, TRUCK[code], period) * t.factor * rng.gauss(1, 0.025)
                    rate_rows.append(
                        {
                            "rate_id": rid,
                            "transporter_id": t.id,
                            "lane_id": lane.id,
                            "truck_type": code,
                            "period": period,
                            "rate_idr": round_rate(rate),
                            "trips": rng.randint(2, 45),
                            "source": rng.choice(["PO", "PO", "PO", "quotation"]),
                        }
                    )
                    rid += 1

    custs = customers()
    contract_rows: list[dict] = []
    cid = 1
    # The tender customer already ships LN-01 CDD with Logisly; the contract ends 30 Nov.
    nal = next(c for c in custs if c["name"] == CUSTOMER)
    contract_rows.append(
        {
            "contract_rate_id": cid,
            "customer_id": nal["customer_id"],
            "lane_id": "LN-01",
            "truck_type": "CDD_BOX",
            "sell_rate_idr": round_rate(market_rate(LANE["LN-01"], TRUCK["CDD_BOX"], "2025-12") * 1.16),
            "valid_from": "2025-12-01",
            "valid_to": "2026-11-30",
        }
    )
    cid += 1
    for c in rng.sample(custs[4:], 180):
        for lane in rng.sample(LANES, rng.randint(1, 4)):
            code = rng.choice(lane.trucks)
            start = rng.choice(["2026-01-01", "2026-04-01", "2026-07-01"])
            end = {"2026-01-01": "2026-12-31", "2026-04-01": "2027-03-31", "2026-07-01": "2027-06-30"}[start]
            contract_rows.append(
                {
                    "contract_rate_id": cid,
                    "customer_id": c["customer_id"],
                    "lane_id": lane.id,
                    "truck_type": code,
                    "sell_rate_idr": round_rate(
                        market_rate(lane, TRUCK[code], start[:7]) * rng.uniform(1.12, 1.24)
                    ),
                    "valid_from": start,
                    "valid_to": end,
                }
            )
            cid += 1

    perf_rows: list[dict] = []
    for t in TRANSPORTERS:
        for q in ("2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3"):
            dip = -2.5 if q == "2026-Q1" else 0.0  # Lebaran congestion
            perf_rows.append(
                {
                    "transporter_id": t.id,
                    "quarter": q,
                    "trips": rng.randint(60, 420),
                    "on_time_pct": round(min(99.5, t.on_time + dip + rng.uniform(-1.2, 1.2)), 1),
                    "claim_pct": round(max(0.0, t.claim + rng.uniform(-0.2, 0.2)), 2),
                    "acceptance_pct": round(rng.uniform(78, 98), 1),
                }
            )

    return {
        "customers": custs,
        "transporters": [
            {"transporter_id": t.id, "name": t.name, "city": t.city, "email": t.email, "contact_person": t.contact, "regions": ",".join(t.regions)}
            for t in TRANSPORTERS
        ],
        "truck_types": [
            {"truck_type": t.code, "name": t.name, "capacity_ton": t.capacity_ton, "toll_factor": t.toll_factor} for t in TRUCK_TYPES
        ],
        "lanes": [
            {"lane_id": lane.id, "origin": lane.origin, "destination": lane.destination, "distance_km": lane.km, "ferry": int(lane.ferry), "base_toll_idr": lane.toll}
            for lane in LANES
        ],
        "rate_history": rate_rows,
        "customer_contract_rates": contract_rows,
        "transporter_performance": perf_rows,
    }


SQLSERVER_DDL = """\
-- Schema of the demo pricing DB, written for SQL Server (Logisly's pricing
-- store per the 29 Oct MOM). The demo ships the same tables in SQLite
-- (pricing.db); a real adapter maps Logisly's actual tables onto these.
CREATE TABLE customers (
    customer_id        VARCHAR(12)   NOT NULL PRIMARY KEY,
    name               NVARCHAR(200) NOT NULL,
    segment            NVARCHAR(60)  NOT NULL,
    city               NVARCHAR(80)  NOT NULL,
    customer_since     DATE          NOT NULL,
    payment_term_days  INT           NOT NULL,
    status             VARCHAR(10)   NOT NULL
);
CREATE TABLE transporters (
    transporter_id  VARCHAR(10)   NOT NULL PRIMARY KEY,
    name            NVARCHAR(200) NOT NULL,
    city            NVARCHAR(80)  NOT NULL,
    email           VARCHAR(200)  NOT NULL,
    contact_person  NVARCHAR(120) NOT NULL,
    regions         VARCHAR(100)  NOT NULL
);
CREATE TABLE truck_types (
    truck_type    VARCHAR(12)   NOT NULL PRIMARY KEY,
    name          NVARCHAR(60)  NOT NULL,
    capacity_ton  DECIMAL(5,1)  NOT NULL,
    toll_factor   DECIMAL(4,2)  NOT NULL  -- multiplier on lanes.base_toll_idr (class I)
);
CREATE TABLE lanes (
    lane_id      VARCHAR(8)    NOT NULL PRIMARY KEY,
    origin       NVARCHAR(80)  NOT NULL,
    destination  NVARCHAR(80)  NOT NULL,
    distance_km  INT           NOT NULL,
    ferry        BIT           NOT NULL,
    base_toll_idr  BIGINT      NOT NULL  -- class I toll per trip
);
-- Agreed buy rates from transporters, one row per transporter, lane, truck and month.
CREATE TABLE rate_history (
    rate_id         INT          NOT NULL PRIMARY KEY,
    transporter_id  VARCHAR(10)  NOT NULL REFERENCES transporters(transporter_id),
    lane_id         VARCHAR(8)   NOT NULL REFERENCES lanes(lane_id),
    truck_type      VARCHAR(12)  NOT NULL REFERENCES truck_types(truck_type),
    period          CHAR(7)      NOT NULL,  -- YYYY-MM
    rate_idr        BIGINT       NOT NULL,  -- per trip, incl. toll and ferry
    trips           INT          NOT NULL,
    source          VARCHAR(12)  NOT NULL   -- PO | quotation
);
-- Sell rates agreed with shippers.
CREATE TABLE customer_contract_rates (
    contract_rate_id  INT          NOT NULL PRIMARY KEY,
    customer_id       VARCHAR(12)  NOT NULL REFERENCES customers(customer_id),
    lane_id           VARCHAR(8)   NOT NULL REFERENCES lanes(lane_id),
    truck_type        VARCHAR(12)  NOT NULL REFERENCES truck_types(truck_type),
    sell_rate_idr     BIGINT       NOT NULL,
    valid_from        DATE         NOT NULL,
    valid_to          DATE         NOT NULL
);
CREATE TABLE transporter_performance (
    transporter_id  VARCHAR(10)   NOT NULL REFERENCES transporters(transporter_id),
    quarter         CHAR(7)       NOT NULL,  -- YYYY-Qn
    trips           INT           NOT NULL,
    on_time_pct     DECIMAL(4,1)  NOT NULL,
    claim_pct       DECIMAL(4,2)  NOT NULL,
    acceptance_pct  DECIMAL(4,1)  NOT NULL,
    PRIMARY KEY (transporter_id, quarter)
);
"""


def write_pricing(tables: dict[str, list[dict]]) -> None:
    d = OUT / "pricing"
    d.mkdir(parents=True, exist_ok=True)
    (d / "schema_sqlserver.sql").write_text(SQLSERVER_DDL)
    db = d / "pricing.db"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    for name, rows in tables.items():
        cols = list(rows[0])
        con.execute(f"CREATE TABLE {name} ({', '.join(cols)})")
        con.executemany(
            f"INSERT INTO {name} VALUES ({', '.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in rows],
        )
        with (d / f"{name}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    con.commit()
    con.close()


def baselines(rate_rows: list[dict]) -> dict[str, float]:
    out = {}
    for key, (lane, truck, _) in TENDER.items():
        vals = [r["rate_idr"] for r in rate_rows if r["lane_id"] == lane and r["truck_type"] == truck and r["period"] in BASELINE_PERIODS]
        out[key] = median(vals)
    return out


def on_time(perf: list[dict], tid: str) -> float:
    vals = [r["on_time_pct"] for r in perf if r["transporter_id"] == tid]
    return round(sum(vals) / len(vals), 1)


# --- quotations -------------------------------------------------------------

# Each quote: transporter, version, date, lanes {key: multiplier vs baseline}, terms, the planted issue.
QUOTES = [
    {"id": "Q-TCL-1", "trp": "TRP-001", "date": "2026-11-03", "fmt": "pdf", "lanes": {"T1": 0.985, "T2": 0.99, "T3": 1.04, "T4": 1.00},
     "payment": 30, "valid_to": "2026-12-31", "basis": "Termasuk tol, penyeberangan dan asuransi muatan s.d. Rp 500 juta per trip", "superseded_by": "Q-TCL-2"},
    {"id": "Q-MAJ-1", "trp": "TRP-002", "date": "2026-11-03", "fmt": "xlsx", "lanes": {"T1": 0.94, "T2": 0.73, "T4": 0.90},
     "payment": 14, "valid_to": "2026-11-30", "basis": "Termasuk tol dan asuransi s.d. Rp 500 juta. *Rute Cikarang - Semarang belum termasuk tol.", "toll_excluded": ["T2"]},
    {"id": "Q-STI-1", "trp": "TRP-003", "date": "2026-11-04", "fmt": "body", "lanes": {"T1": 1.01, "T2": 1.00, "T3": 0.99, "T4": 0.98},
     "payment": None, "valid_to": None, "basis": "Harga sudah all-in (tol, penyeberangan, asuransi)"},
    {"id": "Q-LJE-1", "trp": "TRP-004", "date": "2026-11-04", "fmt": "scan", "lanes": {"T1": 1.12, "T2": 1.06},
     "payment": 30, "valid_to": "2026-12-15", "basis": "Termasuk tol dan asuransi muatan s.d. Rp 1 miliar per trip"},
    {"id": "Q-GAS-1", "trp": "TRP-005", "date": "2026-10-28", "fmt": "pdf", "lanes": {"T3": 0.93},
     "payment": 30, "valid_to": "2026-10-31", "basis": "Harga per ton, termasuk tol dan penyeberangan Merak - Bakauheni; asuransi s.d. Rp 500 juta", "per_ton": ["T3"]},
    {"id": "Q-PFN-1", "trp": "TRP-006", "date": "2026-11-05", "fmt": "xlsx", "lanes": {"T3": 0.80, "T4": 0.94},
     "payment": 45, "valid_to": "2026-12-31", "basis": "Termasuk tol, penyeberangan dan asuransi s.d. Rp 500 juta", "truck_override": {"T3": "FUSO_WB"}},
    {"id": "Q-TCL-2", "trp": "TRP-001", "date": "2026-11-05", "fmt": "pdf", "lanes": {"T1": 0.955, "T2": 0.99, "T3": 1.04, "T4": 1.00},
     "payment": 30, "valid_to": "2026-12-31", "basis": "Termasuk tol, penyeberangan dan asuransi muatan s.d. Rp 500 juta per trip", "revision_of": "Q-TCL-1"},
]


def quote_lines(q: dict, base: dict[str, float]) -> list[dict]:
    lines = []
    for key, mult in q["lanes"].items():
        lane_id, truck, trips = TENDER[key]
        truck = q.get("truck_override", {}).get(key, truck)
        price = round_rate(base[key] * mult)
        line = {"key": key, "lane_id": lane_id, "truck_type": truck, "trips": trips, "price": price, "unit": "trip"}
        if key in q.get("per_ton", []):
            line["unit"] = "ton"
            line["price"] = int(round(base[key] * mult / TRUCK[truck].capacity_ton / 1000) * 1000)
        lines.append(line)
    return lines


def _lane_label(lane_id: str) -> str:
    lane = LANE[lane_id]
    return f"{lane.origin} - {lane.destination}"


def _doc_quote(q: dict, lines: list[dict], path: Path) -> Path:
    t = TRANSPORTER[q["trp"]]
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(10)
    head = doc.add_paragraph()
    run = head.add_run(t.name.upper())
    run.bold = True
    run.font.size = Pt(15)
    run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x68)
    doc.add_paragraph(f"Jasa Angkutan Darat & Pergudangan · {t.city} · {t.email}")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("SURAT PENAWARAN HARGA" + (" (REVISI)" if q.get("revision_of") else ""))
    r.bold = True
    r.font.size = Pt(13)
    d = date.fromisoformat(q["date"])
    no = f"{q['id'].replace('Q-', 'SPH/')}/{d.strftime('%m/%Y')}"
    doc.add_paragraph(f"Nomor: {no}\nTanggal: {d.day} {_bulan(d.month)} {d.year}")
    doc.add_paragraph(
        "Kepada Yth.\nTim Purchasing PT Logisly Teknologi Indonesia\n"
        f"Perihal: Penawaran harga angkutan untuk {RFQ_ID}"
    )
    if q.get("revision_of"):
        doc.add_paragraph("Dengan hormat, bersama ini kami sampaikan revisi penawaran yang menggantikan penawaran kami tanggal 3 November 2026.")
    else:
        doc.add_paragraph("Dengan hormat, menindaklanjuti permintaan penawaran dari Bapak/Ibu, berikut penawaran harga kami:")
    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ["No", "Rute", "Jenis Armada", "Estimasi Volume", "Harga"]):
        cell.text = text
        cell.paragraphs[0].runs[0].bold = True
    for i, line in enumerate(lines, start=1):
        cells = table.add_row().cells
        cells[0].text = str(i)
        cells[1].text = _lane_label(line["lane_id"])
        cells[2].text = TRUCK[line["truck_type"]].name
        cells[3].text = f"{line['trips']} trip/bulan"
        cells[4].text = f"{idr(line['price'])} / {line['unit']}"
    doc.add_paragraph()
    doc.add_paragraph("Syarat dan ketentuan:")
    terms = [q["basis"], "Armada dilengkapi GPS tracking real-time."]
    if q["payment"]:
        terms.append(f"Pembayaran {q['payment']} hari setelah invoice dan POD diterima.")
    if q["valid_to"]:
        v = date.fromisoformat(q["valid_to"])
        terms.append(f"Penawaran berlaku s.d. {v.day} {_bulan(v.month)} {v.year}.")
    for term in terms:
        doc.add_paragraph(term, style="List Bullet")
    doc.add_paragraph("\nHormat kami,\n\n\n" + t.contact + "\nSales & Marketing, " + t.name)
    docx = path.with_suffix(".docx")
    doc.save(docx)
    pdf = docx_to_pdf(docx, path.parent)
    docx.unlink()
    return pdf


def _xlsx_quote(q: dict, lines: list[dict], path: Path) -> Path:
    t = TRANSPORTER[q["trp"]]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Penawaran"
    ws["A1"] = t.name
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Penawaran harga untuk {RFQ_ID} · tanggal {q['date']}"
    hdr = ["No", "Asal", "Tujuan", "Armada", "Volume/bulan", "Harga per trip (IDR)", "Keterangan"]
    ws.append([])
    ws.append(hdr)
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2F5597")
        c.alignment = Alignment(horizontal="center")
    for i, line in enumerate(lines, start=1):
        lane = LANE[line["lane_id"]]
        note = "*belum termasuk tol" if line["key"] in q.get("toll_excluded", []) else ""
        ws.append([i, lane.origin, lane.destination, TRUCK[line["truck_type"]].name, line["trips"], line["price"], note])
        ws.cell(ws.max_row, 6).number_format = "#,##0"
    ws.append([])
    ws.append(["Catatan:"])
    ws.append([q["basis"]])
    ws.append([f"Termin pembayaran: {q['payment']} hari"])
    ws.append([f"Berlaku sampai: {q['valid_to']}"])
    ws.append([f"Kontak: {t.contact} ({t.email})"])
    for col, width in zip("ABCDEFG", (5, 24, 20, 18, 14, 22, 24)):
        ws.column_dimensions[col].width = width
    out = path.with_suffix(".xlsx")
    wb.save(out)
    return out


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("/System/Library/Fonts/Supplemental/Courier New.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scan_quote(q: dict, lines: list[dict], path: Path, rng: random.Random) -> Path:
    """A photographed/scanned paper quote: an image-only PDF with no text layer."""
    t = TRANSPORTER[q["trp"]]
    img = Image.new("L", (1240, 1754), 250)
    dr = ImageDraw.Draw(img)
    y = 90
    dr.text((90, y), t.name.upper(), font=_font(44), fill=20)
    y += 60
    dr.text((90, y), f"Jl. Kaligawe Raya No. 88, {t.city} · {t.email}", font=_font(22), fill=40)
    y += 40
    dr.line((90, y, 1150, y), fill=30, width=3)
    y += 60
    dr.text((380, y), "SURAT PENAWARAN HARGA", font=_font(36), fill=10)
    y += 80
    for text in (f"No: SPH/LJE/{q['date'][5:7]}/2026/041", "Tanggal: 4 November 2026", "Kepada: Purchasing PT Logisly Teknologi Indonesia", f"Perihal: Penawaran {RFQ_ID}"):
        dr.text((90, y), text, font=_font(26), fill=25)
        y += 42
    y += 30
    cols = (90, 160, 620, 860, 1150)
    dr.rectangle((cols[0], y, cols[-1], y + 50), outline=20, width=2)
    for x, head in zip(cols, ("No", "Rute", "Armada", "Harga/trip")):
        dr.text((x + 12, y + 12), head, font=_font(24), fill=10)
    y += 50
    for i, line in enumerate(lines, start=1):
        dr.rectangle((cols[0], y, cols[-1], y + 56), outline=20, width=2)
        vals = (str(i), _lane_label(line["lane_id"]).replace(", Bekasi", ""), TRUCK[line["truck_type"]].name, idr(line["price"]))
        for x, v in zip(cols, vals):
            dr.text((x + 12, y + 14), v, font=_font(24), fill=15)
        y += 56
    y += 50
    v = date.fromisoformat(q["valid_to"])
    for text in ("Ketentuan:", "- " + q["basis"], f"- Pembayaran {q['payment']} hari setelah invoice", f"- Berlaku s.d. {v.day} {_bulan(v.month)} {v.year}"):
        dr.text((90, y), text, font=_font(24), fill=25)
        y += 40
    y += 90
    dr.text((800, y), "Hormat kami,", font=_font(24), fill=25)
    dr.ellipse((780, y + 40, 1010, y + 170), outline=90, width=4)  # company stamp
    dr.text((815, y + 92), "LINTAS JAWA", font=_font(24), fill=90)
    dr.text((800, y + 200), t.contact, font=_font(24), fill=25)
    img = img.rotate(rng.uniform(-1.4, 1.4), fillcolor=235, expand=False)
    noise = Image.effect_noise(img.size, 18)
    img = Image.blend(img, noise, 0.08).filter(ImageFilter.GaussianBlur(0.6))
    out = path.with_suffix(".pdf")
    img.convert("RGB").save(out, "PDF", resolution=150)
    return out


def _bulan(m: int) -> str:
    return ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"][m - 1]


def _body_quote(q: dict, lines: list[dict]) -> str:
    t = TRANSPORTER[q["trp"]]
    rows = "\n".join(
        f"{i}. {_lane_label(line['lane_id'])} | {TRUCK[line['truck_type']].name} | {idr(line['price'])}/trip"
        for i, line in enumerate(lines, start=1)
    )
    return (
        "Selamat siang Bapak/Ibu Tim Purchasing Logisly,\n\n"
        f"Terima kasih atas undangan {RFQ_ID}. Berikut penawaran kami:\n\n{rows}\n\n"
        f"{q['basis']}. Armada kami semua sudah terpasang GPS.\n\n"
        "Kalau ada pertanyaan silakan hubungi saya.\n\n"
        f"Salam,\n{t.contact}\n{t.name}\n"
    )


# --- tender and RFQ documents ------------------------------------------------

def tender_pdf(path: Path) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_heading(f"{CUSTOMER}", level=1)
    doc.add_paragraph("Divisi Supply Chain · Kawasan Industri Jababeka, Cikarang, Bekasi")
    doc.add_heading("UNDANGAN TENDER JASA TRANSPORTASI DARAT", level=2)
    doc.add_paragraph(f"Nomor: {TENDER_ID}\nTanggal: 2 November 2026")
    doc.add_paragraph(
        "Kami mengundang PT Logisly Teknologi Indonesia untuk mengajukan penawaran jasa angkutan "
        "produk pupuk dan benih kemasan untuk periode 1 Desember 2026 s.d. 28 Februari 2027."
    )
    doc.add_heading("1. Lingkup rute", level=3)
    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ["Kode", "Rute", "Armada", "Volume/bulan", "Muatan"]):
        cell.text = text
    cargo = {"T1": "Pupuk kemasan 50 kg", "T2": "Benih kemasan", "T3": "Pupuk kemasan 50 kg", "T4": "Benih & sarana tani"}
    for key, (lane_id, truck, trips) in TENDER.items():
        cells = table.add_row().cells
        for c, v in zip(cells, [key, _lane_label(lane_id), TRUCK[truck].name, f"{trips} trip", cargo[key]]):
            c.text = v
    doc.add_heading("2. Persyaratan", level=3)
    for req in (
        "Jenis armada wajib sesuai tabel di atas; substitusi armada tidak diterima.",
        "Asuransi muatan minimal Rp 500.000.000 per trip.",
        "GPS tracking real-time dan akses tracking untuk tim kami.",
        "Usia armada maksimal 7 tahun; pengemudi memiliki SIM B1/B2 umum yang masih berlaku.",
        "Harga per trip, sudah termasuk tol, penyeberangan dan biaya bongkar muat.",
        "Termin pembayaran kami: 45 hari setelah invoice dan e-POD lengkap.",
    ):
        doc.add_paragraph(req, style="List Bullet")
    doc.add_heading("3. Kriteria penilaian", level=3)
    for c in ("Harga: 50%", "Ketepatan waktu historis: 30%", "Kepatuhan persyaratan dan kelengkapan dokumen: 20%"):
        doc.add_paragraph(c, style="List Bullet")
    doc.add_heading("4. Jadwal", level=3)
    doc.add_paragraph("Batas pengajuan penawaran: Jumat, 13 November 2026 pukul 17.00 WIB, melalui email procurement@nusantaraagro.example.")
    doc.add_paragraph("\nHormat kami,\n\nBapak Arief Wicaksono\nProcurement Manager")
    docx = path.with_suffix(".docx")
    doc.save(docx)
    pdf = docx_to_pdf(docx, path.parent)
    docx.unlink()
    return pdf


def rfq_pdf(path: Path) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_heading("PT Logisly Teknologi Indonesia", level=1)
    doc.add_heading(f"Request for Quotation {RFQ_ID}", level=2)
    doc.add_paragraph("Tanggal: 2 November 2026\nKepada: Mitra transporter Logisly")
    doc.add_paragraph("Mohon kirimkan penawaran harga per trip untuk rute berikut, periode Desember 2026 s.d. Februari 2027:")
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ["Kode", "Rute", "Armada", "Volume/bulan"]):
        cell.text = text
    for key, (lane_id, truck, trips) in TENDER.items():
        cells = table.add_row().cells
        for c, v in zip(cells, [key, _lane_label(lane_id), TRUCK[truck].name, f"{trips} trip"]):
            c.text = v
    doc.add_paragraph("Penawaran wajib mencantumkan:")
    for item in ("Harga per trip dan komponen yang sudah termasuk (tol, penyeberangan, asuransi)", "Termin pembayaran", "Masa berlaku penawaran", "Konfirmasi GPS tracking dan asuransi muatan minimal Rp 500 juta"):
        doc.add_paragraph(item, style="List Bullet")
    doc.add_paragraph("Batas pengiriman penawaran: Senin, 9 November 2026 pukul 12.00 WIB ke purchasing@logisly.example.")
    docx = path.with_suffix(".docx")
    doc.save(docx)
    pdf = docx_to_pdf(docx, path.parent)
    docx.unlink()
    return pdf


def misc_pdf(path: Path, title: str, paras: list[str]) -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.add_heading(title, level=2)
    for p in paras:
        doc.add_paragraph(p)
    docx = path.with_suffix(".docx")
    doc.save(docx)
    pdf = docx_to_pdf(docx, path.parent)
    docx.unlink()
    return pdf


# --- mailbox ---------------------------------------------------------------

MIME = {".pdf": ("application", "pdf"), ".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def eml(path: Path, sender: str, subject: str, when: datetime, body: str, attachments: list[Path] = (), to: str = PURCHASING, in_reply_to: str | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = format_datetime(when)
    msg["Message-ID"] = f"<{path.stem}@mock.logisly.example>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    for a in attachments:
        maintype, subtype = MIME[a.suffix]
        msg.add_attachment(a.read_bytes(), maintype=maintype, subtype=subtype, filename=a.name)
    path.write_bytes(bytes(msg))


def build_mailbox(base: dict[str, float], rng: random.Random) -> list[dict]:
    inbox = OUT / "inbox"
    att = inbox / "attachments"
    att.mkdir(parents=True, exist_ok=True)
    (OUT / "sent").mkdir(exist_ok=True)
    rfq_pdf(OUT / "sent" / f"{RFQ_ID}")
    index: list[dict] = []

    def at(day: str, hh: int, mm: int) -> datetime:
        return datetime.fromisoformat(day).replace(hour=hh, minute=mm, tzinfo=WIB)

    tender = tender_pdf(att / f"{TENDER_ID}_Undangan_Tender")
    eml(inbox / "01_tender_nal.eml", f"Arief Wicaksono <procurement@nusantaraagro.example>", f"Undangan Tender Jasa Transportasi {TENDER_ID}", at("2026-11-02", 9, 12),
        "Selamat pagi Tim Logisly,\n\nTerlampir undangan tender jasa transportasi darat untuk periode Desember 2026 s.d. Februari 2027. "
        "Mohon penawaran dikirim paling lambat 13 November 2026.\n\nTerima kasih,\nArief Wicaksono\nProcurement Manager, PT Nusantara Agro Lestari\n",
        [tender], to="sales@logisly.example, purchasing@logisly.example")
    index.append({"file": "01_tender_nal.eml", "kind": "tender", "attachments": [tender.name]})

    plan = [
        ("02", "Q-TCL-1", "2026-11-03", (10, 5), None),
        ("03", "Q-MAJ-1", "2026-11-03", (15, 40), None),
        ("04", "Q-STI-1", "2026-11-04", (8, 55), None),
        ("05", "Q-LJE-1", "2026-11-04", (13, 20), None),
        ("06", "NEWSLETTER", "2026-11-04", (16, 0), None),
        ("07", "Q-GAS-1", "2026-11-05", (9, 30), None),
        ("08", "Q-PFN-1", "2026-11-05", (11, 10), None),
        ("09", "Q-TCL-2", "2026-11-05", (14, 45), "<02_quote_tcl@mock.logisly.example>"),
        ("10", "INVOICE", "2026-11-06", (10, 0), None),
    ]
    quotes = {q["id"]: q for q in QUOTES}
    for num, qid, day, (hh, mm), reply in plan:
        when = at(day, hh, mm)
        if qid == "NEWSLETTER":
            name = f"{num}_newsletter_dealer.eml"
            eml(inbox / name, "Promo <newsletter@dealertrukmakmur.example>", "Promo Akhir Tahun: DP 15% untuk truk CDD & Fuso baru!", when,
                "Halo mitra logistik!\n\nNikmati DP mulai 15% dan bunga 0% 12 bulan untuk pembelian unit CDD dan Fuso baru sampai 31 Desember 2026. "
                "Kunjungi showroom kami di Cikarang dan Bekasi.\n\nSalam,\nPT Dealer Truk Makmur\n")
            index.append({"file": name, "kind": "not_quotation", "reason": "newsletter"})
            continue
        if qid == "INVOICE":
            t = TRANSPORTER["TRP-002"]
            inv = misc_pdf(att / "INV-MAJ-2610-118", "INVOICE INV-MAJ-2610-118", [
                f"{t.name} · {t.city}", "Kepada: PT Logisly Teknologi Indonesia", "Periode: Oktober 2026",
                "Jasa angkutan 23 trip Cikarang - Semarang (CDD Box) dan 9 trip Cikarang - Bandung (CDE Box).",
                f"Total tagihan: {idr(23 * 5_050_000 + 9 * 1_650_000)}", "Jatuh tempo: 14 hari setelah invoice diterima."])
            name = f"{num}_invoice_maj.eml"
            eml(inbox / name, f"{t.contact} <finance@mitraarmada.example>", "Invoice Oktober 2026 - INV-MAJ-2610-118", when,
                "Dengan hormat,\n\nTerlampir invoice jasa angkutan bulan Oktober 2026. Mohon dapat diproses.\n\nTerima kasih,\nFinance CV Mitra Armada Jaya\n", [inv])
            index.append({"file": name, "kind": "not_quotation", "reason": "invoice", "attachments": [inv.name]})
            continue
        q = quotes[qid]
        t = TRANSPORTER[q["trp"]]
        lines = quote_lines(q, base)
        short = q["id"].split("-")[1].lower()
        stem = att / f"Penawaran_{t.name.replace(' ', '_').replace('.', '')}_{q['id']}"
        files: list[Path] = []
        body = ""
        if q["fmt"] == "pdf":
            files = [_doc_quote(q, lines, stem)]
        elif q["fmt"] == "xlsx":
            files = [_xlsx_quote(q, lines, stem)]
        elif q["fmt"] == "scan":
            files = [_scan_quote(q, lines, stem, rng)]
        if q["fmt"] == "body":
            body = _body_quote(q, lines)
        elif q.get("revision_of"):
            body = (f"Dear Tim Purchasing,\n\nBerikut revisi penawaran kami untuk {RFQ_ID}. Harga rute Cikarang - Surabaya kami turunkan. "
                    f"Penawaran ini menggantikan penawaran tanggal 3 November.\n\nSalam,\n{t.contact}\n{t.name}\n")
        else:
            body = f"Dengan hormat,\n\nTerlampir penawaran harga kami untuk {RFQ_ID}.\n\nHormat kami,\n{t.contact}\n{t.name}\n"
        name = f"{num}_quote_{short}" + ("_rev" if q.get("revision_of") else "") + ".eml"
        if qid == "Q-TCL-1":
            name = "02_quote_tcl.eml"
        subject = (f"Re: Penawaran {RFQ_ID} - {t.name} (REVISI)" if q.get("revision_of") else f"Penawaran {RFQ_ID} - {t.name}")
        eml(inbox / name, f"{t.contact} <{t.email}>", subject, when, body, files, in_reply_to=reply)
        index.append({"file": name, "kind": "quotation", "quote_id": q["id"], "format": q["fmt"], "attachments": [f.name for f in files]})
    (inbox / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    return index


# --- expected answers -------------------------------------------------------

def evaluate(base: dict[str, float], perf: list[dict], contract_rows: list[dict]) -> dict:
    rules_flags = {"above": 10, "below": 15, "on_time": 90, "payment": 30, "validity_days": 21}
    as_of = date(2026, 11, 9)
    per_lane: dict[str, list[dict]] = {k: [] for k in TENDER}
    for q in QUOTES:
        if q.get("superseded_by"):
            continue
        t = TRANSPORTER[q["trp"]]
        ot = on_time(perf, t.id)
        for line in quote_lines(q, base):
            key = line["key"]
            lane_id, truck_req, _ = TENDER[key]
            price = line["price"]
            issues, notes = [], []
            if line["unit"] == "ton":
                price = line["price"] * TRUCK[truck_req].capacity_ton
                notes.append(f"dinormalisasi dari {idr(line['price'])}/ton x {TRUCK[truck_req].capacity_ton:g} ton")
            if key in q.get("toll_excluded", []):
                toll = LANE[lane_id].toll * TRUCK[truck_req].toll_factor
                price += toll
                notes.append(f"ditambah estimasi tol {idr(toll)} (belum termasuk tol)")
            if line["truck_type"] != truck_req:
                issues.append("truck_type_mismatch")
            if q["payment"] is None or q["valid_to"] is None:
                issues.append("incomplete")
            if q["valid_to"]:
                valid_to = date.fromisoformat(q["valid_to"])
                if valid_to < as_of:
                    issues.append("expired_validity")
                elif (valid_to - date.fromisoformat(q["date"])).days < rules_flags["validity_days"]:
                    notes.append("masa berlaku < 21 hari")
            delta = (price - base[key]) / base[key] * 100
            flags = []
            if delta > rules_flags["above"]:
                flags.append("above_baseline")
            if delta < -rules_flags["below"]:
                flags.append("below_baseline_check_hidden_cost")
            if ot < rules_flags["on_time"]:
                flags.append("on_time_below_threshold")
            if q["payment"] is not None and q["payment"] < rules_flags["payment"]:
                flags.append("payment_term_too_short")
            per_lane[key].append({
                "quote_id": q["id"], "transporter": t.name, "truck_type": line["truck_type"],
                "quoted": line["price"], "unit": line["unit"], "normalised_per_trip": int(price),
                "vs_baseline_pct": round(delta, 1), "on_time_pct": ot, "payment_term_days": q["payment"],
                "valid_to": q["valid_to"], "blocking_issues": issues, "flags": flags, "notes": notes,
            })

    result = {"as_of": as_of.isoformat(), "tender": TENDER_ID, "rfq": RFQ_ID, "customer": CUSTOMER, "lanes": {}, "ignored_emails": ["06_newsletter_dealer.eml", "10_invoice_maj.eml"], "superseded": {"Q-TCL-1": "Q-TCL-2"}}
    for key, rows in per_lane.items():
        lane_id, truck, trips = TENDER[key]
        eligible = [r for r in rows if not r["blocking_issues"]]
        if eligible:
            lo, hi = min(r["normalised_per_trip"] for r in eligible), max(r["normalised_per_trip"] for r in eligible)
            for r in eligible:
                price_score = 1.0 if hi == lo else (hi - r["normalised_per_trip"]) / (hi - lo)
                ot_score = min(1.0, max(0.0, (r["on_time_pct"] - 80) / 20))
                comp_score = 1.0 - 0.5 * len([f for f in r["flags"] if f != "above_baseline"])
                r["score"] = round(0.5 * price_score + 0.3 * ot_score + 0.2 * max(0.0, comp_score), 3)
            best = max(eligible, key=lambda r: r["score"])
        else:
            best = None
        buy = best["normalised_per_trip"] if best else None
        sell = round_rate(buy / (1 - 0.15)) if buy else None
        current = next((c for c in contract_rows if c["customer_id"] == "CUST-0004" and c["lane_id"] == lane_id and c["truck_type"] == truck), None)
        follow_up = [
            {"quote_id": r["quote_id"], "transporter": r["transporter"], "ask": _ask(r["blocking_issues"])}
            for r in rows if r["blocking_issues"]
        ]
        result["lanes"][key] = {
            "lane": _lane_label(lane_id), "truck_type": TRUCK[truck].name, "trips_per_month": trips,
            "baseline_median_90d": int(base[key]),
            "quotes": sorted(rows, key=lambda r: r["normalised_per_trip"]),
            "recommended": best["quote_id"] if best else None,
            "recommended_transporter": best["transporter"] if best else None,
            "buy_per_trip": buy, "proposed_sell_per_trip_at_15pct_margin": sell,
            "current_contract_sell": current["sell_rate_idr"] if current else None,
            "monthly_buy_total": buy * trips if buy else None,
            "follow_up": follow_up,
        }
    return result


def _ask(issues: list[str]) -> str:
    asks = []
    if "expired_validity" in issues:
        asks.append("perpanjang masa berlaku dan konfirmasi ulang harga")
    if "incomplete" in issues:
        asks.append("lengkapi termin pembayaran dan masa berlaku")
    if "truck_type_mismatch" in issues:
        asks.append("ajukan ulang dengan armada sesuai tender")
    return "; ".join(asks)


ISSUE_ID = {"truck_type_mismatch": "armada tidak sesuai", "incomplete": "tidak lengkap", "expired_validity": "masa berlaku habis"}
FLAG_ID = {"above_baseline": "di atas baseline >10%", "below_baseline_check_hidden_cost": "jauh di bawah baseline, cek biaya tersembunyi",
           "on_time_below_threshold": "on-time < 90%", "payment_term_too_short": "termin < 30 hari"}


def expected_md(res: dict) -> str:
    out = [f"# Rekomendasi {res['rfq']} untuk tender {res['tender']} ({res['customer']})", "",
           "Kunci jawaban demo. Dihitung otomatis oleh gen_uc2.py dari data yang sama dengan yang dibaca agent.", "",
           "Email yang bukan penawaran dan diabaikan: " + ", ".join(res["ignored_emails"]) + ".",
           "Penawaran Q-TCL-1 digantikan oleh revisi Q-TCL-2.", ""]
    for key, lane in res["lanes"].items():
        out += [f"## {key}: {lane['lane']} · {lane['truck_type']} · {lane['trips_per_month']} trip/bulan", "",
                f"Baseline (median 90 hari): {idr(lane['baseline_median_90d'])}", "",
                "| Penawaran | Transporter | Per trip | vs baseline | On-time | Masalah |", "|---|---|---|---|---|---|"]
        for q in lane["quotes"]:
            probs = [ISSUE_ID[i] for i in q["blocking_issues"]] + [FLAG_ID[f] for f in q["flags"]] + q["notes"]
            out.append(f"| {q['quote_id']} | {q['transporter']} | {idr(q['normalised_per_trip'])} | {q['vs_baseline_pct']:+.1f}% | {q['on_time_pct']}% | {'; '.join(probs) or '-'} |")
        out.append("")
        if lane["recommended"]:
            line = (f"**Rekomendasi:** {lane['recommended_transporter']} ({lane['recommended']}), beli {idr(lane['buy_per_trip'])}/trip. "
                    f"Usulan harga jual ke customer pada margin 15%: {idr(lane['proposed_sell_per_trip_at_15pct_margin'])}/trip.")
            if lane["current_contract_sell"]:
                line += f" Kontrak berjalan saat ini: {idr(lane['current_contract_sell'])}/trip (berakhir 30 Nov 2026)."
            out.append(line)
        else:
            out.append("**Rekomendasi:** belum ada penawaran yang memenuhi syarat.")
        for f in lane["follow_up"]:
            out.append(f"- Follow-up {f['transporter']} ({f['quote_id']}): {f['ask']}.")
        out.append("")
    out.append("Keputusan akhir ada di tim purchasing.")
    return "\n".join(out) + "\n"


def main() -> None:
    rng = random.Random(SEED + 2)
    OUT.mkdir(parents=True, exist_ok=True)
    tables = build_pricing(rng)
    write_pricing(tables)
    (OUT / "business_rules.yaml").write_text(RULES_YAML)
    base = baselines(tables["rate_history"])
    build_mailbox(base, rng)
    res = evaluate(base, tables["transporter_performance"], tables["customer_contract_rates"])
    exp = OUT / "expected"
    exp.mkdir(exist_ok=True)
    (exp / "recommendation.json").write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    (exp / "recommendation.md").write_text(expected_md(res))
    print(f"customers={len(tables['customers'])} rate_history={len(tables['rate_history'])} contracts={len(tables['customer_contract_rates'])}")
    for key, lane in res["lanes"].items():
        print(key, lane["lane"], "->", lane["recommended"], lane["buy_per_trip"], "baseline", lane["baseline_median_90d"])


if __name__ == "__main__":
    main()
