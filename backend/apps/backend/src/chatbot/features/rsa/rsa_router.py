"""HTTP surface for RSA incidents — CRUD + aggregate + CSV export.

Auth mirrors kb_knowledge_router.py's _authorize (x-api-key vs
faq_admin_api_key / proton_backend_key). Manual staff data entry: no
background tasks, no dispatch-system integration.
"""

from __future__ import annotations

import csv
import hmac
import io
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel


class _IncidentRequest(BaseModel):
    incident_date: str
    vehicle_no: str
    cause: str
    vehicle_model: str | None = None
    purchased_from: str | None = None
    breakdown_location: str | None = None
    arrived_location: str | None = None
    customer_called_in_time: datetime | None = None
    towing_assigned_time: datetime | None = None
    time_arrived_breakdown_area: datetime | None = None
    time_arrived_outlet: datetime | None = None
    total_km: int | None = None
    late_reason: str | None = None
    remarks: str | None = None
    created_by: str | None = None


class _IncidentUpdateRequest(BaseModel):
    incident_date: str | None = None
    vehicle_no: str | None = None
    cause: str | None = None
    vehicle_model: str | None = None
    purchased_from: str | None = None
    breakdown_location: str | None = None
    arrived_location: str | None = None
    customer_called_in_time: datetime | None = None
    towing_assigned_time: datetime | None = None
    time_arrived_breakdown_area: datetime | None = None
    time_arrived_outlet: datetime | None = None
    total_km: int | None = None
    late_reason: str | None = None
    remarks: str | None = None


def build_rsa_router(repo, settings) -> APIRouter:
    router = APIRouter()

    def _authorize(x_api_key: str | None) -> None:
        if x_api_key is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        supplied = x_api_key.encode("utf-8")
        for key in (settings.faq_admin_api_key, settings.proton_backend_key):
            if key and hmac.compare_digest(supplied, key.encode("utf-8")):
                return
        raise HTTPException(status_code=401, detail="Unauthorized")

    def _incident_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row.id, "incident_date": row.incident_date, "vehicle_no": row.vehicle_no,
            "vehicle_model": row.vehicle_model, "cause": row.cause,
            "purchased_from": row.purchased_from, "breakdown_location": row.breakdown_location,
            "arrived_location": row.arrived_location,
            "customer_called_in_time": row.customer_called_in_time,
            "towing_assigned_time": row.towing_assigned_time,
            "time_arrived_breakdown_area": row.time_arrived_breakdown_area,
            "time_arrived_outlet": row.time_arrived_outlet,
            "total_km": row.total_km, "late_reason": row.late_reason,
            "remarks": row.remarks, "created_by": row.created_by,
        }

    @router.post("/rsa/incidents")
    async def create_incident(
        payload: _IncidentRequest, x_api_key: str | None = Header(default=None)
    ) -> dict[str, str]:
        _authorize(x_api_key)
        incident_id = await repo.create_incident(**payload.model_dump())
        return {"id": incident_id, "status": "created"}

    @router.get("/rsa/incidents")
    async def list_incidents(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        rows = await repo.list_incidents()
        return {"incidents": [_incident_dict(r) for r in rows]}

    @router.get("/rsa/incidents/aggregate")
    async def aggregate(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        _authorize(x_api_key)
        agg = await repo.aggregate()
        return asdict(agg)

    @router.get("/rsa/incidents/export")
    async def export_csv(
        format: str = "csv", x_api_key: str | None = Header(default=None)
    ) -> Response:
        _authorize(x_api_key)
        if format != "csv":
            raise HTTPException(status_code=400, detail="format must be csv")
        rows = await repo.list_incidents()
        buf = io.StringIO()
        writer = csv.writer(buf)
        if rows:
            fieldnames = list(_incident_dict(rows[0]).keys())
            writer.writerow(fieldnames)
            for row in rows:
                writer.writerow([_incident_dict(row).get(f) for f in fieldnames])
        else:
            writer.writerow(["(no data)"])
        return Response(
            content=buf.getvalue().encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=rsa-incidents.csv"},
        )

    @router.get("/rsa/incidents/{incident_id}")
    async def get_incident(
        incident_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _authorize(x_api_key)
        row = await repo.get_incident(incident_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _incident_dict(row)

    @router.patch("/rsa/incidents/{incident_id}")
    async def update_incident(
        incident_id: str, payload: _IncidentUpdateRequest,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, str]:
        _authorize(x_api_key)
        fields = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not await repo.update_incident(incident_id, **fields):
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": incident_id, "status": "updated"}

    @router.delete("/rsa/incidents/{incident_id}")
    async def delete_incident(
        incident_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, str]:
        _authorize(x_api_key)
        if not await repo.delete_incident(incident_id):
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": incident_id, "status": "deleted"}

    return router
