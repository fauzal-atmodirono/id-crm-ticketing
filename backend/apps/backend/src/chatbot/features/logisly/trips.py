"""Made-up Logisly trips for the driver follow-up demo.

The demo has no connection to Logisly's real tracking system yet, so the
driver agent's trip tools read and write this in-memory table instead. Every
order, plate, driver and consignee here is fictional. Status updates are kept
per process and reset on restart, which is what a rehearsable demo wants.

The shape is deliberately close to what a tracking system would hand over
(order, route, milestone, ETA, event log) so swapping in a real adapter later
means replacing `TripStore`, not the tools that call it.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from chatbot.features.logisly import milestones

_APP_LINK = "https://logisly.example/driver-app"

_SEED: list[dict[str, Any]] = [
    {
        "order_id": "LGS-24017",
        "location": "Tol Cipali KM 102",
        "driver_name": "Pak Budi Santoso",
        "plate": "B 9123 KXT",
        "truck_type": "CDD Box",
        "origin": "Gudang Cikarang, Bekasi",
        "destination": "DC Semarang, Jawa Tengah",
        "consignee": "PT Sumber Pangan Nusantara",
        "cargo": "Makanan kemasan, 420 karton",
        "shipper": "PT Sumber Pangan Nusantara (FMCG)",
        "shipper_kind": "fmcg",
        "milestone": "assigned",
        "facts": {"plate_matches": True, "locations_in_app": True},
        "do_weight": "8 ton",
        "dts_number": "DTS-240170",
        "load_map_link": "https://maps.example/muat-cikarang",
        "driver_app_link": _APP_LINK,
        "rad": "2026-11-06 08:00 WIB",
        "eta": "2026-11-06 17:00 WIB",
        "status": "on_trip",
    },
    {
        "order_id": "LGS-24021",
        "location": "Karawang Barat",
        "driver_name": "Pak Agus Wibowo",
        "plate": "D 8812 FAB",
        "truck_type": "Fuso Wingbox",
        "origin": "Pabrik Karawang",
        "destination": "Gudang Surabaya Rungkut",
        "consignee": "PT Elektronik Jaya Abadi",
        "cargo": "Peralatan elektronik, 36 palet",
        "shipper": "Shopee (gudang Karawang)",
        "shipper_kind": "shopee",
        "milestone": "to_pickup",
        "facts": {
            "plate_matches": True,
            "locations_in_app": True,
            "shopee_driver_id_active": True,
            "shopee_onsite": False,
            "driver_app_active": False,
            "do_in_app": False,
        },
        "do_weight": "15 ton",
        "dts_number": "DTS-240210",
        "load_map_link": "https://maps.example/muat-karawang",
        "driver_app_link": _APP_LINK,
        "rad": "2026-11-07 09:00 WIB",
        "eta": "2026-11-07 09:00 WIB",
        "status": "to_pickup",
    },
    {
        "order_id": "LGS-24033",
        "location": "Jl. Soekarno-Hatta, Bandung",
        "driver_name": "Pak Dedi Kurniawan",
        "plate": "B 9450 UQY",
        "truck_type": "CDE Box",
        "origin": "DC Cibitung",
        "destination": "Toko Mitra, Bandung",
        "consignee": "CV Berkah Retail",
        "cargo": "Minuman botol, 180 karton",
        "shipper": "DC Cibitung (FMCG)",
        "shipper_kind": "fmcg",
        "milestone": "in_transit",
        "facts": {"drop_address_matches_pf": True, "eta_within_rad": False},
        "do_weight": "5 ton",
        "dts_number": "DTS-240330",
        "load_map_link": "https://maps.example/muat-cibitung",
        "driver_app_link": _APP_LINK,
        "rad": "2026-11-06 13:00 WIB",
        "eta": "2026-11-06 13:00 WIB",
        "status": "arrived",
    },
]

STATUSES = ("to_pickup", "on_trip", "delayed", "issue", "arrived", "delivered")


@dataclass
class TripStore:
    trips: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def seeded(cls) -> TripStore:
        store = cls()
        for trip in copy.deepcopy(_SEED):
            trip["events"] = []
            trip["checklists"] = {}
            milestones.apply_system_checks(trip, trip["milestone"])
            store.trips[trip["order_id"]] = trip
        return store

    def get(self, order_id: str) -> dict[str, Any] | None:
        return self.trips.get(order_id.strip().upper())

    def update(self, order_id: str, status: str, note: str, eta: str = "") -> dict[str, Any] | None:
        trip = self.get(order_id)
        if trip is None:
            return None
        if status in STATUSES:
            trip["status"] = status
        if eta.strip():
            # The driver's own estimate replaces the planned ETA on the trip.
            trip["eta"] = eta.strip()
        trip["events"].append(
            {"at": datetime.now(UTC).isoformat(timespec="seconds"), "status": status, "note": note}
        )
        return trip


    def set_milestone(self, order_id: str, milestone: str, source: str) -> dict[str, Any] | None:
        trip = self.get(order_id)
        if trip is None or milestone not in dict(milestones.MILESTONES):
            return None
        trip["milestone"] = milestone
        trip["events"].append(
            {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "status": f"milestone:{milestone}",
                "note": f"{milestone_label(milestone)} ({source})",
            }
        )
        milestones.apply_system_checks(trip, milestone)
        return trip


def milestone_label(code: str) -> str:
    return dict(milestones.MILESTONES).get(code, code)


def is_system_event(event: dict[str, Any]) -> bool:
    """Tracking alerts and milestone moves, as opposed to what the driver reported."""
    return str(event["status"]).startswith(("alert:", "milestone:"))


def describe(trip: dict[str, Any]) -> str:
    """Render a trip for the model: plain lines, no internal field names."""
    lines = [
        f"Order: {trip['order_id']}",
        f"Driver: {trip['driver_name']} ({trip['plate']}, {trip['truck_type']})",
        f"Rute: {trip['origin']} -> {trip['destination']}",
        f"Penerima: {trip['consignee']}",
        f"Muatan: {trip['cargo']}",
        f"Posisi terakhir: {trip['location']}",
        f"Milestone: {milestone_label(trip['milestone'])}",
        f"ETA: {trip['eta']}",
        f"Status: {trip['status']}",
    ]
    for item in milestones.checklist_for(trip, trip["milestone"]):
        rec = trip.get("checklists", {}).get(trip["milestone"], {}).get(item.id)
        status = rec["status"] if rec else "pending"
        value = f": {rec['value']}" if rec and rec.get("value") else ""
        lines.append(f"- {item.id} ({item.source}): {status}{value}")
    reports = [e for e in trip["events"] if not is_system_event(e)]
    if reports:
        last = reports[-1]
        lines.append(f"Update terakhir dari driver: {last['note']} ({last['status']})")
    return "\n".join(lines)


TRIPS = TripStore.seeded()


# ---------------------------------------------------------------------------
# Anomalies the simulated tracking system can raise, and the conversation
# context the driver agent is given for a conversation it opened.
# ---------------------------------------------------------------------------

ANOMALIES: dict[str, dict[str, str]] = {
    "gps_stop": {
        "label": "Truk berhenti lama",
        "event": "GPS: truk berhenti lebih dari 40 menit",
        "opening": (
            "Halo {driver}, ini Logisly. Truk {plate} untuk order {order_id} terpantau "
            "berhenti sekitar 40 menit di sekitar {location}. Apakah ada kendala, Pak?"
        ),
    },
    "late_milestone": {
        "label": "Terlambat dari jadwal",
        "event": "Milestone: belum tiba sesuai ETA {eta}",
        "opening": (
            "Halo {driver}, ini Logisly. Order {order_id} ({origin} ke {destination}) "
            "belum sampai sesuai jadwal ETA {eta}. Boleh info posisi Bapak sekarang dan "
            "perkiraan tibanya, Pak?"
        ),
    },
    "no_pod": {
        "label": "Bukti serah terima belum ada",
        "event": "POD: bukti serah terima belum diterima",
        "opening": (
            "Halo {driver}, ini Logisly. Order {order_id} sudah tercatat tiba di "
            "{destination}, tapi bukti serah terima (POD) belum kami terima. Mohon kirim "
            "foto surat jalan yang sudah ditandatangani penerima ya, Pak."
        ),
    },
}


def render(template: str, trip: dict[str, Any]) -> str:
    return template.format(
        driver=trip["driver_name"],
        plate=trip["plate"],
        order_id=trip["order_id"],
        milestone=milestone_label(trip["milestone"]),
        location=trip["location"],
        eta=trip["eta"],
        origin=trip["origin"],
        destination=trip["destination"],
    )


# session id ("crm-<chatwoot conversation id>") -> what the agent opened it about.
ACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}


def register_session(
    session_id: str, order_id: str, anomaly: str, opening: str, milestone: str = ""
) -> None:
    ACTIVE_SESSIONS[session_id] = {
        "order_id": order_id,
        "anomaly": anomaly,
        "opening": opening,
        "milestone": milestone,
    }


def context_for_session(session_id: str) -> str:
    """Instruction section for a conversation the agent itself started.

    The opening WhatsApp message is posted through Chatwoot, so it never passes
    through the model; without this section the agent would not know which
    trip the driver is answering about and would ask for the order number.
    Empty for a conversation the driver started.
    """
    active = ACTIVE_SESSIONS.get(session_id)
    if not active:
        return ""
    trip = TRIPS.get(active["order_id"])
    if trip is None:
        return ""
    milestone = active.get("milestone", "")
    if milestone:
        reason = (
            f"the trip reached the milestone {milestone_label(milestone)} ({milestone}) "
            "and its checklist must be completed"
        )
    else:
        anomaly = active.get("anomaly", "")
        label = ANOMALIES.get(anomaly, {}).get("label", anomaly or "unknown")
        reason = f"the tracking system raised an alert ({label})"
    text = (
        "## This conversation\n"
        f"You started this conversation because {reason}. You already sent the driver this "
        f'WhatsApp message:\n"{active["opening"]}"\n'
        "The driver's messages are replies to it. Do not ask for the order number.\n\n"
        f"Trip:\n{describe(trip)}\n"
    )
    if milestone:
        text += _checklist_section(trip, milestone)
    return text


def _checklist_section(trip: dict[str, Any], milestone: str) -> str:
    open_ = milestones.open_items(trip, milestone)
    lines = ["\n## Checklist for this milestone"]
    if open_:
        lines.append("Still open (ask the driver only for these):")
        lines += [f"- {i.id}: {i.ask}" for i in open_]
    else:
        lines.append("Every item is already done.")
    lines += [
        "\nFacts you can give the driver if they ask:",
        f"- DO weight: {trip.get('do_weight', '-')}",
        f"- DTS number: {trip.get('dts_number', '-')}",
        f"- Load location map: {trip.get('load_map_link', '-')}",
        f"- Driver app link: {trip.get('driver_app_link', '-')}",
        "\nWhen the driver answers or sends a photo, tick the item with "
        "record_checklist_tool. Do not ask about items that are already ok.",
    ]
    return "\n".join(lines) + "\n"
