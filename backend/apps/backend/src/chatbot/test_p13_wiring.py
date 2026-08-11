"""P13 -- the deep health check and the two retention schedules, through the real app.

All three modules (`features/health_enrichment.py`, `features/authz/audit_purge.py`,
`features/chat/phone/retention.py`) shipped complete and unit-tested and **had no
caller**: `main.py`'s `health_check()` returned a static dict, and nothing
scheduled either purge. Their own tests passed because they called the inner
function and passed its arguments by hand -- one layer below the bug, which is
this run's recurring failure (`.superpowers/sdd/DISPATCH-RULES.md`,
"Reachability"). A test that only calls `start_audit_purge_job(...)` in isolation
does not prove `main.py` calls it.

So every test here boots `bootstrap_application()` and asserts something only the
real app can produce: a route that answers, or a scheduler object the bootstrap
put on `app.state`. The precedents are `test_p11_wiring.py` and
`test_p10_wiring.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("PROTON_BACKEND_KEY", "test_key")
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "test_secret")
    # Every flag that would add a probed subsystem is pinned off unless a test
    # asks for it. Without this, adding e.g. RSA_ENABLED=true to
    # check-suites-both-flag-states.sh would silently turn the "no dependency
    # failed" assertions below into their opposite -- the gate would be red for a
    # reason that has nothing to do with what these tests are about, and the
    # 503 test would pass for the wrong reason.
    for flag in ("RBAC_ENABLED", "KNOWLEDGE_PG_ENABLED", "RSA_ENABLED"):
        monkeypatch.setenv(flag, "false")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    from chatbot.main import bootstrap_application
    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    return bootstrap_application()


# --- the deep health check --------------------------------------------------


def test_the_deep_health_endpoint_is_mounted_and_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 here would mean `health_enrichment.py` is still unreachable."""
    client = TestClient(_boot(monkeypatch))

    res = client.get("/healthz")

    assert res.status_code == 200
    body = res.json()
    assert "subsystems" in body
    assert body["probe_timeout_seconds"] == 2.0


def test_the_deep_check_reports_unprobed_dependencies_rather_than_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect that made mounting it pointless: it used to report every
    subsystem `ok` unconditionally, so it could not fail. On a default tenant
    nothing is probed, and the honest answer is `degraded` with the unprobed
    subsystems named."""
    client = TestClient(_boot(monkeypatch))

    body = client.get("/healthz").json()

    assert body["status"] == "degraded"
    assert set(body["unprobed"]) >= {"crm", "voice", "knowledge"}
    for name in body["unprobed"]:
        assert body["subsystems"][name]["status"] == "unknown"


def test_the_deep_check_returns_503_when_a_configured_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the whole exercise: a health check that can fail, and that
    names which dependency failed.

    RBAC is enabled with a URL whose host does not exist, so the engine builds at
    boot and `SELECT 1` fails at probe time -- exactly the shape of a real
    Postgres outage, and not something the previous static dict could report.
    """
    app = _boot(
        monkeypatch,
        RBAC_ENABLED="true",
        RBAC_DATABASE_URL="postgresql://u:p@127.0.0.1:1/does_not_exist",
    )
    client = TestClient(app)

    res = client.get("/healthz")

    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "unhealthy"
    assert body["subsystems"]["rbac_database"]["status"] == "unhealthy"


def test_a_feature_that_is_off_contributes_no_subsystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent dependency is not an unhealthy one. With RBAC off there is no
    engine, so `rbac_database` must not appear at all -- otherwise every default
    tenant would page for a database it does not use."""
    body = TestClient(_boot(monkeypatch, RBAC_ENABLED="false")).get("/healthz").json()

    assert "rbac_database" not in body["subsystems"]


def test_the_static_liveness_endpoint_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """`GET /` is the container's liveness probe in docker-compose.tenant.yml. A
    liveness probe that fails on a dependency outage restarts a healthy process
    in a loop, so it must keep answering exactly what it did before."""
    client = TestClient(_boot(monkeypatch))

    res = client.get("/")

    assert res.status_code == 200
    assert res.json() == {
        "status": "healthy",
        "crm_provider": "chatwoot",
        "voice_provider": "mock",
        "model": res.json()["model"],
    }


# --- the two retention schedules -------------------------------------------


def test_the_audit_purge_is_scheduled_by_the_real_app_when_the_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, AUDIT_PURGE_JOB_ENABLED="true")
    scheduler = app.state.audit_purge_scheduler
    try:
        assert scheduler is not None, "main.py did not start the audit purge"
        job = scheduler.get_job("audit_log_purge")
        assert job is not None
        # Daily, and NOT at boot: a crash-looping container must not be able to
        # drive a deletion pass per restart.
        assert job.trigger.interval.total_seconds() == 24 * 3600
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


def test_no_audit_purge_scheduler_exists_when_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Byte-identical for a tenant that has not opted in: no scheduler object,
    so no `BackgroundScheduler` and no thread."""
    app = _boot(monkeypatch, AUDIT_PURGE_JOB_ENABLED="false")

    assert app.state.audit_purge_scheduler is None


def test_the_recording_retention_job_is_scheduled_by_the_real_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, PHONE_RETENTION_JOB_ENABLED="true")
    scheduler = app.state.recording_retention_scheduler
    try:
        assert scheduler is not None, "main.py did not start the recording retention job"
        job = scheduler.get_job("phone_recording_retention")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 24 * 3600
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


def test_no_recording_retention_scheduler_exists_when_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, PHONE_RETENTION_JOB_ENABLED="false")

    assert app.state.recording_retention_scheduler is None


def test_the_wired_audit_tick_deletes_nothing_and_reports_a_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runs the job main.py scheduled, exactly as scheduled, and asserts what it
    does today: it reads the real audit-log port and reports `dry_run`, because
    the port has no delete method and `AuditEntry` carries no document id.

    This is what stops "audit retention is enforced" being claimed. If someone
    later wires a deleter, this test fails and has to be rewritten -- which is
    the correct amount of friction for making a purge destructive.
    """
    app = _boot(monkeypatch, AUDIT_PURGE_JOB_ENABLED="true", AUDIT_LOG_RETENTION_DAYS="1")
    scheduler = app.state.recording_retention_scheduler
    audit_scheduler = app.state.audit_purge_scheduler
    try:
        job = audit_scheduler.get_job("audit_log_purge")
        result = job.func()  # the exact callable the schedule will invoke
        assert result["status"] == "dry_run"
        assert result["purged_count"] == 0
        assert result["eligible_count"] == 0  # measured: the in-memory log is empty
    finally:
        audit_scheduler.shutdown(wait=False)
        if scheduler is not None:
            scheduler.shutdown(wait=False)


def test_the_wired_recording_tick_reports_that_it_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The honest state of the 90-day recording policy: scheduled, and unable to
    act, because nothing lists recordings due for purge and no deleter exists.
    Counts are None rather than 0 -- a 0 would read as "nothing is past
    retention", which this pass cannot know."""
    app = _boot(monkeypatch, PHONE_RETENTION_JOB_ENABLED="true")
    scheduler = app.state.recording_retention_scheduler
    try:
        result = scheduler.get_job("phone_recording_retention").func()
        assert result["status"] == "not_executable"
        assert result["reason"] == "no_recording_source_configured"
        assert result["purged_count"] is None
    finally:
        scheduler.shutdown(wait=False)
