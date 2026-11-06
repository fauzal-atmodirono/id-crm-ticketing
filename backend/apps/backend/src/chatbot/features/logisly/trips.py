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
        "milestone": "Dalam perjalanan (Tol Cipali)",
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
        "milestone": "Menuju titik muat",
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
        "milestone": "Tiba di tujuan, menunggu bongkar",
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


def describe(trip: dict[str, Any]) -> str:
    """Render a trip for the model: plain lines, no internal field names."""
    lines = [
        f"Order: {trip['order_id']}",
        f"Driver: {trip['driver_name']} ({trip['plate']}, {trip['truck_type']})",
        f"Rute: {trip['origin']} -> {trip['destination']}",
        f"Penerima: {trip['consignee']}",
        f"Muatan: {trip['cargo']}",
        f"Posisi terakhir: {trip['location']} ({trip['milestone']})",
        f"ETA: {trip['eta']}",
        f"Status: {trip['status']}",
    ]
    if trip["events"]:
        last = trip["events"][-1]
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
        milestone=trip["milestone"],
        location=trip["location"],
        eta=trip["eta"],
        origin=trip["origin"],
        destination=trip["destination"],
    )


# session id ("crm-<chatwoot conversation id>") -> what the agent opened it about.
ACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}


def register_session(session_id: str, order_id: str, anomaly: str, opening: str) -> None:
    ACTIVE_SESSIONS[session_id] = {"order_id": order_id, "anomaly": anomaly, "opening": opening}


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
    return (
        "## This conversation\n"
        "You started this conversation because the tracking system raised an alert "
        f"({ANOMALIES[active['anomaly']]['label']}). You already sent the driver this "
        f'WhatsApp message:\n"{active["opening"]}"\n'
        "The driver's messages are replies to it. Do not ask for the order number.\n\n"
        f"Trip:\n{describe(trip)}\n"
    )
