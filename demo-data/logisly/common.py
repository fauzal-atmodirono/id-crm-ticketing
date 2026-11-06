"""Shared fictional universe for the Logisly UC2/UC3 demo data.

Every company, person, plate and figure here is made up. The three featured
shippers reuse the names from the UC1 trips (features/logisly/trips.py) so
the demo reads as one Logisly, not three unrelated datasets.

Deterministic: everything derives from SEED, so re-running the generators
reproduces byte-identical CSVs and the same expected answers.
"""

from __future__ import annotations

import random
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

SEED = 20261109  # the 9 Nov demo
ROOT = Path(__file__).resolve().parent


# --- truck types ------------------------------------------------------------

@dataclass(frozen=True)
class TruckType:
    code: str
    name: str
    capacity_ton: float
    per_km: int  # IDR per km
    fixed: int  # IDR per trip (loading, driver allowance)
    toll_factor: float  # multiple of the lane's base (class I) toll
    ferry_fee: int  # IDR per crossing


TRUCK_TYPES = [
    TruckType("CDE_BOX", "CDE Box", 2.5, 6500, 350_000, 1.0, 900_000),
    TruckType("CDD_BOX", "CDD Box", 5.0, 8500, 500_000, 1.5, 1_300_000),
    TruckType("CDD_LONG", "CDD Long", 7.0, 9500, 550_000, 1.5, 1_500_000),
    TruckType("FUSO_WB", "Fuso Wingbox", 8.0, 11500, 700_000, 1.5, 2_200_000),
    TruckType("TRONTON_WB", "Tronton Wingbox", 15.0, 14500, 900_000, 2.0, 3_000_000),
    TruckType("TRAILER_40", "Trailer 40ft", 25.0, 18500, 1_200_000, 2.5, 4_200_000),
]
TRUCK = {t.code: t for t in TRUCK_TYPES}


# --- lanes ------------------------------------------------------------------

@dataclass(frozen=True)
class Lane:
    id: str
    origin: str
    destination: str
    km: int
    toll: int  # base class I toll, IDR
    ferry: bool
    region: str
    trucks: tuple[str, ...]


LANES = [
    Lane("LN-01", "Cikarang, Bekasi", "Semarang", 430, 650_000, False, "jawa", ("CDD_BOX", "FUSO_WB", "TRONTON_WB")),
    Lane("LN-02", "Cikarang, Bekasi", "Surabaya", 780, 1_100_000, False, "jawa", ("CDD_BOX", "FUSO_WB", "TRONTON_WB", "TRAILER_40")),
    Lane("LN-03", "Karawang", "Surabaya", 750, 1_050_000, False, "jawa", ("FUSO_WB", "TRONTON_WB")),
    Lane("LN-04", "Cibitung, Bekasi", "Bandung", 130, 150_000, False, "jabar", ("CDE_BOX", "CDD_BOX", "FUSO_WB")),
    Lane("LN-05", "Tanjung Priok, Jakarta", "Bandung", 150, 170_000, False, "jabar", ("CDD_BOX", "TRONTON_WB", "TRAILER_40")),
    Lane("LN-06", "Tangerang", "Cirebon", 260, 380_000, False, "jabar", ("CDE_BOX", "CDD_BOX")),
    Lane("LN-07", "Cikarang, Bekasi", "Yogyakarta", 540, 700_000, False, "jawa", ("CDD_BOX", "FUSO_WB")),
    Lane("LN-08", "Cikarang, Bekasi", "Malang", 860, 1_150_000, False, "jawa", ("CDD_LONG", "FUSO_WB")),
    Lane("LN-09", "Surabaya", "Denpasar", 420, 150_000, True, "jatim", ("CDD_BOX", "FUSO_WB")),
    Lane("LN-10", "Jakarta", "Bandar Lampung", 240, 120_000, True, "sumatra", ("CDD_BOX", "TRONTON_WB")),
    Lane("LN-11", "Jakarta", "Palembang", 560, 350_000, True, "sumatra", ("FUSO_WB", "TRONTON_WB")),
    Lane("LN-12", "Semarang", "Solo", 110, 120_000, False, "jateng", ("CDE_BOX", "CDD_BOX")),
    Lane("LN-13", "Surabaya", "Malang", 95, 60_000, False, "jatim", ("CDE_BOX", "CDD_BOX")),
    Lane("LN-14", "Cikarang, Bekasi", "Purwakarta", 60, 60_000, False, "jabar", ("CDE_BOX", "CDD_BOX", "FUSO_WB")),
    Lane("LN-15", "Tanjung Priok, Jakarta", "Cikarang, Bekasi", 45, 40_000, False, "jabar", ("TRONTON_WB", "TRAILER_40")),
    Lane("LN-16", "Surabaya", "Semarang", 350, 500_000, False, "jawa", ("CDD_BOX", "FUSO_WB")),
    Lane("LN-17", "Bandung", "Semarang", 370, 500_000, False, "jawa", ("CDD_BOX", "CDD_LONG")),
    Lane("LN-18", "Tangerang", "Serang", 70, 60_000, False, "jabar", ("CDE_BOX", "CDD_BOX")),
    Lane("LN-19", "Cikarang, Bekasi", "Cilacap", 400, 400_000, False, "jawa", ("CDD_BOX", "FUSO_WB")),
    Lane("LN-20", "Medan", "Pekanbaru", 590, 150_000, False, "sumatra", ("CDD_BOX", "FUSO_WB", "TRONTON_WB")),
]
LANE = {lane.id: lane for lane in LANES}


