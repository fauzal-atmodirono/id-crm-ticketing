import pytest

from chatbot.features.chat.sla_policy_db import (
    build_engine,
    build_session_maker,
    init_sla_policy_db,
)
from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository


@pytest.fixture
async def repo(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/sla_policy.db")
    await init_sla_policy_db(engine)
    return SlaPolicyRepository(build_session_maker(engine))


@pytest.mark.asyncio
async def test_get_tenant_default_absent_returns_none(repo):
    assert await repo.get_tenant_default() is None


@pytest.mark.asyncio
async def test_upsert_and_get_tenant_default(repo):
    await repo.upsert_tenant_default(response_hours=4.0, pic_whatsapp="+6281234567890")
    values = await repo.get_tenant_default()
    assert values.response_hours == 4.0
    assert values.pic_whatsapp == "+6281234567890"
    assert values.resolution_hours is None


@pytest.mark.asyncio
async def test_upsert_tenant_default_twice_updates_same_row(repo):
    await repo.upsert_tenant_default(response_hours=4.0)
    await repo.upsert_tenant_default(response_hours=8.0)
    values = await repo.get_tenant_default()
    assert values.response_hours == 8.0


@pytest.mark.asyncio
async def test_get_for_inbox_absent_returns_none(repo):
    assert await repo.get_for_inbox(42) is None


@pytest.mark.asyncio
async def test_upsert_and_get_for_inbox(repo):
    await repo.upsert_for_inbox(42, response_hours=2.0)
    values = await repo.get_for_inbox(42)
    assert values.response_hours == 2.0


@pytest.mark.asyncio
async def test_resolve_with_no_rows_returns_none(repo):
    assert await repo.resolve(42) is None
    assert await repo.resolve(None) is None


@pytest.mark.asyncio
async def test_resolve_falls_back_to_tenant_default_when_no_inbox_row(repo):
    await repo.upsert_tenant_default(response_hours=4.0, resolution_hours=24.0)
    resolved = await repo.resolve(42)
    assert resolved.response_hours == 4.0
    assert resolved.resolution_hours == 24.0


@pytest.mark.asyncio
async def test_resolve_inbox_row_overrides_tenant_default_field_by_field(repo):
    await repo.upsert_tenant_default(response_hours=4.0, resolution_hours=24.0)
    await repo.upsert_for_inbox(42, response_hours=1.0)  # only overrides response_hours
    resolved = await repo.resolve(42)
    assert resolved.response_hours == 1.0
    assert resolved.resolution_hours == 24.0  # inherited from tenant default


@pytest.mark.asyncio
async def test_resolve_inbox_only_no_tenant_default(repo):
    await repo.upsert_for_inbox(42, engine_enabled=False)
    resolved = await repo.resolve(42)
    assert resolved.engine_enabled is False
    assert resolved.response_hours is None
