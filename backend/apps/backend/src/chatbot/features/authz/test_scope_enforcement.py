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
from chatbot.features.chat.pic_store import DealerRecord, PicRecord
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

    async def _by_status(status, **kw):
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


# --- security review fixes (C1, I1, I3, minors) ---------------------------------


def _report_client(repo, port=None, settings=None):
    settings = settings or scoped_settings(
        metrics_api_key=METRICS_KEY, metrics_provider="bigquery", ai_cost_reporting_enabled=True
    )
    port = port or BigQueryMetricsQuery(settings, client=_ScopeHonouringClient({}))
    return _client(
        build_metrics_insights_router(port, settings), repo=repo, validator=FakeValidator(TOKENS)
    )


async def test_ai_cost_and_unfilterable_reports_refuse_a_scoped_caller(tmp_path) -> None:
    """C1: ai-cost reads token usage through its own raw queries that never
    see the scope, so it must refuse, not serve tenant-wide cost."""
    repo = await _repo_with_scoped_user(tmp_path, Scope.build(dealer_slugs=["kl"]))
    client = _report_client(repo)
    headers = {"x-api-key": METRICS_KEY, **session("scoped")}
    for path in ("ai-cost", "departments", "callcenter", "sla-buckets"):
        assert client.get(f"/metrics/{path}", headers=headers).status_code == 403, path