# --- transporters (Logisly's vendors) ---------------------------------------

@dataclass(frozen=True)
class Transporter:
    id: str
    name: str
    city: str
    factor: float  # price level vs market
    on_time: float  # on-time delivery, %
    claim: float  # cargo claim rate, %
    regions: tuple[str, ...]
    email: str
    contact: str


TRANSPORTERS = [
    Transporter("TRP-001", "PT Trans Cahaya Logistik", "Bekasi", 0.98, 94.2, 0.4, ("jawa", "jabar", "sumatra"), "sales@transcahaya.example", "Ibu Rina Marlina"),
    Transporter("TRP-002", "CV Mitra Armada Jaya", "Karawang", 0.93, 82.5, 1.6, ("jawa", "jabar"), "marketing@mitraarmada.example", "Bapak Joko Susilo"),
    Transporter("TRP-003", "PT Samudra Truk Indonesia", "Jakarta Utara", 1.01, 91.0, 0.7, ("jawa", "jabar", "sumatra"), "quotation@samudratruk.example", "Bapak Hendra Gunawan"),
    Transporter("TRP-004", "PT Lintas Jawa Ekspres", "Semarang", 1.08, 97.1, 0.2, ("jawa", "jateng"), "cs@lintasjawa.example", "Ibu Siti Rahayu"),
    Transporter("TRP-005", "PT Garuda Angkut Sejahtera", "Tangerang", 1.00, 90.4, 0.9, ("jabar", "sumatra"), "admin@garudaangkut.example", "Bapak Rudi Hartono"),
    Transporter("TRP-006", "PT Prima Fleet Nusantara", "Cibitung", 0.96, 92.8, 0.5, ("jabar", "sumatra", "jawa"), "bid@primafleet.example", "Bapak Andi Saputra"),
    Transporter("TRP-007", "CV Bintang Timur Cargo", "Surabaya", 0.99, 89.0, 1.1, ("jatim", "jawa"), "order@bintangtimur.example", "Bapak Wahyu Prasetyo"),
    Transporter("TRP-008", "PT Sinar Muda Transport", "Bandung", 1.03, 93.5, 0.6, ("jabar", "jateng"), "sales@sinarmuda.example", "Ibu Dewi Lestari"),
    Transporter("TRP-009", "PT Andalas Trans Sumatera", "Medan", 1.02, 88.7, 1.0, ("sumatra",), "info@andalastrans.example", "Bapak Fauzi Nasution"),
    Transporter("TRP-010", "PT Kencana Logistik Utama", "Surabaya", 1.05, 95.6, 0.3, ("jatim", "jawa"), "sales@kencanalog.example", "Ibu Maya Anggraini"),
    Transporter("TRP-011", "CV Sejahtera Abadi Truk", "Cirebon", 0.95, 86.3, 1.4, ("jabar",), "cv.sejahtera@abaditruk.example", "Bapak Slamet Riyadi"),
    Transporter("TRP-012", "PT Mega Armada Sentosa", "Cikarang", 1.00, 92.0, 0.6, ("jawa", "jabar"), "rfq@megaarmada.example", "Bapak Yusuf Hidayat"),
    Transporter("TRP-013", "PT Nusa Kargo Mandiri", "Semarang", 0.97, 90.9, 0.8, ("jateng", "jawa"), "marketing@nusakargo.example", "Ibu Lina Kusuma"),
    Transporter("TRP-014", "PT Bahari Lintas Selat", "Jakarta Utara", 1.04, 93.9, 0.5, ("sumatra", "jatim"), "sales@baharilintas.example", "Bapak Taufik Ismail"),
    Transporter("TRP-015", "CV Cahaya Rejeki Transport", "Malang", 0.94, 87.6, 1.2, ("jatim",), "cahayarejeki@transport.example", "Bapak Eko Purnomo"),
    Transporter("TRP-016", "PT Global Truk Persada", "Tangerang", 1.06, 96.0, 0.3, ("jabar", "jawa"), "bd@globaltruk.example", "Ibu Fitri Handayani"),
]
TRANSPORTER = {t.id: t for t in TRANSPORTERS}


