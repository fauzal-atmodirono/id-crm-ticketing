import importlib

from google.adk.tools import FunctionTool

from logisly_agents.config import Settings
from logisly_agents.uc2.agent import build_uc2_agent
from logisly_agents.uploads import convert_uploads

NAMES = {
    "record_tender",
    "record_quotation",
    "list_quotations",
    "compare_quotes",
    "price_history",
    "query_pricing_sql",
    "record_decision",
}


def _tools(agent):
    wrapped = [t if isinstance(t, FunctionTool) else FunctionTool(t) for t in agent.tools]
    return {t.name: t for t in wrapped}


def test_agent_wiring(monkeypatch):
    agent = build_uc2_agent(Settings())
    assert agent.name == "logisly_purchasing"
    assert set(_tools(agent)) == NAMES
    assert agent.before_model_callback is convert_uploads
    dec = _tools(agent)["record_decision"]
    assert isinstance(dec, FunctionTool) and dec._require_confirmation is False
    assert _tools(build_uc2_agent(Settings(require_confirmation=True)))[
        "record_decision"
    ]._require_confirmation is True
    monkeypatch.setenv("LOGISLY_REQUIRE_CONFIRMATION", "true")
    assert Settings().require_confirmation is True


def test_declarations_hide_tool_context():
    agent = build_uc2_agent(Settings())
    for t in _tools(agent).values():
        schema = t._get_declaration().parameters_json_schema or {}
        assert "tool_context" not in schema.get("properties", {})
    schema = _tools(agent)["record_quotation"]._get_declaration().parameters_json_schema
    assert schema["required"] == ["quotation"]
    assert "lines" in str(schema["properties"]["quotation"]) or "$defs" in schema


def test_adk_entry_imports():
    mod = importlib.import_module("adk_apps.uc2_purchasing.agent")
    assert mod.root_agent.name == "logisly_purchasing"
