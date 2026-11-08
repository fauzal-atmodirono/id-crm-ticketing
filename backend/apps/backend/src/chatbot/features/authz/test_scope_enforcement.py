"""Data scope enforcement on every backend-owned surface (Wave 2 Task 28).

Each test goes through the real router over HTTP, so a surface that forgets
to apply the scope fails here -- unlike the P10 tests this file replaces,
which called the filter helper by hand and could not see a missing caller.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.data_scope import Scope
from chatbot.features.authz.router import build_authz_router
from chatbot.features.authz.test_data_scope import (
    FakeValidator,
    make_repo,
    scoped_settings,
    session,
)
from chatbot.features.chat.ai_activity_router import build_ai_activity_router
from chatbot.features.chat.case_fields_router import build_case_fields_router
from chatbot.features.chat.customer360_router import build_customer360_router
from chatbot.features.chat.pic_admin_router import build_pic_admin_router
from chatbot.features.chat.pic_store import DealerRecord
from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.insights_router import build_metrics_insights_router
from chatbot.features.metrics.query_adapter import BigQueryMetricsQuery
from chatbot.features.rsa.rsa_repository import InMemoryRsaRepository
from chatbot.features.rsa.rsa_router import build_rsa_router

METRICS_KEY = "metrics-key"
TOKENS = {"admin": (5, False), "scoped": (7, False), "plain": (8, False)}


async def _repo_with_scoped_user(tmp_path, scope: Scope, *perms: str):
    """User 7 holds `agent` + a scoped role carrying `perms`; user 5 is
    administrator; user 8 holds the same perms with no scope."""
    repo = await make_repo(tmp_path)
    await repo.create_role("scoped_role", "Scoped")
    await repo.create_role("plain_role", "Plain")
    for perm in perms:
        await repo.grant_permission("scoped_role", perm)
        await repo.grant_permission("plain_role", perm)
    await repo.set_role_scope("scoped_role", scope)
    await repo.assign_role(7, "agent")
    await repo.assign_role(7, "scoped_role")
    await repo.assign_role(8, "plain_role")
    await repo.assign_role(5, "administrator")
    return repo


def _client(*routers, repo=None, validator=None) -> TestClient:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    app.state.authz_repo = repo
    app.state.authz_validator = validator
    return TestClient(app)


def _conv(cid: int, inbox: int, labels: list[str] | None = None, **extra: Any) -> dict:
    return {"id": cid, "inbox_id": inbox, "labels": labels or [], "status": "pending", **extra}


# --- Customer 360 -----------------------------------------------------------


async def test_scoped_user_sees_only_their_inbox_conversations(tmp_path) -> None:
    repo = await _repo_with_scoped_user(
        tmp_path, Scope.build(inbox_ids=[3]), "customer360.view"
    )
    chatwoot = AsyncMock()
    chatwoot.search_contacts.return_value = [{"id": 11, "phone_number": "+60123456789"}]
    chatwoot.list_contact_conversations.return_value = [_conv(1, 3), _conv(2, 4), _conv(3, 3)]
    rsa = InMemoryRsaRepository()
    await rsa.create_incident(
        incident_date="2026-11-01", vehicle_no="WXY1", cause="flat",
        customer_phone="+60123456789", purchased_from="kl",
    )
    validator = FakeValidator(TOKENS)
    settings = scoped_settings()
    client = _client(build_customer360_router(chatwoot, rsa, repo, validator, settings))

    scoped = client.get(
        "/admin/customer360/search?q=+60123456789", headers=session("scoped")
    ).json()
    assert [c["id"] for c in scoped["conversations"]] == [1, 3]
    # An RSA incident names no inbox, so an inbox-scoped user cannot see it.
    assert scoped["rsa_incidents"] == []

    plain = client.get("/admin/customer360/search?q=+60123456789", headers=session("plain"))
    assert [c["id"] for c in plain.json()["conversations"]] == [1, 2, 3]
    assert len(plain.json()["rsa_incidents"]) == 1


# --- Cases (case-record panel) -----------------------------------------------


async def test_case_fields_outside_scope_read_as_not_found(tmp_path) -> None:
    repo = await _repo_with_scoped_user(
        tmp_path, Scope.build(inbox_ids=[3]), "cases.view", "cases.manage"
    )
    convs = {"1": _conv(1, 3), "2": _conv(2, 4)}

    async def _read(conv_id):
        return convs[str(conv_id)]

    merge = AsyncMock()
    settings = scoped_settings(case_fields_enabled=True)
    client = _client(
        build_case_fields_router(_read, merge, repo, FakeValidator(TOKENS), settings)
    )
    assert client.get("/cases/1/fields", headers=session("scoped")).status_code == 200
    assert client.get("/cases/2/fields", headers=session("scoped")).status_code == 404
    res = client.patch(
        "/cases/2/fields", json={"fields": {}}, headers=session("scoped")
    )
    assert res.status_code == 404
    merge.assert_not_called()
    assert client.get("/cases/2/fields", headers=session("plain")).status_code == 200


# --- Reports -----------------------------------------------------------------


class _ScopeHonouringClient:
    """Fake BigQuery client that really applies bound `scope_dealer_*`
    parameters, so the response -- not just the SQL -- shows the filter."""

    def __init__(self, by_view: dict[str, list[dict]]) -> None:
        self._by_view = by_view
        self.queries: list[str] = []

    def query(self, sql: str, job_config: Any = None):
        self.queries.append(sql)
        params = {p.name: p.value for p in getattr(job_config, "query_parameters", None) or []}
        dealers = {v for k, v in params.items() if k.startswith("scope_dealer_")}
        view = next((v for v in sorted(self._by_view, key=len, reverse=True) if v in sql), None)
        rows = self._by_view.get(view, [])
        if dealers:
            rows = [r for r in rows if r["dealer"].lower() in dealers]

        class _Job:
            def result(self_inner):
                return rows

        return _Job()


def _dealer_rows(dealer: str) -> dict:
    return {
        "dealer": dealer,
        "cases_escalated": 4,
        "avg_turnaround_days": 1.0,
        "p50_turnaround_days": 1.0,
        "p90_turnaround_days": 2.0,
    }


async def test_reports_filtered_by_scope(tmp_path) -> None:
    repo = await _repo_with_scoped_user(tmp_path, Scope.build(dealer_slugs=["kl"]))
    fake = _ScopeHonouringClient(
        {
            "v_dealer_escalation": [_dealer_rows("kl"), _dealer_rows("penang")],
            "v_dealer_escalation_slowest_cases": [
                {"conversation_id": "1", "dealer": "kl", "turnaround_days": 3.0},
                {"conversation_id": "2", "dealer": "penang", "turnaround_days": 9.0},
            ],
        }
    )
    settings = scoped_settings(metrics_api_key=METRICS_KEY, metrics_provider="bigquery")
    port = BigQueryMetricsQuery(settings, client=fake)
    client = _client(
        build_metrics_insights_router(port, settings),
        build_metrics_query_router(port, settings),
        repo=repo,
        validator=FakeValidator(TOKENS),
    )

    def _get(path: str, who: str):
        return client.get(path, headers={"x-api-key": METRICS_KEY, **session(who)})

    scoped = _get("/metrics/dealer-escalation", "scoped")
    assert scoped.status_code == 200
    assert [r["dealer"] for r in scoped.json()["by_dealer"]] == ["kl"]
    assert [r["dealer"] for r in scoped.json()["slowest_cases"]] == ["kl"]

    admin = _get("/metrics/dealer-escalation", "admin").json()
    assert sorted(r["dealer"] for r in admin["by_dealer"]) == ["kl", "penang"]

    # A report with no dealer dimension is refused, never served unfiltered.
    assert _get("/metrics/callcenter", "scoped").status_code == 403
    assert _get("/metrics/lifecycle", "scoped").status_code == 403
    assert _get("/metrics/dashboard", "scoped").status_code == 403
    assert _get("/metrics/dashboard", "admin").status_code == 200
    # Flag on: the shared metrics key alone carries no person, so no scope.
    no_session = client.get(
        "/metrics/dealer-escalation", headers={"x-api-key": METRICS_KEY}
    )
    assert no_session.status_code == 401


async def test_reports_flag_off_need_no_session() -> None:
    settings = scoped_settings(
        metrics_api_key=METRICS_KEY, metrics_provider="bigquery", data_scoped_rbac_enabled=False
    )
    fake = _ScopeHonouringClient({"v_dealer_escalation": [_dealer_rows("kl")]})
    port = BigQueryMetricsQuery(settings, client=fake)
    client = _client(
        build_metrics_insights_router(port, settings), build_metrics_query_router(port, settings)
    )
    res = client.get("/metrics/dealer-escalation", headers={"x-api-key": METRICS_KEY})
    assert res.status_code == 200
    assert not any("scope_" in q for q in fake.queries)
    assert client.get("/metrics/dashboard").status_code == 200


# --- Escalation admin ----------------------------------------------------------


async def test_escalation_admin_lists_only_scoped_dealers(tmp_path) -> None:
    repo = await _repo_with_scoped_user(
        tmp_path, Scope.build(dealer_slugs=["kl"]), "escalation.manage"
    )
    dealer_store = AsyncMock()
    dealer_store.list_all.return_value = [DealerRecord(dealer="kl"), DealerRecord(dealer="penang")]
    dealer_store.get.return_value = None
    pic_store = AsyncMock()
    pic_store.list_all.return_value = []
    client = _client(
        build_pic_admin_router(pic_store, dealer_store, repo, FakeValidator(TOKENS), scoped_settings())
    )
    listed = client.get("/admin/escalation/dealers", headers=session("scoped")).json()
    assert [d["dealer"] for d in listed["dealers"]] == ["kl"]
    res = client.put(
        "/admin/escalation/dealers/penang", json={"emails": ["a@b.c"]}, headers=session("scoped")
    )
    assert res.status_code == 403
    dealer_store.set.assert_not_called()


# --- RSA incidents -------------------------------------------------------------


async def test_rsa_incidents_filtered_by_dealer_scope(tmp_path) -> None:
    repo = await _repo_with_scoped_user(
        tmp_path, Scope.build(dealer_slugs=["kl"]), "sla.manage"
    )
    rsa = InMemoryRsaRepository()
    kl = await rsa.create_incident(
        incident_date="2026-11-01", vehicle_no="A1", cause="x", purchased_from="KL"
    )
    other = await rsa.create_incident(
        incident_date="2026-11-01", vehicle_no="B2", cause="x", purchased_from="penang"
    )
    await rsa.create_incident(incident_date="2026-11-01", vehicle_no="C3", cause="x")
    client = _client(build_rsa_router(rsa, scoped_settings(), repo, FakeValidator(TOKENS)))
    listed = client.get("/rsa/incidents", headers=session("scoped")).json()["incidents"]
    assert [i["id"] for i in listed] == [kl]
    assert client.get(f"/rsa/incidents/{other}", headers=session("scoped")).status_code == 404
    assert client.get("/rsa/incidents/aggregate", headers=session("scoped")).status_code == 403
    assert len(client.get("/rsa/incidents", headers=session("plain")).json()["incidents"]) == 3


# --- AI activity -----------------------------------------------------------------


async def test_ai_activity_shows_only_scoped_conversations(tmp_path) -> None:
    repo = await _repo_with_scoped_user(
        tmp_path, Scope.build(inbox_ids=[3]), "ai_activity.view"
    )
    chatwoot = AsyncMock()

    async def _by_status(status, **kw):  # noqa: ARG001
        return [_conv(1, 3), _conv(2, 4)] if status == "pending" else []

    chatwoot.list_conversations_by_status.side_effect = _by_status
    chatwoot.is_ai_paused.return_value = False
    client = _client(
        build_ai_activity_router(chatwoot, repo, FakeValidator(TOKENS), scoped_settings())
    )
    scoped_res = client.get("/admin/ai-activity", headers=session("scoped"))
    assert scoped_res.status_code == 200, scoped_res.text
    scoped = scoped_res.json()
    assert [r["conversation_id"] for r in scoped["conversations"]] == [1]
    assert scoped["summary"]["ai_active"] == 1
    # Same cache window, different caller: the cache must not leak the scope.
    plain = client.get("/admin/ai-activity", headers=session("plain")).json()
    assert sorted(r["conversation_id"] for r in plain["conversations"]) == [1, 2]
    assert plain["summary"]["ai_active"] == 2


# --- the admin surface -----------------------------------------------------------


async def test_role_scope_admin_endpoints(tmp_path) -> None:
    repo = await make_repo(tmp_path)
    await repo.create_role("kl_dealer", "KL dealer")
    await repo.assign_role(5, "administrator")
    await repo.assign_role(7, "kl_dealer")
    validator = FakeValidator(TOKENS)
    client = _client(build_authz_router(repo, validator, scoped_settings()))

    put = client.put(
        "/authz/roles/kl_dealer/scope",
        json={"dealer_slugs": ["Dealer_KL"], "inbox_ids": [3]},
        headers=session("admin"),
    )
    assert put.status_code == 200, put.text
    got = client.get("/authz/roles/kl_dealer/scope", headers=session("admin")).json()
    assert got["scope"]["dealer_slugs"] == ["kl"]
    assert got["scope"]["inbox_ids"] == [3]
    assert got["enabled"] is True
    # A scoped user may read their own scope but not edit anyone's.
    mine = client.get("/authz/me/scope", headers=session("scoped")).json()
    assert mine["scope"]["dealer_slugs"] == ["kl"]
    denied = client.put(
        "/authz/roles/kl_dealer/scope", json={}, headers=session("scoped")
    )
    assert denied.status_code == 403
    # The administrator role can never be scoped.
    admin_role = client.put(
        "/authz/roles/administrator/scope", json={"inbox_ids": [3]}, headers=session("admin")
    )
    assert admin_role.status_code == 400
    # Clearing returns the role to unrestricted.
    client.put("/authz/roles/kl_dealer/scope", json={}, headers=session("admin"))
    cleared = client.get("/authz/roles/kl_dealer/scope", headers=session("admin")).json()
    assert cleared["scope"]["unrestricted"] is True
