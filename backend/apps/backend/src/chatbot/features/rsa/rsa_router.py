"""HTTP surface for RSA incidents — CRUD + aggregate + CSV export.

Every route sits behind `require_permission("sla.manage")`. With RBAC on that
demands a validated Chatwoot session holding the permission (the shared
x-api-key alone is refused, since the browser ships that key to every agent);
with RBAC off it falls back to the shared-secret check, as before. Manual
staff data entry: no background tasks, no dispatch-system integration.

Vehicle model is required on this operator API (tracker RSA-001): a create
without a non-blank `vehicle_model`, or a PATCH that blanks it, is a 422 naming
the field. The rule lives here, not in the repository, so rows saved before it
(and the demo seeder, which writes through the repository) still load, list
and accept edits that leave the model alone.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from chatbot.features.authz.data_scope import (
    Scope,
    apply_scope,
    build_scope_dependency,
    require_unrestricted,
)
from chatbot.features.authz.deps import require_permission
from chatbot.features.metrics.export import spreadsheet_safe
from chatbot.features.privacy.access import PiiPolicy, build_pii_dependency


_VEHICLE_MODEL_REQUIRED = "vehicle_model is required"


def _require_vehicle_model(value: str | None) -> str:
    model = (value or "").strip()
    if not model:
        raise HTTPException(status_code=422, detail=_VEHICLE_MODEL_REQUIRED)
    return model


class _IncidentRequest(BaseModel):
    incident_date: str
    vehicle_no: str
    cause: str
    vehicle_model: str | None = None
    customer_phone: str | None = None
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
    customer_phone: str | None = None
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


def build_rsa_router(repo, settings, authz_repo=None, validator=None) -> APIRouter:
    router = APIRouter()
    guard = Depends(
        require_permission("sla.manage", repo=authz_repo, validator=validator, settings=settings)
    )

    # Wave 2 Task 28: an incident carries one scope dimension, the dealer it
    # was purchased from. Under a dealer scope only that dealer's incidents
    # are visible; under an inbox/team/department scope none are (an incident
    # names no inbox -- data_scope.py rule 5). Out-of-scope ids read as 404.
    data_scope = build_scope_dependency(settings, repo=authz_repo, validator=validator)

    # Wave 2 Task 29: rows (list, get, CSV export) are masked for a caller
    # without pii.view; a write carrying a masked value is refused so a masked
    # page can never overwrite the real phone number. Flag off: no-ops.
    pii_policy = build_pii_dependency(settings, repo=authz_repo, validator=validator)

    def _dims(row: Any) -> dict[str, Any]:
        return {"dealer_slugs": getattr(row, "purchased_from", None)}

    def _visible(row: Any, scope: Scope) -> bool:
        return row is not None and (scope.unrestricted or scope.permits(_dims(row)))

    def _require_dealer_in_scope(purchased_from: str | None, scope: Scope) -> None:
        if scope.unrestricted:
            return
        if not scope.permits({"dealer_slugs": purchased_from}):
            raise HTTPException(
                status_code=403, detail="purchased_from is outside your data scope"
            )

    def _incident_dict(row: Any) -> dict[str, Any]:
        return {
            "id": row.id,
            "incident_date": row.incident_date,
            "vehicle_no": row.vehicle_no,
            "vehicle_model": row.vehicle_model,
            "customer_phone": row.customer_phone,
            "cause": row.cause,
            "purchased_from": row.purchased_from,
            "breakdown_location": row.breakdown_location,
            "arrived_location": row.arrived_location,
            "customer_called_in_time": row.customer_called_in_time,
            "towing_assigned_time": row.towing_assigned_time,
            "time_arrived_breakdown_area": row.time_arrived_breakdown_area,
            "time_arrived_outlet": row.time_arrived_outlet,
            "total_km": row.total_km,
            "late_reason": row.late_reason,
            "remarks": row.remarks,
            "created_by": row.created_by,
        }

    @router.post("/rsa/incidents", dependencies=[guard])
    async def create_incident(
        payload: _IncidentRequest,
        scope: Scope = Depends(data_scope),
        pii: PiiPolicy = Depends(pii_policy),
    ) -> dict[str, str]:
        vehicle_model = _require_vehicle_model(payload.vehicle_model)
        _require_dealer_in_scope(payload.purchased_from, scope)
        pii.refuse_masked_write(payload.model_dump(mode="json"))
        incident_id = await repo.create_incident(
            **{**payload.model_dump(), "vehicle_model": vehicle_model}
        )
        return {"id": incident_id, "status": "created"}

    @router.get("/rsa/incidents", dependencies=[guard])
    async def list_incidents(
        scope: Scope = Depends(data_scope), pii: PiiPolicy = Depends(pii_policy)
    ) -> dict[str, Any]:
        rows = apply_scope(await repo.list_incidents(), scope, _dims)
        return {"incidents": [pii.apply(_incident_dict(r)) for r in rows]}

    @router.get("/rsa/incidents/aggregate", dependencies=[guard])
    async def aggregate(scope: Scope = Depends(data_scope)) -> dict[str, Any]:
        # Computed over every incident in the store: no way to narrow it.
        require_unrestricted(scope, "The RSA aggregate")
        agg = await repo.aggregate()
        return asdict(agg)

    @router.get("/rsa/incidents/export", dependencies=[guard])
    async def export_csv(
        format: str = "csv",
        scope: Scope = Depends(data_scope),
        pii: PiiPolicy = Depends(pii_policy),
    ) -> Response:
        if format != "csv":
            raise HTTPException(status_code=400, detail="format must be csv")
        rows = apply_scope(await repo.list_incidents(), scope, _dims)
        buf = io.StringIO()
        writer = csv.writer(buf)
        if rows:
            # customer_phone last so existing CSV consumers' column positions hold.
            fieldnames = [k for k in _incident_dict(rows[0]) if k != "customer_phone"]
            fieldnames.append("customer_phone")
            writer.writerow(fieldnames)
            for row in rows:
                record = pii.apply(_incident_dict(row))
                # Final review M1: free-text fields (cause, remarks) are
                # operator-typed; never let one run as a formula.
                writer.writerow([spreadsheet_safe(record.get(f)) for f in fieldnames])
        else:
            writer.writerow(["(no data)"])
        return Response(
            content=buf.getvalue().encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=rsa-incidents.csv"},
        )

    @router.get("/rsa/incidents/{incident_id}", dependencies=[guard])
    async def get_incident(
        incident_id: str,
        scope: Scope = Depends(data_scope),
        pii: PiiPolicy = Depends(pii_policy),
    ) -> dict[str, Any]:
        row = await repo.get_incident(incident_id)
        if not _visible(row, scope):
            raise HTTPException(status_code=404, detail="Not found")
        return pii.apply(_incident_dict(row))

    @router.patch("/rsa/incidents/{incident_id}", dependencies=[guard])
    async def update_incident(
        incident_id: str,
        payload: _IncidentUpdateRequest,
        scope: Scope = Depends(data_scope),
        pii: PiiPolicy = Depends(pii_policy),
    ) -> dict[str, str]:
        fields = {k: v for k, v in payload.model_dump().items() if v is not None}
        # Omitted/null leaves the stored model alone; a sent blank would erase it.
        if "vehicle_model" in fields:
            fields["vehicle_model"] = _require_vehicle_model(fields["vehicle_model"])
        current = None
        if not scope.unrestricted or pii.masked:
            current = await repo.get_incident(incident_id)
        if not scope.unrestricted:
            if not _visible(current, scope):
                raise HTTPException(status_code=404, detail="Not found")
            if "purchased_from" in fields:
                _require_dealer_in_scope(fields["purchased_from"], scope)
        # The RSA page re-sends every field of the row it loaded; a masked
        # caller's untouched phone/remarks come back in masked form and are
        # dropped here as unchanged (Task 29 review fix 1).
        fields = pii.reconcile_write(
            fields, _incident_dict(current) if current is not None else None
        )
        if not await repo.update_incident(incident_id, **fields):
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": incident_id, "status": "updated"}

    @router.delete("/rsa/incidents/{incident_id}", dependencies=[guard])
    async def delete_incident(
        incident_id: str, scope: Scope = Depends(data_scope)
    ) -> dict[str, str]:
        if not scope.unrestricted and not _visible(await repo.get_incident(incident_id), scope):
            raise HTTPException(status_code=404, detail="Not found")
        if not await repo.delete_incident(incident_id):
            raise HTTPException(status_code=404, detail="Not found")
        return {"id": incident_id, "status": "deleted"}

    return router
