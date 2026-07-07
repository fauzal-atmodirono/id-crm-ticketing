"""Async SQLAlchemy engine/session wiring, built from AGENT_DATABASE_URL.

Production points AGENT_DATABASE_URL at postgres via the psycopg3 driver
(`postgresql+psycopg://...`), which SQLAlchemy's async engine supports
natively. Tests point it at sqlite via aiosqlite
(`sqlite+aiosqlite:///...`). `_to_async_url` upgrades bare `postgresql://`
or `sqlite://` URLs to their async-capable driver form so either style of
URL works without the caller having to know the driver name.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


settings = get_settings()
engine = create_async_engine(_to_async_url(settings.agent_database_url))
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session
