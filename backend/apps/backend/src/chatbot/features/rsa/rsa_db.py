"""Async SQLAlchemy layer for the RSA (roadside assistance) incident log.

Own Postgres table, NOT synced through BigQuery and NOT a Chatwoot
conversation — staff-entered operational data with no message thread,
structurally unlike everything else in this codebase's metrics pipeline.
Patterned on kb_db.py (same _to_async_url upgrade, same lazy-engine +
init-on-startup shape) minus the pgvector-specific bits — RSA has no
embeddings, it's a plain CRUD table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RsaIncident(Base):
    __tablename__ = "rsa_incidents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    incident_date: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_no: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_model: Mapped[str | None] = mapped_column(Text)
    cause: Mapped[str] = mapped_column(Text, nullable=False)
    purchased_from: Mapped[str | None] = mapped_column(Text)
    breakdown_location: Mapped[str | None] = mapped_column(Text)
    arrived_location: Mapped[str | None] = mapped_column(Text)
    customer_called_in_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    towing_assigned_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_arrived_breakdown_area: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_arrived_outlet: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_km: Mapped[int | None] = mapped_column(Integer)
    late_reason: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def build_engine(url: str) -> AsyncEngine:
    return create_async_engine(_to_async_url(url))


def build_session_maker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_rsa_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
