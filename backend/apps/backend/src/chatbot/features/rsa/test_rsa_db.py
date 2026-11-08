import pytest
from sqlalchemy import select, text

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


@pytest.mark.asyncio
async def test_init_adds_customer_phone_to_existing_table(tmp_path):
    """A table created before customer_phone existed gains it on startup and
    keeps its rows; running init twice is harmless."""
    engine = build_engine(f"sqlite:///{tmp_path}/old.db")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE rsa_incidents (id TEXT PRIMARY KEY, incident_date TEXT NOT NULL,"
            " vehicle_no TEXT NOT NULL, vehicle_model TEXT, cause TEXT NOT NULL,"
            " purchased_from TEXT, breakdown_location TEXT, arrived_location TEXT,"
            " customer_called_in_time TIMESTAMP, towing_assigned_time TIMESTAMP,"
            " time_arrived_breakdown_area TIMESTAMP, time_arrived_outlet TIMESTAMP,"
            " total_km INTEGER, late_reason TEXT, remarks TEXT, created_by TEXT,"
            " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,"
            " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        ))
        await conn.execute(text(
            "INSERT INTO rsa_incidents (id, incident_date, vehicle_no, cause)"
            " VALUES ('old', '2026-01-01', 'OLD1', 'x')"
        ))
    await init_rsa_db(engine)
    await init_rsa_db(engine)
    async with build_session_maker(engine)() as session:
        row = (await session.execute(select(RsaIncident))).scalar_one()
        assert row.id == "old" and row.customer_phone is None
