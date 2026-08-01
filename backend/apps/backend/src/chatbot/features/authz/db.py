"""RBAC storage — roles, permissions, and their assignments.

Independent Postgres connection from the KB feature's pgvector database (see
config.py's rbac_database_url) — RBAC must work without the KB feature being
enabled. No vector columns here, so tests run against sqlite+aiosqlite.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # slug, e.g. "administrator"
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Permission(Base):
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. "sla.manage"
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_key: Mapped[str] = mapped_column(ForeignKey("permissions.key"), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    chatwoot_user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), primary_key=True)


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


async def init_authz_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
