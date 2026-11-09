import pytest
from sqlalchemy import select, text
from structlog.testing import capture_logs

from chatbot.features.rsa import rsa_db
from chatbot.features.rsa.rsa_db import RsaIncident, build_engine, build_session_maker, init_rsa_db
from chatbot.features.rsa.rsa_repository import PgRsaRepository


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


@pytest.mark.asyncio
async def test_failed_alter_is_fail_open_and_rsa_still_works(tmp_path, monkeypatch):
    """ALTER raising must not crash init; a warning names table/column and
    list/create/update keep working with customer_phone omitted."""
    engine = build_engine(f"sqlite:///{tmp_path}/legacy.db")
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
    monkeypatch.setattr(rsa_db, "_ALTER_SQLITE", "ALTER TABLE nope ADD COLUMN boom")
    with capture_logs() as logs:
        await init_rsa_db(engine)
    warn = [e for e in logs if e["event"] == "rsa_customer_phone_migration_failed"]
    assert warn and warn[0]["table"] == "rsa_incidents" and warn[0]["column"] == "customer_phone"

    repo = PgRsaRepository(build_session_maker(engine))
    rows = await repo.list_incidents()
    assert [r.id for r in rows] == ["old"] and rows[0].customer_phone is None
    new_id = await repo.create_incident(
        incident_date="2026-02-01", vehicle_no="NEW1", cause="y", customer_phone="0123456789"
    )
    assert (await repo.get_incident(new_id)).customer_phone is None
    assert await repo.update_incident(new_id, remarks="ok", customer_phone="1") is True
    assert len(await repo.list_incidents()) == 2
    assert (await repo.aggregate()).by_cause
    assert await repo.delete_incident(new_id) is True
