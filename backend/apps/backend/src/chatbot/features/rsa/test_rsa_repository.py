import pytest

from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository


@pytest.mark.asyncio
async def test_create_list_get_update_delete():
    repo = InMemoryRsaRepository()
    incident_id = await repo.create_incident(
        incident_date="2026-07-01", vehicle_no="VPP8636", vehicle_model="e.MAS 7",
        cause="Flat Tyre", purchased_from="Wheelcorp EV", breakdown_location="Highway PLUS",
        arrived_location="Wheelcorp EV Setia Alam", total_km=8, remarks="Water leaking",
        created_by="agent-1",
    )
    rows = await repo.list_incidents()
    assert len(rows) == 1
    assert rows[0].id == incident_id

    row = await repo.get_incident(incident_id)
    assert row is not None
    assert row.vehicle_no == "VPP8636"

    updated = await repo.update_incident(incident_id, remarks="Water leaking, resolved on-site")
    assert updated is True
    row = await repo.get_incident(incident_id)
    assert row.remarks == "Water leaking, resolved on-site"

    deleted = await repo.delete_incident(incident_id)
    assert deleted is True
    assert await repo.get_incident(incident_id) is None


@pytest.mark.asyncio
async def test_update_delete_nonexistent_returns_false():
    repo = InMemoryRsaRepository()
    assert await repo.update_incident("nope", remarks="x") is False
    assert await repo.delete_incident("nope") is False


@pytest.mark.asyncio
async def test_aggregate_by_cause_and_dealer():
    repo = InMemoryRsaRepository()
    await repo.create_incident(
        incident_date="2026-07-01", vehicle_no="A1", vehicle_model="e.MAS 7",
        cause="Flat Tyre", purchased_from="Dealer A", breakdown_location="X",
        arrived_location="Y", total_km=1, remarks="", created_by="a",
    )
    await repo.create_incident(
        incident_date="2026-07-02", vehicle_no="A2", vehicle_model="e.MAS 5",
        cause="Flat Tyre", purchased_from="Dealer A", breakdown_location="X",
        arrived_location="Y", total_km=1, remarks="", created_by="a",
    )
    await repo.create_incident(
        incident_date="2026-07-03", vehicle_no="A3", vehicle_model="e.MAS 7",
        cause="Flat Battery", purchased_from="Dealer B", breakdown_location="X",
        arrived_location="Y", total_km=1, remarks="", created_by="a",
    )
    agg = await repo.aggregate()
    by_cause = {r.cause: r.count for r in agg.by_cause}
    assert by_cause == {"Flat Tyre": 2, "Flat Battery": 1}
    by_dealer = {r.dealer: r.count for r in agg.by_dealer}
    assert by_dealer == {"Dealer A": 2, "Dealer B": 1}
