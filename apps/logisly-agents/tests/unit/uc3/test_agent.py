import importlib

from google.adk.tools import FunctionTool

from logisly_agents.config import Settings
from logisly_agents.uc3.agent import build_uc3_agent
from logisly_agents.uploads import convert_uploads

NAMES = {
    "get_templates", "get_conventions", "get_team", "search_issues", "get_issue", "capacity_check",
    "save_document", "propose_changes", "apply_proposal",
    "create_issue", "update_issue", "comment_issue", "close_issue",
}
WRITES = {"apply_proposal", "create_issue", "update_issue", "comment_issue", "close_issue"}


def _tools(agent):
    wrapped = [t if isinstance(t, FunctionTool) else FunctionTool(t) for t in agent.tools]
    return {t.name: t for t in wrapped}


def test_agent_wiring():
    agent = build_uc3_agent(Settings())
    assert agent.name == "logisly_product_manager"
    assert set(_tools(agent)) == NAMES
    assert agent.before_model_callback is convert_uploads
    for n in WRITES:
        t = _tools(agent)[n]
        assert isinstance(t, FunctionTool) and t._require_confirmation is False
    on = _tools(build_uc3_agent(Settings(require_confirmation=True)))
    assert all(on[n]._require_confirmation is True for n in WRITES)


def test_declarations_hide_tool_context():
    for t in _tools(build_uc3_agent(Settings())).values():
        schema = t._get_declaration().parameters_json_schema or {}
        assert "tool_context" not in schema.get("properties", {})


def test_instruction_copy_rules():
    from logisly_agents.uc3.agent import INSTRUCTION

    assert "—" not in INSTRUCTION and "Proton" not in INSTRUCTION


def test_adk_entry_imports():
    mod = importlib.import_module("adk_apps.uc3_product.agent")
    assert mod.root_agent.name == "logisly_product_manager"


def test_instruction_carries_demo_date_and_proposal_flow():
    from datetime import date

    agent = build_uc3_agent(Settings(as_of=date(2026, 11, 9)))
    assert "Tanggal hari ini: 09-11-2026 (gunakan tanggal ini, bukan tanggal sistem)." in agent.instruction
    assert "propose_changes" in agent.instruction and "apply_proposal" in agent.instruction
    assert "—" not in agent.instruction
