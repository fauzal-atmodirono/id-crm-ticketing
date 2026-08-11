"""P13 -- the backend's deep health check, served at `GET /healthz`.

Two endpoints, on purpose:

* `GET /` stays exactly as it was -- a static dict. It is the container's
  liveness probe in `deploy/docker-compose.tenant.yml`, and a liveness probe
  that fails when a *dependency* is down restarts a healthy process in a loop,
  turning one broken dependency into an outage. Nothing here changes it.
* `GET /healthz` is the readiness/monitoring surface: it probes what is
  configured and can answer **503**. A health check that cannot fail is not a
  health check, and the one thing on-call needs from it is which dependency is
  down.

**Bounded, never hanging.** Every probe runs under
`asyncio.wait_for(..., PROBE_TIMEOUT_SECONDS)` -- **2.0 seconds** -- and all
probes run concurrently, so the endpoint's worst case is ~2s regardless of how
many dependencies are configured. An unresponsive dependency therefore surfaces
as `unhealthy` with `reason="timeout"`, never as a request that never returns.
That matters twice over: the monitoring uptime check has its own timeout, and a
health endpoint that hangs takes a worker with it.

**An unprobed subsystem reports `unknown`, never `ok`.** The version of this
module that had no caller reported every subsystem `ok` unconditionally -- it
named the configured provider rather than probing it -- so mounting it as-is
would have produced a second health check that could not fail. Naming a provider
is not evidence it is reachable, and reporting `ok` for something nobody looked
at is the same class of error as rendering an unmeasured value as `0`.

Status vocabulary, and what maps to 503:

* ``ok``        -- every subsystem was checked and passed. 200.
* ``degraded``  -- nothing failed, but at least one subsystem is unprobed. 200.
                   Deliberately not 503: an alert that fires because of missing
                   instrumentation trains on-call to ignore it.
* ``unhealthy`` -- at least one probe failed or timed out. **503.**
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

#: A probe returns None when the dependency answered, and raises otherwise.
Probe = Callable[[], Awaitable[None]]

PROBE_TIMEOUT_SECONDS = 2.0

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNHEALTHY = "unhealthy"
STATUS_UNKNOWN = "unknown"

_MAX_ERROR_CHARS = 300


def build_sql_probe(engine: Any) -> Probe:
    """`SELECT 1` on a SQLAlchemy async engine.

    The same check the `agent` service's `/healthz` makes, and for the same
    reason: it proves the process *and* its connection to that database are
    alive, which is what "the database is down" means to whoever is paged.
    """

    async def _probe() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return _probe


def build_sql_probes(engines: Mapping[str, Any]) -> dict[str, Probe]:
    """Probes for whichever engines exist. A `None` engine means that feature is
    switched off for this tenant, so it is not a subsystem and must not appear as
    one -- an absent dependency is not an unhealthy dependency."""
    return {name: build_sql_probe(engine) for name, engine in engines.items() if engine is not None}


async def _run_probe(name: str, probe: Probe, timeout_seconds: float) -> dict[str, Any]:
    try:
        await asyncio.wait_for(probe(), timeout=timeout_seconds)
    except TimeoutError:
        _log.warning("health_probe_timeout", subsystem=name, timeout_seconds=timeout_seconds)
        return {
            "status": STATUS_UNHEALTHY,
            "reason": "timeout",
            "timeout_seconds": timeout_seconds,
        }
    except Exception as exc:
        _log.warning("health_probe_failed", subsystem=name, error=str(exc))
        return {
            "status": STATUS_UNHEALTHY,
            "reason": "error",
            "error": str(exc)[:_MAX_ERROR_CHARS],
        }
    return {"status": STATUS_OK, "reason": "probed"}


def _declared_subsystems(settings: Settings) -> dict[str, dict[str, Any]]:
    """The dependencies this tenant's configuration implies, with the provider
    named but nothing claimed about reachability."""
    declared: dict[str, dict[str, Any]] = {
        "crm": {"provider": settings.crm_provider},
        "voice": {"provider": settings.voice_provider},
        "knowledge": {"provider": settings.knowledge_provider},
        "database": {"provider": settings.handoff_store},
    }
    for entry in declared.values():
        entry["status"] = STATUS_UNKNOWN
        entry["reason"] = "not_probed"
    # An in-process dict is the one dependency whose health follows from the
    # process answering at all: if this handler ran, the store is there. That is
    # a real `ok`, not an assumed one.
    if settings.handoff_store == "memory":
        declared["database"] = {
            "provider": "memory",
            "status": STATUS_OK,
            "reason": "in_process_store",
        }
    return declared


def _environment_name(settings: Settings) -> str:
    """Which system you are looking at, or an admission that we do not know.

    The version of this module that had no caller read `settings.environment`
    through `getattr(..., "production")`, and no such field exists -- so it
    reported "production" on every box, including a staging one. `getattr` is
    used deliberately here rather than a hard attribute read: this is a display
    string, and a health check must not fail to answer because a cosmetic field
    is absent. Anything absent reports as unspecified, never as an environment
    it might not be.
    """
    for name in ("app_environment", "environment"):
        value = getattr(settings, name, None)
        if value:
            return str(value)
    return "unspecified"


def overall_status(subsystems: Mapping[str, Mapping[str, Any]]) -> str:
    statuses = {entry.get("status") for entry in subsystems.values()}
    if STATUS_UNHEALTHY in statuses:
        return STATUS_UNHEALTHY
    if STATUS_UNKNOWN in statuses:
        return STATUS_DEGRADED
    return STATUS_OK


def http_status_for(status: str) -> int:
    """503 only for a subsystem that actually failed. See the module docstring."""
    return 503 if status == STATUS_UNHEALTHY else 200


async def get_enriched_health_status(
    settings: Settings,
    *,
    probes: Mapping[str, Probe] | None = None,
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Per-subsystem operational status, bounded by `timeout_seconds` overall."""
    subsystems = _declared_subsystems(settings)

    if probes:
        names = list(probes)
        results = await asyncio.gather(
            *(_run_probe(name, probes[name], timeout_seconds) for name in names)
        )
        for name, result in zip(names, results, strict=True):
            entry = dict(subsystems.get(name, {}))
            entry.update(result)
            subsystems[name] = entry

    status = overall_status(subsystems)
    return {
        "status": status,
        "checked_at": datetime.now(UTC).isoformat(),
        # There is no `environment` setting in `Settings`; the previous code read
        # one via `getattr` and therefore always reported "production", which on
        # a staging box is a false statement about which system you are looking
        # at. Reported as unspecified until something actually configures it.
        "environment": _environment_name(settings),
        "probe_timeout_seconds": timeout_seconds,
        "unprobed": sorted(
            name for name, entry in subsystems.items() if entry.get("reason") == "not_probed"
        ),
        "subsystems": subsystems,
    }


def build_health_router(
    settings: Settings,
    engines: Callable[[], Mapping[str, Any]],
    *,
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> APIRouter:
    """`GET /healthz` -- the deep check, mounted unconditionally in `main.py`.

    `engines` is a callable, not a mapping, because the engines live on
    `app.state` and are created during bootstrap: reading them per request also
    means a probe reflects the app as it is now rather than as it was at boot.

    Unauthenticated on purpose. It is the endpoint a monitoring uptime check and
    a container probe call, neither of which can hold a credential here, and the
    body carries provider names and up/down state -- no data, no configuration
    values, no secrets. It stays cheap for the same reason: bounded probes only,
    so it cannot be used to load the dependencies it reports on.
    """
    router = APIRouter(tags=["health"])

    @router.get("/healthz")
    async def deep_health_check() -> JSONResponse:
        result = await get_enriched_health_status(
            settings,
            probes=build_sql_probes(engines()),
            timeout_seconds=timeout_seconds,
        )
        return JSONResponse(status_code=http_status_for(result["status"]), content=result)

    return router
