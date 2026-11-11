"""Milestone checklists for the Logisly driver follow-up (UC1).

A trip moves through six milestones and each one has a short checklist taken
from Logisly's SOP sheet. Some items the system can answer by itself (a fact
on the trip: plate matches, DO in the driver app...), some need a photo and
some need the driver to say something. The agent should ask the driver only
for what is still open, so this module keeps that bookkeeping in one pure,
easily tested place: which items apply to a trip, which are open, what a
"not OK" means (a ticket for the owning team, or a fix the driver can do
themselves) and when the driver must not be disturbed.

Trips are plain dicts; checklist state lives in `trip["checklists"]`
(milestone -> item id -> record) so it persists with the trip. A system item
whose fact is unknown stays pending and is asked like a driver item, so a
missing integration degrades to a question instead of a wrong answer.

Tickets are only queued here (`PENDING_TICKETS`, module-global, drained by a
later task); a not-OK is queued once per item so repeated reports do not
create duplicates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal

import structlog

logger = structlog.get_logger(__name__)

WIB = timezone(timedelta(hours=7))

MILESTONES: list[tuple[str, str]] = [
    ("assigned", "Pre check driver"),
    ("to_pickup", "Menuju lokasi muat"),
    ("at_pickup", "Di lokasi muat"),
    ("in_transit", "Dalam pengiriman"),
    ("at_drop", "Di lokasi bongkar"),
    ("unloaded", "Bongkar selesai"),
]
_LABELS = dict(MILESTONES)
_QUIET_FROM_HOUR = 22
_QUIET_UNTIL_HOUR = 5
_DAYPARTS = ((11, "pagi"), (15, "siang"), (18, "sore"))


@dataclass(frozen=True)
class Item:
    id: str
    label: str
    source: Literal["system", "photo", "driver"]
    ask: str
    team: str | None
    fact: str | None
    shopee_only: bool = False


def _sys(id: str, label: str, ask: str, team: str | None, fact: str, shopee: bool = False) -> Item:
    return Item(id, label, "system", ask, team, fact, shopee)


def _photo(id: str, label: str, ask: str, team: str | None) -> Item:
    return Item(id, label, "photo", ask, team, None)


def _drv(id: str, label: str, ask: str, team: str | None) -> Item:
    return Item(id, label, "driver", ask, team, None)


CHECKLISTS: dict[str, list[Item]] = {
    "assigned": [
        _sys(
            "driver_plate",
            "Nopol sesuai",
            "Nopol unit yang dibawa berapa, Pak?",
            "alloc",
            "plate_matches",
        ),
        _sys(
            "locations",
            "Lokasi muat dan bongkar di aplikasi",
            "Lokasi muat dan bongkar sudah muncul di aplikasi driver, Pak?",
            "alloc",
            "locations_in_app",
        ),
        _sys(
            "shopee_id",
            "ID driver Shopee aktif",
            "ID driver Shopee Bapak sudah aktif, Pak?",
            "alloc",
            "shopee_driver_id_active",
            shopee=True,
        ),
        _drv(
            "unit_empty",
            "Unit kosong",
            "Unit sudah kosong, Pak? Kalau belum, tolong info titik bongkar, jam selesai, dan muatannya.",
            None,
        ),
    ],
    "to_pickup": [],
    "at_pickup": [
        _photo(
            "unit_photo",
            "Foto unit di lokasi muat",
            "Boleh kirim foto unit di lokasi muat, Pak? Pastikan nopol dan jam kelihatan.",
            "alloc",
        ),
        _sys(
            "onsite",
            "Status onsite Shopee",
            "Status onsite di aplikasi Shopee sudah diklik, Pak?",
            None,
            "shopee_onsite",
            shopee=True,
        ),
        _drv("load_eta", "Jam mulai muat", "Kira-kira jam berapa mulai muat, Pak?", "warehouse"),
        _sys(
            "driver_app",
            "Aplikasi driver aktif",
            "Aplikasi driver sudah aktif di HP Bapak?",
            None,
            "driver_app_active",
        ),
        _sys(
            "do_in_app",
            "DO di aplikasi driver",
            "DO sudah muncul di aplikasi driver, Pak?",
            "alloc",
            "do_in_app",
        ),
    ],
    "in_transit": [
        _photo("surat_jalan", "Foto surat jalan", "Boleh kirim foto surat jalan, Pak?", "alloc"),
        _sys(
            "drop_address",
            "Alamat bongkar sesuai PF",
            "Alamat bongkar di surat jalan sudah sesuai, Pak?",
            "alloc",
            "drop_address_matches_pf",
        ),
        _drv(
            "eta_drop",
            "Estimasi tiba di lokasi bongkar",
            "Perkiraan tiba di lokasi bongkar tanggal dan jam berapa, Pak?",
            None,
        ),
        _sys(
            "trip_ok",
            "Perjalanan sesuai RAD",
            "Perjalanan sudah sesuai jadwal, Pak?",
            "ct",
            "eta_within_rad",
        ),
        _drv("kendala", "Kendala di jalan", "Ada kendala di perjalanan, Pak?", "ct"),
    ],
    "at_drop": [
        _photo(
            "unit_photo",
            "Foto unit di lokasi bongkar",
            "Boleh kirim foto unit di lokasi bongkar, Pak? Pastikan nopol dan jam kelihatan.",
            "alloc",
        ),
        _sys(
            "unloading_registered",
            "Bongkar tercatat di Shopee",
            "Kedatangan di lokasi bongkar sudah diklik di aplikasi Shopee, Pak?",
            None,
            "unloading_registered",
            shopee=True,
        ),
        _drv(
            "unload_eta",
            "Jam mulai bongkar",
            "Kira-kira jam berapa mulai bongkar, Pak?",
            "warehouse",
        ),
    ],
    "unloaded": [
        _drv("unload_done", "Bongkar selesai", "Bongkar sudah selesai, Pak?", None),
        _photo(
            "pod",
            "Foto POD bertanda tangan",
            "Boleh kirim foto POD atau surat jalan yang sudah ditandatangani, Pak?",
            "mm",
        ),
        _sys(
            "pod_uploaded",
            "POD diunggah di aplikasi driver",
            "POD sudah diunggah di aplikasi driver, Pak?",
            None,
            "pod_uploaded",
        ),
    ],
}

PENDING_TICKETS: list[dict[str, Any]] = []


def checklist_for(trip: dict, milestone: str) -> list[Item]:
    shopee = trip.get("shipper_kind") == "shopee"
    return [i for i in CHECKLISTS.get(milestone, []) if shopee or not i.shopee_only]


def _state(trip: dict, milestone: str, item_id: str) -> str:
    rec = trip.get("checklists", {}).get(milestone, {}).get(item_id)
    return rec["status"] if rec else "pending"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def apply_system_checks(trip: dict, milestone: str) -> list[Item]:
    """Fill system items from trip facts; return the ones that are not OK."""
    facts = trip.get("facts", {})
    bad: list[Item] = []
    for item in checklist_for(trip, milestone):
        if item.source != "system" or item.fact not in facts:
            continue
        ok = bool(facts[item.fact])
        record_item(trip, milestone, item.id, ok, "", "system", None)
        if not ok:
            bad.append(item)
    return bad


def open_items(trip: dict, milestone: str) -> list[Item]:
    return [i for i in checklist_for(trip, milestone) if _state(trip, milestone, i.id) == "pending"]


def record_item(
    trip: dict,
    milestone: str,
    item_id: str,
    ok: bool,
    value: str,
    source: str,
    session_id: str | None,
) -> str:
    item = next((i for i in CHECKLISTS.get(milestone, []) if i.id == item_id), None)
    if item is None:
        return f"ERROR: unknown item {item_id!r} for milestone {milestone!r}"
    previous = _state(trip, milestone, item_id)
    status = "ok" if ok else "not_ok"
    trip.setdefault("checklists", {}).setdefault(milestone, {})[item_id] = {
        "status": status,
        "value": value,
        "source": source,
        "at": _now(),
    }
    if status == "not_ok" and previous != "not_ok" and item.team is not None:
        PENDING_TICKETS.append(
            {
                "session_id": session_id,
                "order_id": trip.get("order_id"),
                "milestone": milestone,
                "item_id": item_id,
                "team": item.team,
                "value": value,
            }
        )
        logger.info(
            "logisly_ticket_queued", order_id=trip.get("order_id"), item=item_id, team=item.team
        )
    return status


def is_complete(trip: dict, milestone: str) -> bool:
    return not open_items(trip, milestone)


def driver_fixes(trip: dict, milestone: str) -> list[str]:
    """Bahasa lines for not-OK system items the driver can fix themselves."""
    lines: list[str] = []
    for item in checklist_for(trip, milestone):
        if _state(trip, milestone, item.id) != "not_ok":
            continue
        if item.id == "driver_app":
            lines.append(
                f"Mohon install aplikasi driver lewat link ini, Pak: {trip.get('driver_app_link', '')}"
            )
        elif item.id == "onsite":
            lines.append('Mohon klik status "onsite" di aplikasi Shopee, Pak.')
        elif item.id == "unloading_registered":
            lines.append('Mohon klik status "arrive" di aplikasi Shopee, Pak.')
        elif item.id == "pod_uploaded":
            lines.append("Mohon unggah POD di aplikasi driver, Pak.")
    return lines


def opening_message(trip: dict, milestone: str) -> str | None:
    fixes = driver_fixes(trip, milestone)
    asks = [i.ask for i in open_items(trip, milestone)[:2]]
    if not fixes and not asks:
        return None
    # Seed names already carry the honorific ("Pak Agus"); don't say "Pak Pak".
    name = re.sub(r"(?i)^pak\s+", "", trip.get("driver_name", ""))
    greeting = (
        f"Selamat {_greeting_time()} Pak {name}, "
        f"saya dari Logisly untuk order {trip.get('order_id', '')} ({_LABELS.get(milestone, milestone)})."
    )
    return "\n".join([greeting, *fixes, *asks])


def _greeting_time() -> str:
    h = datetime.now(WIB).hour
    for limit, word in _DAYPARTS:
        if h < limit:
            return word
    return "malam"


def is_quiet(now: datetime) -> bool:
    """22:00 <= WIB time < 05:00: no proactive messages to drivers."""
    t = now.astimezone(WIB)
    return t.hour >= _QUIET_FROM_HOUR or t.hour < _QUIET_UNTIL_HOUR
