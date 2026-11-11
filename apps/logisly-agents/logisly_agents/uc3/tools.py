"""ADK tools for the UC3 product manager agent.

Every tool returns a JSON-safe dict and never raises: failures come back as
``{"error": ...}``. Tools that touch session state take ``tool_context`` first
(ADK hides it from the model). The docstrings are shown to the model, so they
stay in Bahasa Indonesia.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from typing import Any

from google.adk.tools import ToolContext

from logisly_agents.config import get_settings
from logisly_agents.uc3 import gdoc
from logisly_agents.uc3.capacity import capacity_check as _capacity_check
from logisly_agents.uc3.context import read_context
from logisly_agents.uc3.jira import KEY as JIRA_KEY
from logisly_agents.uc3.jira import MockJira, TrackerError

DOCS_KEY = "uc3:docs"
PROPOSAL_KEY = "uc3:proposal"
GDOCS_KEY = "uc3:gdocs"

# Keys each proposal item may carry; the first set is required.
_ITEM_KEYS: dict[str, tuple[set[str], set[str]]] = {
    "create": (
        {"type", "summary", "components"},
        {
            "temp_id",
            "description",
            "acceptance",
            "labels",
            "story_points",
            "sprint",
            "assignee",
            "epic",
            "links",
        },
    ),
    "update": (
        {"key"},
        {
            "add_labels",
            "remove_labels",
            "sprint",
            "assignee",
            "story_points",
            "description_append",
            "acceptance_append",
            "add_links",
            "comment",
        },
    ),
    "transition": ({"key", "status"}, {"resolution", "comment", "duplicate_of"}),
}
_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def _uc3_dir():
    return get_settings().data_dir / "uc3"


@lru_cache
def _context() -> dict:
    # YAML parses dates into date objects; the model-facing responses must be JSON-safe.
    return json.loads(json.dumps(read_context(_uc3_dir()), default=str))


def _jira(tool_context: ToolContext) -> MockJira:
    return _jira_on(tool_context.state)


def _jira_on(state) -> MockJira:
    ctx = _context()
    snapshot = _uc3_dir() / "jira" / "issues_snapshot.json"
    return MockJira(state, snapshot, ctx["conventions"], ctx["calendar"])


def _err(prefix: str, exc: Exception) -> dict:
    if isinstance(exc, TrackerError):
        return {"error": str(exc)}
    return {"error": f"{prefix}: {exc}"}


def get_templates() -> dict:
    """Ambil template dokumen produk (template PRD format Logisly dan template user story) dari repo tim.

    Ikuti struktur template ini persis saat menulis PRD dan story.
    """
    try:
        return {"templates": _context()["templates"]}
    except Exception as exc:
        return {"error": f"Gagal membaca template: {exc}"}


def get_conventions() -> dict:
    """Ambil konvensi Jira tim.

    Isinya: project, tipe issue, status, resolution, tipe link, component, story point,
    format summary, definition of ready dan definition of done.
    """
    try:
        return {"conventions": _context()["conventions"]}
    except Exception as exc:
        return {"error": f"Gagal membaca konvensi: {exc}"}


def get_team() -> dict:
    """Ambil roster tim (id Jira, component, kapasitas story point per sprint, cuti) dan kalender sprint."""
    try:
        ctx = _context()
        return {"roster": ctx["roster"], "calendar": ctx["calendar"]}
    except Exception as exc:
        return {"error": f"Gagal membaca tim: {exc}"}


def search_issues(
    tool_context: ToolContext,
    text: str = "",
    status: str = "open",
    component: str = "",
    issue_type: str = "",
    label: str = "",
) -> dict:
    """Cari issue Jira yang sudah ada sebelum mengusulkan issue baru.

    Args:
        text: Kata kunci yang dicari di summary dan description.
        status: open (To Do dan In Progress), done (Done dan Won't Do) atau all.
        component: Hanya issue dengan component ini, mis. Driver App.
        issue_type: Epic, Story, Task atau Bug. Kosong berarti semua tipe.
        label: Hanya issue dengan label ini.
    """
    try:
        return {"issues": _jira(tool_context).search(text, status, component, issue_type, label)}
    except Exception as exc:
        return _err("Gagal mencari issue", exc)


def get_issue(tool_context: ToolContext, key: str) -> dict:
    """Ambil satu issue lengkap (deskripsi, acceptance, link dan komentar).

    Args:
        key: Kunci issue, mis. LOG-3412.
    """
    try:
        return {"issue": _jira(tool_context).get(key)}
    except Exception as exc:
        return _err("Gagal membaca issue", exc)


def capacity_check(items: list[dict]) -> dict:
    """Hitung kapasitas tim terhadap story yang diusulkan untuk sprint sebelum code freeze.

    Args:
        items: Daftar story usulan, tiap elemen berisi assignee (id orang di roster, mis. 'rizky-r'),
            sp (story point, bilangan bulat) dan sprint (nama sprint, mis. 'Sprint 26.23').
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
        name: Nama dokumen, mis. prd_uc1-milestone-checklist.
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


def publish_prd(tool_context: ToolContext, name: str, markdown: str) -> dict:
    """Simpan PRD ke sesi lalu terbitkan sebagai Google Doc.

    Panggil lagi dengan nama yang sama untuk memperbarui Doc yang sama.

    Args:
        name: Nama dokumen, sama dengan yang dipakai di save_document.
        markdown: Isi PRD lengkap (markdown, tabel didukung).
    """
    try:
        saved = save_document(tool_context, name, markdown)
        if "error" in saved:
            return saved
        folder = get_settings().prd_folder_id
        if not folder:
            return {
                "status": "unavailable",
                "reason": "LOGISLY_PRD_FOLDER_ID belum diisi",
                "saved": True,
            }
        gdocs = dict(tool_context.state.get(GDOCS_KEY) or {})
        prev = gdocs.get(name) or {}
        file_id = prev.get("file_id")
        try:
            try:
                res = gdoc.publish(f"PRD - {name}", markdown, folder, file_id=file_id)
            except Exception as exc:
                if not (file_id and getattr(exc, "status", None) == 404):
                    raise
                # The stored Doc was deleted or trashed: recreate once and remember the new id.
                file_id = None
                res = gdoc.publish(f"PRD - {name}", markdown, folder)
        except Exception as exc:
            status = getattr(exc, "status", "")
            reason = f"{type(exc).__name__}: {status}" if status else type(exc).__name__
            return {"status": "unavailable", "reason": reason, "saved": True}
        gdocs[name] = {"file_id": res["file_id"], "url": res["url"]}
        tool_context.state[GDOCS_KEY] = gdocs  # re-assign: ADK State persists only on key assignment
        return {"status": "published", "url": res["url"], "updated": bool(file_id)}
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__, "saved": True}


