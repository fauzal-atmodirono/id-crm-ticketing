"""Repository for the SLA policy store — get/upsert tenant-default and
per-inbox rows, plus the inbox-specific -> tenant-default resolution used by
sla.py (env fallback happens one layer up, in sla.py itself, since this
repository has no knowledge of Settings)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from chatbot.features.chat.sla_policy_db import SlaPolicy, SlaPolicyValues

_FIELDS = (
    "response_hours",
    "resolution_hours",
    "ack_minutes_by_channel_json",
    "pic_whatsapp",
    "engine_enabled",
)


def _to_values(row: SlaPolicy) -> SlaPolicyValues:
    return SlaPolicyValues(**{f: getattr(row, f) for f in _FIELDS})


class SlaPolicyRepository:
    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._sm = session_maker

    async def get_tenant_default(self) -> SlaPolicyValues | None:
        async with self._sm() as session:
            row = (
                await session.execute(select(SlaPolicy).where(SlaPolicy.inbox_id.is_(None)))
            ).scalars().first()
            return _to_values(row) if row is not None else None

    async def get_for_inbox(self, inbox_id: int) -> SlaPolicyValues | None:
        async with self._sm() as session:
            row = (
                await session.execute(select(SlaPolicy).where(SlaPolicy.inbox_id == inbox_id))
            ).scalars().first()
            return _to_values(row) if row is not None else None

    async def upsert_tenant_default(self, **fields: object) -> SlaPolicyValues:
        return await self._upsert(None, fields)

    async def upsert_for_inbox(self, inbox_id: int, **fields: object) -> SlaPolicyValues:
        return await self._upsert(inbox_id, fields)

    async def _upsert(self, inbox_id: int | None, fields: dict) -> SlaPolicyValues:
        async with self._sm() as session:
            row = (
                await session.execute(select(SlaPolicy).where(SlaPolicy.inbox_id == inbox_id))
            ).scalars().first()
            if row is None:
                row = SlaPolicy(inbox_id=inbox_id)
                session.add(row)
            for key, value in fields.items():
                if key in _FIELDS:
                    setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return _to_values(row)

    async def resolve(self, inbox_id: int | None) -> SlaPolicyValues | None:
        """inbox-specific row's non-None fields win; unset fields fall back to
        the tenant-default row's value; returns None only when neither row
        exists at all (caller falls back fully to env)."""
        inbox_row = await self.get_for_inbox(inbox_id) if inbox_id is not None else None
        default_row = await self.get_tenant_default()
        if inbox_row is None and default_row is None:
            return None
        merged = {}
        for f in _FIELDS:
            inbox_val = getattr(inbox_row, f) if inbox_row is not None else None
            default_val = getattr(default_row, f) if default_row is not None else None
            merged[f] = inbox_val if inbox_val is not None else default_val
        return SlaPolicyValues(**merged)
