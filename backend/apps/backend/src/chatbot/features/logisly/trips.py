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

    def update(self, order_id: str, status: str, note: str) -> dict[str, Any] | None:
        trip = self.get(order_id)
        if trip is None:
            return None
        if status in STATUSES:
            trip["status"] = status
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
        f"Posisi terakhir: {trip['milestone']}",
        f"ETA: {trip['eta']}",
        f"Status: {trip['status']}",
    ]
    if trip["events"]:
        last = trip["events"][-1]
        lines.append(f"Update terakhir dari driver: {last['note']} ({last['status']})")
    return "\n".join(lines)


TRIPS = TripStore.seeded()