# --- customers (Logisly's shippers) -----------------------------------------

FEATURED_CUSTOMERS = [
    # (name, segment, city) -- the first three are the UC1 consignees
    ("PT Sumber Pangan Nusantara", "FMCG", "Semarang"),
    ("PT Elektronik Jaya Abadi", "Elektronik", "Surabaya"),
    ("CV Berkah Retail", "Retail", "Bandung"),
    ("PT Nusantara Agro Lestari", "Agribisnis", "Cikarang, Bekasi"),  # UC2 tender
]
SEGMENTS = ["FMCG", "Elektronik", "Retail", "Agribisnis", "Kimia", "Suku Cadang Otomotif", "Farmasi", "Bahan Bangunan", "Tekstil", "E-commerce"]
_W1 = ["Sumber", "Mitra", "Cahaya", "Karya", "Sinar", "Bumi", "Indo", "Graha", "Mulia", "Prima", "Surya", "Tunas", "Citra", "Anugerah", "Bina", "Jaya", "Mega", "Dwi", "Tri", "Panca", "Inti", "Duta", "Fajar", "Harapan", "Sentosa"]
_W2 = ["Makmur", "Abadi", "Sejahtera", "Mandiri", "Utama", "Persada", "Lestari", "Gemilang", "Perkasa", "Sukses", "Kencana", "Agung", "Pratama", "Selaras", "Raya", "Nusantara", "Indah", "Bersama", "Cemerlang", "Gemala"]
_W3 = ["Pangan", "Plastik", "Kemasan", "Kimia", "Tekstil", "Farma", "Elektrik", "Baja", "Niaga", "Distribusi", "Logam", "Kertas", "Food", "Industri", "Retailindo", ""]
CITIES = ["Jakarta", "Bekasi", "Cikarang, Bekasi", "Karawang", "Tangerang", "Bandung", "Semarang", "Surabaya", "Malang", "Medan", "Palembang", "Yogyakarta", "Solo", "Denpasar", "Cirebon"]


def customers(n: int = 600) -> list[dict]:
    rng = random.Random(SEED)
    rows: list[dict] = []
    seen: set[str] = set()
    for i, (name, seg, city) in enumerate(FEATURED_CUSTOMERS, start=1):
        rows.append(_customer(i, name, seg, city, rng))
        seen.add(name)
    while len(rows) < n:
        prefix = "PT" if rng.random() < 0.8 else "CV"
        name = " ".join(w for w in (prefix, rng.choice(_W1), rng.choice(_W2), rng.choice(_W3)) if w)
        if name in seen:
            continue
        seen.add(name)
        rows.append(_customer(len(rows) + 1, name, rng.choice(SEGMENTS), rng.choice(CITIES), rng))
    return rows


def _customer(i: int, name: str, seg: str, city: str, rng: random.Random) -> dict:
    return {
        "customer_id": f"CUST-{i:04d}",
        "name": name,
        "segment": seg,
        "city": city,
        "customer_since": f"{rng.randint(2018, 2026)}-{rng.randint(1, 12):02d}-01",
        "payment_term_days": rng.choice([30, 30, 45, 45, 60]),
        "status": "active" if rng.random() < 0.9 else "inactive",
    }


# --- pricing model ----------------------------------------------------------

# Nov 2025 .. Oct 2026. Lebaran 2026 falls around 20 March, so March peaks.
PERIODS = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 11)]
SEASON = {"2025-12": 1.06, "2026-02": 1.04, "2026-03": 1.15, "2026-04": 1.03}


def market_rate(lane: Lane, truck: TruckType, period: str) -> float:
    """Market buy rate per trip (IDR), before transporter factor and noise."""
    base = truck.fixed + truck.per_km * lane.km + lane.toll * truck.toll_factor
    if lane.ferry:
        base += truck.ferry_fee
    month_idx = PERIODS.index(period) if period in PERIODS else len(PERIODS)
    inflation = 1 + 0.003 * month_idx
    return base * inflation * SEASON.get(period, 1.0)


def round_rate(x: float) -> int:
    return int(round(x / 50_000) * 50_000)


def median(values: list[float]) -> float:
    return float(statistics.median(values))


def idr(x: float) -> str:
    return "Rp " + f"{int(round(x)):,}".replace(",", ".")


# --- document helpers -------------------------------------------------------

def docx_to_pdf(docx_path: Path, out_dir: Path) -> Path:
    """Convert with headless LibreOffice; the PDF keeps a real text layer."""
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)],
        check=True,
        capture_output=True,
    )
    return out_dir / (docx_path.stem + ".pdf")
