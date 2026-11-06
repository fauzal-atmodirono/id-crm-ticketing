"""ADK tools for the UC3 product manager agent.

Every tool returns a JSON-safe dict and never raises: failures come back as
``{"error": ...}``. Tools that touch session state take ``tool_context`` first
(ADK hides it from the model). The docstrings are shown to the model, so they
stay in Bahasa Indonesia.
"""

from __future__ import annotations

import json
from functools import lru_cache

from google.adk.tools import ToolContext

from logisly_agents.config import get_settings
from logisly_agents.uc3.capacity import capacity_check as _capacity_check
from logisly_agents.uc3.context import read_context
from logisly_agents.uc3.tracker import MockTracker, TrackerError

DOCS_KEY = "uc3:docs"


def _uc3_dir():
    return get_settings().data_dir / "uc3"


@lru_cache
def _context() -> dict:
    # YAML parses dates into date objects; the model-facing responses must be JSON-safe.
    return json.loads(json.dumps(read_context(_uc3_dir()), default=str))


def _tracker(tool_context: ToolContext) -> MockTracker:
    ctx = _context()
    snapshot = _uc3_dir() / "github" / "issues_snapshot.json"
    return MockTracker(tool_context.state, snapshot, ctx["conventions"], ctx["calendar"])


def _err(prefix: str, exc: Exception) -> dict:
    if isinstance(exc, TrackerError):
        return {"error": str(exc)}
    return {"error": f"{prefix}: {exc}"}


def get_templates() -> dict:
    """Ambil template dokumen produk (template PRD dan template user story) dari repo tim.

    Ikuti struktur template ini persis saat menulis PRD dan story.
    """
    try:
        return {"templates": _context()["templates"]}
    except Exception as exc:
        return {"error": f"Gagal membaca template: {exc}"}


def get_conventions() -> dict:
    """Ambil konvensi GitHub tim: daftar repo, aturan label, format judul dan story point."""
    try:
        return {"conventions": _context()["conventions"]}
    except Exception as exc:
        return {"error": f"Gagal membaca konvensi: {exc}"}


def get_team() -> dict:
    """Ambil roster tim (login GitHub, repo, kapasitas story point per sprint, cuti) dan kalender sprint."""
    try:
        ctx = _context()
        return {"roster": ctx["roster"], "calendar": ctx["calendar"]}
    except Exception as exc:
        return {"error": f"Gagal membaca tim: {exc}"}


def search_issues(
    tool_context: ToolContext, repo: str = "", state: str = "open", text: str = "", label: str = ""
) -> dict:
    """Cari issue GitHub yang sudah ada sebelum mengusulkan issue baru.

    Args:
        repo: Nama repo, mis. driver-app. Kosong berarti semua repo.
        state: open, closed atau all.
        text: Kata kunci yang dicari di judul dan isi issue.
        label: Hanya issue dengan label ini.
    """
    try:
        if state not in ("open", "closed", "all"):
            return {"error": "state harus open, closed atau all"}
        return {"issues": _tracker(tool_context).search(repo, state, text, label)}
    except Exception as exc:
        return _err("Gagal mencari issue", exc)


def get_issue(tool_context: ToolContext, ref: str) -> dict:
    """Ambil satu issue lengkap (isi dan komentar).

    Args:
        ref: Rujukan issue, format '<repo>#<nomor>', mis. platform-api#131.
    """
    try:
        return {"issue": _tracker(tool_context).get(ref)}
    except Exception as exc:
        return _err("Gagal membaca issue", exc)


def capacity_check(items: list[dict]) -> dict:
    """Hitung kapasitas tim terhadap story yang diusulkan untuk sprint sebelum code freeze.

    Args:
        items: Daftar story usulan, tiap elemen berisi assignee (login GitHub), sp (story point,
            bilangan bulat) dan sprint (nama sprint, mis. 'Sprint 26.23').
    """
    try:
        ctx = _context()
        clean = [
            {"assignee": str(i["assignee"]), "sp": int(i["sp"]), "sprint": str(i["sprint"])} for i in items
        ]
        return _capacity_check(clean, ctx["roster"], ctx["calendar"])
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": f"items harus berisi assignee, sp, sprint: {exc}"}
    except Exception as exc:
        return {"error": f"Gagal menghitung kapasitas: {exc}"}


