"""SLA policy store — operator-editable overrides for sla.py's thresholds.

Reuses the RBAC feature's Postgres connection string (rbac_database_url) but
owns its own engine/Base, matching how kb_db.py and authz/db.py each
independently own their engine (see CLAUDE.md's per-feature-engine
convention). A `(inbox_id)` row with inbox_id NULL is the tenant-wide
default; a specific inbox_id row overrides it for that inbox. The single
tenant-default-row invariant is enforced at the repository layer (get-then-
upsert), not by the database — Postgres treats multiple NULLs in a UNIQUE
column as distinct, so a DB constraint alone can't express "at most one
NULL row."
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (UniqueConstraint("inbox_id", name="uq_sla_policies_inbox_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier2_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    reminder_warning_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    ack_minutes_by_channel_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pic_whatsapp: Mapped[str | None] = mapped_column(Text, nullable=True)
    engine_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # P1: per-inbox override of settings.sla_working_hours_enabled. NULL means
    # "inherit the global setting" -- deliberately not False, so a policy row
    # written before this column existed does not silently opt its inbox out
    # of the working-hours clock.
    working_hours_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


@dataclass
class SlaPolicyValues:
    response_hours: float | None = None
    resolution_hours: float | None = None
    tier2_hours: float | None = None
    reminder_warning_minutes: float | None = None
    ack_minutes_by_channel_json: str | None = None
    pic_whatsapp: str | None = None
    engine_enabled: bool | None = None
    working_hours_enabled: bool | None = None


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


async def init_sla_policy_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        columns_to_ensure = [
            ("inbox_id", "INTEGER", "INTEGER"),
            ("response_hours", "DOUBLE PRECISION", "REAL"),
            ("resolution_hours", "DOUBLE PRECISION", "REAL"),
            ("tier2_hours", "DOUBLE PRECISION", "REAL"),
            ("reminder_warning_minutes", "DOUBLE PRECISION", "REAL"),
            ("ack_minutes_by_channel_json", "TEXT", "TEXT"),
            ("pic_whatsapp", "TEXT", "TEXT"),
            ("engine_enabled", "BOOLEAN", "BOOLEAN"),
            ("working_hours_enabled", "BOOLEAN", "BOOLEAN"),
            ("updated_at", "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ]
        is_sqlite = engine.dialect.name == "sqlite"
        for col_name, pg_type, sqlite_type in columns_to_ensure:
            if is_sqlite:
                with suppress(Exception):
                    await conn.execute(text(f"ALTER TABLE sla_policies ADD COLUMN {col_name} {sqlite_type}"))
            else:
                await conn.execute(text(f"ALTER TABLE sla_policies ADD COLUMN IF NOT EXISTS {col_name} {pg_type}"))