def create_issue(
    tool_context: ToolContext,
    issue_type: str,
    summary: str,
    description: str,
    acceptance: list[str],
    components: list[str],
    labels: list[str] | None = None,
    story_points: int = 0,
    sprint: str = "",
    assignee: str = "",
    epic: str = "",
) -> dict:
    """Buat issue Jira baru. Panggil hanya setelah pengguna menyetujui usulan.

    Args:
        issue_type: Epic, Story, Task atau Bug.
        summary: Untuk Story format '[Driver App|BE|FE|Ops Console] As <role>, I want ... so that ...'.
        description: Latar belakang dan konteks issue.
        acceptance: Daftar kriteria penerimaan.
        components: Component Jira, mis. ["Driver App"]. Satu component per story.
        labels: Label bebas, mis. ["uc1-milestone", "pilot"].
        story_points: 1, 2, 3, 5 atau 8. Wajib untuk Story. 0 berarti tidak diisi.
        sprint: Nama sprint, mis. 'Sprint 26.23'. Kosong bila belum direncanakan.
        assignee: Id orang di roster, mis. 'rizky-r'.
        epic: Kunci Epic induk, mis. LOG-3420.
    """
    try:
        _check_assignee(assignee)
        return _jira(tool_context).create(
            issue_type,
            summary,
            description,
            list(acceptance or []),
            list(components or []),
            list(labels or []),
            story_points or None,
            sprint or None,
            assignee or None,
            epic or None,
        )
    except Exception as exc:
        return _err("Gagal membuat issue", exc)


