"""Operator-editable overrides for the escalation ladder's switches and timers.

Same shape as `sla_policy_db.py` -- its own engine/Base on the RBAC Postgres
connection string, per this repo's per-feature-engine convention -- with one
difference: the ladder is tenant-wide, so there is exactly **one row**
(`id == SINGLETON_ID`) rather than a per-inbox table. The ladder addresses
dealers, and a dealer is not a property of the inbox a case arrived on.

Every column is nullable and NULL means "inherit the env value". That is what
makes this additive: a tenant that never opens the page has an empty table,
every field resolves to its `Settings` value, and behaviour is exactly what it
was before the page existed.

Deliberately **not** stored here: which roles each rung addresses. That is the
SOP's contract -- step 3 is the Dealer Principal, step 4 the Dealer Owner --
and the reason the ladder is worth having at all. `ESCALATION_POLICY_STEPS_JSON`
remains the escape hatch for a genuine structural change; this table owns the
numbers operators actually retune.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, func, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The ladder is tenant-wide, so the table holds one row and this is its id.
# A fixed primary key (rather than "the first row you find") makes the upsert
# a plain get-or-create and removes any chance of two rows racing into
# existence and the sweep reading whichever it happened to select.
SINGLETON_ID = 1


class Base(DeclarativeBase):
    pass


class LadderPolicy(Base):
    __tablename__ = "escalation_ladder_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dry_run: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    scan_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Working hours from the step-1 send at which each rung becomes due.
    # Step 1 has no delay by definition -- it IS the escalation.
    step2_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    step3_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    step4_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    step5_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


@dataclass
class LadderPolicyValues:
    enabled: bool | None = None
    dry_run: bool | None = None
    scan_interval_seconds: int | None = None
    step2_hours: float | None = None
    step3_hours: float | None = None
    step4_hours: float | None = None
    step5_hours: float | None = None

    def delay_for(self, step_no: int) -> float | None:
        return getattr(self, f"step{step_no}_hours", None)


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


async def init_ladder_policy_db(engine: AsyncEngine) -> None:
    """Create the table, and add any column a running deployment predates.

    The ADD COLUMN sweep mirrors `init_sla_policy_db`: this platform has no
    migration tool, so a new field has to be able to reach a tenant whose
    table already exists.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        columns_to_ensure = [
            ("enabled", "BOOLEAN", "BOOLEAN"),
            ("dry_run", "BOOLEAN", "BOOLEAN"),
            ("scan_interval_seconds", "INTEGER", "INTEGER"),
            ("step2_hours", "DOUBLE PRECISION", "REAL"),
            ("step3_hours", "DOUBLE PRECISION", "REAL"),
            ("step4_hours", "DOUBLE PRECISION", "REAL"),
            ("step5_hours", "DOUBLE PRECISION", "REAL"),
            (
                "updated_at",
                "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
                "DATETIME DEFAULT CURRENT_TIMESTAMP",
            ),
        ]
        is_sqlite = engine.dialect.name == "sqlite"
        for col_name, pg_type, sqlite_type in columns_to_ensure:
            if is_sqlite:
                with suppress(Exception):
                    await conn.execute(
                        text(
                            f"ALTER TABLE escalation_ladder_policy ADD COLUMN {col_name} {sqlite_type}"
                        )
                    )
            else:
                await conn.execute(
                    text(
                        f"ALTER TABLE escalation_ladder_policy "
                        f"ADD COLUMN IF NOT EXISTS {col_name} {pg_type}"
                    )
                )
