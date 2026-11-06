"""ADK tools for the UC3 product manager agent.

Every tool returns a JSON-safe dict and never raises: failures come back as
``{"error": ...}``. Tools that touch session state take ``tool_context`` first
(ADK hides it from the model). The docstrings are shown to the model, so they
stay in Bahasa Indonesia.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from typing import Any

from google.adk.tools import ToolContext

from logisly_agents.config import get_settings
from logisly_agents.uc3.capacity import capacity_check as _capacity_check
from logisly_agents.uc3.context import read_context
from logisly_agents.uc3.tracker import KEY as TRACKER_KEY
from logisly_agents.uc3.tracker import MockTracker, TrackerError

DOCS_KEY = "uc3:docs"
PROPOSAL_KEY = "uc3:proposal"

# Keys each proposal item may carry; the first set is required.
_ITEM_KEYS: dict[str, tuple[set[str], set[str]]] = {
    "create": ({"repo", "title", "labels"}, {"body", "milestone", "assignees"}),
    "update": ({"ref"}, {"add_labels", "remove_labels", "milestone", "assignees", "body_append", "comment"}),
    "close": ({"ref", "reason"}, {"comment"}),
}


def _uc3_dir():
    return get_settings().data_dir / "uc3"


@lru_cache
def _context() -> dict:
    # YAML parses dates into date objects; the model-facing responses must be JSON-safe.
    return json.loads(json.dumps(read_context(_uc3_dir()), default=str))


def _tracker(tool_context: ToolContext) -> MockTracker:
    return _tracker_on(tool_context.state)


def _tracker_on(state) -> MockTracker:
    ctx = _context()
    snapshot = _uc3_dir() / "github" / "issues_snapshot.json"
    return MockTracker(state, snapshot, ctx["conventions"], ctx["calendar"])


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


def _check_item(kind: str, args: Any) -> dict:
    if not isinstance(args, dict):
        raise TrackerError("setiap usulan harus berupa objek")
    required, optional = _ITEM_KEYS[kind]
    missing = sorted(required - set(args))
    unknown = sorted(set(args) - required - optional)
    if missing:
        raise TrackerError(f"kolom wajib tidak ada: {missing}")
    if unknown:
        raise TrackerError(f"kolom tidak dikenal: {unknown}")
    return args


def _run_item(tracker: MockTracker, kind: str, args: dict) -> dict:
    """Apply one proposal item to ``tracker``. The same code runs for the dry run and the real apply."""
    args = _check_item(kind, args)
    if kind == "create":
        return tracker.create(
            args["repo"],
            args["title"],
            args.get("body") or "",
            list(args.get("labels") or []),
            args.get("milestone") or None,
            list(args.get("assignees") or []),
        )
    if kind == "update":
        out = tracker.update(
            args["ref"],
            add_labels=list(args.get("add_labels") or []),
            remove_labels=list(args.get("remove_labels") or []),
            milestone=args.get("milestone") or None,
            assignees=list(args["assignees"]) if args.get("assignees") else None,
            body_append=args.get("body_append") or "",
        )
        if args.get("comment"):
            tracker.comment(args["ref"], args["comment"])
        return out
    return tracker.close(args["ref"], args["reason"], args.get("comment") or "")


def _label(kind: str, args: Any) -> str:
    if not isinstance(args, dict):
        return kind
    if kind == "create":
        return f"buat {args.get('repo', '?')}: {args.get('title', '?')}"
    verb = "ubah" if kind == "update" else "tutup"
    return f"{verb} {args.get('ref', '?')}"


def propose_changes(
    tool_context: ToolContext,
    creates: list[dict],
    updates: list[dict],
    closes: list[dict],
) -> dict:
    """Validasi seluruh usulan perubahan backlog TANPA menulis apa pun, lalu simpan sebagai usulan P1..Pn.

    Panggil sebelum menyajikan tabel usulan. Bila ada item berstatus error, perbaiki lalu panggil
    lagi dengan seluruh usulan. Usulan baru menggantikan usulan sebelumnya.

    Args:
        creates: Issue baru. Tiap elemen: repo, title ('[area] kalimat'), labels (tepat satu type:*,
            area:*, priority:*), body, milestone (nama sprint), assignees (login GitHub).
        updates: Perubahan issue yang ada. Tiap elemen: ref ('<repo>#<nomor>'), dan opsional
            add_labels, remove_labels, milestone, assignees, body_append, comment.
        closes: Penutupan issue. Tiap elemen: ref, reason (completed, not_planned atau duplicate),
            comment (mis. rujukan ke issue pengganti).
    """
    try:
        sim = {TRACKER_KEY: copy.deepcopy(_tracker(tool_context).state[TRACKER_KEY])}
        tracker = _tracker_on(sim)
        items: list[dict] = []
        report: list[dict] = []
        batches = (("create", creates), ("update", updates), ("close", closes))
        for kind, batch in batches:
            for args in batch or []:
                pid = f"P{len(items) + 1}"
                row: dict[str, Any] = {"id": pid, "kind": kind, "usulan": _label(kind, args)}
                try:
                    out = _run_item(tracker, kind, args)
                    row["status"] = "ok"
                    if kind == "create":
                        row["predicted_ref"] = out["ref"]
                except Exception as exc:  # noqa: BLE001 - every item failure is reported, not raised
                    row["status"] = "error"
                    row["error"] = str(exc) or type(exc).__name__
                items.append({"id": pid, "kind": kind, "args": copy.deepcopy(args), "applied": False})
                report.append(row)
        tool_context.state[PROPOSAL_KEY] = {"items": items}  # re-assign: State persists on assignment
        ok = sum(1 for r in report if r["status"] == "ok")
        return {"items": report, "ok": ok, "errors": len(report) - ok}
    except Exception as exc:
        return {"error": f"Gagal memvalidasi usulan: {str(exc) or type(exc).__name__}"}


def apply_proposal(tool_context: ToolContext, ids: list[str] | None = None) -> dict:
    """Terapkan usulan yang disimpan propose_changes, berurutan. Panggil SEKALI setelah pengguna setuju.

    Item yang sudah diterapkan tidak diterapkan ulang. Laporkan setiap error ke pengguna.

    Args:
        ids: Id usulan yang disetujui, mis. ["P1", "P3"]. Kosong berarti semua usulan.
    """
    try:
        proposal = tool_context.state.get(PROPOSAL_KEY)
        if not proposal or not proposal.get("items"):
            return {"error": "Belum ada usulan. Panggil propose_changes dulu."}
        items = copy.deepcopy(proposal["items"])
        known = {i["id"] for i in items}
        wanted = list(ids or [])
        tracker = _tracker(tool_context)
        results: list[dict] = []
        applied = failed = already = 0
        for item in items:
            if wanted and item["id"] not in wanted:
                continue
            if item["applied"]:
                already += 1
                results.append({"id": item["id"], "status": "already_applied", **(item.get("result") or {})})
                continue
            try:
                out = _run_item(tracker, item["kind"], item["args"])
            except Exception as exc:  # noqa: BLE001 - every item failure is reported, not raised
                failed += 1
                results.append({"id": item["id"], "status": "error", "error": str(exc) or type(exc).__name__})
                continue
            item["applied"] = True
            item["result"] = {"ref": out["ref"]}
            applied += 1
            results.append({"id": item["id"], "status": "applied", "ref": out["ref"]})
            # Persist after each item so a crash mid-way never re-applies finished items.
            tool_context.state[PROPOSAL_KEY] = {"items": copy.deepcopy(items)}
        for pid in wanted:
            if pid not in known:
                failed += 1
                results.append({"id": pid, "status": "error", "error": f"usulan {pid} tidak ada"})
        tool_context.state[PROPOSAL_KEY] = {"items": items}
        return {"applied": applied, "failed": failed, "already_applied": already, "results": results}
    except Exception as exc:
        return {"error": f"Gagal menerapkan usulan: {str(exc) or type(exc).__name__}"}