def test_only_provably_filtered_report_routes_take_the_scoped_dependency() -> None:
    """Sweep guard: a route may bind the scope (and so serve a scoped caller)
    only if every read behind it goes through the adapter's scope-aware
    blocks on a SCOPABLE_VIEW_COLUMNS view. Adding one means proving it."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "metrics" / "insights_router.py").read_text()
    scoped = set(re.findall(r'@router\.get\("/metrics/([a-z-]+)", dependencies=scoped\)', src))
    assert scoped == {"dealer-escalation", "case-aging"}


async def test_case_aging_is_filtered_by_scope(tmp_path) -> None:
    repo = await _repo_with_scoped_user(tmp_path, Scope.build(dealer_slugs=["kl"]))
    row = {
        "case_type": "t", "division": "d", "pic": "p", "status": "open",
        "created_at": None, "age_days": 1.0, "bucket_label": "1-3 days",
    }
    fake = _ScopeHonouringClient(
        {"v_case_aging": [{**row, "conversation_id": "1", "dealer": "kl"},
                          {**row, "conversation_id": "2", "dealer": "penang"}]}
    )
    settings = scoped_settings(metrics_api_key=METRICS_KEY, metrics_provider="bigquery")
    client = _report_client(repo, BigQueryMetricsQuery(settings, client=fake), settings)
    res = client.get("/metrics/case-aging", headers={"x-api-key": METRICS_KEY, **session("scoped")})
    assert res.status_code == 200, res.text
    assert [c["dealer"] for c in res.json()["cases"]] == ["kl"]


async def test_freshness_metadata_is_not_blocked_for_scoped_users(tmp_path) -> None:
    from chatbot.features.metrics.freshness_router import (
        build_metrics_freshness_router,
    )

    repo = await _repo_with_scoped_user(tmp_path, Scope.build(dealer_slugs=["kl"]))
    settings = scoped_settings(dashboard_freshness_enabled=True)
    client = _client(build_metrics_freshness_router(settings), repo=repo, validator=FakeValidator(TOKENS))
    assert client.get("/metrics/freshness", headers=session("scoped")).status_code == 200


def _escalation_client(repo, *, pronet=None):
    dealer_store = AsyncMock()
    dealer_store.list_all.return_value = [DealerRecord(dealer="kl"), DealerRecord(dealer="penang")]
    dealer_store.get.return_value = None
    pic_store = AsyncMock()
    pic_store.list_all.return_value = [
        PicRecord(department="sales", pic_name="a", pic_email="a@x", pic_whatsapp=""),
        PicRecord(department="service", pic_name="b", pic_email="b@x", pic_whatsapp=""),
    ]
    pic_store.get.return_value = None
    client = _client(
        build_pic_admin_router(
            pic_store, dealer_store, repo, FakeValidator(TOKENS), scoped_settings(),
            pronet_store=pronet,
        )
    )
    return client, pic_store, dealer_store


_PIC_BODY = {"pic_name": "n", "pic_email": "n@x"}
_DEALER_BODY = {"emails": ["a@b.c"]}


async def test_dealer_scoped_manager_cannot_touch_pics(tmp_path) -> None:
    repo = await _repo_with_scoped_user(tmp_path, Scope.build(dealer_slugs=["kl"]), "escalation.manage")
    client, pic_store, _ = _escalation_client(repo)
    assert client.get("/admin/escalation/pics", headers=session("scoped")).json()["pics"] == []
    res = client.put("/admin/escalation/pics/sales", json=_PIC_BODY, headers=session("scoped"))
    assert res.status_code == 403
    assert client.delete("/admin/escalation/pics/sales", headers=session("scoped")).status_code == 403
    pic_store.set.assert_not_called()
    pic_store.delete.assert_not_called()


async def test_department_scoped_manager_sees_only_their_pics_and_no_dealers(tmp_path) -> None:
    repo = await _repo_with_scoped_user(tmp_path, Scope.build(departments=["sales"]), "escalation.manage")
    client, pic_store, dealer_store = _escalation_client(repo)
    pics = client.get("/admin/escalation/pics", headers=session("scoped")).json()["pics"]
    assert [p["department"] for p in pics] == ["sales"]
    assert client.put("/admin/escalation/pics/service", json=_PIC_BODY, headers=session("scoped")).status_code == 403
    assert client.put("/admin/escalation/pics/sales", json=_PIC_BODY, headers=session("scoped")).status_code == 200
    assert client.get("/admin/escalation/dealers", headers=session("scoped")).json()["dealers"] == []
    assert client.put("/admin/escalation/dealers/kl", json=_DEALER_BODY, headers=session("scoped")).status_code == 403
    dealer_store.set.assert_not_called()


async def test_mixed_or_inbox_scope_gets_no_routing_rows(tmp_path) -> None:
    repo = await _repo_with_scoped_user(
        tmp_path, Scope.build(dealer_slugs=["kl"], inbox_ids=[3]), "escalation.manage"
    )
    client, _, dealer_store = _escalation_client(repo)
    assert client.get("/admin/escalation/dealers", headers=session("scoped")).json()["dealers"] == []
    assert client.get("/admin/escalation/pics", headers=session("scoped")).json()["pics"] == []
    assert client.put("/admin/escalation/dealers/kl", json=_DEALER_BODY, headers=session("scoped")).status_code == 403
    dealer_store.set.assert_not_called()


async def test_deny_all_lists_nothing_and_refuses_writes(tmp_path) -> None:
    repo = await _repo_with_scoped_user(tmp_path, Scope.build(dealer_slugs=["kl"]), "escalation.manage")

    async def _boom(user_id):
        raise RuntimeError("down")

    repo.effective_scope = _boom  # type: ignore[method-assign]
    pronet = AsyncMock()
    pronet.list_all.return_value = []
    client, _, dealer_store = _escalation_client(repo, pronet=pronet)
    assert client.get("/admin/escalation/dealers", headers=session("scoped")).json()["dealers"] == []
    assert client.get("/admin/escalation/pics", headers=session("scoped")).json()["pics"] == []
    assert client.put("/admin/escalation/dealers/kl", json=_DEALER_BODY, headers=session("scoped")).status_code == 403
    assert client.get("/admin/escalation/pronet", headers=session("scoped")).status_code == 403
    dealer_store.set.assert_not_called()


async def test_pronet_routing_is_unrestricted_only(tmp_path) -> None:
    repo = await _repo_with_scoped_user(tmp_path, Scope.build(dealer_slugs=["kl"]), "escalation.manage")
    pronet = AsyncMock()
    pronet.list_all.return_value = []
    pronet.get.return_value = None
    client, _, _ = _escalation_client(repo, pronet=pronet)
    assert client.get("/admin/escalation/pronet", headers=session("scoped")).status_code == 403
    assert client.put("/admin/escalation/pronet/central", json={}, headers=session("scoped")).status_code == 403
    assert client.delete("/admin/escalation/pronet/central", headers=session("scoped")).status_code == 403
    pronet.set.assert_not_called()
    pronet.delete.assert_not_called()
    assert client.get("/admin/escalation/pronet", headers=session("plain")).status_code == 200


async def test_case_fields_patch_cannot_move_a_case_out_of_dealer_scope(tmp_path) -> None:
    repo = await _repo_with_scoped_user(
        tmp_path, Scope.build(dealer_slugs=["kl"]), "cases.view", "cases.manage"
    )

    async def _read(conv_id):
        return _conv(1, 3, ["dealer_kl"])

    merge = AsyncMock()
    client = _client(
        build_case_fields_router(
            _read, merge, repo, FakeValidator(TOKENS), scoped_settings(case_fields_enabled=True)
        )
    )

    def _patch(value):
        return client.patch(
            "/cases/1/fields", json={"fields": {"purchased_from_dealer": value}},
            headers=session("scoped"),
        )

    assert _patch("penang").status_code == 403
    assert _patch("").status_code == 403
    merge.assert_not_called()
    assert _patch("kl").status_code == 200
    merge.assert_called_once()


async def test_customer360_returns_nothing_under_deny_all(tmp_path) -> None:
    repo = await _repo_with_scoped_user(tmp_path, Scope.build(inbox_ids=[3]), "customer360.view")

    async def _boom(user_id):
        raise RuntimeError("down")

    repo.effective_scope = _boom  # type: ignore[method-assign]
    chatwoot = AsyncMock()
    client = _client(
        build_customer360_router(
            chatwoot, InMemoryRsaRepository(), repo, FakeValidator(TOKENS), scoped_settings()
        )
    )
    body = client.get("/admin/customer360/search?q=+60123456789", headers=session("scoped")).json()
    assert body == {"contact": None, "conversations": [], "rsa_incidents": []}
    chatwoot.search_contacts.assert_not_called()


async def test_put_role_scope_unknown_role_reread_failure_and_unchanged_audit(tmp_path) -> None:
    repo = await make_repo(tmp_path)
    await repo.create_role("kl_dealer", "KL dealer")
    await repo.assign_role(5, "administrator")
    audit = AsyncMock()
    client = _client(build_authz_router(repo, FakeValidator(TOKENS), scoped_settings(), audit=audit))
    admin = session("admin")
    assert client.put("/authz/roles/nope/scope", json={}, headers=admin).status_code == 404

    assert client.put("/authz/roles/kl_dealer/scope", json={"dealer_slugs": ["kl"]}, headers=admin).status_code == 200
    writes = audit.append.await_count
    assert writes == 1
    # Same scope again: applied (idempotent), but no audit row for a no-op.
    assert client.put("/authz/roles/kl_dealer/scope", json={"dealer_slugs": ["kl"]}, headers=admin).status_code == 200
    assert audit.append.await_count == writes

    real_get = repo.get_role_scope
    calls = {"n": 0}

    async def _flaky(role_id):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("read failed after write")
        return await real_get(role_id)

    repo.get_role_scope = _flaky  # type: ignore[method-assign]
    res = client.put("/authz/roles/kl_dealer/scope", json={"dealer_slugs": ["penang"]}, headers=admin)
    assert res.status_code == 200
    assert res.json()["scope"]["dealer_slugs"] == ["penang"]
    repo.get_role_scope = real_get  # type: ignore[method-assign]
    assert (await repo.get_role_scope("kl_dealer")).dealer_slugs == {"penang"}