def update_issue(
    tool_context: ToolContext,
    key: str,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
    sprint: str = "",
    assignee: str = "",
    story_points: int = 0,
    description_append: str = "",
    acceptance_append: list[str] | None = None,
    add_links: list[dict] | None = None,
) -> dict:
    """Ubah issue yang ada. Panggil hanya setelah pengguna menyetujui perubahan itu.

    Args:
        key: Kunci issue, mis. LOG-3412.
        add_labels: Label yang ditambahkan.
        remove_labels: Label yang dilepas.
        sprint: Sprint baru. Kosong berarti tidak diubah.
        assignee: Id penanggung jawab baru. Kosong berarti tidak diubah.
        story_points: Story point baru (1, 2, 3, 5, 8). 0 berarti tidak diubah.
        description_append: Teks yang ditambahkan di akhir description.
        acceptance_append: Kriteria penerimaan yang ditambahkan.
        add_links: Link baru, tiap elemen {"type": ..., "key": ...}; dibaca "issue ini <type> <key>".
    """
    try:
        _check_assignee(assignee)
        return _jira(tool_context).update(
            key,
            add_labels=list(add_labels or []),
            remove_labels=list(remove_labels or []),
            sprint=sprint or None,
            assignee=assignee or None,
            story_points=story_points or None,
            description_append=description_append,
            acceptance_append=list(acceptance_append or []),
            add_links=list(add_links or []),
        )
    except Exception as exc:
        return _err("Gagal mengubah issue", exc)


def comment_issue(tool_context: ToolContext, key: str, body: str) -> dict:
    """Tambah komentar pada issue. Panggil hanya setelah pengguna menyetujui.

    Args:
        key: Kunci issue, mis. LOG-3398.
        body: Isi komentar.
    """
    try:
        return _jira(tool_context).comment(key, body)
    except Exception as exc:
        return _err("Gagal menambah komentar", exc)


def transition_issue(
    tool_context: ToolContext,
    key: str,
    status: str,
    resolution: str = "",
    comment: str = "",
    duplicate_of: str = "",
) -> dict:
    """Pindahkan status issue. Panggil hanya setelah pengguna menyetujui perpindahan itu.

    Args:
        key: Kunci issue, mis. LOG-3417.
        status: To Do, In Progress, Done atau Won't Do.
        resolution: Done, Duplicate atau Won't Do. Kosong berarti sesuai status.
        comment: Komentar, mis. rujukan ke issue pengganti.
        duplicate_of: Kunci issue yang diduplikasi. Menambah link 'duplicates' dan resolution Duplicate.
    """
    try:
        return _jira(tool_context).transition(key, status, resolution, comment, duplicate_of)
    except Exception as exc:
        return _err("Gagal memindahkan status issue", exc)


def _check_item(kind: str, args: Any) -> dict:
    if not isinstance(args, dict):
        raise TrackerError("setiap usulan harus berupa objek")
    args = dict(args)
    if kind == "create" and "issue_type" in args and "type" not in args:
        args["type"] = args.pop("issue_type")  # accept the tool's own parameter name
    required, optional = _ITEM_KEYS[kind]
    missing = sorted(required - set(args))
    unknown = sorted(set(args) - required - optional)
    if missing:
        raise TrackerError(f"kolom wajib tidak ada: {missing}")
    if unknown:
        raise TrackerError(f"kolom tidak dikenal: {unknown}")
    return args


def _resolve(ref: Any, temp: dict[str, str]) -> Any:
    """Map a temp_id to the key it was created under; real keys pass through; anything else is an error."""
    if not ref:
        return ref
    if ref in temp:
        return temp[ref]
    if isinstance(ref, str) and _ISSUE_KEY_RE.match(ref):
        return ref
    raise TrackerError(f"temp_id tidak dikenal (belum dibuat lebih dulu di usulan ini): {ref}")


def _resolve_links(links: Any, temp: dict[str, str]) -> list[dict]:
    out = []
    for ln in links or []:
        if not isinstance(ln, dict):
            raise TrackerError(f"link harus berupa objek {{type, key}}: {ln!r}")
        out.append({**ln, "key": _resolve(ln.get("key"), temp)})
    return out


def _check_assignee(assignee: Any) -> None:
    if not assignee:
        return
    ids = {p["id"] for p in _context()["roster"]["people"]}
    if assignee not in ids:
        raise TrackerError(f"assignee tidak ada di roster: {assignee!r}; pilihan {sorted(ids)}")


