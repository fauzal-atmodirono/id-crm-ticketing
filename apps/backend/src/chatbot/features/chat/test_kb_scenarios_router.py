"""Tests for kb_scenarios_router — CRUD, validation, auth, list filtering."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.scenarios_store import InMemoryScenariosStore
from chatbot.features.chat.adapters.tools_store import CustomTool, InMemoryToolsStore
from chatbot.features.chat.kb_scenarios_router import build_kb_scenarios_router
from chatbot.platform.config import Settings

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _settings(**kw) -> Settings:
    base = {
        "faq_admin_api_key": "fk",
        "proton_backend_key": "pk",
    }
    base.update(kw)
    return Settings(**base)


def _client(
    scenarios_store=None,
    tools_store=None,
    settings: Settings | None = None,
) -> TestClient:
    if scenarios_store is None:
        scenarios_store = InMemoryScenariosStore()
    if tools_store is None:
        tools_store = InMemoryToolsStore()
    if settings is None:
        settings = _settings()
    app = FastAPI()
    app.include_router(build_kb_scenarios_router(scenarios_store, tools_store, settings))
    return TestClient(app, raise_server_exceptions=False)


def _valid_payload(**kw) -> dict:
    base = {
        "assistant_id": "asst_1",
        "title": "Warranty Claim",
        "description": "How to handle warranty claims",
        "instruction": "When a customer mentions warranty, ask for proof of purchase.",
        "tools": [],
        "enabled": True,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_list_requires_api_key() -> None:
    c = _client()
    r = c.get("/kb/scenarios", params={"assistant_id": "asst_1"})
    assert r.status_code == 401


def test_list_wrong_key_rejected() -> None:
    c = _client()
    r = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "nope"})
    assert r.status_code == 401


def test_list_proton_key_accepted() -> None:
    c = _client()
    r = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "pk"})
    assert r.status_code == 200


def test_list_faq_key_accepted() -> None:
    c = _client()
    r = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "fk"})
    assert r.status_code == 200


def test_create_requires_api_key() -> None:
    c = _client()
    r = c.post("/kb/scenarios", json=_valid_payload())
    assert r.status_code == 401


def test_put_requires_api_key() -> None:
    c = _client()
    r = c.put("/kb/scenarios/fake-id", json={"title": "New"})
    assert r.status_code == 401


def test_delete_requires_api_key() -> None:
    c = _client()
    r = c.delete("/kb/scenarios/fake-id")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# CRUD: list, create, update, delete
# ---------------------------------------------------------------------------


def test_list_empty() -> None:
    c = _client()
    r = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "pk"})
    assert r.status_code == 200
    assert r.json() == {"scenarios": []}


def test_create_returns_id() -> None:
    c = _client()
    r = c.post("/kb/scenarios", json=_valid_payload(), headers={"x-api-key": "pk"})
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert isinstance(body["id"], str) and body["id"]


def test_create_then_list() -> None:
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)
    r = c.post("/kb/scenarios", json=_valid_payload(), headers={"x-api-key": "pk"})
    assert r.status_code == 200
    scenario_id = r.json()["id"]

    r2 = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "pk"})
    assert r2.status_code == 200
    scenarios = r2.json()["scenarios"]
    assert len(scenarios) == 1
    assert scenarios[0]["id"] == scenario_id
    assert scenarios[0]["title"] == "Warranty Claim"


def test_update_scenario() -> None:
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)
    scenario_id = c.post("/kb/scenarios", json=_valid_payload(), headers={"x-api-key": "pk"}).json()["id"]

    r = c.put(f"/kb/scenarios/{scenario_id}", json={"title": "Updated Title"}, headers={"x-api-key": "pk"})
    assert r.status_code == 200
    assert r.json() == {"id": scenario_id, "status": "ok"}

    scenarios = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "pk"}).json()["scenarios"]
    assert scenarios[0]["title"] == "Updated Title"


def test_update_nonexistent_returns_404() -> None:
    c = _client()
    r = c.put("/kb/scenarios/nonexistent", json={"title": "X"}, headers={"x-api-key": "pk"})
    assert r.status_code == 404


def test_delete_scenario() -> None:
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)
    scenario_id = c.post("/kb/scenarios", json=_valid_payload(), headers={"x-api-key": "pk"}).json()["id"]

    r = c.delete(f"/kb/scenarios/{scenario_id}", headers={"x-api-key": "pk"})
    assert r.status_code == 200
    assert r.json() == {"id": scenario_id, "status": "ok"}

    scenarios = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "pk"}).json()["scenarios"]
    assert scenarios == []


def test_delete_nonexistent_returns_404() -> None:
    c = _client()
    r = c.delete("/kb/scenarios/nonexistent", headers={"x-api-key": "pk"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# list filters by assistant_id
# ---------------------------------------------------------------------------


def test_list_filters_by_assistant_id() -> None:
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)

    c.post("/kb/scenarios", json=_valid_payload(assistant_id="asst_1", title="A1"), headers={"x-api-key": "pk"})
    c.post("/kb/scenarios", json=_valid_payload(assistant_id="asst_2", title="A2"), headers={"x-api-key": "pk"})

    r1 = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "pk"})
    scenarios_1 = r1.json()["scenarios"]
    assert len(scenarios_1) == 1
    assert scenarios_1[0]["title"] == "A1"

    r2 = c.get("/kb/scenarios", params={"assistant_id": "asst_2"}, headers={"x-api-key": "pk"})
    scenarios_2 = r2.json()["scenarios"]
    assert len(scenarios_2) == 1
    assert scenarios_2[0]["title"] == "A2"


# ---------------------------------------------------------------------------
# Tool slug validation
# ---------------------------------------------------------------------------


def test_builtin_tool_slug_accepted() -> None:
    """A built-in tool name is always valid."""
    c = _client()
    payload = _valid_payload(tools=["search_knowledge_base"])
    r = c.post("/kb/scenarios", json=payload, headers={"x-api-key": "pk"})
    assert r.status_code == 200


def test_unknown_tool_slug_rejected_with_422() -> None:
    """A slug that is neither a built-in nor a registered custom tool → 422."""
    c = _client()
    payload = _valid_payload(tools=["no_such_tool_xyz"])
    r = c.post("/kb/scenarios", json=payload, headers={"x-api-key": "pk"})
    assert r.status_code == 422
    assert "no_such_tool_xyz" in r.json()["detail"]


async def test_custom_tool_slug_accepted() -> None:
    """A slug that exists in the tools store is valid."""
    tools_store = InMemoryToolsStore()
    ct = CustomTool(
        slug="custom_check_order",
        title="Check Order",
        description="Checks order status",
        endpoint_url="https://example.com/hook",
        http_method="POST",
        auth_type="none",
        param_schema={"type": "object", "properties": {}},
        enabled=True,
    )
    await tools_store.create_custom(ct)

    c = _client(tools_store=tools_store)
    payload = _valid_payload(tools=["custom_check_order"])
    r = c.post("/kb/scenarios", json=payload, headers={"x-api-key": "pk"})
    assert r.status_code == 200


def test_update_with_unknown_tool_slug_rejected() -> None:
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)
    scenario_id = c.post("/kb/scenarios", json=_valid_payload(), headers={"x-api-key": "pk"}).json()["id"]

    r = c.put(
        f"/kb/scenarios/{scenario_id}",
        json={"tools": ["bogus_tool"]},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Instruction length cap
# ---------------------------------------------------------------------------


def test_instruction_within_limit_accepted() -> None:
    c = _client()
    payload = _valid_payload(instruction="x" * 4096)
    r = c.post("/kb/scenarios", json=payload, headers={"x-api-key": "pk"})
    assert r.status_code == 200


def test_instruction_over_limit_rejected_with_422() -> None:
    c = _client()
    payload = _valid_payload(instruction="x" * 4097)
    r = c.post("/kb/scenarios", json=payload, headers={"x-api-key": "pk"})
    assert r.status_code == 422
    assert "4097" in r.json()["detail"] or "4096" in r.json()["detail"]


def test_update_instruction_over_limit_rejected() -> None:
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)
    scenario_id = c.post("/kb/scenarios", json=_valid_payload(), headers={"x-api-key": "pk"}).json()["id"]

    r = c.put(
        f"/kb/scenarios/{scenario_id}",
        json={"instruction": "y" * 4097},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Scenario cap (≤ 20)
# ---------------------------------------------------------------------------


def test_cap_20_scenarios_per_assistant() -> None:
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)

    # Create 20 scenarios.
    for i in range(20):
        r = c.post("/kb/scenarios", json=_valid_payload(title=f"S{i}"), headers={"x-api-key": "pk"})
        assert r.status_code == 200, f"Failed on scenario {i}: {r.json()}"

    # 21st must be rejected.
    r = c.post("/kb/scenarios", json=_valid_payload(title="S20"), headers={"x-api-key": "pk"})
    assert r.status_code == 409
    assert "20" in r.json()["detail"]


def test_cap_applies_per_assistant_not_globally() -> None:
    """The 20-scenario cap is per assistant_id — another assistant can still create."""
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)

    for i in range(20):
        c.post("/kb/scenarios", json=_valid_payload(assistant_id="asst_A", title=f"S{i}"), headers={"x-api-key": "pk"})

    # Different assistant_id → should succeed.
    r = c.post("/kb/scenarios", json=_valid_payload(assistant_id="asst_B", title="S0"), headers={"x-api-key": "pk"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# enabled flag persisted
# ---------------------------------------------------------------------------


def test_enabled_false_persisted() -> None:
    store = InMemoryScenariosStore()
    c = _client(scenarios_store=store)
    payload = _valid_payload(enabled=False)
    scenario_id = c.post("/kb/scenarios", json=payload, headers={"x-api-key": "pk"}).json()["id"]

    scenarios = c.get("/kb/scenarios", params={"assistant_id": "asst_1"}, headers={"x-api-key": "pk"}).json()["scenarios"]
    assert scenarios[0]["id"] == scenario_id
    assert scenarios[0]["enabled"] is False
