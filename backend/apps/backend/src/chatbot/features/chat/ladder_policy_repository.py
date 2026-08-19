"""Read and write the ladder's single policy row, and resolve it against env.

`resolve()` is the one place that answers "what is the ladder actually doing
right now", so the sweep, the admin page and the in-flight view can never
disagree about it. Precedence is simple and total: a non-NULL stored value
wins, otherwise the `Settings` value.

Fail-open on read. A Postgres blip must not decide whether an escalation
ladder runs -- it degrades to the env configuration, which is the state the
tenant was deployed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from chatbot.features.chat.ladder_policy_db import (
    SINGLETON_ID,
    LadderPolicy,
    LadderPolicyValues,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_FIELDS = (
    "enabled",
    "dry_run",
    "scan_interval_seconds",
    "step2_hours",
    "step3_hours",
    "step4_hours",
    "step5_hours",
)


@dataclass(frozen=True)
class EffectiveLadderConfig:
    """What the ladder is doing, after the store has been laid over env."""

    enabled: bool
    dry_run: bool
    scan_interval_seconds: int
    # step_no -> delay in working hours, for the rungs the operator has
    # overridden. A rung absent here keeps whatever the step table says.
    delay_overrides: dict[int, float]
    # True when a stored row supplied at least one value, so the page can say
    # "configured here" rather than implying env is being ignored.
    from_store: bool = False


def _from_env(settings: Settings) -> EffectiveLadderConfig:
    return EffectiveLadderConfig(
        enabled=bool(getattr(settings, "escalation_policy_enabled", False)),
        dry_run=bool(getattr(settings, "escalation_policy_dry_run", True)),
        scan_interval_seconds=int(
            getattr(settings, "escalation_policy_scan_interval_seconds", 300)
        ),
        delay_overrides={},
    )


class LadderPolicyRepository:
    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def get(self) -> LadderPolicyValues:
        """The stored row, or an all-NULL value object when there is none."""
        try:
            async with self._session_maker() as session:
                row = await session.get(LadderPolicy, SINGLETON_ID)
                if row is None:
                    return LadderPolicyValues()
                return LadderPolicyValues(**{f: getattr(row, f) for f in _FIELDS})
        except Exception as exc:
            _log.warning("ladder_policy_get_failed", error=str(exc))
            return LadderPolicyValues()

    async def upsert(self, values: LadderPolicyValues) -> LadderPolicyValues:
        """Write the row, creating it on first save.

        Every field is written, including the NULLs -- clearing a field in the
        page has to mean "go back to inheriting env", and a partial update
        could not express that.
        """
        async with self._session_maker() as session:
            row = await session.get(LadderPolicy, SINGLETON_ID)
            if row is None:
                row = LadderPolicy(id=SINGLETON_ID)
                session.add(row)
            for field in _FIELDS:
                setattr(row, field, getattr(values, field))
            await session.commit()
        return values

    async def resolve(self, settings: Settings) -> EffectiveLadderConfig:
        stored = await self.get()
        env = _from_env(settings)

        overrides = {
            step_no: stored.delay_for(step_no)
            for step_no in (2, 3, 4, 5)
            if stored.delay_for(step_no) is not None
        }
        touched = any(getattr(stored, f) is not None for f in _FIELDS)

        return EffectiveLadderConfig(
            enabled=env.enabled if stored.enabled is None else bool(stored.enabled),
            dry_run=env.dry_run if stored.dry_run is None else bool(stored.dry_run),
            scan_interval_seconds=(
                env.scan_interval_seconds
                if stored.scan_interval_seconds is None
                else int(stored.scan_interval_seconds)
            ),
            delay_overrides=overrides,  # type: ignore[arg-type]
            from_store=touched,
        )


async def resolve_ladder_config(
    repo: Any | None, settings: Settings
) -> EffectiveLadderConfig:
    """Resolve with no repository wired -- the pre-store behaviour, env only.

    Kept as a function rather than a null-object class so a caller that has
    never heard of the store (a test, an older composition root) reads the
    same values the sweep does.
    """
    if repo is None:
        return _from_env(settings)
    return await repo.resolve(settings)