def _run_item(jira: MockJira, kind: str, args: dict, temp: dict[str, str]) -> dict:
    """Apply one proposal item to ``jira``. The same code runs for the dry run and the real apply.

    ``temp`` maps temp_id -> created key; a successful create registers its own temp_id in it.
    """
    args = _check_item(kind, args)
    if kind in ("create", "update"):
        _check_assignee(args.get("assignee"))
    if kind == "create":
        tid = args.get("temp_id")
        if tid and (tid in temp or _ISSUE_KEY_RE.match(str(tid))):
            raise TrackerError(f"temp_id tidak valid atau dipakai ganda: {tid}")
        links = _resolve_links(args.get("links"), temp)
        out = jira.create(
            args["type"],
            args["summary"],
            args.get("description") or "",
            list(args.get("acceptance") or []),
            list(args["components"] or []),
            list(args.get("labels") or []),
            args.get("story_points") or None,
            args.get("sprint") or None,
            args.get("assignee") or None,
            _resolve(args.get("epic"), temp) or None,
            links,
        )
        if tid:
            temp[tid] = out["key"]
        return out
    if kind == "update":
        links = _resolve_links(args.get("add_links"), temp)
        out = jira.update(
            args["key"],
            add_labels=list(args.get("add_labels") or []),
            remove_labels=list(args.get("remove_labels") or []),
            sprint=args.get("sprint") or None,
            assignee=args.get("assignee") or None,
            story_points=args.get("story_points") or None,
            description_append=args.get("description_append") or "",
            acceptance_append=list(args.get("acceptance_append") or []),
            add_links=links,
        )
        if args.get("comment"):
            jira.comment(args["key"], args["comment"])
        return out
    return jira.transition(
        args["key"],
        args["status"],
        args.get("resolution") or "",
        args.get("comment") or "",
        _resolve(args.get("duplicate_of"), temp) or "",
    )


_SPLIT_WARNING = (
    "summary menyebut beberapa kemampuan sekaligus; pecah menjadi story terpisah bila tiap kemampuan "
    "bisa dikerjakan dan diuji sendiri"
)


def _combined(args: dict) -> bool:
    """A Story whose capability ('I want ...' up to 'so that') lists several things."""
    if args.get("type") != "Story":
        return False
    t = str(args.get("summary") or "").lower()
    m = re.search(r"(?:i want|saya ingin)\s+(.*?)(?:\s+so that\b|\s+agar\b|$)", t)
    cap = m.group(1) if m else t
    joins = cap.count(" and ") + cap.count(" dan ")
    # A bare comma enumerating values ("gps, app or manual") is fine; commas plus "and"/"dan" are not.
    return joins >= 2 or ("," in cap and joins >= 1)


def _check_estimate(args: dict) -> None:
    """Definition of Ready: every Story carries story points 1|2|3|5|8; above 8 it must be split."""
    if args.get("type") != "Story":
        return
    sp = args.get("story_points")
    if sp is None or sp == 0:
        raise TrackerError("Story wajib punya story point 1|2|3|5|8")
    if isinstance(sp, int) and sp > 8:
        raise TrackerError(f"story point {sp} terlalu besar (maks 8); pecah story")


def _label(kind: str, args: Any) -> str:
    if not isinstance(args, dict):
        return kind
    if kind == "create":
        return f"buat {args.get('type', args.get('issue_type', '?'))}: {args.get('summary', '?')}"
    if kind == "update":
        return f"ubah {args.get('key', '?')}"
    return f"pindahkan {args.get('key', '?')} ke {args.get('status', '?')}"


