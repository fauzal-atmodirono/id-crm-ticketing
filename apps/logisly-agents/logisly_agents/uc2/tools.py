"""ADK tools for the UC2 purchasing agent.

Every tool takes ``tool_context`` first (ADK hides it from the model), returns a
JSON-safe dict and never raises: failures come back as ``{"error": ...}``.
The docstrings are shown to the model, so they stay in Bahasa Indonesia.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from google.adk.tools import ToolContext
from pydantic import ValidationError

from logisly_agents.config import get_settings
from logisly_agents.uc2 import store
from logisly_agents.uc2.evaluate import compare
from logisly_agents.uc2.pricing import PricingRepo
from logisly_agents.uc2.rules import load_rules
from logisly_agents.uc2.schemas import Quotation, Tender


@lru_cache
def _repo() -> PricingRepo:
    return PricingRepo(get_settings().data_dir / "uc2" / "pricing" / "pricing.db")


@lru_cache
def _rules():
    return load_rules(get_settings().data_dir / "uc2" / "business_rules.yaml")


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def record_tender(tool_context: ToolContext, tender: Tender) -> dict:
    """Catat dokumen tender (undangan tender / RFQ dari pelanggan) yang baru dibaca.

    Salin angka persis seperti tertulis di dokumen. Satu baris lane per rute dan jenis armada.
    Memanggil ulang akan menimpa tender sebelumnya.

    Args:
        tender: Isi tender: nomor referensi, nama pelanggan, daftar lane (asal, tujuan, jenis
            armada, jumlah trip per bulan), periode, batas pengumpulan, dan persyaratan.
    """
    try:
        repo = _repo()
        unresolved = []
        for ln in tender.lanes:
            if repo.lane_for_route(ln.origin, ln.destination) is None:
                unresolved.append(f"rute tidak dikenal: {ln.origin} - {ln.destination}")
            if repo.truck_code(ln.truck_type) is None:
                unresolved.append(f"jenis armada tidak dikenal: {ln.truck_type}")
        if unresolved:
            return {
                "error": "Tender tidak disimpan karena ada rute atau armada yang tidak dikenal. "
                "Periksa ejaan di dokumen atau tanyakan ke pengguna.",
                "unresolved": unresolved,
            }
        dump = store.set_tender(tool_context.state, tender)
        return {"status": "ok", "reference": dump["reference"], "lanes": len(tender.lanes)}
    except Exception as exc:
        return {"error": f"Gagal mencatat tender: {exc}"}


def record_quotation(tool_context: ToolContext, quotation: Quotation) -> dict:
    """Catat satu penawaran harga dari transporter (dari lampiran atau isi email).

    Salin angka persis seperti tertulis, lengkap dengan satuannya (per trip, per ton, per kg).
    Jika ada catatan "belum termasuk tol", isi excludes_toll=true pada baris itu. Jika termin
    pembayaran atau masa berlaku tidak tercantum, biarkan kosong (null), jangan ditebak.
    Satu transporter hanya memiliki satu penawaran aktif: dokumen yang lebih baru menggantikan
    yang lama.

    Args:
        quotation: Nama transporter, nama dokumen, tanggal dokumen, apakah revisi, daftar baris
            harga (asal, tujuan, jenis armada, harga rupiah, satuan, trip per bulan,
            excludes_toll), termin pembayaran (hari), masa berlaku, dan catatan.
    """
    try:
        if not isinstance(quotation, Quotation):
            quotation = Quotation.model_validate(quotation)
        res = store.add_quotation(tool_context.state, quotation)
        return {"status": "ok", **res, "transporter": quotation.transporter, "lines": len(quotation.lines)}
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return {"error": f"Penawaran tidak valid: {errs}"}
    except Exception as exc:
        return {"error": f"Gagal mencatat penawaran: {exc}"}


def list_quotations(tool_context: ToolContext, transporter: str = "", route: str = "") -> dict:
    """Tampilkan penawaran yang sudah dicatat, dengan filter opsional.

    Args:
        transporter: Sebagian nama transporter, misalnya "Mitra Armada". Kosong berarti semua.
        route: Sebagian nama kota asal atau tujuan, misalnya "Surabaya". Kosong berarti semua.
    """
    try:
        records = store.list_quotations(tool_context.state)
        t, r = _norm(transporter), _norm(route)
        out = []
        for rec in records:
            if t and t not in _norm(rec["transporter"]):
                continue
            lines = rec["lines"]
            if r:
                lines = [ln for ln in lines if r in _norm(ln["origin"]) or r in _norm(ln["destination"])]
                if not lines:
                    continue
            out.append({**rec, "lines": lines})
        return {"count": len(out), "quotations": out}
    except Exception as exc:
        return {"error": f"Gagal membaca penawaran: {exc}"}


def compare_quotes(tool_context: ToolContext) -> dict:
    """Bandingkan semua penawaran aktif terhadap tender dan aturan bisnis.

    Hasilnya per lane (T1, T2, ...): harga per trip yang sudah dinormalisasi, selisih terhadap
    baseline 90 hari, masalah penghalang (blocking_issues), flag, rekomendasi, harga beli,
    usulan harga jual, dan daftar tindak lanjut. Semua angka dalam jawaban harus diambil dari
    hasil ini. Panggil setelah tender dan penawaran dicatat.
    """
    try:
        tender = store.get_tender(tool_context.state)
        if tender is None:
            return {"error": "Belum ada dokumen tender"}
        quotes = store.list_quotations(tool_context.state)
        if not quotes:
            return {"error": "Belum ada penawaran yang dicatat"}
        return compare(tender, quotes, _repo(), _rules(), as_of=get_settings().as_of)
    except Exception as exc:
        return {"error": f"Gagal membandingkan penawaran: {exc}"}


def price_history(origin: str, destination: str, truck_type: str) -> dict:
    """Riwayat harga beli per bulan (median, min, maks) untuk satu rute dan jenis armada.

    Args:
        origin: Kota asal.
        destination: Kota tujuan.
        truck_type: Jenis armada, misalnya "Fuso Wingbox" atau "CDD Box".
    """
    try:
        repo = _repo()
        lane = repo.lane_for_route(origin, destination)
        truck = repo.truck_code(truck_type)
        if lane is None or truck is None:
            return {"error": "Rute atau jenis armada tidak dikenal."}
        return {"lane_id": lane, "truck": truck, "history": repo.price_history(lane, truck)}
    except Exception as exc:
        return {"error": f"Gagal membaca riwayat harga: {exc}"}


def query_pricing_sql(sql: str) -> dict:
    """Jalankan satu query SELECT (hanya baca) pada database harga Logisly.

    Gunakan untuk pertanyaan lanjutan di luar compare_quotes, misalnya performa transporter,
    kontrak pelanggan, atau riwayat tarif. Hanya satu pernyataan SELECT atau WITH.

    Args:
        sql: Query SQLite.
    """
    try:
        return _repo().run_select(sql)
    except Exception as exc:
        return {"error": str(exc)}


def record_decision(
    tool_context: ToolContext, lane: str, quote_id: str, decision: str, note: str = ""
) -> dict:
    """Catat keputusan akhir tim purchasing untuk satu lane. Hanya panggil setelah pengguna setuju.

    Args:
        lane: Kode lane, misalnya "T1".
        quote_id: Id penawaran yang diputuskan, misalnya "Q3".
        decision: "approve" (disetujui) atau "reject" (ditolak).
        note: Catatan alasan, opsional.
    """
    try:
        if decision not in ("approve", "reject"):
            return {"error": 'Keputusan harus "approve" atau "reject".'}
        entry: dict[str, Any] = store.add_decision(tool_context.state, lane, quote_id, decision, note)
        return {"status": "ok", "decision": entry}
    except Exception as exc:
        return {"error": f"Gagal mencatat keputusan: {exc}"}
