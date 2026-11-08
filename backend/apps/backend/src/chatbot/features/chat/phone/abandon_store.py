"""FIX-16 (RFP 2026_028 4.66): persistence for the abandoned-call rate.

Detection point: Twilio's handoff `<Dial action>` callback
(`/webhooks/phone/dial-status`). `DialCallStatus=canceled` means the caller
hung up while the target was still ringing (i.e. before any agent answered),
which is the RFP's "customer dropped off while put on hold". `completed` is an
answered call; `no-answer`/`busy`/`failed` are "unanswered" (the caller stayed
on the line and heard the apology) -- counted in the denominator and shown
separately, NOT as abandoned.

Known gap: only abandons while the handoff target was ringing are counted
(no Twilio call status callback route exists, so a hang-up during the AI
bridge / IVR / hold prompt before the <Dial> is not seen).

Own small table, one row per call, keyed on CallSid so Twilio's redelivered
callbacks are idempotent. Not routed through BigQuery: call legs are not
Chatwoot conversations, so the conversation sync cannot see them.

FAIL-OPEN throughout: `record` and `daily_rates` swallow and log every error.
A metrics write must never break the TwiML a live caller is waiting on.
Days are bucketed in the reporting timezone, not UTC, so a 1 a.m. KL call
lands on the KL day the report reader expects.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import DateTime, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_log = structlog.get_logger(__name__)


class AbandonRecorder(Protocol):
    async def record(
        self, call_sid: str, dial_status: str, at: datetime | None = None
    ) -> None: ...


class AbandonReader(Protocol):
    async def daily_rates(self, start: date, end: date) -> list[dict[str, Any]]: ...


class Base(DeclarativeBase):
    pass


class CallDialOutcome(Base):
    __tablename__ = "call_dial_outcomes"

    call_sid: Mapped[str] = mapped_column(Text, primary_key=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)  # answered|abandoned|unanswered
    dial_status: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # ISO, reporting tz
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def classify_dial_status(dial_status: str) -> str:
    if dial_status == "completed":
        return "answered"
    if dial_status == "canceled":
        return "abandoned"
    return "unanswered"


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


class AbandonStore:
    def __init__(self, engine: AsyncEngine, timezone: str = "UTC") -> None:
        self.engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            self._tz = ZoneInfo(timezone or "UTC")
        except Exception:
            self._tz = ZoneInfo("UTC")

    async def init(self) -> None:
        """Idempotent create; never raises (a missing table just means the
        feature records nothing, it must not stop backend startup)."""
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        except Exception as exc:
            _log.warning("call_abandon_init_failed", error_type=type(exc).__name__)

    async def record(
        self, call_sid: str, dial_status: str, at: datetime | None = None
    ) -> None:
        at = at or datetime.now(UTC)
        try:
            row = CallDialOutcome(
                call_sid=call_sid,
                outcome=classify_dial_status(dial_status),
                dial_status=dial_status or "",
                day=at.astimezone(self._tz).date().isoformat(),
                occurred_at=at,
            )
            async with self._sessions() as session:
                session.add(row)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()  # redelivered callback: first write wins
        except Exception as exc:
            _log.warning("call_abandon_record_failed", error_type=type(exc).__name__)

    async def daily_rates(self, start: date, end: date) -> list[dict[str, Any]]:
        try:
            async with self._sessions() as session:
                result = await session.execute(
                    select(CallDialOutcome.day, CallDialOutcome.outcome).where(
                        CallDialOutcome.day >= start.isoformat(),
                        CallDialOutcome.day <= end.isoformat(),
                    )
                )
                rows = result.all()
        except Exception as exc:
            _log.warning("call_abandon_query_failed", error_type=type(exc).__name__)
            return []
        days: dict[str, dict[str, int]] = {}
        for day, outcome in rows:
            d = days.setdefault(day, {"answered": 0, "abandoned": 0, "unanswered": 0})
            d[outcome] = d.get(outcome, 0) + 1
        out: list[dict[str, Any]] = []
        for day in sorted(days):
            d = days[day]
            offered = d["answered"] + d["abandoned"] + d["unanswered"]
            out.append(
                {
                    "day": day,
                    "offered": offered,
                    "answered": d["answered"],
                    "abandoned": d["abandoned"],
                    "unanswered": d["unanswered"],
                    "abandon_rate": round(d["abandoned"] / offered, 4) if offered else 0.0,
                }
            )
        return out


def build_abandon_store(url: str, timezone: str = "UTC") -> AbandonStore:
    return AbandonStore(create_async_engine(_to_async_url(url)), timezone)
