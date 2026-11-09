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

import structlog
from sqlalchemy import DateTime, Integer, Text, func, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_log = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    pass


class RsaIncident(Base):
    __tablename__ = "rsa_incidents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    incident_date: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_no: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_model: Mapped[str | None] = mapped_column(Text)
    # Optional customer phone so Customer 360 phone search can surface RSA
    # incidents (they have no Chatwoot contact). Added after launch: see
    # init_rsa_db for the idempotent ALTER on existing tables.
    customer_phone: Mapped[str | None] = mapped_column(Text)
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


_ALTER_PG = "ALTER TABLE rsa_incidents ADD COLUMN IF NOT EXISTS customer_phone TEXT"
_ALTER_SQLITE = "ALTER TABLE rsa_incidents ADD COLUMN customer_phone TEXT"
PHONE_COLUMN_INFO_KEY = "_rsa_phone_column"  # attribute set on the sync engine


def phone_column_available(bind) -> bool:
    """False only when init_rsa_db could not add/see ``customer_phone``; the
    repository then omits the column instead of erroring on every query."""
    engine = getattr(bind, "sync_engine", bind)
    return bool(getattr(engine, PHONE_COLUMN_INFO_KEY, True))


async def init_rsa_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # create_all never alters an existing table, so a tenant whose
    # rsa_incidents predates customer_phone needs the column added in place.
    # Idempotent and nullable. FAIL-OPEN: it runs in its own transaction (a
    # failed statement aborts a Postgres transaction) and any error is logged
    # and swallowed -- a migration hiccup (permissions, lock timeout) must not
    # crash backend startup; RSA then works without the phone column.
    setattr(engine.sync_engine, PHONE_COLUMN_INFO_KEY, True)
    try:
        async with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                await conn.execute(text(_ALTER_PG))
            else:
                cols = (await conn.execute(text("PRAGMA table_info(rsa_incidents)"))).fetchall()
                if "customer_phone" not in {c[1] for c in cols}:
                    await conn.execute(text(_ALTER_SQLITE))
    except Exception as exc:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT customer_phone FROM rsa_incidents LIMIT 0"))
            present = True
        except Exception:
            present = False
        setattr(engine.sync_engine, PHONE_COLUMN_INFO_KEY, present)
        _log.warning(
            "rsa_customer_phone_migration_failed",
            table="rsa_incidents",
            column="customer_phone",
            column_present=present,
            error_type=type(exc).__name__,
        )
