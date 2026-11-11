import os
from types import SimpleNamespace

from chatbot.features.chat.agents import build_ai_agent
from chatbot.features.chat.prompts import (
    AGENT_INSTRUCTION,
    DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION,
    LOGISLY_AGENT_INSTRUCTION,
    LOGISLY_MEDIA_INSTRUCTION,
    build_agent_instruction,
    instructions_for_persona,
)
from chatbot.features.logisly import milestones
from chatbot.features.logisly.trips import ACTIVE_SESSIONS, TRIPS, TripStore, describe
from chatbot.platform.config import Settings


def test_proton_is_the_default_persona_and_unchanged():
    assert instructions_for_persona("proton") == (
        AGENT_INSTRUCTION,
        DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION,
    )
    assert (
        build_agent_instruction(media_diagnosis_prompt_enabled=True, has_image=False)
        == AGENT_INSTRUCTION
    )


def test_logisly_persona_swaps_base_and_media_wording():
    base, media = instructions_for_persona("logisly")
    assert base is LOGISLY_AGENT_INSTRUCTION and media is LOGISLY_MEDIA_INSTRUCTION
    out = build_agent_instruction(
        media_diagnosis_prompt_enabled=True,
        has_image=True,
        base=base,
        default_media_instruction=media,
    )
    assert out == f"{LOGISLY_AGENT_INSTRUCTION}\n\n{LOGISLY_MEDIA_INSTRUCTION}"
    assert "Proton" not in out


def test_logisly_prompt_keeps_ordinary_trip_events_out_of_escalation():
    for phrase in ("Traffic or congestion", "flat or burst tyre", "not a complaint"):
        assert phrase in LOGISLY_AGENT_INSTRUCTION


def test_trip_store_lookup_is_case_insensitive_and_records_events():
    store = TripStore.seeded()
    assert store.get("lgs-24017")["driver_name"] == "Pak Budi Santoso"
    trip = store.update("LGS-24017", "delayed", "Ban pecah, ganti ban di rest area KM 57")
    assert trip["status"] == "delayed"
    assert "Ban pecah" in describe(trip)
    assert store.update("LGS-00000", "delayed", "x") is None


def test_unknown_status_is_logged_but_does_not_change_status():
    store = TripStore.seeded()
    trip = store.update("LGS-24017", "teleported", "x")
    assert trip["status"] == "on_trip" and trip["events"][-1]["status"] == "teleported"


def test_ambiguous_problems_get_one_question_before_escalation():
    assert "ask before escalating" in LOGISLY_AGENT_INSTRUCTION
    assert "only after the driver says they cannot continue" in LOGISLY_AGENT_INSTRUCTION
    assert "is not an accident" in LOGISLY_MEDIA_INSTRUCTION


def test_driver_eta_replaces_planned_eta_only_when_given():
    store = TripStore.seeded()
    store.update("LGS-24017", "delayed", "Telat 1 jam", "2026-11-06 15:45 WIB")
    assert store.get("LGS-24017")["eta"] == "2026-11-06 15:45 WIB"
    store.update("LGS-24017", "on_trip", "Jalan lagi")
    assert store.get("LGS-24017")["eta"] == "2026-11-06 15:45 WIB"


def test_logisly_prompt_has_checklist_rules():
    text = LOGISLY_AGENT_INSTRUCTION
    assert "## Milestone checklist" in text
    for phrase in ("record_checklist_tool", "two", "install", "belum bisa login"):
        assert phrase in text
    assert "overlay" in LOGISLY_MEDIA_INSTRUCTION


def _logisly_tools():
    os.environ.setdefault("GOOGLE_API_KEY", "test-key")
    settings = Settings()
    agent = build_ai_agent(settings, None, None, persona="logisly")
    return {t.__name__: t for t in agent.tools}, agent


def test_logisly_tools_include_record_checklist():
    tools, agent = _logisly_tools()
    assert "record_checklist_tool" in tools
    assert agent.tools[0].__name__ == "record_checklist_tool"


async def test_record_checklist_tool_records_and_queues_ticket():
    tools, _ = _logisly_tools()
    trip = TRIPS.get("LGS-24017")
    ms = "at_pickup"
    item = next(i for i in milestones.CHECKLISTS[ms] if i.team)
    ctx = SimpleNamespace(state={}, session=SimpleNamespace(id="crm-777"))
    ACTIVE_SESSIONS["crm-777"] = {"order_id": "LGS-24017", "milestone": ms}
    before = len(milestones.PENDING_TICKETS)
    try:
        out = await tools["record_checklist_tool"](ctx, "LGS-24017", item.id, False, "B 9 XX")
        assert out == "[internal] not_ok"
        assert ctx.state["order_id"] == "LGS-24017"
        assert milestones.PENDING_TICKETS[-1]["session_id"] == "crm-777"
        assert (
            await tools["record_checklist_tool"](ctx, "LGS-00000", item.id, True, "")
        ).startswith("NOT_FOUND")
        assert "ERROR" in await tools["record_checklist_tool"](ctx, "LGS-24017", "nope", True, "")
    finally:
        ACTIVE_SESSIONS.pop("crm-777", None)
        del milestones.PENDING_TICKETS[before:]
        trip.get("checklists", {}).pop(ms, None)
