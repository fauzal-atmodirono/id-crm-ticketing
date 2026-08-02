import pytest
from sqlalchemy import select

from chatbot.features.rsa.rsa_db import RsaIncident, build_engine, build_session_maker, init_rsa_db


@pytest.mark.asyncio
async def test_init_and_insert_incident(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path}/rsa.db")
    await init_rsa_db(engine)
    session_maker = build_session_maker(engine)

    async with session_maker() as session:
        incident = RsaIncident(
            id="rsa-1", incident_date="2026-07-01", vehicle_no="VPP8636",
            vehicle_model="e.MAS 7", cause="Flat Tyre",
            purchased_from="Proton e.MAS - Wheelcorp EV (Setia Alam - SVC)",
            breakdown_location="Highway PLUS", arrived_location="Wheelcorp EV Setia Alam",
            customer_called_in_time=None, towing_assigned_time=None,
            time_arrived_breakdown_area=None, time_arrived_outlet=None,
            total_km=8, late_reason=None, remarks="Water leaking", created_by="agent-1",
        )
        session.add(incident)
        await session.commit()

    async with session_maker() as session:
        result = await session.execute(select(RsaIncident).where(RsaIncident.id == "rsa-1"))
        row = result.scalar_one()
        assert row.vehicle_no == "VPP8636"
        assert row.cause == "Flat Tyre"