def save_document(tool_context: ToolContext, name: str, markdown: str) -> dict:
    """Simpan dokumen (mis. PRD final) dalam format markdown ke sesi.

    Args:
        name: Nama dokumen, mis. prd_driver-app-ai-assistant.
        markdown: Isi dokumen lengkap.
    """
    try:
        if not name or not name.strip():
            return {"error": "nama dokumen tidak boleh kosong"}
        docs = dict(tool_context.state.get(DOCS_KEY) or {})
        docs[name] = markdown
        tool_context.state[DOCS_KEY] = docs  # re-assign: ADK State persists only on key assignment
        return {"name": name, "chars": len(markdown)}
    except Exception as exc:
        return {"error": f"Gagal menyimpan dokumen: {exc}"}


def create_issue(
    tool_context: ToolContext,
    repo: str,
    title: str,
    body: str,
    labels: list[str],
    milestone: str = "",
    assignees: list[str] | None = None,
) -> dict:
    """Buat issue baru. Panggil hanya setelah pengguna menyetujui usulan.

    Args:
        repo: Repo tujuan.
        title: Judul format '[area] kalimat'.
        body: Isi issue (story, kriteria penerimaan, dependensi).
        labels: Tepat satu label type:*, satu area:* dan satu priority:*, ditambah label lain bila perlu.
        milestone: Nama sprint, mis. 'Sprint 26.23'. Kosong bila tanpa milestone.
        assignees: Login GitHub penanggung jawab.
    """
    try:
        return _tracker(tool_context).create(
            repo, title, body, list(labels), milestone or None, list(assignees or [])
        )
    except Exception as exc:
        return _err("Gagal membuat issue", exc)


def update_issue(
    tool_context: ToolContext,
    ref: str,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
    milestone: str = "",
    assignees: list[str] | None = None,
    body_append: str = "",
) -> dict:
    """Ubah issue yang ada. Panggil hanya setelah pengguna menyetujui perubahan itu.

    Args:
        ref: Rujukan issue '<repo>#<nomor>'.
        add_labels: Label yang ditambahkan.
        remove_labels: Label yang dilepas.
        milestone: Sprint baru. Kosong berarti milestone tidak diubah.
        assignees: Daftar penanggung jawab baru. Kosong/tidak diisi berarti tidak diubah.
        body_append: Teks yang ditambahkan di akhir isi issue.
    """
    try:
        return _tracker(tool_context).update(
            ref,
            add_labels=list(add_labels or []),
            remove_labels=list(remove_labels or []),
            milestone=milestone or None,
            assignees=list(assignees) if assignees else None,
            body_append=body_append,
        )
    except Exception as exc:
        return _err("Gagal mengubah issue", exc)


def comment_issue(tool_context: ToolContext, ref: str, body: str) -> dict:
    """Tambah komentar pada issue. Panggil hanya setelah pengguna menyetujui.

    Args:
        ref: Rujukan issue '<repo>#<nomor>'.
        body: Isi komentar.
    """
    try:
        return _tracker(tool_context).comment(ref, body)
    except Exception as exc:
        return _err("Gagal menambah komentar", exc)


def close_issue(tool_context: ToolContext, ref: str, reason: str, comment: str = "") -> dict:
    """Tutup issue. Panggil hanya setelah pengguna menyetujui penutupan itu.

    Args:
        ref: Rujukan issue '<repo>#<nomor>'.
        reason: completed, not_planned atau duplicate.
        comment: Komentar penutup, mis. rujukan ke issue pengganti.
    """
    try:
        return _tracker(tool_context).close(ref, reason, comment)  # type: ignore[arg-type]
    except Exception as exc:
        return _err("Gagal menutup issue", exc)
