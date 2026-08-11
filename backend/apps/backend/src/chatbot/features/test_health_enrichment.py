"""Unit tests for the deep health check (P13).

The previous version asserted `res["status"] == "ok"` against a function that
reported every subsystem `ok` unconditionally -- it named the configured provider
rather than probing it. So the assertion held no matter what was actually up, and
mounting the function would have produced a second health check that could not
fail. These tests are about the two properties that make it a health check: an
unprobed dependency is `unknown` rather than `ok`, and a failing or hanging one
comes back `unhealthy` within the timeout.

Reachability through the real app is `test_p13_wiring.py`'s job.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from chatbot.features.health_enrichment import (
    PROBE_TIMEOUT_SECONDS,
    build_sql_probes,
    get_enriched_health_status,
    http_status_for,
)
from chatbot.platform.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings()


async def test_an_unprobed_subsystem_is_unknown_and_the_overall_status_is_degraded(
    settings: Settings,
) -> None:
    res = await get_enriched_health_status(settings)

    assert res["status"] == "degraded"
    for name in ("crm", "voice", "knowledge"):
        assert res["subsystems"][name]["status"] == "unknown"
        assert res["subsystems"][name]["reason"] == "not_probed"
    assert "crm" in res["unprobed"]
    # Degraded is NOT a page: nothing failed, we just have no instrumentation.
    assert http_status_for(res["status"]) == 200


async def test_the_in_memory_handoff_store_is_a_real_ok(settings: Settings) -> None:
    """The one subsystem whose health follows from the handler having run."""
    assert settings.handoff_store == "memory"
    res = await get_enriched_health_status(settings)

    assert res["subsystems"]["database"]["status"] == "ok"
    assert res["subsystems"]["database"]["reason"] == "in_process_store"


async def test_a_failing_probe_makes_the_check_unhealthy_and_names_the_subsystem(
    settings: Settings,
) -> None:
    async def _broken() -> None:
        raise RuntimeError("connection refused")

    res = await get_enriched_health_status(settings, probes={"rbac_database": _broken})

    assert res["status"] == "unhealthy"
    assert http_status_for(res["status"]) == 503
    entry = res["subsystems"]["rbac_database"]
    assert entry["status"] == "unhealthy"
    assert "connection refused" in entry["error"]


async def test_a_hanging_probe_times_out_rather_than_hanging_the_request(
    settings: Settings,
) -> None:
    """The property the endpoint depends on: an unresponsive dependency is
    reported as unhealthy, not as a request that never returns."""

    async def _hangs() -> None:
        await asyncio.sleep(30)

    started = time.monotonic()
    res = await get_enriched_health_status(settings, probes={"crm": _hangs}, timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 5, "the probe timeout did not bound the call"
    assert res["status"] == "unhealthy"
    assert res["subsystems"]["crm"]["reason"] == "timeout"


async def test_probes_run_concurrently_so_the_bound_does_not_multiply(
    settings: Settings,
) -> None:
    """Three hanging dependencies must cost one timeout, not three."""

    async def _hangs() -> None:
        await asyncio.sleep(30)

    started = time.monotonic()
    res = await get_enriched_health_status(
        settings,
        probes={"crm": _hangs, "voice": _hangs, "knowledge": _hangs},
        timeout_seconds=0.2,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.6, f"probes appear to run serially ({elapsed:.2f}s)"
    assert res["status"] == "unhealthy"


async def test_all_probes_passing_is_ok_when_nothing_is_left_unprobed(
    settings: Settings,
) -> None:
    async def _fine() -> None:
        return None

    res = await get_enriched_health_status(
        settings,
        probes={"crm": _fine, "voice": _fine, "knowledge": _fine},
    )

    assert res["status"] == "ok"
    assert res["unprobed"] == []
    assert http_status_for(res["status"]) == 200


def test_a_switched_off_feature_contributes_no_subsystem() -> None:
    """`None` engine = that feature is off for this tenant. An absent dependency
    is not an unhealthy one, so it must not appear as a subsystem at all."""
    probes = build_sql_probes({"rbac_database": None, "knowledge_database": object()})

    assert "rbac_database" not in probes
    assert "knowledge_database" in probes


async def test_a_sql_probe_reports_a_dead_engine_as_unhealthy(settings: Settings) -> None:
    class _Engine:
        def connect(self) -> Any:
            raise OSError("could not connect to server")

    probes = build_sql_probes({"rbac_database": _Engine()})
    res = await get_enriched_health_status(settings, probes=probes)

    assert res["subsystems"]["rbac_database"]["status"] == "unhealthy"
    assert res["status"] == "unhealthy"


async def test_a_sql_probe_selects_one_on_a_live_engine() -> None:
    """Proves the probe issues a real query, against a real (sqlite) engine."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        probe = build_sql_probes({"rbac_database": engine})["rbac_database"]
        assert await probe() is None
    finally:
        await engine.dispose()


def test_the_default_timeout_is_two_seconds() -> None:
    """Named in the module docstring, the runbook and the commit message; if it
    changes, those become wrong, so the number is asserted rather than assumed."""
    assert PROBE_TIMEOUT_SECONDS == 2.0
