"""Port + InMemory + Postgres repository for RSA incidents. Mirrors
kb_repository.py's port/adapter split (see docs/superpowers/plans/
2026-07-26-pgvector-knowledge-base.md for the precedent this follows)."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import insert, select
from sqlalchemy.orm import defer

from chatbot.features.rsa.rsa_db import RsaIncident, phone_column_available


@dataclass(frozen=True)
class CauseCount:
    cause: str
    count: int


@dataclass(frozen=True)
class DealerCount:
    dealer: str
    count: int


@dataclass(frozen=True)
class RsaAggregate:
    by_cause: list[CauseCount]
    by_dealer: list[DealerCount]


class RsaRepositoryPort(Protocol):
    async def create_incident(self, **fields) -> str: ...
    async def list_incidents(self) -> list[RsaIncident]: ...
    async def get_incident(self, incident_id: str) -> RsaIncident | None: ...
    async def update_incident(self, incident_id: str, **fields) -> bool: ...
    async def delete_incident(self, incident_id: str) -> bool: ...
    async def aggregate(self) -> RsaAggregate: ...


@dataclass
class _InMemoryRow:
    id: str
    incident_date: str
    vehicle_no: str
    vehicle_model: str | None = None
    customer_phone: str | None = None
    cause: str = ""
    purchased_from: str | None = None
    breakdown_location: str | None = None
    arrived_location: str | None = None
    customer_called_in_time: datetime | None = None
    towing_assigned_time: datetime | None = None
    time_arrived_breakdown_area: datetime | None = None
    time_arrived_outlet: datetime | None = None
    total_km: int | None = None
    late_reason: str | None = None
    remarks: str | None = None
    created_by: str | None = None


class InMemoryRsaRepository:
    """Dev/test repository — no DB needed."""

    def __init__(self) -> None:
        self._rows: dict[str, _InMemoryRow] = {}

    async def create_incident(self, **fields) -> str:
        incident_id = str(uuid.uuid4())
        self._rows[incident_id] = _InMemoryRow(id=incident_id, **fields)
        return incident_id

    async def list_incidents(self) -> list[_InMemoryRow]:
        return list(self._rows.values())

    async def get_incident(self, incident_id: str) -> _InMemoryRow | None:
        return self._rows.get(incident_id)

    async def update_incident(self, incident_id: str, **fields) -> bool:
        row = self._rows.get(incident_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True

    async def delete_incident(self, incident_id: str) -> bool:
        return self._rows.pop(incident_id, None) is not None

    async def aggregate(self) -> RsaAggregate:
        cause_counter = Counter(r.cause for r in self._rows.values())
        dealer_counter = Counter(
            r.purchased_from for r in self._rows.values() if r.purchased_from
        )
        return RsaAggregate(
            by_cause=[CauseCount(cause, count) for cause, count in cause_counter.items()],
            by_dealer=[DealerCount(dealer, count) for dealer, count in dealer_counter.items()],
        )


class PgRsaRepository:
    """Postgres-backed repository, using the SQLAlchemy model from rsa_db.py.

    If init_rsa_db could not add ``customer_phone`` (see its fail-open note),
    every query below leaves that column out so RSA keeps working; the phone
    simply reads as None and is not stored.
    """

    def __init__(self, session_maker) -> None:
        self._session_maker = session_maker

    def _has_phone(self) -> bool:
        return phone_column_available(self._session_maker.kw.get("bind"))

    def _select(self):
        stmt = select(RsaIncident)
        if not self._has_phone():
            stmt = stmt.options(defer(RsaIncident.customer_phone))
        return stmt

    def _plain(self, row: RsaIncident):
        """Detach a row loaded without customer_phone into a plain object, so
        callers reading ``.customer_phone`` never trigger a lazy load."""
        if self._has_phone():
            return row
        data = {
            c.key: getattr(row, c.key)
            for c in RsaIncident.__table__.columns
            if c.key not in ("customer_phone", "created_at", "updated_at")
        }
        return _InMemoryRow(**data)

    async def create_incident(self, **fields) -> str:
        incident_id = str(uuid.uuid4())
        async with self._session_maker() as session:
            if self._has_phone():
                session.add(RsaIncident(id=incident_id, **fields))
            else:
                fields.pop("customer_phone", None)
                await session.execute(insert(RsaIncident).values(id=incident_id, **fields))
            await session.commit()
        return incident_id

    async def list_incidents(self) -> list[RsaIncident]:
        async with self._session_maker() as session:
            result = await session.execute(
                self._select().order_by(RsaIncident.created_at.desc())
            )
            return [self._plain(r) for r in result.scalars().all()]

    async def get_incident(self, incident_id: str) -> RsaIncident | None:
        async with self._session_maker() as session:
            result = await session.execute(
                self._select().where(RsaIncident.id == incident_id)
            )
            row = result.scalar_one_or_none()
            return None if row is None else self._plain(row)

    async def update_incident(self, incident_id: str, **fields) -> bool:
        if not self._has_phone():
            fields.pop("customer_phone", None)
        async with self._session_maker() as session:
            result = await session.execute(
                self._select().where(RsaIncident.id == incident_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            for key, value in fields.items():
                setattr(row, key, value)
            await session.commit()
            return True

    async def delete_incident(self, incident_id: str) -> bool:
        async with self._session_maker() as session:
            result = await session.execute(
                self._select().where(RsaIncident.id == incident_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def aggregate(self) -> RsaAggregate:
        async with self._session_maker() as session:
            result = await session.execute(self._select())
            rows = list(result.scalars().all())
        cause_counter = Counter(r.cause for r in rows)
        dealer_counter = Counter(r.purchased_from for r in rows if r.purchased_from)
        return RsaAggregate(
            by_cause=[CauseCount(c, n) for c, n in cause_counter.items()],
            by_dealer=[DealerCount(d, n) for d, n in dealer_counter.items()],
        )