def propose_changes(
    tool_context: ToolContext,
    creates: list[dict],
    updates: list[dict],
    transitions: list[dict],
) -> dict:
    """Validasi seluruh usulan perubahan backlog TANPA menulis apa pun, lalu simpan sebagai usulan P1..Pn.

    Panggil sebelum menyajikan tabel usulan. Bila ada item berstatus error, atau item dengan
    peringatan (summary menggabungkan beberapa kemampuan), perbaiki lalu panggil lagi dengan seluruh
    usulan. Usulan baru menggantikan usulan sebelumnya.

    Args:
        creates: Issue baru. Tiap elemen: type (Epic, Story, Task, Bug), summary, components,
            dan opsional description, acceptance (daftar), labels, story_points (wajib untuk Story,
            1|2|3|5|8), sprint, assignee (id roster), epic, links ([{type, key}]). Beri temp_id
            (mis. "E1") pada Epic baru; story di usulan yang sama boleh mengisi epic dengan temp_id itu.
        updates: Perubahan issue yang ada. Tiap elemen: key (mis. LOG-3412), dan opsional add_labels,
            remove_labels, sprint, assignee, story_points, description_append, acceptance_append,
            add_links, comment.
        transitions: Perpindahan status. Tiap elemen: key, status (To Do, In Progress, Done, Won't Do),
            dan opsional resolution, comment, duplicate_of (kunci issue yang diduplikasi).
    """
    try:
        sim = {JIRA_KEY: copy.deepcopy(_jira(tool_context).state[JIRA_KEY])}
        jira = _jira_on(sim)
        temp: dict[str, str] = {}
        items: list[dict] = []
        report: list[dict] = []
        batches = (("create", creates), ("update", updates), ("transition", transitions))
        for kind, batch in batches:
            for args in batch or []:
                pid = f"P{len(items) + 1}"
                row: dict[str, Any] = {"id": pid, "kind": kind, "usulan": _label(kind, args)}
                try:
                    clean = _check_item(kind, args)
                    if kind == "create":
                        _check_estimate(clean)
                    out = _run_item(jira, kind, args, temp)
                    row["status"] = "ok"
                    if kind == "create":
                        row["predicted_key"] = out["key"]
                        if _combined(clean):
                            row["peringatan"] = _SPLIT_WARNING
                except Exception as exc:  # noqa: BLE001 - every item failure is reported, not raised
                    row["status"] = "error"
                    row["error"] = str(exc) or type(exc).__name__
                items.append({"id": pid, "kind": kind, "args": copy.deepcopy(args), "applied": False})
                report.append(row)
        tool_context.state[PROPOSAL_KEY] = {"items": items}  # re-assign: State persists on assignment
        ok = sum(1 for r in report if r["status"] == "ok")
        warnings = sum(1 for r in report if "peringatan" in r)
        return {"items": report, "ok": ok, "errors": len(report) - ok, "warnings": warnings}
    except Exception as exc:
        return {"error": f"Gagal memvalidasi usulan: {str(exc) or type(exc).__name__}"}


def apply_proposal(tool_context: ToolContext, ids: list[str] | None = None) -> dict:
    """Terapkan usulan yang disimpan propose_changes, berurutan. Panggil SEKALI setelah pengguna setuju.

    Item yang sudah diterapkan tidak diterapkan ulang. temp_id Epic diganti kunci Jira yang sebenarnya.
    Laporkan setiap error ke pengguna.

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
        jira = _jira(tool_context)
        # temp_ids of items applied in an earlier call keep resolving to their real keys.
        temp: dict[str, str] = {}
        for item in items:
            tid = item["args"].get("temp_id") if isinstance(item["args"], dict) else None
            if item["applied"] and tid and (item.get("result") or {}).get("key"):
                temp[tid] = item["result"]["key"]
        results: list[dict] = []
        errors: list[dict] = []
        applied = failed = already = 0
        for item in items:
            if wanted and item["id"] not in wanted:
                continue
            if item["applied"]:
                already += 1
                results.append({"id": item["id"], "status": "already_applied", **(item.get("result") or {})})
                continue
            try:
                out = _run_item(jira, item["kind"], item["args"], temp)
            except Exception as exc:  # noqa: BLE001 - every item failure is reported, not raised
                failed += 1
                err = {"id": item["id"], "status": "error", "error": str(exc) or type(exc).__name__}
                results.append(err)
                errors.append(err)
                continue
            item["applied"] = True
            item["result"] = {"key": out["key"]}
            applied += 1
            results.append({"id": item["id"], "status": "applied", "key": out["key"]})
            # Persist after each item so a crash mid-way never re-applies finished items.
            tool_context.state[PROPOSAL_KEY] = {"items": copy.deepcopy(items)}
        for pid in wanted:
            if pid not in known:
                failed += 1
                err = {"id": pid, "status": "error", "error": f"usulan {pid} tidak ada"}
                results.append(err)
                errors.append(err)
        tool_context.state[PROPOSAL_KEY] = {"items": items}
        return {
            "applied": applied,
            "failed": failed,
            "already_applied": already,
            "errors": errors,
            "results": results,
        }
    except Exception as exc:
        return {"error": f"Gagal menerapkan usulan: {str(exc) or type(exc).__name__}"}
